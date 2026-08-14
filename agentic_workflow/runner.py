from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .agents import AgentBackend, create_experiment_backend, describe_agent_roles
from .config import WorkflowConfig
from .context import build_context, build_observation
from .disturbances import apply_disturbances
from .io import (
    WorkbookSeries,
    dataframe_records,
    initialize_realtime_plan,
    load_day_ahead_reference,
    load_disturbances,
    load_forecast_tables,
)
from .models import (
    EvaluationDecision,
    EvaluationFeedback,
    NoticeInterpretation,
    PricingDecision,
    TriggerDecision,
)
from .notices import (
    NoticeRecord,
    NoticeSeries,
    apply_notice_updates,
    frozen_rule_parse,
    merge_interpretations,
    resolve_notice_coreferences,
)
from .optimizer import OptimizerBackend, create_optimizer_backend
from .physical_events import (
    PhysicalEventSeries,
    advance_realized_energy,
    advance_physical_notice_truth,
    apply_realized_energy_to_observation,
    interpretation_active_at,
    realize_notice_consequences,
    settle_realized_step,
)
from .state import WorkflowState
from .telemetry import ResourceMeter, summarize_agent_calls, system_profile


OBJECTIVE_TIE_TOLERANCE = 1e-3


@dataclass(slots=True)
class OptimizationCandidate:
    pricing: PricingDecision
    result: dict[str, Any]
    evaluation: EvaluationDecision
    attempt: int


class WorkflowRunner:
    def __init__(
        self,
        config: WorkflowConfig,
        *,
        agents: AgentBackend | None = None,
        optimizer: OptimizerBackend | None = None,
    ):
        config.validate()
        self.config = config
        self.day_ahead = load_day_ahead_reference(config.state_workbook, config.mode)
        forecast_prices, forecast_energy = load_forecast_tables(
            config.forecast_workbook,
            config.spot_prices_workbook,
        )
        self.realtime_series = WorkbookSeries(config.realtime_states)
        self.price_series = WorkbookSeries(config.intraday_prices)
        requested = set(range(config.start_timestep, config.end_timestep + 1))
        for label, series in (
            ("realtime state", self.realtime_series),
            ("intraday price", self.price_series),
        ):
            missing = sorted(requested - set(series.timesteps))
            if missing:
                raise ValueError(f"Missing {label} workbooks for timesteps: {missing}")
        self.scenarios = load_disturbances(config.disturbance_workbook, config.scenario_ids)
        self.notices = NoticeSeries(config.notices_file)
        self.physical_events = PhysicalEventSeries(config.physical_events_file)
        self.state = WorkflowState(
            realtime_plan=initialize_realtime_plan(self.day_ahead),
            forecast_prices=forecast_prices.copy(),
            forecast_energy=forecast_energy.copy(),
        )
        self.state.initialize_tariff_schedule(
            list(self.day_ahead.summary.get("buy_multipliers") or []),
            list(self.day_ahead.summary.get("sell_multipliers") or []),
        )
        self.agents = agents or create_experiment_backend(
            config.experiment_configuration, config.agent_backend, config.model
        )
        self._agent_call_cursor = 0
        self.optimizer = optimizer or create_optimizer_backend(
            config.optimizer_backend,
            url=config.optimizer_url,
            timeout_seconds=config.request_timeout_seconds,
            poll_interval_seconds=config.poll_interval_seconds,
        )

    def _workbook_inputs(self, timestep: int) -> dict[str, pd.DataFrame]:
        required = ["Buses", "Chargers", "Trips"]
        available = self.realtime_series.read_sheets(timestep, required)
        try:
            available["Realtime state"] = self.realtime_series.read_sheet(timestep, "Realtime state")
        except ValueError:
            available["Realtime state"] = pd.DataFrame()
        return available

    def _build_payload(
        self,
        *,
        timestep: int,
        workbook: dict[str, pd.DataFrame],
        observation: list[dict[str, Any]],
        disturbance: Any,
        pricing: PricingDecision,
        trigger: TriggerDecision,
    ) -> dict[str, Any]:
        effective_notice = self._effective_notice_interpretation(trigger)
        revised_chargers, revised_trips, revised_energy = apply_notice_updates(
            effective_notice,
            chargers=workbook["Chargers"],
            trips=disturbance.trips,
            energy_consumption=disturbance.energy_consumption,
        )
        return {
            "input": {
                "buses": dataframe_records(workbook["Buses"]),
                "chargers": dataframe_records(revised_chargers),
                "trip_time": dataframe_records(revised_trips),
                "energy_consumption": dataframe_records(revised_energy),
                "prices": dataframe_records(disturbance.prices),
                "realtime_state": observation,
                "timestep_minutes": 30,
                "disturbance": disturbance.scenarios,
                "v2g_enabled": True,
            },
            "optimization_mode": "real_time",
            # The observation at t has already settled interval t. Optimize
            # only executable future intervals, beginning at t+1.
            "current_timestep": timestep + 1,
            "price_guidance": {
                "buy_multipliers": pricing.buy_multipliers,
                "sell_multipliers": pricing.sell_multipliers,
                "mode": self.config.mode,
            },
            "disturbances": [
                {**item, "already_applied": True}
                for item in disturbance.optimizer_disturbances
            ],
            "rerun_count": 0,
            "v2g_enabled": True,
        }

    def _is_better(
        self,
        candidate: OptimizationCandidate,
        incumbent: OptimizationCandidate | None,
    ) -> bool:
        if not self._candidate_is_usable(candidate):
            return False
        if incumbent is None or not self._candidate_is_usable(incumbent):
            return True
        if self.config.mode == "selfish":
            candidate_value = candidate.result.get("aggregator_revenue")
            incumbent_value = incumbent.result.get("aggregator_revenue")
            candidate_score = float(
                candidate_value if candidate_value is not None else float("-inf")
            )
            incumbent_score = float(
                incumbent_value if incumbent_value is not None else float("-inf")
            )
            if candidate_score > incumbent_score + OBJECTIVE_TIE_TOLERANCE:
                return True
            if candidate_score < incumbent_score - OBJECTIVE_TIE_TOLERANCE:
                return False
        else:
            candidate_value = candidate.result.get("pto_daily_cost")
            incumbent_value = incumbent.result.get("pto_daily_cost")
            candidate_score = float(
                candidate_value if candidate_value is not None else float("inf")
            )
            incumbent_score = float(
                incumbent_value if incumbent_value is not None else float("inf")
            )
            if candidate_score < incumbent_score - OBJECTIVE_TIE_TOLERANCE:
                return True
            if candidate_score > incumbent_score + OBJECTIVE_TIE_TOLERANCE:
                return False
        # When the objective is tied, prefer the evaluator-accepted candidate.
        return candidate.evaluation.accept and not incumbent.evaluation.accept

    @staticmethod
    def _candidate_is_usable(candidate: OptimizationCandidate) -> bool:
        if candidate.result.get("is_mock"):
            return False
        solver_status = str(candidate.result.get("solver_status", "")).lower()
        return solver_status not in {"infeasible", "error", "unknown", "mock"}

    def _record_attempt(
        self,
        *,
        timestep: int,
        attempt: int,
        pricing: PricingDecision,
        result: dict[str, Any],
        evaluation: EvaluationDecision,
    ) -> None:
        optimizer_telemetry = result.get("optimizer_telemetry") or {}
        solver_telemetry = result.get("solver_telemetry") or {}
        self.state.attempts.append(
            {
                "timestep": timestep,
                "attempt": attempt,
                "mode": self.config.mode,
                "buy_multipliers": json.dumps(pricing.buy_multipliers),
                "sell_multipliers": json.dumps(pricing.sell_multipliers),
                "pricing_reasoning": pricing.reasoning,
                "is_mock": result.get("is_mock"),
                "solver_status": result.get("solver_status"),
                "solver_name": result.get("solver_name"),
                "solver_fallback_errors": json.dumps(result.get("solver_fallback_errors", [])),
                "optimizer_latency_seconds": result.get("optimizer_latency_seconds"),
                "optimizer_process_cpu_seconds": optimizer_telemetry.get(
                    "process_cpu_seconds"
                ),
                "optimizer_average_cpu_cores": optimizer_telemetry.get(
                    "average_cpu_cores"
                ),
                "optimizer_peak_rss_mb": optimizer_telemetry.get("peak_rss_mb"),
                "optimizer_peak_rss_delta_mb": optimizer_telemetry.get(
                    "peak_rss_delta_mb"
                ),
                "solver_wall_seconds": solver_telemetry.get("wall_seconds"),
                "solver_process_cpu_seconds": solver_telemetry.get(
                    "process_cpu_seconds"
                ),
                "solver_average_cpu_cores": solver_telemetry.get("average_cpu_cores"),
                "solver_peak_rss_mb": solver_telemetry.get("peak_rss_mb"),
                "solver_peak_rss_delta_mb": solver_telemetry.get("peak_rss_delta_mb"),
                "solver_branch_and_bound_nodes": solver_telemetry.get(
                    "branch_and_bound_nodes"
                ),
                "solver_iterations": solver_telemetry.get("iterations"),
                "solver_reported_time_seconds": solver_telemetry.get(
                    "reported_time_seconds"
                ),
                "model_variables": solver_telemetry.get("model_variables"),
                "model_constraints": solver_telemetry.get("model_constraints"),
                "model_binary_variables": solver_telemetry.get(
                    "model_binary_variables"
                ),
                "model_integer_variables": solver_telemetry.get(
                    "model_integer_variables"
                ),
                "solver_lower_bound": solver_telemetry.get("lower_bound"),
                "solver_upper_bound": solver_telemetry.get("upper_bound"),
                "solver_relative_gap": solver_telemetry.get("relative_gap"),
                "solver_attempts": json.dumps(result.get("solver_attempts", [])),
                "pto_daily_cost": result.get("pto_daily_cost"),
                "aggregator_revenue": result.get("aggregator_revenue"),
                "total_kwh_bought": result.get("total_kwh_bought"),
                "total_kwh_sold": result.get("total_kwh_sold"),
                "proposed_period_boundaries": json.dumps(
                    result.get("period_boundaries", []), default=str
                ),
                "proposed_w_buy_kwh": json.dumps(result.get("w_buy", []), default=str),
                "proposed_w_sell_kwh": json.dumps(
                    result.get("w_sell", []), default=str
                ),
                "proposed_energy_by_bus_kwh": json.dumps(
                    result.get("energy", []), default=str
                ),
                "accepted": evaluation.accept,
                "selected_for_execution": evaluation.accept,
                "evaluator_accepted": evaluation.accept,
                "forced_at_rerun_cap": False,
                "retained_better_candidate": False,
                "evaluation_reasoning": evaluation.reasoning,
                "selection_reasoning": (
                    "Accepted by evaluator." if evaluation.accept else None
                ),
                "feedback": evaluation.feedback.model_dump_json(),
            }
        )

    def _optimize(
        self,
        *,
        timestep: int,
        context: dict[str, Any],
        trigger: TriggerDecision,
        workbook: dict[str, pd.DataFrame],
        observation: list[dict[str, Any]],
        disturbance: Any,
    ) -> OptimizationCandidate:
        previous: PricingDecision | None = None
        feedback: EvaluationFeedback | None = None
        best: OptimizationCandidate | None = None
        last: OptimizationCandidate | None = None
        for attempt in range(1, self.config.max_reruns + 2):
            try:
                pricing = self.agents.price(
                    context,
                    trigger,
                    rerun_count=attempt - 1,
                    previous=previous,
                    feedback=feedback,
                )
            except Exception:
                self._checkpoint_agent_failure(timestep)
                raise
            payload = self._build_payload(
                timestep=timestep,
                workbook=workbook,
                observation=observation,
                disturbance=disturbance,
                pricing=pricing,
                trigger=trigger,
            )
            payload["rerun_count"] = attempt - 1
            with ResourceMeter() as optimizer_meter:
                result = self.optimizer.optimize(payload)
            optimizer_telemetry = optimizer_meter.metrics or {}
            result["optimizer_telemetry"] = optimizer_telemetry
            result["optimizer_latency_seconds"] = optimizer_telemetry.get("wall_seconds")
            try:
                evaluation = self.agents.evaluate(
                    context,
                    trigger,
                    pricing,
                    result,
                    rerun_count=attempt - 1,
                )
            except Exception:
                self._checkpoint_agent_failure(timestep)
                raise
            if result.get("is_mock") and evaluation.accept:
                evaluation = evaluation.model_copy(
                    update={
                        "accept": False,
                        "reasoning": "A mock optimizer result cannot be accepted.",
                    }
                )
            candidate = OptimizationCandidate(
                pricing=pricing,
                result=result,
                evaluation=evaluation,
                attempt=attempt,
            )
            self._record_attempt(
                timestep=timestep,
                attempt=attempt,
                pricing=pricing,
                result=result,
                evaluation=evaluation,
            )
            if self._is_better(candidate, best):
                best = candidate
            last = candidate
            if evaluation.accept and not result.get("is_mock"):
                break
            previous = pricing
            feedback = evaluation.feedback
        if best is not None:
            evaluator_accepted_any = any(
                bool(row.get("evaluator_accepted"))
                for row in self.state.attempts
                if row["timestep"] == timestep
            )
            retained_better_candidate = (
                evaluator_accepted_any and not best.evaluation.accept
            )
            if not best.evaluation.accept:
                if retained_better_candidate:
                    selection_reasoning = (
                        "A solver-usable earlier attempt had a better mode-aligned "
                        "economic objective than the evaluator-accepted rerun, so the "
                        "earlier schedule was retained."
                    )
                else:
                    selection_reasoning = (
                        f"Rerun cap of {self.config.max_reruns} reached; "
                        "the best solver-usable result was retained."
                    )
                accepted = best.evaluation.model_copy(
                    update={
                        "accept": True,
                        "reasoning": selection_reasoning,
                    }
                )
                best = OptimizationCandidate(
                    pricing=best.pricing,
                    result=best.result,
                    evaluation=accepted,
                    attempt=best.attempt,
                )
                for attempt_row in self.state.attempts:
                    if attempt_row["timestep"] == timestep:
                        attempt_row["accepted"] = False
                        attempt_row["selected_for_execution"] = False
                for attempt_row in reversed(self.state.attempts):
                    if attempt_row["timestep"] == timestep and attempt_row["attempt"] == best.attempt:
                        attempt_row["accepted"] = True
                        attempt_row["selected_for_execution"] = True
                        attempt_row["forced_at_rerun_cap"] = not evaluator_accepted_any
                        attempt_row["retained_better_candidate"] = (
                            retained_better_candidate
                        )
                        attempt_row["selection_reasoning"] = accepted.reasoning
                        break
            else:
                for attempt_row in self.state.attempts:
                    if attempt_row["timestep"] == timestep:
                        selected = attempt_row["attempt"] == best.attempt
                        attempt_row["accepted"] = selected
                        attempt_row["selected_for_execution"] = selected
                        if selected:
                            attempt_row["selection_reasoning"] = (
                                "Selected as the best solver-usable evaluator-accepted "
                                "attempt."
                            )
            return best
        if last is None:
            raise RuntimeError("The optimization loop produced no attempts")
        return last

    def run(self) -> WorkflowState:
        run_meter = ResourceMeter().start()
        run_status = "complete"
        try:
            for timestep in range(self.config.start_timestep, self.config.end_timestep + 1):
                timestep_meter = ResourceMeter().start()
                timestep_started = time.perf_counter()
                workbook = self._workbook_inputs(timestep)
                prices = self.price_series.read_sheet(timestep, "Prices")
                prices = prices[["timestep", "spot_market"]].dropna().copy()
                prices["timestep"] = prices["timestep"].astype(int)
                disturbance = apply_disturbances(
                scenarios=self.scenarios,
                timestep=timestep,
                prices=prices,
                trips=workbook["Trips"],
                realtime_plan=self.state.realtime_plan,
                )
                notice_records = self.notices.at(
                timestep,
                scenario_ids=self.config.notice_scenario_ids,
                wording_variant=self.config.notice_variant,
                )
                planner_assumption = merge_interpretations(
                    NoticeInterpretation.model_validate(value)
                    for _, value in sorted(
                        self.state.active_notice_interpretations.items()
                    )
                )
                planner_assumption_now = interpretation_active_at(
                    planner_assumption, timestep
                )
                physical_truth = None
                observable_truth = None
                observation_disturbance = disturbance
                if self.config.realize_notice_truth:
                    if self.config.physical_events_file is not None:
                        physical_truth = self.physical_events.truth_at(
                            timestep,
                            scenario_ids=self.config.notice_scenario_ids,
                        )
                        observable_truth = self.physical_events.truth_at(
                            timestep,
                            scenario_ids=self.config.notice_scenario_ids,
                            observable=True,
                        )
                    else:
                        physical_truth = advance_physical_notice_truth(
                            notice_records,
                            self.state.active_physical_notice_interpretations,
                        )
                        observable_truth = physical_truth
                    observation_disturbance = realize_notice_consequences(
                        disturbance, observable_truth, planner_assumption_now
                    )
                observation = build_observation(
                timestep=timestep,
                realtime_plan=self.state.realtime_plan,
                disturbance=observation_disturbance,
                workbook_state=workbook.get("Realtime state"),
                state_source=self.config.state_source,
                )
                if self.config.realize_notice_truth:
                    realized_energy = advance_realized_energy(
                        timestep=timestep,
                        realtime_plan=self.state.realtime_plan,
                        disturbance=observation_disturbance,
                        chargers=workbook["Chargers"],
                        trips=disturbance.trips,
                        energy_consumption=disturbance.energy_consumption,
                        physical_truth=physical_truth,
                        realized_energy_by_bus=self.state.realized_energy_by_bus,
                    )
                    observation = apply_realized_energy_to_observation(
                        observation, realized_energy
                    )
                context = build_context(
                mode=self.config.mode,
                timestep=timestep,
                observation=observation,
                realtime_plan=self.state.realtime_plan,
                day_ahead=self.day_ahead,
                forecast_prices=self.state.forecast_prices,
                forecast_energy=self.state.forecast_energy,
                disturbance=observation_disturbance,
                price_history=self.state.price_history,
                context_history=self.state.context_history,
                )
                context["operational_notices"] = [record.public_dict() for record in notice_records]
                context["maximum_reruns"] = self.config.max_reruns
                context["numerical_event_telemetry"] = (
                    {
                        "return_delay_minutes_by_bus": dict(
                            observable_truth.updates.return_delay_minutes_by_bus
                        ),
                        "charger_power_kw": dict(
                            observable_truth.updates.charger_power_kw
                        ),
                        "unavailable_chargers": list(
                            observable_truth.updates.unavailable_chargers
                        ),
                        "effective_timestep": observable_truth.effective_timestep,
                        "expected_end_timestep": observable_truth.expected_end_timestep,
                    }
                    if observable_truth is not None
                    else {
                        "return_delay_minutes_by_bus": {},
                        "charger_power_kw": {},
                        "unavailable_chargers": [],
                        "effective_timestep": None,
                        "expected_end_timestep": None,
                    }
                )
                context["active_operational_events"] = [
                    value
                    for _, value in sorted(
                        self.state.active_observed_notice_interpretations.items()
                    )
                ]
                context["notice_event_memory"] = [
                    {
                        "event_id": event_id,
                        "previous_phase": value.get("phase"),
                        "previous_timestep": value.get("timestep"),
                        "incorporated": event_id in self.state.notice_memory,
                    }
                    for event_id, value in sorted(
                        self.state.observed_notice_memory.items()
                    )
                ]
                notice_path = self._resolved_notice_path()
                preinterpreted = self._preinterpret_notices(
                notice_path, notice_records, workbook["Trips"]
                )
                if preinterpreted is not None:
                    context["notice_interpretation"] = preinterpreted.model_dump()
                    context["notice_flags"] = self._notice_flags(preinterpreted)
                context["information_processing_seconds"] = round(
                time.perf_counter() - timestep_started, 6
                )
                try:
                    trigger = self.agents.trigger(context)
                except Exception:
                    self._checkpoint_agent_failure(timestep)
                    raise
                if trigger.notice_interpretation is None and preinterpreted is not None:
                    trigger = trigger.model_copy(update={"notice_interpretation": preinterpreted})
                if trigger.notice_interpretation is not None:
                    context["notice_interpretation"] = trigger.notice_interpretation.model_dump()
                    context["notice_flags"] = self._notice_flags(trigger.notice_interpretation)
                    self._observe_notice(trigger.notice_interpretation)
                raw_trigger = getattr(self.agents, "last_raw_trigger", None)
                context["llm_raw_trigger"] = (
                raw_trigger.model_dump() if raw_trigger is not None else None
                )
                context["trigger_guard_applied"] = bool(
                getattr(self.agents, "last_trigger_guard_applied", False)
                )
                if (
                    trigger.action == "optimize"
                    and int(context["remaining_timesteps"]) == 0
                ):
                    trigger = trigger.model_copy(
                        update={
                            "action": "skip",
                            "trigger_type": "none",
                            "flagged_buses": [],
                            "reasoning": (
                                "No future interval remains after the current "
                                "observation was settled."
                            ),
                        }
                    )
                pricing: PricingDecision | None = None
                evaluation: EvaluationDecision | None = None
                result: dict[str, Any] | None = None
                rerun_count = 0
                # The observation at timestep t settles the interval planned at
                # t - 1.  Capture settlement before a decision at t can mutate
                # the plan or the planner's active event assumptions.
                current_price = context["intraday_prices"]["current_price"]
                self.state.settlement.append(
                    settle_realized_step(
                        timestep=timestep,
                        realtime_plan=self.state.realtime_plan,
                        observation=observation,
                        spot_price=current_price,
                        buy_multiplier=self.state.buy_multiplier_schedule[timestep],
                        sell_multiplier=self.state.sell_multiplier_schedule[timestep],
                        chargers=workbook["Chargers"],
                        trips=disturbance.trips,
                        physical_truth=physical_truth,
                        planner_assumption=planner_assumption_now,
                    )
                )
                if trigger.action == "optimize":
                    self.state.update_forecasts(
                    timestep=timestep,
                    observation=observation,
                    disturbance=disturbance,
                    )
                    selected = self._optimize(
                    timestep=timestep,
                    context=context,
                    trigger=trigger,
                    workbook=workbook,
                    observation=observation,
                    disturbance=disturbance,
                    )
                    pricing = selected.pricing
                    evaluation = selected.evaluation
                    result = selected.result
                    rerun_count = max(0, selected.attempt - 1)
                    if not result.get("is_mock"):
                        self.state.apply_optimized_plan(
                        timestep=timestep,
                        trigger=trigger,
                        pricing=pricing,
                        result=result,
                        intraday_prices=context["intraday_prices"]["prices"],
                        )
                        context["history_entry"]["reoptimized"] = True
                        if trigger.notice_interpretation is not None:
                            self._account_for_notice(trigger.notice_interpretation)
                if current_price is not None:
                    self.state.price_history[timestep] = float(current_price)
                context["workflow_latency_seconds"] = round(
                time.perf_counter() - timestep_started, 6
                )
                timestep_resources = timestep_meter.stop()
                new_agent_calls = self._collect_agent_calls(timestep)
                usage_summary = summarize_agent_calls(new_agent_calls)
                context.update(usage_summary)
                context.update(
                    {
                        "workflow_process_cpu_seconds": timestep_resources.get(
                            "process_cpu_seconds"
                        ),
                        "workflow_average_cpu_cores": timestep_resources.get(
                            "average_cpu_cores"
                        ),
                        "workflow_peak_rss_mb": timestep_resources.get("peak_rss_mb"),
                        "workflow_peak_rss_delta_mb": timestep_resources.get(
                            "peak_rss_delta_mb"
                        ),
                    }
                )
                self.state.resource_usage.append(
                    {"scope": "timestep", "timestep": timestep, **usage_summary, **timestep_resources}
                )
                self.state.append_log(
                mode=self.config.mode,
                timestep=timestep,
                trigger=trigger,
                pricing=pricing,
                evaluation=evaluation.model_dump() if evaluation else None,
                result=result,
                rerun_count=rerun_count,
                context=context,
                )
                self.state.context_history.append(context)
                if timestep % self.config.checkpoint_every == 0:
                    self.state.save(self.config.output_workbook, self.config)
        except Exception:
            run_status = "failed"
            raise
        finally:
            run_resources = run_meter.stop()
            usage_summary = summarize_agent_calls(self.state.agent_calls)
            profile = system_profile()
            settlement = self.state.settlement
            self.state.run_summary = {
                "status": run_status,
                "start_timestep": self.config.start_timestep,
                "end_timestep": self.config.end_timestep,
                "timesteps_completed": len(self.state.logs),
                "optimizer_calls": len(self.state.attempts),
                "maximum_pricing_reruns": self.config.max_reruns,
                "maximum_optimizer_attempts_per_trigger": self.config.max_reruns + 1,
                "agent_role_provenance": json.dumps(
                    describe_agent_roles(self.agents), sort_keys=True
                ),
                "accepted_optimizer_calls": sum(
                    bool(row.get("accepted")) for row in self.state.attempts
                ),
                "evaluator_accepted_optimizer_calls": sum(
                    bool(row.get("evaluator_accepted")) for row in self.state.attempts
                ),
                "forced_optimizer_selections": sum(
                    bool(row.get("forced_at_rerun_cap")) for row in self.state.attempts
                ),
                "retained_better_candidate_selections": sum(
                    bool(row.get("retained_better_candidate"))
                    for row in self.state.attempts
                ),
                "optimize_decisions": sum(
                    row.get("action") == "optimize" for row in self.state.logs
                ),
                "skip_decisions": sum(row.get("action") == "skip" for row in self.state.logs),
                "realized_pto_cost": sum(
                    float(row.get("realized_pto_cost") or 0) for row in settlement
                ),
                "realized_aggregator_revenue": sum(
                    float(row.get("realized_aggregator_revenue") or 0)
                    for row in settlement
                ),
                "realized_grid_net_cost": sum(
                    float(row.get("realized_grid_net_cost") or 0)
                    for row in settlement
                ),
                "realized_buy_kwh": sum(
                    float(row.get("realized_buy_kwh") or 0) for row in settlement
                ),
                "realized_sell_kwh": sum(
                    float(row.get("realized_sell_kwh") or 0) for row in settlement
                ),
                "curtailed_energy_kwh": sum(
                    float(row.get("curtailed_buy_kwh") or 0)
                    + float(row.get("curtailed_sell_kwh") or 0)
                    for row in settlement
                ),
                "event_model_mismatch_timesteps": sum(
                    bool(row.get("event_model_mismatch")) for row in settlement
                ),
                "delay_parameter_absolute_error_minutes": sum(
                    float(row.get("delay_parameter_absolute_error_minutes") or 0)
                    for row in settlement
                ),
                "return_delay_parameter_absolute_error_minutes": sum(
                    float(
                        row.get(
                            "return_delay_parameter_absolute_error_minutes"
                        )
                        or 0
                    )
                    for row in settlement
                ),
                "charger_power_absolute_error_kw": sum(
                    float(row.get("charger_power_absolute_error_kw") or 0)
                    for row in settlement
                ),
                "charger_availability_mismatch_count": sum(
                    int(row.get("charger_availability_mismatch_count") or 0)
                    for row in settlement
                ),
                "minimum_observed_soc_fraction": min(
                    (
                        float(row["minimum_observed_soc_fraction"])
                        for row in settlement
                        if row.get("minimum_observed_soc_fraction") is not None
                    ),
                    default=None,
                ),
                "maximum_reserve_shortfall_kwh": max(
                    (
                        float(row.get("reserve_shortfall_kwh") or 0)
                        for row in settlement
                    ),
                    default=0.0,
                ),
                "reserve_violation_timesteps": sum(
                    float(row.get("reserve_shortfall_kwh") or 0) > 1e-6
                    for row in settlement
                ),
                "terminal_minimum_soc_fraction": (
                    settlement[-1].get("minimum_observed_soc_fraction")
                    if settlement
                    else None
                ),
                **usage_summary,
                **{f"run_{key}": value for key, value in run_resources.items()},
                **profile,
            }
            self.state.resource_usage.append(
                {"scope": "run", "timestep": None, **usage_summary, **run_resources}
            )
            self.state.save(self.config.output_workbook, self.config)
        return self.state

    def _resolved_notice_path(self) -> str:
        if self.config.notice_path != "none":
            return self.config.notice_path
        return {
            "structured_reference": "manual",
            "oracle_event_trigger": "manual",
            "numerical_event_trigger": "none",
            "rule_text_event_trigger": "rule",
            "agent_trigger_only": "llm",
            "full_deterministic": "rule",
            "rule_parser_trigger_substitution": "rule",
            "full_agentic": "llm",
            "mathematical_pricing_substitution": "llm",
            "evaluator_removal": "llm",
        }.get(self.config.experiment_configuration, "none")

    def _preinterpret_notices(
        self,
        notice_path: str,
        records: list[NoticeRecord],
        trips: pd.DataFrame,
    ):
        if notice_path == "none" or notice_path == "llm" or not records:
            return None
        bus_route_map = {
            int(row["bus_id"]): int(row["trip_id"])
            for row in dataframe_records(trips)
            if row.get("bus_id") is not None and row.get("trip_id") is not None
        }
        if notice_path == "manual":
            missing = [record.notice_id for record in records if record.canonical is None]
            if missing:
                raise ValueError(f"Manual notice path requires canonical truth: {missing}")
            return merge_interpretations(record.canonical for record in records if record.canonical)
        if notice_path == "rule":
            return merge_interpretations(
                resolve_notice_coreferences(
                    record,
                    frozen_rule_parse(record, bus_route_map),
                    self.state.active_observed_notice_interpretations,
                )
                for record in records
            )
        raise ValueError(f"Unsupported notice path: {notice_path}")

    def _notice_flags(self, interpretation) -> dict[str, Any]:
        signature = interpretation.model_dump_json(
            exclude={"phase", "evidence", "effective_timestep", "expected_end_timestep"}
        )
        previous = self.state.notice_memory.get(interpretation.event_id)
        same = bool(
            previous
            and previous.get("signature") == signature
            and interpretation.phase in {"persistence", "stable"}
        )
        return {
            "same_event_already_accounted": same,
            "previous_phase": previous.get("phase") if previous else None,
            "previous_timestep": previous.get("timestep") if previous else None,
        }

    def _account_for_notice(self, interpretation) -> None:
        self.state.notice_memory[interpretation.event_id] = {
            "phase": interpretation.phase,
            "timestep": interpretation.effective_timestep,
            "signature": interpretation.model_dump_json(
                exclude={"phase", "evidence", "effective_timestep", "expected_end_timestep"}
            ),
        }
        if interpretation.phase in {"recovery", "stable"}:
            self.state.active_notice_interpretations.pop(interpretation.event_id, None)
        else:
            self.state.active_notice_interpretations[
                interpretation.event_id
            ] = interpretation.model_dump()

    def _observe_notice(self, interpretation) -> None:
        """Retain conversational context without treating it as optimizer truth."""

        self.state.observed_notice_memory[interpretation.event_id] = {
            "phase": interpretation.phase,
            "timestep": interpretation.effective_timestep,
        }
        if interpretation.phase in {"recovery", "stable"}:
            self.state.active_observed_notice_interpretations.pop(
                interpretation.event_id, None
            )
        else:
            self.state.active_observed_notice_interpretations[
                interpretation.event_id
            ] = interpretation.model_dump()

    def _effective_notice_interpretation(self, trigger: TriggerDecision):
        current = trigger.notice_interpretation
        current_ids = set(current.event_id.split("+")) if current else set()
        active = []
        for event_id, value in self.state.active_notice_interpretations.items():
            if event_id not in current_ids:
                active.append(NoticeInterpretation.model_validate(value))
        if current is not None:
            active.append(current)
        return merge_interpretations(active)

    def _collect_agent_calls(self, timestep: int) -> list[dict[str, Any]]:
        records = getattr(self.agents, "call_records", [])
        collected: list[dict[str, Any]] = []
        for record in records[self._agent_call_cursor :]:
            flattened = {"timestep": timestep}
            for key, value in record.items():
                flattened[key] = (
                    json.dumps(value, default=str, separators=(",", ":"))
                    if isinstance(value, (dict, list))
                    else value
                )
            self.state.agent_calls.append(flattened)
            collected.append(flattened)
        self._agent_call_cursor = len(records)
        return collected

    def _checkpoint_agent_failure(self, timestep: int) -> None:
        self._collect_agent_calls(timestep)
        self.state.save(self.config.output_workbook, self.config)

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_CACHED_INPUT_USD_PER_MILLION,
    DEFAULT_CACHE_WRITE_MULTIPLIER,
    DEFAULT_INPUT_USD_PER_MILLION,
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_USD_PER_MILLION,
    DEFAULT_REASONING_EFFORT,
)
from .models import (
    EvaluationDecision,
    EvaluationFeedback,
    NULL_FEEDBACK,
    NoticeInterpretation,
    NoticeParameterUpdates,
    PricingDecision,
    StructuredTriggerDecision,
    TriggerDecision,
)


PROMPT_DIR = Path(__file__).with_name("prompts")


def _prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


class AgentBackend(ABC):
    @abstractmethod
    def trigger(self, context: dict[str, Any]) -> TriggerDecision: ...

    @abstractmethod
    def price(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        *,
        rerun_count: int,
        previous: PricingDecision | None,
        feedback: EvaluationFeedback | None,
    ) -> PricingDecision: ...

    @abstractmethod
    def evaluate(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        pricing: PricingDecision,
        result: dict[str, Any],
        *,
        rerun_count: int,
    ) -> EvaluationDecision: ...


class OpenAIAgentBackend(AgentBackend):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        allow_deterministic_trigger_fallback: bool = True,
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the 'openai' package to use the OpenAI backend") from exc
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for --agent-backend openai")
        self.client = OpenAI()
        self.model = model
        self.last_raw_trigger: TriggerDecision | None = None
        self.last_trigger_guard_applied = False
        self.call_records: list[dict[str, Any]] = []
        self.allow_deterministic_trigger_fallback = (
            allow_deterministic_trigger_fallback
        )

    def _parse(
        self,
        system: str,
        user_data: dict[str, Any],
        schema: type[Any],
        *,
        role: str = "unknown",
    ) -> Any:
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_data, default=str, separators=(",", ":"))},
            ],
            "response_format": schema,
        }
        if self.model.startswith("gpt-5.6"):
            request["reasoning_effort"] = DEFAULT_REASONING_EFFORT
        last_error: Exception | None = None
        for attempt in range(1, 4):
            started = time.perf_counter()
            record = {
                "role": role,
                "model": self.model,
                "schema": schema.__name__,
                "attempt": attempt,
                "request": user_data,
            }
            try:
                completion = self.client.chat.completions.parse(**request)
                message = completion.choices[0].message
                record["raw_output"] = getattr(message, "content", None)
                record["refusal"] = getattr(message, "refusal", None)
                if record["refusal"]:
                    raise RuntimeError(f"Model refused the workflow request: {record['refusal']}")
                if message.parsed is None:
                    raise RuntimeError("Model returned no parsed structured output")
                record["schema_valid"] = True
                record["parsed_output"] = message.parsed.model_dump()
                usage = getattr(completion, "usage", None)
                if usage is not None:
                    usage_data = usage.model_dump() if hasattr(usage, "model_dump") else {}
                    record["usage"] = usage_data
                    input_tokens = int(usage_data.get("prompt_tokens") or usage_data.get("input_tokens") or 0)
                    output_tokens = int(usage_data.get("completion_tokens") or usage_data.get("output_tokens") or 0)
                    total_tokens = int(usage_data.get("total_tokens") or input_tokens + output_tokens)
                    input_details = (
                        usage_data.get("prompt_tokens_details")
                        or usage_data.get("input_tokens_details")
                        or {}
                    )
                    output_details = (
                        usage_data.get("completion_tokens_details")
                        or usage_data.get("output_tokens_details")
                        or {}
                    )
                    cached_input_tokens = int(input_details.get("cached_tokens") or 0)
                    cache_write_tokens = int(input_details.get("cache_write_tokens") or 0)
                    uncached_input_tokens = max(
                        0, input_tokens - cached_input_tokens - cache_write_tokens
                    )
                    reasoning_tokens = int(output_details.get("reasoning_tokens") or 0)
                    record.update(
                        {
                            "input_tokens": input_tokens,
                            "cached_input_tokens": cached_input_tokens,
                            "cache_write_tokens": cache_write_tokens,
                            "uncached_input_tokens": uncached_input_tokens,
                            "output_tokens": output_tokens,
                            "reasoning_tokens": reasoning_tokens,
                            "total_tokens": total_tokens,
                        }
                    )
                    default_input_rate = (
                        DEFAULT_INPUT_USD_PER_MILLION if self.model == DEFAULT_MODEL else 0.0
                    )
                    default_cached_rate = (
                        DEFAULT_CACHED_INPUT_USD_PER_MILLION
                        if self.model == DEFAULT_MODEL
                        else 0.0
                    )
                    default_output_rate = (
                        DEFAULT_OUTPUT_USD_PER_MILLION if self.model == DEFAULT_MODEL else 0.0
                    )
                    input_rate = float(
                        os.environ.get(
                            "OPENAI_INPUT_USD_PER_MILLION", str(default_input_rate)
                        )
                    )
                    output_rate = float(
                        os.environ.get(
                            "OPENAI_OUTPUT_USD_PER_MILLION", str(default_output_rate)
                        )
                    )
                    cached_input_rate = float(
                        os.environ.get(
                            "OPENAI_CACHED_INPUT_USD_PER_MILLION",
                            str(default_cached_rate),
                        )
                    )
                    cache_write_rate = float(
                        os.environ.get(
                            "OPENAI_CACHE_WRITE_USD_PER_MILLION",
                            str(default_input_rate * DEFAULT_CACHE_WRITE_MULTIPLIER),
                        )
                    )
                    record["cost_rates_usd_per_million"] = {
                        "input": input_rate,
                        "cached_input": cached_input_rate,
                        "cache_write": cache_write_rate,
                        "output": output_rate,
                    }
                    record["approximate_cost_usd"] = round(
                        (
                            uncached_input_tokens * input_rate
                            + cached_input_tokens * cached_input_rate
                            + cache_write_tokens * cache_write_rate
                            + output_tokens * output_rate
                        )
                        / 1_000_000,
                        8,
                    )
                return message.parsed
            except Exception as exc:
                last_error = exc
                record["schema_valid"] = False
                record["error"] = f"{type(exc).__name__}: {exc}"
                if attempt == 3:
                    raise
            finally:
                record["latency_seconds"] = round(time.perf_counter() - started, 6)
                getattr(self, "call_records", []).append(record)
        raise RuntimeError("Structured-output retries exhausted") from last_error

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        structured = self._parse(
            _prompt("trigger_system.txt"),
            context,
            StructuredTriggerDecision,
            role="trigger",
        )
        decision = structured.to_domain()
        effective = normalize_trigger_decision(
            decision,
            context,
            allow_numerical_fallback=self.allow_deterministic_trigger_fallback,
        )
        self.last_raw_trigger = decision
        self.last_trigger_guard_applied = decision.model_dump() != effective.model_dump()
        return effective

    def price(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        *,
        rerun_count: int,
        previous: PricingDecision | None,
        feedback: EvaluationFeedback | None,
    ) -> PricingDecision:
        prompt_name = (
            "pricing_selfish_system.txt"
            if context["mode"] == "selfish"
            else "pricing_altruistic_system.txt"
        )
        system_prompt = _prompt(prompt_name) + (
            "\n\nPYTHON MIGRATION COMPATIBILITY NOTE:\n"
            "The trigger schema emits event onset and recovery types. Treat "
            "energy_recovery, price_recovery, delay_recovery, and delay_removal as "
            "the restoration of the corresponding pre-disturbance assumptions."
        )
        user_data = {
            "mode": context["mode"],
            "timestep": context["timestep"],
            "remaining_timesteps": context["remaining_timesteps"],
            "remaining_hours": context["remaining_hours"],
            "trigger": trigger.model_dump(),
            "realtime_state": context["realtime_state"],
            "deviations": context["deviations"],
            "deviation_summary": context["deviation_summary"],
            "trigger_flags": context["trigger_flags"],
            "remaining_prices": context["intraday_prices"],
            "day_ahead_summary": context["day_ahead_summary"],
            "reoptimization_history": context["reoptimization_history"],
            "rerun_count": rerun_count,
            "previous_multipliers": previous.model_dump() if previous else None,
            "evaluator_feedback": feedback.model_dump() if feedback else None,
        }
        decision = self._parse(system_prompt, user_data, PricingDecision, role="pricing")
        return normalize_pricing_decision(decision, context["mode"], context["remaining_timesteps"])

    def evaluate(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        pricing: PricingDecision,
        result: dict[str, Any],
        *,
        rerun_count: int,
    ) -> EvaluationDecision:
        energy = result.get("energy") or []
        end_energy = [series[-1] if series else None for series in energy]
        user_data = {
            "mode": context["mode"],
            "timestep": context["timestep"],
            "remaining_timesteps": context["remaining_timesteps"],
            "rerun_count": rerun_count,
            "trigger": trigger.model_dump(),
            "pricing": pricing.model_dump(),
            "optimization_result": result,
            "end_of_day_energy_kwh": {
                str(bus_id): value for bus_id, value in enumerate(end_energy, start=1)
            },
            "deviations": context["deviations"],
            "day_ahead_summary": context["day_ahead_summary"],
            "da_benchmark": context["da_benchmark"],
            "intraday_prices": context["intraday_prices"],
        }
        return self._parse(
            _prompt("evaluator_system.txt"), user_data, EvaluationDecision, role="evaluator"
        )


class RuleBasedAgentBackend(AgentBackend):
    """Deterministic substitute used for tests and API-key-free local runs."""

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        timestep = int(context["timestep"])
        remaining = int(context["remaining_timesteps"])
        flags = context["trigger_flags"]
        history = context["reoptimization_history"]
        last_type = history.get("last_reopt_trigger_type")
        last_timestep = history.get("last_reopt_timestep")

        if timestep == 1 or remaining < 4 or last_timestep == timestep:
            return TriggerDecision(
                action="skip",
                reasoning="No actionable real-time deviation is available at this timestep.",
                confidence=1.0,
                trigger_type="none",
                flagged_buses=[],
            )
        notice = context.get("notice_interpretation")
        notice_flags = context.get("notice_flags", {})
        if notice is not None:
            recommendation = (
                notice.get("uncertainty_details") or {}
            ).get("recommended_action", "optimize")
            if (
                notice_flags.get("same_event_already_accounted")
                or not notice.get("material", True)
                or recommendation in {"wait", "request_confirmation"}
            ):
                return TriggerDecision(
                    action="skip",
                    reasoning=(
                        "The operational notice is informational, awaits confirmation, "
                        "or its unchanged state is already incorporated in the active plan."
                    ),
                    confidence=1.0,
                    trigger_type="none",
                    flagged_buses=[],
                    notice_interpretation=notice,
                )
            event_type = str(notice.get("event_type"))
            trigger_type = (
                "combined_notice"
                if event_type == "combined"
                else "charger_event"
                if event_type.startswith("charger_")
                else "service_notice"
            )
            return TriggerDecision(
                action="optimize",
                reasoning=f"A material {event_type} notice in phase {notice.get('phase')} changes optimizer inputs.",
                confidence=1.0,
                trigger_type=trigger_type,
                flagged_buses=[int(bus) for bus in notice.get("affected_buses", [])],
                notice_interpretation=notice,
            )
        if flags.get("energy_recovery_active"):
            return TriggerDecision(
                action="optimize",
                reasoning="The persistent energy-consumption disturbance has ended, so the active plan's route-energy assumptions must be restored.",
                confidence=0.98,
                trigger_type="energy_recovery",
                flagged_buses=[int(bus) for bus in flags.get("energy_event_buses", [])],
            )
        if flags.get("price_recovery_active"):
            return TriggerDecision(
                action="optimize",
                reasoning="The persistent price disturbance has ended, so the active plan's price assumptions must be restored.",
                confidence=0.98,
                trigger_type="price_recovery",
                flagged_buses=[],
            )
        if flags.get("delay_removal_active"):
            buses = [int(item["bus_id"]) for item in flags.get("severe_delay_buses", [])]
            return TriggerDecision(
                action="optimize",
                reasoning="The active delay assumption is being removed, so the current plan is outdated.",
                confidence=0.95,
                trigger_type="delay_removal",
                flagged_buses=buses,
            )
        if flags.get("delay_sign_reversed"):
            buses = [int(item["bus_id"]) for item in flags.get("delay_sign_reversed_buses", [])]
            return TriggerDecision(
                action="optimize",
                reasoning="A severe delay reversed direction after the prior delay-related re-optimization.",
                confidence=0.95,
                trigger_type="delay",
                flagged_buses=buses,
            )
        if flags.get("has_severe_delay") and last_type not in {"delay", "delay_removal", "delay_recovery"}:
            buses = [int(item["bus_id"]) for item in flags.get("severe_delay_buses", [])]
            return TriggerDecision(
                action="optimize",
                reasoning="At least one bus has a severe unaccounted delay.",
                confidence=0.95,
                trigger_type="delay",
                flagged_buses=buses,
            )
        summary = context["deviation_summary"]
        if summary.get("has_energy_disturbance"):
            return TriggerDecision(
                action="optimize",
                reasoning="Observed interval energy consumption differs materially from the forecast.",
                confidence=0.95,
                trigger_type="energy_disturbance",
                flagged_buses=[int(bus) for bus in summary.get("disturbed_energy_buses", [])],
            )
        if flags.get("price_deviation_significant") and remaining >= 6:
            return TriggerDecision(
                action="optimize",
                reasoning="Observed price differs from the forecast by more than the configured threshold.",
                confidence=0.95,
                trigger_type="price",
                flagged_buses=[],
            )
        if flags.get("unexpected_discharging_buses"):
            return TriggerDecision(
                action="optimize",
                reasoning="A bus is discharging while its observed energy no longer matches the active plan.",
                confidence=0.9,
                trigger_type="deviation",
                flagged_buses=[int(bus) for bus in flags["unexpected_discharging_buses"]],
            )
        if flags.get("has_high_energy_deviation") or flags.get("multi_bus_moderate_deviation"):
            items = flags.get("high_energy_deviation_buses") or flags.get("moderate_deviation_buses") or []
            return TriggerDecision(
                action="optimize",
                reasoning="Fleet battery energy has drifted materially from the active plan.",
                confidence=0.9,
                trigger_type="deviation",
                flagged_buses=[int(item["bus_id"]) for item in items],
            )
        return TriggerDecision(
            action="skip",
            reasoning="All monitored deviations remain within the workflow thresholds.",
            confidence=0.95,
            trigger_type="none",
            flagged_buses=[],
        )

    def price(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        *,
        rerun_count: int,
        previous: PricingDecision | None,
        feedback: EvaluationFeedback | None,
    ) -> PricingDecision:
        mode = context["mode"]
        rows = context["intraday_prices"]["prices"]
        buy: list[float] = []
        sell: list[float] = []
        for index, row in enumerate(rows):
            zone = row.get("price_zone", "transition")
            if mode == "selfish":
                buy_value = {"cheap": 1.10, "transition": 1.14, "expensive": 1.18}[zone]
                sell_value = {"cheap": 0.58, "transition": 0.66, "expensive": 0.72}[zone]
                if index < 6:
                    buy_value = min(buy_value, 1.10)
            else:
                buy_value = {"cheap": 1.01, "transition": 1.03, "expensive": 1.05}[zone]
                sell_value = {"cheap": 0.82, "transition": 0.89, "expensive": 0.96}[zone]
            buy.append(buy_value)
            sell.append(sell_value)
        if previous and rerun_count > 0:
            buy = list(previous.buy_multipliers)
            sell = list(previous.sell_multipliers)
            if feedback and feedback.reason == "infeasible":
                buy = [1.03] * len(buy)
                sell = [0.65] * len(sell)
            elif feedback and feedback.reason == "cost_too_high":
                buy = [max(1.01, value - 0.05) for value in buy]
            elif feedback and feedback.reason == "v2g_unused":
                sell = [min(0.97, value + 0.05) for value in sell]
            else:
                buy = [max(1.01, value - 0.01) for value in buy]
                sell = [min(0.97, value + 0.02) for value in sell]
        decision = PricingDecision(
            buy_multipliers=buy,
            sell_multipliers=sell,
            reasoning=f"Deterministic {mode} pricing policy for {len(rows)} remaining timesteps.",
            confidence=1.0,
        )
        return normalize_pricing_decision(decision, mode, int(context["remaining_timesteps"]))

    def evaluate(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        pricing: PricingDecision,
        result: dict[str, Any],
        *,
        rerun_count: int,
    ) -> EvaluationDecision:
        if result.get("is_mock"):
            feedback = EvaluationFeedback(
                reason="infeasible" if result.get("solver_status") in {None, "unknown", "infeasible", "mock"} else "solver_error",
                buy_multiplier_adjustment=None,
                sell_multiplier_adjustment=None,
                period_adjustment="Set all buy multipliers to 1.03 and sell multipliers to 0.65.",
                priority="mock_recovery",
            )
            return EvaluationDecision(
                accept=False,
                reasoning="The optimizer returned a mock result, so another attempt is required.",
                confidence=1.0,
                feedback=feedback,
            )
        mode = context["mode"]
        benchmark = context["da_benchmark"]
        if benchmark.get("da_benchmark_valid"):
            if mode == "selfish":
                result_value = float(result.get("aggregator_revenue") or 0)
                target = float(benchmark.get("da_revenue_remaining") or 0)
                accept = result_value >= 0.90 * target
                reason = "The remaining-horizon revenue is acceptable relative to the day-ahead benchmark."
                feedback_reason = "revenue_too_low"
            else:
                result_value = float(result.get("pto_daily_cost") or 0)
                target = float(benchmark.get("da_cost_remaining") or 0)
                accept = target <= 0 or result_value <= 1.10 * target
                reason = "The remaining-horizon PTO cost is acceptable relative to the day-ahead benchmark."
                feedback_reason = "cost_too_high"
        else:
            accept = True
            reason = "The optimization is feasible and no valid like-for-like benchmark is available."
            feedback_reason = "cost_too_high" if mode == "altruistic" else "revenue_too_low"
        feedback = NULL_FEEDBACK if accept else EvaluationFeedback(
            reason=feedback_reason,
            buy_multiplier_adjustment=None,
            sell_multiplier_adjustment=None,
            period_adjustment="Move the multipliers toward the day-ahead baseline.",
            priority="cost_reduction",
        )
        return EvaluationDecision(
            accept=accept,
            reasoning=reason,
            confidence=1.0,
            feedback=feedback,
        )


class FixedPlanAgentBackend(RuleBasedAgentBackend):
    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        return TriggerDecision(
            action="skip",
            reasoning="Fixed day-ahead configuration: adaptive optimization is disabled.",
            confidence=1.0,
            trigger_type="none",
            flagged_buses=[],
        )


class NoticeOnlyAgentBackend(RuleBasedAgentBackend):
    """Trigger exclusively from the interpretation supplied by its text path."""

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        if context.get("notice_interpretation") is None:
            return TriggerDecision(
                action="skip",
                reasoning="No new interpreted operational notice is available.",
                confidence=1.0,
                trigger_type="none",
                flagged_buses=[],
            )
        return super().trigger(context)


class NumericalOnlyAgentBackend(RuleBasedAgentBackend):
    """Trigger from numerical deviations without access to notice semantics."""

    def __init__(self) -> None:
        self._active_telemetry_signature: str | None = None

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        numerical_context = dict(context)
        numerical_context.pop("notice_interpretation", None)
        numerical_context.pop("notice_flags", None)
        numerical_context["operational_notices"] = []
        numerical_context["active_operational_events"] = []
        numerical_context["notice_event_memory"] = []
        timestep = int(context["timestep"])
        telemetry = context.get("numerical_event_telemetry") or {}
        return_delay = {
            int(key): int(value)
            for key, value in (telemetry.get("return_delay_minutes_by_bus") or {}).items()
        }
        power = {
            int(key): float(value)
            for key, value in (telemetry.get("charger_power_kw") or {}).items()
        }
        unavailable = sorted(
            int(value) for value in (telemetry.get("unavailable_chargers") or [])
        )
        signature = json.dumps(
            {
                "return_delay": return_delay,
                "power": power,
                "unavailable": unavailable,
            },
            sort_keys=True,
        )
        nominal_signature = json.dumps(
            {"return_delay": {}, "power": {}, "unavailable": []}, sort_keys=True
        )
        if signature != nominal_signature and signature != self._active_telemetry_signature:
            self._active_telemetry_signature = signature
            has_bus = bool(return_delay)
            has_charger = bool(power or unavailable)
            event_type = (
                "combined"
                if has_bus and has_charger
                else "charger_fault"
                if unavailable
                else "charger_derating"
                if has_charger
                else "service_delay"
            )
            interpretation = NoticeInterpretation(
                event_id="NUMERICAL-SENSOR-EVENT",
                source_type=("combined" if has_bus and has_charger else "ocpp" if has_charger else "service_alert"),
                event_type=event_type,
                phase="onset",
                affected_buses=sorted(return_delay),
                affected_chargers=sorted(set(power) | set(unavailable)),
                effective_timestep=int(
                    telemetry.get("effective_timestep") or timestep
                ),
                expected_end_timestep=(
                    int(telemetry["expected_end_timestep"])
                    if telemetry.get("expected_end_timestep") is not None
                    else None
                ),
                updates=NoticeParameterUpdates(
                    return_delay_minutes_by_bus=return_delay,
                    charger_power_kw=power,
                    unavailable_chargers=unavailable,
                ),
                evidence=["causal_numerical_telemetry_v1"],
            )
            return TriggerDecision(
                action="optimize",
                reasoning="A new stateful numerical estimator event was detected from causal charger or return telemetry.",
                confidence=1.0,
                trigger_type=(
                    "combined_notice" if has_bus and has_charger else "charger_event" if has_charger else "delay"
                ),
                flagged_buses=sorted(return_delay),
                notice_interpretation=interpretation,
            )
        if signature == nominal_signature and self._active_telemetry_signature is not None:
            self._active_telemetry_signature = None
            interpretation = NoticeInterpretation(
                event_id="NUMERICAL-SENSOR-EVENT",
                source_type="combined",
                event_type="combined",
                phase="recovery",
                effective_timestep=timestep,
                updates=NoticeParameterUpdates(),
                evidence=["causal_numerical_telemetry_recovery_v1"],
            )
            return TriggerDecision(
                action="optimize",
                reasoning="The stateful numerical estimator detected recovery to nominal telemetry.",
                confidence=1.0,
                trigger_type="combined_notice",
                flagged_buses=[],
                notice_interpretation=interpretation,
            )
        return TriggerDecision(
            action="skip",
            reasoning=(
                "The stateful numerical estimator has no new causal charger-capacity "
                "or return-time event; unchanged telemetry does not retrigger."
            ),
            confidence=1.0,
            trigger_type="none",
            flagged_buses=[],
        )


class HardCheckAgentBackend(RuleBasedAgentBackend):
    """Evaluator-removal comparator: only solver and feasibility guards remain."""

    def evaluate(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        pricing: PricingDecision,
        result: dict[str, Any],
        *,
        rerun_count: int,
    ) -> EvaluationDecision:
        accepted = not bool(result.get("is_mock")) and str(result.get("solver_status", "")).lower() not in {
            "infeasible",
            "error",
            "unknown",
        }
        return EvaluationDecision(
            accept=accepted,
            reasoning="Accepted by deterministic solver/feasibility checks only." if accepted else "Rejected by deterministic solver/feasibility checks.",
            confidence=1.0,
            feedback=NULL_FEEDBACK if accepted else EvaluationFeedback(
                reason="infeasible",
                buy_multiplier_adjustment=None,
                sell_multiplier_adjustment=None,
                period_adjustment=None,
                priority="mock_recovery",
            ),
        )


class CompositeAgentBackend(AgentBackend):
    """Role-level composition used by the prespecified ablation configurations."""

    def __init__(
        self,
        trigger_backend: AgentBackend,
        pricing_backend: AgentBackend,
        evaluator_backend: AgentBackend,
    ):
        self.trigger_backend = trigger_backend
        self.pricing_backend = pricing_backend
        self.evaluator_backend = evaluator_backend

    @property
    def last_raw_trigger(self) -> TriggerDecision | None:
        return getattr(self.trigger_backend, "last_raw_trigger", None)

    @property
    def last_trigger_guard_applied(self) -> bool:
        return bool(getattr(self.trigger_backend, "last_trigger_guard_applied", False))

    @property
    def call_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[int] = set()
        for backend in (self.trigger_backend, self.pricing_backend, self.evaluator_backend):
            if id(backend) in seen:
                continue
            seen.add(id(backend))
            records.extend(getattr(backend, "call_records", []))
        return records

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        return self.trigger_backend.trigger(context)

    def price(self, context, trigger, *, rerun_count, previous, feedback):
        return self.pricing_backend.price(
            context, trigger, rerun_count=rerun_count, previous=previous, feedback=feedback
        )

    def evaluate(self, context, trigger, pricing, result, *, rerun_count):
        return self.evaluator_backend.evaluate(
            context, trigger, pricing, result, rerun_count=rerun_count
        )


def normalize_trigger_decision(
    decision: TriggerDecision,
    context: dict[str, Any],
    *,
    allow_numerical_fallback: bool = True,
) -> TriggerDecision:
    """Enforce trigger invariants and recover clear rule-detected events."""
    deterministic = RuleBasedAgentBackend().trigger(context)
    timestep = int(context["timestep"])
    remaining = int(context["remaining_timesteps"])
    last_timestep = context["reoptimization_history"].get("last_reopt_timestep")

    if timestep == 1 or remaining < 4 or last_timestep == timestep:
        return deterministic
    notice = decision.notice_interpretation
    if notice is not None:
        recommendation = notice.uncertainty_details.recommended_action
        if decision.action == "optimize" and (
            recommendation in {"wait", "request_confirmation"}
            or notice.phase in {"warning", "stable"}
            or not notice.material
        ):
            return decision.model_copy(
                update={
                    "action": "skip",
                    "trigger_type": "none",
                    "flagged_buses": [],
                    "reasoning": (
                        "Structured-decision guard skipped optimization because the "
                        f"notice is phase={notice.phase}, material={notice.material}, "
                        f"and recommends {recommendation}."
                    ),
                }
            )
        if (
            decision.action == "skip"
            and recommendation == "optimize"
            and notice.material
            and notice.phase in {"onset", "severity_change", "recovery"}
        ):
            trigger_type = (
                "combined_notice"
                if notice.event_type == "combined"
                else "charger_event"
                if notice.event_type.startswith("charger_")
                else "service_notice"
            )
            return decision.model_copy(
                update={
                    "action": "optimize",
                    "trigger_type": trigger_type,
                    "flagged_buses": notice.affected_buses,
                    "reasoning": (
                        "Structured-decision guard triggered optimization because "
                        "the model's normalized material notice recommends optimize."
                    ),
                }
            )
    if (
        allow_numerical_fallback
        and decision.action == "skip"
        and deterministic.action == "optimize"
    ):
        return deterministic.model_copy(
            update={
                "reasoning": (
                    "Deterministic safety guard overrode an LLM skip because the supplied "
                    f"context contains an actionable {deterministic.trigger_type} event."
                )
            }
        )
    notice_already_accounted = bool(
        context["trigger_flags"].get("same_event_already_accounted")
        or context.get("notice_flags", {}).get("same_event_already_accounted")
    )
    if notice is not None and notice.phase in {"persistence", "stable"}:
        remembered_event_ids = {
            str(item.get("event_id"))
            for item in context.get("notice_event_memory", [])
            if item.get("event_id") is not None and item.get("incorporated", True)
        }
        current_event_ids = set(notice.event_id.split("+"))
        notice_already_accounted = notice_already_accounted or bool(
            current_event_ids and current_event_ids.issubset(remembered_event_ids)
        )
    if (
        decision.action == "optimize"
        and deterministic.action == "skip"
        and notice_already_accounted
    ):
        return deterministic.model_copy(
            update={
                "reasoning": (
                    "Deterministic event-memory guard overrode a duplicate LLM optimization: "
                    "the continuing disturbance is already represented in the active plan."
                )
            }
        )
    if decision.action == "skip":
        return decision.model_copy(update={"trigger_type": "none", "flagged_buses": []})
    if decision.trigger_type == "none":
        fallback_type = (
            deterministic.trigger_type if deterministic.action == "optimize" else "trend"
        )
        return decision.model_copy(update={"trigger_type": fallback_type})
    return decision


def normalize_pricing_decision(
    decision: PricingDecision,
    mode: str,
    remaining_timesteps: int,
) -> PricingDecision:
    def exact_length(values: list[float], fallback: float) -> list[float]:
        if not values:
            return [fallback] * remaining_timesteps
        result = list(values[:remaining_timesteps])
        result.extend([result[-1]] * (remaining_timesteps - len(result)))
        return result

    buy = exact_length(decision.buy_multipliers, 1.05)
    sell = exact_length(decision.sell_multipliers, 0.80)
    buy_upper = 1.50 if mode == "selfish" else 1.20
    sell_lower = 0.40 if mode == "selfish" else 0.55
    for index in range(remaining_timesteps):
        buy[index] = min(buy_upper, max(1.01, float(buy[index])))
        sell[index] = min(0.99, max(sell_lower, float(sell[index])))
        if mode == "selfish" and index < 6:
            buy[index] = min(buy[index], 1.10)
        if remaining_timesteps <= 10:
            buy[index] = min(buy[index], 1.15)
        sell[index] = min(sell[index], buy[index] - 0.01)
    return decision.model_copy(update={"buy_multipliers": buy, "sell_multipliers": sell})


def create_agent_backend(name: str, model: str) -> AgentBackend:
    resolved = name
    if name == "auto":
        resolved = "openai" if os.environ.get("OPENAI_API_KEY") else "rule"
    if resolved == "openai":
        return OpenAIAgentBackend(model=model)
    if resolved == "rule":
        return RuleBasedAgentBackend()
    raise ValueError(f"Unsupported agent backend: {name}")


def create_experiment_backend(configuration: str, legacy_backend: str, model: str) -> AgentBackend:
    if configuration == "legacy":
        return create_agent_backend(legacy_backend, model)
    rule = RuleBasedAgentBackend()
    if configuration == "fixed_da_plan":
        return FixedPlanAgentBackend()
    if configuration in {
        "structured_reference",
        "oracle_event_trigger",
        "rule_text_event_trigger",
    }:
        return NoticeOnlyAgentBackend()
    if configuration == "numerical_event_trigger":
        return NumericalOnlyAgentBackend()
    if configuration == "full_deterministic":
        return rule
    if configuration == "agent_trigger_only":
        trigger_llm = OpenAIAgentBackend(
            model=model, allow_deterministic_trigger_fallback=False
        )
        return CompositeAgentBackend(trigger_llm, rule, rule)
    llm = OpenAIAgentBackend(model=model)
    if configuration == "full_agentic":
        return llm
    if configuration == "rule_parser_trigger_substitution":
        return CompositeAgentBackend(rule, llm, llm)
    if configuration == "mathematical_pricing_substitution":
        return CompositeAgentBackend(llm, rule, llm)
    if configuration == "evaluator_removal":
        return CompositeAgentBackend(llm, llm, HardCheckAgentBackend())
    raise ValueError(f"Unsupported experiment configuration: {configuration}")

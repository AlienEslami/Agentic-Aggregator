from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import WorkflowConfig
from .disturbances import DisturbanceApplication
from .io import bus_columns, bus_ids_from_frame
from .models import PricingDecision, TriggerDecision


AGENT_CALL_COLUMNS = [
    "timestep",
    "role",
    "model",
    "schema",
    "attempt",
    "schema_valid",
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
    "latency_seconds",
    "approximate_cost_usd",
    "cost_rates_usd_per_million",
    "request",
    "raw_output",
    "refusal",
    "parsed_output",
    "usage",
    "error",
]
EXCEL_CELL_CHARACTER_LIMIT = 32_767


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _excel_safe_agent_calls(frame: pd.DataFrame, sidecar_name: str) -> pd.DataFrame:
    """Replace overlong Excel cells with verifiable JSONL sidecar pointers."""

    safe = frame.copy()
    for row_index in safe.index:
        for column in safe.columns:
            value = safe.at[row_index, column]
            if not isinstance(value, str) or len(value) <= EXCEL_CELL_CHARACTER_LIMIT:
                continue
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            safe.at[row_index, column] = (
                f"[Full value: {sidecar_name}, JSONL row {int(row_index) + 1}, "
                f"field {column}; characters={len(value)}; sha256={digest}]"
            )
    return safe


@dataclass(slots=True)
class WorkflowState:
    realtime_plan: pd.DataFrame
    forecast_prices: pd.DataFrame
    forecast_energy: pd.DataFrame
    logs: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    context_history: list[dict[str, Any]] = field(default_factory=list)
    price_history: dict[int, float] = field(default_factory=dict)
    notice_memory: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_notice_interpretations: dict[str, dict[str, Any]] = field(default_factory=dict)
    observed_notice_memory: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_observed_notice_interpretations: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )
    active_physical_notice_interpretations: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )
    realized_energy_by_bus: dict[int, float] = field(default_factory=dict)
    buy_multiplier_schedule: dict[int, float] = field(default_factory=dict)
    sell_multiplier_schedule: dict[int, float] = field(default_factory=dict)
    settlement: list[dict[str, Any]] = field(default_factory=list)
    agent_calls: list[dict[str, Any]] = field(default_factory=list)
    resource_usage: list[dict[str, Any]] = field(default_factory=list)
    run_summary: dict[str, Any] = field(default_factory=dict)

    def initialize_tariff_schedule(
        self, buy_multipliers: list[float], sell_multipliers: list[float]
    ) -> None:
        if not buy_multipliers or not sell_multipliers:
            raise ValueError("Day-ahead tariff multipliers must not be empty")
        for timestep in range(1, 49):
            buy_index = min(len(buy_multipliers) - 1, timestep - 1)
            sell_index = min(len(sell_multipliers) - 1, timestep - 1)
            self.buy_multiplier_schedule[timestep] = float(
                buy_multipliers[buy_index]
            )
            self.sell_multiplier_schedule[timestep] = float(
                sell_multipliers[sell_index]
            )

    def update_forecasts(
        self,
        *,
        timestep: int,
        observation: list[dict[str, Any]],
        disturbance: DisturbanceApplication,
    ) -> None:
        price_map = {
            int(row["timestep"]): float(row["spot_market"])
            for row in disturbance.prices.to_dict(orient="records")
        }
        for index, row in self.forecast_prices.iterrows():
            current = int(row["timestep"])
            if current >= timestep and current in price_map:
                self.forecast_prices.at[index, "spot_market"] = price_map[current]

        observation_by_bus = {int(row["bus_id"]): float(row["current_energy_kwh"]) for row in observation}
        plan = self.realtime_plan.sort_values("timestep").reset_index(drop=True)
        future = plan.loc[plan["timestep"] >= timestep - 1].copy()
        if future.empty:
            return
        running = dict(observation_by_bus)
        updates: dict[int, dict[str, float]] = {}
        previous_plan: dict[str, Any] | None = None
        for _, row in future.iterrows():
            state_index = int(row["timestep"])
            values: dict[str, float] = {}
            for bus_id in bus_ids_from_frame(self.realtime_plan):
                key = f"bus_{bus_id}_kwh"
                if previous_plan is None:
                    value = running[bus_id]
                else:
                    delta = float(row[key]) - float(previous_plan[key])
                    multiplier = disturbance.energy_multipliers.get(bus_id, 1.0)
                    if multiplier != 1.0 and delta < 0:
                        delta *= multiplier
                    value = min(365.0, max(73.0, running[bus_id] + delta))
                    running[bus_id] = value
                values[key] = round(value, 4)
            updates[state_index] = values
            previous_plan = row.to_dict()
        for index, row in self.forecast_energy.iterrows():
            state_index = int(row["timestep"])
            if state_index in updates:
                for key, value in updates[state_index].items():
                    self.forecast_energy.at[index, key] = value

    def apply_optimized_plan(
        self,
        *,
        timestep: int,
        trigger: TriggerDecision,
        pricing: PricingDecision,
        result: dict[str, Any],
        intraday_prices: list[dict[str, Any]],
    ) -> None:
        w_buy = list(result.get("w_buy") or [])
        w_sell = list(result.get("w_sell") or [])
        energy = list(result.get("energy") or [])
        bus_ids = bus_ids_from_frame(self.realtime_plan)
        if not w_buy or not w_sell or len(energy) < len(bus_ids):
            return
        # Observation timestep t is settled before this method is called. The
        # first executable optimizer action therefore belongs to interval t+1,
        # represented by zero-based plan state index t.
        start_state_index = timestep
        if "decision_timestep" not in self.realtime_plan:
            self.realtime_plan["decision_timestep"] = None
        commitment_steps = int(result.get("commitment_steps") or len(w_buy))
        if commitment_steps < 1:
            return
        for offset in range(
            min(len(w_buy), commitment_steps, 48 - start_state_index)
        ):
            state_index = start_state_index + offset
            matches = self.realtime_plan.index[self.realtime_plan["timestep"].astype(int) == state_index]
            if len(matches) == 0:
                continue
            index = matches[-1]
            self.realtime_plan.at[index, "w_buy"] = w_buy[offset]
            self.realtime_plan.at[index, "w_sell"] = w_sell[offset]
            for position, bus_id in enumerate(bus_ids):
                series = energy[position] if position < len(energy) else []
                if offset < len(series):
                    self.realtime_plan.at[index, f"bus_{bus_id}_kwh"] = series[offset]
                    forecast_matches = self.forecast_energy.index[
                        self.forecast_energy["timestep"].astype(int) == state_index
                    ]
                    if len(forecast_matches):
                        self.forecast_energy.at[
                            forecast_matches[-1], f"bus_{bus_id}_kwh"
                        ] = series[offset]
            is_first = offset == 0
            self.realtime_plan.at[index, "reoptimized"] = is_first
            self.realtime_plan.at[index, "decision_timestep"] = (
                timestep if is_first else None
            )
            self.realtime_plan.at[index, "trigger_type"] = trigger.trigger_type if is_first else None
            self.realtime_plan.at[index, "buy_multipliers"] = _json(pricing.buy_multipliers) if is_first else None
            self.realtime_plan.at[index, "sell_multipliers"] = _json(pricing.sell_multipliers) if is_first else None
            if offset < len(pricing.buy_multipliers):
                self.realtime_plan.at[index, "buy_multiplier"] = pricing.buy_multipliers[offset]
                self.buy_multiplier_schedule[timestep + 1 + offset] = float(
                    pricing.buy_multipliers[offset]
                )
            if offset < len(pricing.sell_multipliers):
                self.realtime_plan.at[index, "sell_multiplier"] = pricing.sell_multipliers[offset]
                self.sell_multiplier_schedule[timestep + 1 + offset] = float(
                    pricing.sell_multipliers[offset]
                )
            self.realtime_plan.at[index, "intraday_prices"] = _json(intraday_prices) if is_first else None

    def append_log(
        self,
        *,
        mode: str,
        timestep: int,
        trigger: TriggerDecision,
        pricing: PricingDecision | None,
        evaluation: dict[str, Any] | None,
        result: dict[str, Any] | None,
        rerun_count: int,
        context: dict[str, Any],
    ) -> None:
        price_rows = context["intraday_prices"]["prices"]
        intraday_price = context["intraday_prices"]["current_price"]
        raw_trigger = context.get("llm_raw_trigger") or {}
        buy_price = (
            intraday_price * pricing.buy_multipliers[0]
            if pricing is not None and intraday_price is not None
            else None
        )
        sell_price = (
            intraday_price * pricing.sell_multipliers[0]
            if pricing is not None and intraday_price is not None
            else None
        )
        optimizer_telemetry = result.get("optimizer_telemetry", {}) if result else {}
        solver_telemetry = result.get("solver_telemetry", {}) if result else {}
        self.logs.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "timestep": timestep,
                "mode": mode,
                "action": trigger.action,
                "trigger_type": trigger.trigger_type,
                "flagged_buses": _json(trigger.flagged_buses),
                "llm_raw_action": raw_trigger.get("action"),
                "llm_raw_trigger_type": raw_trigger.get("trigger_type"),
                "llm_raw_flagged_buses": _json(raw_trigger.get("flagged_buses")),
                "llm_raw_reasoning": raw_trigger.get("reasoning"),
                "trigger_guard_applied": context.get("trigger_guard_applied", False),
                "llm1_reasoning": trigger.reasoning,
                "llm1_confidence": trigger.confidence,
                "buy_multipliers": _json(pricing.buy_multipliers) if pricing else None,
                "sell_multipliers": _json(pricing.sell_multipliers) if pricing else None,
                "llm2_reasoning": pricing.reasoning if pricing else None,
                "accept": evaluation.get("accept") if evaluation else None,
                "rerun_count": rerun_count,
                "llm3_reasoning": evaluation.get("reasoning") if evaluation else None,
                "pto_daily_cost": result.get("pto_daily_cost") if result else None,
                "aggregator_revenue": result.get("aggregator_revenue") if result else None,
                "remaining_horizon_pto_cost": result.get("remaining_horizon_pto_cost") if result else None,
                "remaining_horizon_aggregator_revenue": result.get("remaining_horizon_aggregator_revenue") if result else None,
                "projected_full_day_pto_cost": result.get("projected_full_day_pto_cost") if result else None,
                "projected_full_day_aggregator_revenue": result.get("projected_full_day_aggregator_revenue") if result else None,
                "revenue_neutrality_active": result.get("revenue_neutrality_active") if result else None,
                "revenue_neutrality_floor": result.get("revenue_neutrality_floor") if result else None,
                "revenue_neutrality_shortfall": result.get("revenue_neutrality_shortfall") if result else None,
                "revenue_neutrality_compliant": result.get("revenue_neutrality_compliant") if result else None,
                "baseline_revenue_retention_fraction": result.get("baseline_revenue_retention_fraction") if result else None,
                "baseline_revenue_retention_floor": result.get("baseline_revenue_retention_floor") if result else None,
                "baseline_revenue_retention_shortfall": result.get("baseline_revenue_retention_shortfall") if result else None,
                "baseline_revenue_retention_compliant": result.get("baseline_revenue_retention_compliant") if result else None,
                "canonical_priority_satisfied": result.get("benchmark_canonical_priority_satisfied") if result else None,
                "canonical_priority_compliance_gap": result.get("benchmark_canonical_priority_compliance_gap") if result else None,
                "interpreted_operational_priority": _json(
                    evaluation.get("interpreted_priority") if evaluation else None
                ),
                "priority_assessment": _json(
                    evaluation.get("priority_assessment") if evaluation else None
                ),
                "total_kwh_bought": result.get("total_kwh_bought") if result else None,
                "total_kwh_sold": result.get("total_kwh_sold") if result else None,
                "avg_grid_price": result.get("avg_grid_price") if result else None,
                "max_energy_deviation_pct": context["deviation_summary"]["max_energy_deviation_pct"],
                "is_mock": result.get("is_mock") if result else None,
                "solver_status": result.get("solver_status") if result else None,
                "solver_name": result.get("solver_name") if result else None,
                "optimizer_latency_seconds": result.get("optimizer_latency_seconds") if result else None,
                "optimizer_process_cpu_seconds": optimizer_telemetry.get(
                    "process_cpu_seconds"
                ),
                "optimizer_peak_rss_delta_mb": optimizer_telemetry.get(
                    "peak_rss_delta_mb"
                ),
                "solver_wall_seconds": solver_telemetry.get("wall_seconds"),
                "solver_process_cpu_seconds": solver_telemetry.get(
                    "process_cpu_seconds"
                ),
                "solver_branch_and_bound_nodes": solver_telemetry.get(
                    "branch_and_bound_nodes"
                ),
                "solver_relative_gap": solver_telemetry.get("relative_gap"),
                "solver_fallback_errors": _json(result.get("solver_fallback_errors", [])) if result else None,
                "information_processing_seconds": context.get("information_processing_seconds"),
                "workflow_latency_seconds": context.get("workflow_latency_seconds"),
                "workflow_process_cpu_seconds": context.get("workflow_process_cpu_seconds"),
                "workflow_average_cpu_cores": context.get("workflow_average_cpu_cores"),
                "workflow_peak_rss_mb": context.get("workflow_peak_rss_mb"),
                "workflow_peak_rss_delta_mb": context.get("workflow_peak_rss_delta_mb"),
                "llm_request_attempts": context.get("llm_request_attempts", 0),
                "llm_successful_requests": context.get("llm_successful_requests", 0),
                "llm_failed_attempts": context.get("llm_failed_attempts", 0),
                "llm_input_tokens": context.get("llm_input_tokens", 0),
                "llm_cached_input_tokens": context.get("llm_cached_input_tokens", 0),
                "llm_cache_write_tokens": context.get("llm_cache_write_tokens", 0),
                "llm_uncached_input_tokens": context.get("llm_uncached_input_tokens", 0),
                "llm_output_tokens": context.get("llm_output_tokens", 0),
                "llm_reasoning_tokens": context.get("llm_reasoning_tokens", 0),
                "llm_total_tokens": context.get("llm_total_tokens", 0),
                "llm_latency_seconds": context.get("llm_latency_seconds", 0),
                "llm_approximate_cost_usd": context.get("llm_approximate_cost_usd", 0),
                "intraday_price": intraday_price,
                "intraday_prices": _json(price_rows),
                "buy_price": buy_price,
                "sell_price": sell_price,
                "active_scenarios": _json(context.get("active_scenarios", [])),
                "operational_notices": _json(context.get("operational_notices", [])),
                "notice_interpretation": _json(context.get("notice_interpretation")),
                "notice_guard_applied": bool(
                    context.get("notice_flags", {}).get("same_event_already_accounted")
                ),
            }
        )

    def save(self, path: Path, config: WorkflowConfig) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path = path.with_suffix(".agent_calls.jsonl")
        jsonl_path.write_text(
            "".join(json.dumps(row, default=str) + "\n" for row in self.agent_calls),
            encoding="utf-8",
        )
        config_record = asdict(config)
        for key, value in list(config_record.items()):
            if isinstance(value, Path):
                config_record[key] = str(value)
            elif isinstance(value, tuple):
                config_record[key] = list(value)
        config_rows = [{"field": key, "value": _json(value) if isinstance(value, (list, dict)) else value} for key, value in config_record.items()]
        agent_calls_frame = pd.DataFrame(self.agent_calls)
        if agent_calls_frame.empty:
            agent_calls_frame = pd.DataFrame(columns=AGENT_CALL_COLUMNS)
        agent_calls_excel = _excel_safe_agent_calls(
            agent_calls_frame, jsonl_path.name
        )
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame(self.logs).to_excel(writer, sheet_name="realtime_log", index=False)
            self.realtime_plan.to_excel(writer, sheet_name="Realtime_plan", index=False)
            self.forecast_prices.to_excel(writer, sheet_name="Forecasted", index=False)
            self.forecast_energy[["timestep", *bus_columns(self.forecast_energy)]].to_excel(
                writer, sheet_name="Forecasted Energy", index=False
            )
            pd.DataFrame(self.attempts).to_excel(writer, sheet_name="optimization_attempts", index=False)
            agent_calls_excel.to_excel(writer, sheet_name="agent_calls", index=False)
            pd.DataFrame(self.resource_usage).to_excel(writer, sheet_name="resource_usage", index=False)
            pd.DataFrame(self.settlement).to_excel(
                writer, sheet_name="ex_post_settlement", index=False
            )
            pd.DataFrame([self.run_summary]).to_excel(writer, sheet_name="run_summary", index=False)
            pd.DataFrame(config_rows).to_excel(writer, sheet_name="run_config", index=False)
        path.with_suffix(".run_summary.json").write_text(
            json.dumps(self.run_summary, default=str, indent=2), encoding="utf-8"
        )

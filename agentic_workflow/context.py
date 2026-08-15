from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .disturbances import DisturbanceApplication
from .io import bus_ids_from_frame, DayAheadReference, dataframe_records, planned_row_for_observation


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_optional_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            return None
    return None


def build_observation(
    *,
    timestep: int,
    realtime_plan: pd.DataFrame,
    disturbance: DisturbanceApplication,
    workbook_state: pd.DataFrame | None,
    state_source: str,
) -> list[dict[str, Any]]:
    planned = planned_row_for_observation(realtime_plan, timestep) or {}
    workbook_by_bus: dict[int, dict[str, Any]] = {}
    if workbook_state is not None:
        workbook_by_bus = {
            int(row["bus_id"]): row for row in dataframe_records(workbook_state)
        }
    observation: list[dict[str, Any]] = []
    bus_ids = bus_ids_from_frame(realtime_plan)
    if workbook_by_bus:
        bus_ids = sorted(set(bus_ids) | set(workbook_by_bus))
    for bus_id in bus_ids:
        key = f"bus_{bus_id}_kwh"
        workbook_row = workbook_by_bus.get(bus_id, {})
        if state_source == "workbook" and workbook_row.get("current_energy_kwh") is not None:
            energy = float(workbook_row["current_energy_kwh"])
        else:
            energy = float(planned.get(key) or 0)
            disturbed_delta = disturbance.disturbed_delta.get(key)
            undisturbed_delta = disturbance.undisturbed_delta.get(key)
            if disturbed_delta is not None and undisturbed_delta is not None:
                energy += disturbed_delta - undisturbed_delta
            energy = min(365.0, max(73.0, energy))
        observation.append(
            {
                "bus_id": bus_id,
                "current_timestep": timestep,
                "current_soc": workbook_row.get("current_soc"),
                "current_energy_kwh": round(energy, 4),
                "operation_status": str(workbook_row.get("operation_status", "idle")),
                "delay_minutes": int(workbook_row.get("delay_minutes") or 0)
                + int(disturbance.delay_info.get(bus_id, 0)),
            }
        )
    return observation


def _last_reoptimization(realtime_plan: pd.DataFrame, timestep: int) -> dict[str, Any] | None:
    if "reoptimized" not in realtime_plan:
        return None
    flags = realtime_plan["reoptimized"].fillna(False).astype(bool)
    state_index = timestep - 1
    if "decision_timestep" in realtime_plan:
        decisions = pd.to_numeric(
            realtime_plan["decision_timestep"], errors="coerce"
        )
        candidates = realtime_plan.loc[flags & decisions.le(timestep)]
    else:
        candidates = realtime_plan.loc[
            flags & (realtime_plan["timestep"] <= state_index)
        ]
    if candidates.empty:
        return None
    row = candidates.sort_values("timestep").iloc[-1].to_dict()
    decision_timestep = _float(row.get("decision_timestep"))
    row["observation_timestep"] = (
        int(decision_timestep)
        if decision_timestep is not None
        else int(row["timestep"]) + 1
    )
    return row


def _price_zone(value: float, minimum: float, maximum: float) -> str:
    if maximum <= minimum:
        return "transition"
    position = (value - minimum) / (maximum - minimum)
    if position <= 1 / 3:
        return "cheap"
    if position >= 2 / 3:
        return "expensive"
    return "transition"


def build_context(
    *,
    mode: str,
    altruistic_revenue_retention_fraction: float = 0.50,
    timestep: int,
    observation: list[dict[str, Any]],
    realtime_plan: pd.DataFrame,
    day_ahead: DayAheadReference,
    forecast_prices: pd.DataFrame,
    forecast_energy: pd.DataFrame,
    disturbance: DisturbanceApplication,
    price_history: dict[int, float],
    context_history: list[dict[str, Any]],
) -> dict[str, Any]:
    planned = planned_row_for_observation(realtime_plan, timestep)
    forecast_by_timestep = {
        int(row["timestep"]): row for row in dataframe_records(forecast_energy)
    }
    forecast_state_index = timestep - 1
    forecast_current = forecast_by_timestep.get(forecast_state_index, {})
    forecast_previous = forecast_by_timestep.get(forecast_state_index - 1, {})

    deviations: list[dict[str, Any]] = []
    for bus in observation:
        bus_id = int(bus["bus_id"])
        key = f"bus_{bus_id}_kwh"
        planned_kwh = _float(planned.get(key)) if planned else None
        current_kwh = float(bus["current_energy_kwh"])
        energy_deviation = current_kwh - planned_kwh if planned_kwh is not None else 0.0
        energy_deviation_pct = (
            100 * energy_deviation / planned_kwh if planned_kwh not in {None, 0} else 0.0
        )
        actual_delta = disturbance.disturbed_delta.get(key)
        forecast_current_kwh = _float(forecast_current.get(key))
        forecast_previous_kwh = _float(forecast_previous.get(key))
        forecast_delta = (
            forecast_current_kwh - forecast_previous_kwh
            if forecast_current_kwh is not None and forecast_previous_kwh is not None
            else None
        )
        delta_deviation = (
            actual_delta - forecast_delta
            if actual_delta is not None and forecast_delta is not None
            else None
        )
        delta_deviation_pct = (
            100 * delta_deviation / abs(forecast_delta)
            if delta_deviation is not None and forecast_delta is not None and abs(forecast_delta) > 0.01
            else None
        )
        delay_minutes = int(bus.get("delay_minutes") or 0)
        deviations.append(
            {
                "bus_id": bus_id,
                "current_energy_kwh": current_kwh,
                "planned_energy_kwh": planned_kwh,
                "energy_deviation_kwh": round(energy_deviation, 2),
                "energy_deviation_pct": round(energy_deviation_pct, 2),
                "actual_interval_delta_kwh": actual_delta,
                "forecast_interval_delta_kwh": forecast_delta,
                "energy_delta_deviation_kwh": round(delta_deviation, 2) if delta_deviation is not None else None,
                "energy_delta_deviation_pct": round(delta_deviation_pct, 2) if delta_deviation_pct is not None else None,
                "delay_minutes": delay_minutes,
                "operation_status": bus["operation_status"],
                "is_delayed": abs(delay_minutes) > 0,
                "is_in_trip": bus["operation_status"] == "in_trip",
            }
        )

    forecast_price_map = {
        int(row["timestep"]): float(row["spot_market"])
        for row in dataframe_records(forecast_prices)
    }
    disturbed_price_map = {
        int(row["timestep"]): float(row["spot_market"])
        for row in dataframe_records(disturbance.prices)
    }
    observed_price = disturbed_price_map.get(timestep)
    forecasted_price = forecast_price_map.get(timestep)
    price_deviation_pct = (
        round(100 * (observed_price - forecasted_price) / forecasted_price, 2)
        if observed_price is not None and forecasted_price not in {None, 0}
        else None
    )
    planning_start_timestep = timestep + 1
    remaining_prices = [
        {"timestep": current, "spot_market": price}
        for current, price in sorted(disturbed_price_map.items())
        if current >= planning_start_timestep
    ]
    remaining_values = [row["spot_market"] for row in remaining_prices]
    minimum_price = min(remaining_values) if remaining_values else None
    maximum_price = max(remaining_values) if remaining_values else None
    for row in remaining_prices:
        row["price_zone"] = _price_zone(row["spot_market"], minimum_price or 0, maximum_price or 0)

    full_price_map = dict(price_history)
    if observed_price is not None:
        full_price_map[timestep] = observed_price
    full_price_map.update({row["timestep"]: row["spot_market"] for row in remaining_prices})
    full_prices_complete = all(
        current in full_price_map for current in range(planning_start_timestep, 49)
    )
    summary = day_ahead.summary
    reference_aggregator_revenue = _float(summary.get("aggregator_revenue"))
    retained_revenue_floor = (
        reference_aggregator_revenue * altruistic_revenue_retention_fraction
        if reference_aggregator_revenue is not None
        else None
    )
    buy_multipliers = list(summary.get("buy_multipliers") or [1.05, 1.10, 1.05])
    sell_multipliers = list(summary.get("sell_multipliers") or [0.80, 0.85, 0.80])
    period_size = max(1, 48 // len(buy_multipliers))

    da_revenue_remaining = 0.0
    da_cost_remaining = 0.0
    da_benchmark_valid = full_prices_complete
    if da_benchmark_valid:
        for current in range(planning_start_timestep, 49):
            plan_row = planned_row_for_observation(day_ahead.plan, current)
            if plan_row is None:
                da_benchmark_valid = False
                break
            price = full_price_map[current]
            period = min(len(buy_multipliers) - 1, (current - 1) // period_size)
            w_buy = _float(plan_row.get("w_buy"))
            w_sell = _float(plan_row.get("w_sell"))
            if w_buy is None or w_sell is None:
                da_benchmark_valid = False
                break
            buy_price = buy_multipliers[period] * price
            sell_price = sell_multipliers[period] * price
            da_revenue_remaining += (buy_price - price) * w_buy + (price - sell_price) * w_sell
            da_cost_remaining += buy_price * w_buy - sell_price * w_sell

    last_reopt = _last_reoptimization(realtime_plan, timestep)
    last_reopt_timestep = last_reopt.get("observation_timestep") if last_reopt else None
    last_trigger = last_reopt.get("trigger_type") if last_reopt else None
    severe_delay = [item for item in deviations if abs(item["delay_minutes"]) >= 15]
    high_energy = [item for item in deviations if abs(item["energy_deviation_pct"]) > 10]
    moderate_energy = [item for item in deviations if abs(item["energy_deviation_pct"]) > 5]
    disturbed_energy = [
        item
        for item in deviations
        if item["energy_delta_deviation_pct"] is not None
        and abs(item["energy_delta_deviation_pct"]) > 10
    ]
    event_status = disturbance.event_status or {}
    price_event = event_status.get("price", {})
    energy_event = event_status.get("energy", {})
    delay_event = event_status.get("delay", {})
    energy_disturbance_actionable = bool(energy_event.get("onset_pending")) or (
        bool(disturbed_energy) and not bool(energy_event.get("configured"))
    )
    disturbed_energy_buses = (
        list(energy_event.get("target_buses") or [])
        if energy_event.get("onset_pending")
        else [item["bus_id"] for item in disturbed_energy]
    )
    price_deviation_actionable = bool(price_event.get("onset_pending")) or bool(
        price_deviation_pct is not None
        and abs(price_deviation_pct) > 15
        and not price_event.get("configured")
    )
    delay_sign_reversed = []
    if not disturbance.delay_removal_active and last_trigger in {"delay", "delay_removal", "delay_recovery"}:
        delay_sign_reversed = [item for item in severe_delay if item["delay_minutes"] < 0]
    trigger_flags = {
        "has_severe_delay": bool(severe_delay),
        "severe_delay_buses": [
            {"bus_id": item["bus_id"], "delay_minutes": item["delay_minutes"]}
            for item in severe_delay
        ],
        "has_high_energy_deviation": bool(high_energy),
        "high_energy_deviation_buses": [
            {"bus_id": item["bus_id"], "energy_deviation_pct": item["energy_deviation_pct"]}
            for item in high_energy
        ],
        "multi_bus_moderate_deviation": len(moderate_energy) >= 2,
        "moderate_deviation_buses": [
            {"bus_id": item["bus_id"], "energy_deviation_pct": item["energy_deviation_pct"]}
            for item in moderate_energy
        ],
        "price_high": price_deviation_actionable and price_deviation_pct > 15,
        "price_low": price_deviation_actionable and price_deviation_pct < -15,
        "price_deviation_significant": price_deviation_actionable,
        "reported_discharging_buses": [
            item["bus_id"] for item in deviations if item["operation_status"] == "discharging"
        ],
        "unexpected_discharging_buses": [
            item["bus_id"]
            for item in deviations
            if item["operation_status"] == "discharging"
            and abs(item["energy_deviation_pct"]) > 1
        ],
        "delay_sign_reversed": bool(delay_sign_reversed),
        "delay_sign_reversed_buses": [
            {"bus_id": item["bus_id"], "delay_now": item["delay_minutes"]}
            for item in delay_sign_reversed
        ],
        "delay_removal_active": disturbance.delay_removal_active,
        "price_event_onset_pending": bool(price_event.get("onset_pending")),
        "price_event_active": bool(price_event.get("active")),
        "price_event_active_accounted": bool(price_event.get("active_accounted")),
        "price_recovery_active": bool(price_event.get("recovery_pending")),
        "energy_event_onset_pending": bool(energy_event.get("onset_pending")),
        "energy_event_active": bool(energy_event.get("active")),
        "energy_event_active_accounted": bool(energy_event.get("active_accounted")),
        "energy_recovery_active": bool(energy_event.get("recovery_pending")),
        "energy_event_buses": list(energy_event.get("target_buses") or []),
        "delay_event_onset_pending": bool(delay_event.get("onset_pending")),
        "delay_event_active": bool(delay_event.get("active")),
        "delay_event_active_accounted": bool(delay_event.get("active_accounted")),
        "delay_recovery_active": bool(delay_event.get("recovery_pending")),
    }
    trigger_flags["same_event_already_accounted"] = bool(
        any(
            status.get("active_accounted")
            for status in (price_event, energy_event, delay_event)
        )
        and not any(
            status.get("onset_pending") or status.get("recovery_pending")
            for status in (price_event, energy_event, delay_event)
        )
        and not high_energy
        and len(moderate_energy) < 2
        and not trigger_flags["unexpected_discharging_buses"]
        and not price_deviation_actionable
        and not energy_disturbance_actionable
        and not severe_delay
    )
    max_energy_deviation = max((abs(item["energy_deviation_pct"]) for item in deviations), default=0)
    max_delta_deviation = max(
        (
            abs(item["energy_delta_deviation_pct"])
            for item in deviations
            if item["energy_delta_deviation_pct"] is not None
        ),
        default=0,
    )
    remaining_timesteps = 48 - timestep
    history = [entry["history_entry"] for entry in context_history]

    context = {
        "timestep": timestep,
        "planning_start_timestep": (
            planning_start_timestep if planning_start_timestep <= 48 else None
        ),
        "total_timesteps": 48,
        "remaining_timesteps": remaining_timesteps,
        "remaining_hours": f"{remaining_timesteps * 0.5:.1f}",
        "mode": mode,
        "trigger_flags": trigger_flags,
        "n_periods": len(buy_multipliers),
        "period_size": period_size,
        "realtime_state": observation,
        "day_ahead_state": [
            {"bus_id": item["bus_id"], "planned_energy_kwh": item["planned_energy_kwh"]}
            for item in deviations
        ],
        "day_ahead_summary": {
            "pto_daily_cost": _float(summary.get("pto_daily_cost")),
            "aggregator_revenue": _float(summary.get("aggregator_revenue")),
            "buy_multipliers": buy_multipliers,
            "sell_multipliers": sell_multipliers,
            "avg_grid_price": _float(summary.get("avg_grid_price")),
        },
        "revenue_neutrality": {
            "active": mode == "altruistic",
            "policy": "baseline_revenue_retention_floor",
            "baseline_full_day_aggregator_revenue": (
                round(reference_aggregator_revenue, 6)
                if reference_aggregator_revenue is not None
                else None
            ),
            "retention_fraction": altruistic_revenue_retention_fraction,
            "full_day_revenue_floor": (
                round(retained_revenue_floor, 6)
                if retained_revenue_floor is not None
                else None
            ),
            "source": (
                "altruistic_revenue_retention_fraction * "
                "day_ahead_summary.aggregator_revenue"
            ),
            "fixed_before_realtime_disturbances": True,
            "realized_prefix_aggregator_revenue": None,
            "remaining_revenue_required": None,
        },
        "reoptimization_history": {
            "last_reopt_timestep": last_reopt_timestep,
            "last_reopt_trigger_type": last_trigger,
            "last_reopt_buy_multipliers": _parse_optional_list(last_reopt.get("buy_multipliers")) if last_reopt else None,
            "last_reopt_sell_multipliers": _parse_optional_list(last_reopt.get("sell_multipliers")) if last_reopt else None,
            "last_reopt_prices": _parse_optional_list(last_reopt.get("intraday_prices")) if last_reopt else None,
            "using_reopt_plan": last_reopt is not None,
        },
        "da_benchmark": {
            "da_revenue_remaining": round(da_revenue_remaining, 4),
            "da_cost_remaining": round(da_cost_remaining, 4),
            "da_remaining_steps": remaining_timesteps,
            "da_benchmark_valid": da_benchmark_valid,
        },
        "intraday_prices": {
            "remaining_count": len(remaining_prices),
            "avg_price": round(sum(remaining_values) / len(remaining_values), 6) if remaining_values else None,
            "min_price": round(minimum_price, 6) if minimum_price is not None else None,
            "max_price": round(maximum_price, 6) if maximum_price is not None else None,
            "current_price": observed_price,
            "forecasted_price": forecasted_price,
            "undisturbed_price": next(
                (float(row["spot_market"]) for row in dataframe_records(disturbance.base_prices) if int(row["timestep"]) == timestep),
                None,
            ),
            "price_deviation_pct": price_deviation_pct,
            "prices": remaining_prices,
        },
        "deviations": deviations,
        "deviation_summary": {
            "max_energy_deviation_pct": max_energy_deviation,
            "has_severe_deviation": max_energy_deviation > 10,
            "max_energy_delta_deviation_pct": max_delta_deviation,
            "has_energy_disturbance": energy_disturbance_actionable,
            "disturbed_energy_buses": disturbed_energy_buses,
            "disturbed_energy_bus_count": len(disturbed_energy_buses),
            "delayed_buses": [item["bus_id"] for item in deviations if abs(item["delay_minutes"]) > 0],
            "delayed_bus_count": len([item for item in deviations if abs(item["delay_minutes"]) > 0]),
            "in_trip_bus_count": len([item for item in deviations if item["is_in_trip"]]),
            "has_delays": any(abs(item["delay_minutes"]) > 0 for item in deviations),
        },
        "history": history,
        "active_scenarios": disturbance.scenarios,
        "event_status": event_status,
    }
    context["history_entry"] = {
        "timestep": timestep,
        "reoptimized": bool(last_reopt and last_reopt_timestep == timestep),
        "buses": [
            {
                "bus_id": item["bus_id"],
                "current_energy_kwh": item["current_energy_kwh"],
                "planned_energy_kwh": item["planned_energy_kwh"],
                "energy_deviation_kwh": item["energy_deviation_kwh"],
                "energy_deviation_pct": item["energy_deviation_pct"],
                "energy_delta_deviation_pct": item["energy_delta_deviation_pct"],
                "operation_status": item["operation_status"],
                "delay_minutes": item["delay_minutes"],
            }
            for item in deviations
        ],
    }
    return context

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .disturbances import DisturbanceApplication
from .io import dataframe_records, planned_row_for_observation
from .models import NoticeInterpretation, NoticeParameterUpdates
from .notices import NoticeRecord, merge_interpretations


ACTIVE_PHASES = {"onset", "persistence", "severity_change"}
CLEARING_PHASES = {"recovery", "stable"}


class PhysicalEventSeries:
    """Hidden benchmark truth kept in a file that is never sent to a trigger."""

    def __init__(self, path: Path | None):
        self.path = path
        self._events: list[dict[str, Any]] = []
        if path is None:
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("events", [])
        for row in rows:
            start = int(row["effective_timestep"])
            end = int(row["end_timestep"])
            detection = int(row.get("sensor_detection_timestep", start))
            if not 1 <= start <= end <= 48:
                raise ValueError(f"Invalid physical event window for {row.get('event_id')}")
            if not start <= detection <= end:
                raise ValueError(f"Invalid sensor detection time for {row.get('event_id')}")
            updates = NoticeParameterUpdates.model_validate(row.get("updates") or {})
            self._events.append(
                {
                    "scenario_id": str(row["scenario_id"]),
                    "event_id": str(row["event_id"]),
                    "effective_timestep": start,
                    "end_timestep": end,
                    "sensor_detection_timestep": detection,
                    "updates": updates,
                }
            )

    def truth_at(
        self,
        timestep: int,
        *,
        scenario_ids: tuple[str, ...] = (),
        observable: bool = False,
    ) -> NoticeInterpretation | None:
        selected = set(scenario_ids)
        active: list[NoticeInterpretation] = []
        for row in self._events:
            if selected and row["scenario_id"] not in selected:
                continue
            start = (
                row["sensor_detection_timestep"]
                if observable
                else row["effective_timestep"]
            )
            if not start <= timestep <= row["end_timestep"]:
                continue
            updates = row["updates"]
            has_bus = bool(
                updates.delay_minutes_by_bus
                or updates.return_delay_minutes_by_bus
                or updates.energy_multiplier_by_bus
            )
            has_charger = bool(
                updates.charger_power_kw or updates.unavailable_chargers
            )
            event_type = (
                "combined"
                if has_bus and has_charger
                else "charger_fault"
                if updates.unavailable_chargers
                else "charger_derating"
                if has_charger
                else "route_energy_change"
                if updates.energy_multiplier_by_bus
                else "service_delay"
            )
            active.append(
                NoticeInterpretation(
                    event_id=row["event_id"],
                    source_type="combined" if event_type == "combined" else (
                        "ocpp" if has_charger else "service_alert"
                    ),
                    event_type=event_type,
                    phase="onset" if timestep == start else "persistence",
                    affected_buses=sorted(
                        set(updates.delay_minutes_by_bus)
                        | set(updates.return_delay_minutes_by_bus)
                        | set(updates.energy_multiplier_by_bus)
                    ),
                    affected_chargers=sorted(
                        set(updates.charger_power_kw)
                        | set(updates.unavailable_chargers)
                    ),
                    effective_timestep=row["effective_timestep"],
                    expected_end_timestep=row["end_timestep"],
                    updates=updates,
                    evidence=["hidden_physical_truth_v2"],
                )
            )
        return merge_interpretations(active)


def interpretation_active_at(
    interpretation: NoticeInterpretation | None, timestep: int
) -> NoticeInterpretation | None:
    """Return a planner event only while its physical window is current."""

    if interpretation is None or timestep < interpretation.effective_timestep:
        return None
    if (
        interpretation.expected_end_timestep is not None
        and timestep > interpretation.expected_end_timestep
    ):
        return None
    return interpretation


def advance_physical_notice_truth(
    records: Iterable[NoticeRecord],
    active_events: dict[str, dict[str, Any]],
) -> NoticeInterpretation | None:
    """Advance hidden canonical event state without exposing it to any trigger."""

    for record in records:
        truth = record.canonical
        if truth is None:
            continue
        if truth.phase in CLEARING_PHASES:
            active_events.pop(truth.event_id, None)
        elif truth.phase in ACTIVE_PHASES:
            active_events[truth.event_id] = truth.model_dump()
    return merge_interpretations(
        NoticeInterpretation.model_validate(value)
        for _, value in sorted(active_events.items())
    )


def _time_to_end_step(value: Any) -> int:
    hour, minute = (int(part) for part in str(value).split(":", 1))
    step_value = (hour * 60 + minute) / 30.0
    step = int(step_value) + 1 if step_value.is_integer() else int(step_value) + 2
    return max(1, min(48, step))


def _extended_trip_buses(
    timestep: int,
    truth: NoticeInterpretation | None,
    trips: pd.DataFrame,
) -> set[int]:
    if truth is None:
        return set()
    affected: set[int] = set()
    bus_series = pd.to_numeric(trips.get("bus_id"), errors="coerce")
    for bus_id, delay in truth.updates.return_delay_minutes_by_bus.items():
        rows = trips.loc[bus_series == int(bus_id)]
        if rows.empty:
            continue
        base_end = max(_time_to_end_step(value) for value in rows["time_end"])
        extended_end = min(49, base_end + int(math.ceil(float(delay) / 30.0)))
        if base_end <= timestep < extended_end:
            affected.add(int(bus_id))
    return affected


def realize_notice_consequences(
    disturbance: DisturbanceApplication,
    truth: NoticeInterpretation | None,
    planner_assumption: NoticeInterpretation | None = None,
) -> DisturbanceApplication:
    """Apply residual consequences not already represented in the active plan."""

    if truth is None and planner_assumption is None:
        return disturbance
    disturbed_delta = dict(disturbance.disturbed_delta)
    energy_multipliers = dict(disturbance.energy_multipliers)
    true_energy = (
        truth.updates.energy_multiplier_by_bus if truth is not None else {}
    )
    assumed_energy = (
        planner_assumption.updates.energy_multiplier_by_bus
        if planner_assumption is not None
        else {}
    )
    for bus_id in set(true_energy) | set(assumed_energy):
        multiplier = float(true_energy.get(bus_id, 1.0))
        key = f"bus_{bus_id}_kwh"
        energy_multipliers[bus_id] = (
            energy_multipliers.get(bus_id, 1.0) * multiplier
        )
        assumed_multiplier = float(assumed_energy.get(bus_id, 1.0))
        residual_multiplier = multiplier / max(assumed_multiplier, 1e-9)
        delta = disturbed_delta.get(key)
        if delta is not None and delta < 0:
            disturbed_delta[key] = round(float(delta) * residual_multiplier, 4)
    delay_info = dict(disturbance.delay_info)
    true_delays = truth.updates.delay_minutes_by_bus if truth is not None else {}
    assumed_delays = (
        planner_assumption.updates.delay_minutes_by_bus
        if planner_assumption is not None
        else {}
    )
    for bus_id in set(true_delays) | set(assumed_delays):
        delay_info[bus_id] = (
            delay_info.get(bus_id, 0)
            + int(true_delays.get(bus_id, 0))
            - int(assumed_delays.get(bus_id, 0))
        )
    true_return = (
        truth.updates.return_delay_minutes_by_bus if truth is not None else {}
    )
    assumed_return = (
        planner_assumption.updates.return_delay_minutes_by_bus
        if planner_assumption is not None
        else {}
    )
    for bus_id in set(true_return) | set(assumed_return):
        delay_info[bus_id] = (
            delay_info.get(bus_id, 0)
            + int(true_return.get(bus_id, 0))
            - int(assumed_return.get(bus_id, 0))
        )
    return replace(
        disturbance,
        disturbed_delta=disturbed_delta,
        energy_multipliers=energy_multipliers,
        delay_info=delay_info,
    )


def _charger_capacity_kwh(
    chargers: pd.DataFrame,
    truth: NoticeInterpretation | None,
    *,
    timestep_hours: float,
) -> float:
    powers = {
        int(row["charger_id"]): float(
            row.get("max_power_kw")
            if row.get("max_power_kw") is not None
            else row.get("charger_kw") or 0
        )
        for row in dataframe_records(chargers)
    }
    if truth is not None:
        for charger_id in truth.updates.unavailable_chargers:
            powers[int(charger_id)] = 0.0
        for charger_id, power in truth.updates.charger_power_kw.items():
            powers[int(charger_id)] = max(0.0, float(power))
    return timestep_hours * sum(max(0.0, value) for value in powers.values())


def advance_realized_energy(
    *,
    timestep: int,
    realtime_plan: pd.DataFrame,
    disturbance: DisturbanceApplication,
    chargers: pd.DataFrame,
    physical_truth: NoticeInterpretation | None,
    realized_energy_by_bus: dict[int, float],
    trips: pd.DataFrame | None = None,
    energy_consumption: pd.DataFrame | None = None,
    timestep_hours: float = 0.5,
) -> dict[int, float]:
    """Advance cumulative physical bus energy through the executed interval.

    The optimizer plan is a target trajectory.  This state is maintained
    independently so an unmodelled energy increase or curtailed charging
    remains visible at later timesteps instead of disappearing after one row.
    """

    current = planned_row_for_observation(realtime_plan, timestep) or {}
    bus_ids = sorted(
        int(column.removeprefix("bus_").removesuffix("_kwh"))
        for column in realtime_plan.columns
        if str(column).startswith("bus_") and str(column).endswith("_kwh")
    )
    if not realized_energy_by_bus:
        previous = (
            planned_row_for_observation(realtime_plan, timestep - 1)
            if timestep > 1
            else None
        ) or current
        realized_energy_by_bus.update(
            {
                bus_id: float(previous.get(f"bus_{bus_id}_kwh") or 0.0)
                for bus_id in bus_ids
            }
        )
        if timestep <= 1:
            return dict(realized_energy_by_bus)

    previous_plan = (
        planned_row_for_observation(realtime_plan, timestep - 1)
        if timestep > 1
        else None
    ) or current
    planned_buy = max(0.0, float(current.get("w_buy") or 0.0))
    capacity = _charger_capacity_kwh(
        chargers, physical_truth, timestep_hours=timestep_hours
    )
    charging_fraction = (
        min(1.0, capacity / planned_buy) if planned_buy > 1e-9 else 1.0
    )
    extended_trip_buses = _extended_trip_buses(
        timestep, physical_truth, trips if trips is not None else pd.DataFrame()
    )
    consumption_by_bus = {
        int(row["bus_id"]): float(row.get("energy_kwhkm") or 0.0)
        for row in dataframe_records(
            energy_consumption if energy_consumption is not None else pd.DataFrame()
        )
        if row.get("bus_id") is not None
    }
    velocity_by_bus = {
        int(row["bus_id"]): float(
            row.get("velocity_kmh") or row.get("average_velocity_kmh") or 0.0
        )
        for row in dataframe_records(trips if trips is not None else pd.DataFrame())
        if row.get("bus_id") is not None
    }
    for bus_id in bus_ids:
        key = f"bus_{bus_id}_kwh"
        planned_delta = float(current.get(key) or 0.0) - float(
            previous_plan.get(key) or 0.0
        )
        physical_delta = float(disturbance.disturbed_delta.get(key, planned_delta))
        if bus_id in extended_trip_buses:
            physical_delta = -(
                consumption_by_bus.get(bus_id, 0.0)
                * velocity_by_bus.get(bus_id, 0.0)
                * timestep_hours
            )
        if physical_delta > 0:
            physical_delta *= charging_fraction
        realized_energy_by_bus[bus_id] = min(
            365.0,
            max(0.0, realized_energy_by_bus.get(bus_id, 0.0) + physical_delta),
        )
    return dict(realized_energy_by_bus)


def apply_realized_energy_to_observation(
    observation: list[dict[str, Any]], realized_energy_by_bus: dict[int, float]
) -> list[dict[str, Any]]:
    """Replace planned energy readings with cumulative physical readings."""

    for item in observation:
        bus_id = int(item["bus_id"])
        if bus_id in realized_energy_by_bus:
            item["current_energy_kwh"] = round(
                float(realized_energy_by_bus[bus_id]), 4
            )
    return observation


def _parameter_mismatch(
    truth: NoticeInterpretation | None,
    assumed: NoticeInterpretation | None,
) -> dict[str, float | int | bool]:
    true_updates = truth.updates if truth is not None else None
    assumed_updates = assumed.updates if assumed is not None else None
    true_delay = true_updates.delay_minutes_by_bus if true_updates else {}
    assumed_delay = assumed_updates.delay_minutes_by_bus if assumed_updates else {}
    true_return = true_updates.return_delay_minutes_by_bus if true_updates else {}
    assumed_return = (
        assumed_updates.return_delay_minutes_by_bus if assumed_updates else {}
    )
    true_energy = true_updates.energy_multiplier_by_bus if true_updates else {}
    assumed_energy = assumed_updates.energy_multiplier_by_bus if assumed_updates else {}
    true_power = true_updates.charger_power_kw if true_updates else {}
    assumed_power = assumed_updates.charger_power_kw if assumed_updates else {}
    delay_error = sum(
        abs(int(true_delay.get(key, 0)) - int(assumed_delay.get(key, 0)))
        for key in set(true_delay) | set(assumed_delay)
    )
    return_delay_error = sum(
        abs(int(true_return.get(key, 0)) - int(assumed_return.get(key, 0)))
        for key in set(true_return) | set(assumed_return)
    )
    energy_error = sum(
        abs(float(true_energy.get(key, 1.0)) - float(assumed_energy.get(key, 1.0)))
        for key in set(true_energy) | set(assumed_energy)
    )
    power_error = sum(
        abs(float(true_power.get(key, 200.0)) - float(assumed_power.get(key, 200.0)))
        for key in set(true_power) | set(assumed_power)
    )
    true_unavailable = set(true_updates.unavailable_chargers if true_updates else [])
    assumed_unavailable = set(
        assumed_updates.unavailable_chargers if assumed_updates else []
    )
    unavailable_error = len(true_unavailable ^ assumed_unavailable)
    return {
        "delay_parameter_absolute_error_minutes": delay_error,
        "return_delay_parameter_absolute_error_minutes": return_delay_error,
        "energy_multiplier_absolute_error": energy_error,
        "charger_power_absolute_error_kw": power_error,
        "charger_availability_mismatch_count": unavailable_error,
        "event_model_mismatch": bool(
            delay_error
            or return_delay_error
            or energy_error
            or power_error
            or unavailable_error
        ),
    }


def settle_realized_step(
    *,
    timestep: int,
    realtime_plan: pd.DataFrame,
    observation: list[dict[str, Any]],
    spot_price: float | None,
    buy_multiplier: float,
    sell_multiplier: float,
    chargers: pd.DataFrame,
    physical_truth: NoticeInterpretation | None,
    planner_assumption: NoticeInterpretation | None,
    trips: pd.DataFrame | None = None,
    timestep_hours: float = 0.5,
) -> dict[str, Any]:
    """Settle the single executed interval against common physical truth."""

    row = planned_row_for_observation(realtime_plan, timestep) or {}
    planned_buy = max(0.0, float(row.get("w_buy") or 0.0))
    planned_sell = max(0.0, float(row.get("w_sell") or 0.0))
    capacity = _charger_capacity_kwh(
        chargers, physical_truth, timestep_hours=timestep_hours
    )
    extended_trip_buses = _extended_trip_buses(
        timestep, physical_truth, trips if trips is not None else pd.DataFrame()
    )
    previous = planned_row_for_observation(realtime_plan, timestep - 1) or row
    blocked_buy = 0.0
    blocked_sell = 0.0
    for bus_id in extended_trip_buses:
        key = f"bus_{bus_id}_kwh"
        delta = float(row.get(key) or 0.0) - float(previous.get(key) or 0.0)
        if delta > 0:
            blocked_buy += delta / 0.90
        elif delta < 0:
            blocked_sell += -delta * 0.90
    realized_buy = min(max(0.0, planned_buy - blocked_buy), capacity)
    realized_sell = min(max(0.0, planned_sell - blocked_sell), capacity)
    price = float(spot_price or 0.0)
    pto_cost = price * (
        float(buy_multiplier) * realized_buy
        - float(sell_multiplier) * realized_sell
    )
    aggregator_revenue = price * (
        (float(buy_multiplier) - 1.0) * realized_buy
        + (1.0 - float(sell_multiplier)) * realized_sell
    )
    energies = [float(item["current_energy_kwh"]) for item in observation]
    energy_by_bus = {
        int(item["bus_id"]): float(item["current_energy_kwh"])
        for item in observation
    }
    reserve_shortfall = sum(max(0.0, 73.0 - value) for value in energies)
    mismatch = _parameter_mismatch(physical_truth, planner_assumption)
    return {
        "timestep": timestep,
        "spot_price": price,
        "buy_multiplier": float(buy_multiplier),
        "sell_multiplier": float(sell_multiplier),
        "planned_buy_kwh": planned_buy,
        "planned_sell_kwh": planned_sell,
        "physical_charger_capacity_kwh": capacity,
        "realized_buy_kwh": realized_buy,
        "realized_sell_kwh": realized_sell,
        "curtailed_buy_kwh": planned_buy - realized_buy,
        "curtailed_sell_kwh": planned_sell - realized_sell,
        "realized_pto_cost": pto_cost,
        "realized_aggregator_revenue": aggregator_revenue,
        "realized_grid_net_cost": price * (realized_buy - realized_sell),
        "minimum_observed_energy_kwh": min(energies) if energies else None,
        "minimum_observed_soc_fraction": (
            min(energies) / 365.0 if energies else None
        ),
        "reserve_shortfall_kwh": reserve_shortfall,
        "realized_energy_by_bus": energy_by_bus,
        "physical_event_ids": (
            physical_truth.event_id.split("+") if physical_truth else []
        ),
        "planner_event_ids": (
            planner_assumption.event_id.split("+") if planner_assumption else []
        ),
        **mismatch,
    }

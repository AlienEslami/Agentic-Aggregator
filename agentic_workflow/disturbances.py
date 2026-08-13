from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .io import bus_columns, bus_ids_from_frame, dataframe_records, planned_row_for_observation


@dataclass(slots=True)
class DisturbanceApplication:
    scenarios: list[dict[str, Any]]
    base_prices: pd.DataFrame
    prices: pd.DataFrame
    trips: pd.DataFrame
    energy_consumption: pd.DataFrame
    energy_multipliers: dict[int, float]
    disturbed_delta: dict[str, float | None]
    undisturbed_delta: dict[str, float | None]
    delay_info: dict[int, int]
    delay_removal_active: bool
    optimizer_disturbances: list[dict[str, Any]]
    event_status: dict[str, dict[str, Any]] = field(default_factory=dict)


def active_scenarios(
    scenarios: list[dict[str, Any]],
    timestep: int,
) -> list[dict[str, Any]]:
    return [
        scenario
        for scenario in scenarios
        if int(scenario.get("start_timestep") or 1)
        <= timestep
        <= int(scenario.get("end_timestep") or 48)
    ]


def _target_bus_ids(scenario: dict[str, Any], bus_ids: list[int]) -> set[int]:
    if str(scenario.get("target_scope", "")).lower() == "global":
        return set(bus_ids)
    value = scenario.get("target_bus_id")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    if not isinstance(value, list):
        value = [value]
    result: set[int] = set()
    for item in value:
        try:
            result.add(int(float(item)))
        except (TypeError, ValueError):
            continue
    return result


def _reoptimization_observation_timesteps(
    realtime_plan: pd.DataFrame,
    trigger_types: set[str],
) -> list[int]:
    if "trigger_type" not in realtime_plan:
        return []
    flags = realtime_plan.get("reoptimized", pd.Series(False, index=realtime_plan.index))
    flags = flags.fillna(False).astype(bool)
    triggers = realtime_plan["trigger_type"].fillna("").astype(str)
    timesteps = pd.to_numeric(realtime_plan["timestep"], errors="coerce")
    return [
        int(value) + 1
        for value in timesteps.loc[flags & triggers.isin(trigger_types)].dropna().tolist()
    ]


def _scenario_phase(
    scenario: dict[str, Any],
    timestep: int,
    realtime_plan: pd.DataFrame,
) -> str:
    family = str(scenario.get("scenario_family", "")).lower()
    trigger_types = {
        "price_pct": ({"price"}, {"price_recovery"}),
        "energy_pct": ({"energy_disturbance"}, {"energy_recovery"}),
        "delay_minutes": ({"delay"}, {"delay_recovery", "delay_removal"}),
    }
    if family not in trigger_types:
        return "inactive"
    onset_types, recovery_types = trigger_types[family]
    start = int(scenario.get("start_timestep") or 1)
    end = int(scenario.get("end_timestep") or 48)
    onset_accepted = any(
        start <= observed <= end
        for observed in _reoptimization_observation_timesteps(realtime_plan, onset_types)
    )
    recovery_accepted = any(
        observed > end
        for observed in _reoptimization_observation_timesteps(realtime_plan, recovery_types)
    )
    if start <= timestep <= end:
        return "active_accounted" if onset_accepted else "onset_pending"
    if timestep > end and onset_accepted and not recovery_accepted:
        return "recovery_pending"
    if timestep > end and recovery_accepted:
        return "recovered"
    return "inactive"


def _event_status(
    scenarios: list[dict[str, Any]],
    timestep: int,
    realtime_plan: pd.DataFrame,
    bus_ids: list[int],
) -> dict[str, dict[str, Any]]:
    family_names = {
        "price_pct": "price",
        "energy_pct": "energy",
        "delay_minutes": "delay",
    }
    result: dict[str, dict[str, Any]] = {
        name: {
            "configured": False,
            "onset_pending": False,
            "active": False,
            "active_accounted": False,
            "recovery_pending": False,
            "scenario_ids": [],
            "target_buses": [],
        }
        for name in family_names.values()
    }
    for scenario in scenarios:
        family = str(scenario.get("scenario_family", "")).lower()
        name = family_names.get(family)
        if name is None:
            continue
        phase = _scenario_phase(scenario, timestep, realtime_plan)
        status = result[name]
        status["configured"] = True
        if phase in {"onset_pending", "active_accounted"}:
            status["active"] = True
        if phase == "onset_pending":
            status["onset_pending"] = True
        elif phase == "active_accounted":
            status["active_accounted"] = True
        elif phase == "recovery_pending":
            status["recovery_pending"] = True
        if phase != "inactive":
            status["scenario_ids"].append(str(scenario.get("scenario_id") or ""))
            status["target_buses"].extend(sorted(_target_bus_ids(scenario, bus_ids)))
    for status in result.values():
        status["scenario_ids"] = sorted(set(status["scenario_ids"]))
        status["target_buses"] = sorted(set(status["target_buses"]))
    return result


def _add_minutes(value: Any, minutes: int) -> str:
    text = str(value)
    hour, minute = (int(part) for part in text.split(":", 1))
    total = (hour * 60 + minute + minutes) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def _time_to_timestep(value: Any) -> int:
    hour, minute = (int(part) for part in str(value).split(":", 1))
    return (hour * 60 + minute) // 30 + 1


def apply_disturbances(
    *,
    scenarios: list[dict[str, Any]],
    timestep: int,
    prices: pd.DataFrame,
    trips: pd.DataFrame,
    realtime_plan: pd.DataFrame,
) -> DisturbanceApplication:
    active = active_scenarios(scenarios, timestep)
    bus_ids = bus_ids_from_frame(realtime_plan)
    event_status = _event_status(scenarios, timestep, realtime_plan, bus_ids)

    disturbed_prices = prices[["timestep", "spot_market"]].copy()
    for scenario in active:
        if str(scenario.get("scenario_family", "")).lower() != "price_pct":
            continue
        multiplier = 1 + (
            float(scenario.get("disturbance_sign") or 0)
            * float(scenario.get("scenario_level") or 0)
            / 100
        )
        start = int(scenario.get("start_timestep") or 1)
        end = int(scenario.get("end_timestep") or 48)
        mask = disturbed_prices["timestep"].between(start, end)
        disturbed_prices.loc[mask, "spot_market"] *= multiplier

    current_row = planned_row_for_observation(realtime_plan, timestep)
    previous_row = planned_row_for_observation(realtime_plan, timestep - 1)
    undisturbed_delta: dict[str, float | None] = {}
    disturbed_delta: dict[str, float | None] = {}
    for key in bus_columns(realtime_plan):
        current = current_row.get(key) if current_row else None
        previous = previous_row.get(key) if previous_row else None
        delta = float(current) - float(previous) if current is not None and previous is not None else None
        undisturbed_delta[key] = delta
        disturbed_delta[key] = delta

    energy_multipliers = {bus_id: 1.0 for bus_id in bus_ids}
    for scenario in active:
        if str(scenario.get("scenario_family", "")).lower() != "energy_pct":
            continue
        base = 1 + (
            float(scenario.get("disturbance_sign") or 0)
            * float(scenario.get("scenario_level") or 0)
            / 100
        )
        targets = _target_bus_ids(scenario, bus_ids)
        for bus_id in targets:
            energy_multipliers[bus_id] *= base
            if _scenario_phase(scenario, timestep, realtime_plan) == "onset_pending":
                key = f"bus_{bus_id}_kwh"
                if disturbed_delta[key] is not None and disturbed_delta[key] < 0:
                    disturbed_delta[key] = round(float(disturbed_delta[key]) * base, 4)

    for scenario in scenarios:
        if str(scenario.get("scenario_family", "")).lower() != "energy_pct":
            continue
        if _scenario_phase(scenario, timestep, realtime_plan) != "recovery_pending":
            continue
        base = 1 + (
            float(scenario.get("disturbance_sign") or 0)
            * float(scenario.get("scenario_level") or 0)
            / 100
        )
        if not base:
            continue
        for bus_id in _target_bus_ids(scenario, bus_ids):
            key = f"bus_{bus_id}_kwh"
            if disturbed_delta[key] is not None and disturbed_delta[key] < 0:
                disturbed_delta[key] = round(float(disturbed_delta[key]) / base, 4)

    energy_consumption = trips.copy()
    for index, row in energy_consumption.iterrows():
        bus_id = int(row["bus_id"])
        multiplier = energy_multipliers.get(bus_id, 1.0)
        if multiplier != 1.0:
            energy_consumption.at[index, "energy_kwhkm"] = float(row["energy_kwhkm"]) * multiplier
    delayed_trips = trips.copy()
    delay_info = {bus_id: 0 for bus_id in bus_ids}
    delay_removal_active = False
    optimizer_disturbances: list[dict[str, Any]] = []
    for scenario in active:
        if str(scenario.get("scenario_family", "")).lower() != "delay_minutes":
            continue
        delay_minutes = int(
            float(scenario.get("disturbance_sign") or 0)
            * float(scenario.get("scenario_level") or 0)
        )
        targets = _target_bus_ids(scenario, bus_ids)
        for index, row in delayed_trips.iterrows():
            bus_id = int(row["bus_id"])
            if bus_id not in targets:
                continue
            trip_started = timestep >= _time_to_timestep(row["time_begin"])
            if not trip_started:
                delayed_trips.at[index, "time_begin"] = _add_minutes(row["time_begin"], delay_minutes)
            delayed_trips.at[index, "time_end"] = _add_minutes(row["time_end"], delay_minutes)
        if _scenario_phase(scenario, timestep, realtime_plan) == "onset_pending":
            for bus_id in targets:
                delay_info[bus_id] += delay_minutes
        optimizer_disturbances.append(
            {
                "bus_id": sorted(targets),
                "delay_minutes": delay_minutes,
                "disturbance_type": "late" if delay_minutes > 0 else "early_return",
            }
        )

    for scenario in scenarios:
        if str(scenario.get("scenario_family", "")).lower() != "delay_minutes":
            continue
        if _scenario_phase(scenario, timestep, realtime_plan) != "recovery_pending":
            continue
        delay_minutes = int(
            float(scenario.get("disturbance_sign") or 0)
            * float(scenario.get("scenario_level") or 0)
        )
        for bus_id in _target_bus_ids(scenario, bus_ids):
            delay_info[bus_id] -= delay_minutes
        delay_removal_active = True

    return DisturbanceApplication(
        scenarios=active,
        base_prices=prices[["timestep", "spot_market"]].copy(),
        prices=disturbed_prices.sort_values("timestep").reset_index(drop=True),
        trips=delayed_trips,
        energy_consumption=energy_consumption,
        energy_multipliers=energy_multipliers,
        disturbed_delta=disturbed_delta,
        undisturbed_delta=undisturbed_delta,
        delay_info=delay_info,
        delay_removal_active=delay_removal_active,
        optimizer_disturbances=optimizer_disturbances,
        event_status=event_status,
    )


def disturbance_records(application: DisturbanceApplication) -> list[dict[str, Any]]:
    return dataframe_records(pd.DataFrame(application.scenarios)) if application.scenarios else []

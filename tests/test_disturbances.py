from __future__ import annotations

import pandas as pd
import pytest

from agentic_workflow.disturbances import apply_disturbances


def _plan() -> pd.DataFrame:
    rows = []
    for timestep in range(48):
        row = {"timestep": timestep, "reoptimized": False, "trigger_type": None}
        row.update({f"bus_{bus}_kwh": 200 - 5 * timestep for bus in range(1, 9)})
        rows.append(row)
    return pd.DataFrame(rows)


def test_price_energy_and_delay_disturbances_compose():
    scenarios = [
        {
            "scenario_id": "price_plus_50",
            "scenario_family": "price_pct",
            "scenario_level": 50,
            "disturbance_sign": 1,
            "target_scope": "global",
            "start_timestep": 5,
            "end_timestep": 48,
        },
        {
            "scenario_id": "energy_plus_50_b1",
            "scenario_family": "energy_pct",
            "scenario_level": 50,
            "disturbance_sign": 1,
            "target_scope": "single_bus",
            "target_bus_id": 1,
            "start_timestep": 5,
            "end_timestep": 48,
        },
        {
            "scenario_id": "delay_plus_30_b2",
            "scenario_family": "delay_minutes",
            "scenario_level": 30,
            "disturbance_sign": 1,
            "target_scope": "single_bus",
            "target_bus_id": 2,
            "start_timestep": 5,
            "end_timestep": 48,
        },
    ]
    prices = pd.DataFrame({"timestep": range(1, 49), "spot_market": [0.1] * 48})
    trips = pd.DataFrame(
        {
            "trip_id": range(1, 9),
            "bus_id": range(1, 9),
            "time_begin": ["07:00"] * 8,
            "time_end": ["20:00"] * 8,
            "energy_kwhkm": [1.0] * 8,
            "average_velocity_kmh": [12.0] * 8,
        }
    )
    result = apply_disturbances(
        scenarios=scenarios,
        timestep=5,
        prices=prices,
        trips=trips,
        realtime_plan=_plan(),
    )
    assert result.prices.loc[result.prices["timestep"] == 5, "spot_market"].iloc[0] == pytest.approx(0.15)
    assert result.energy_multipliers[1] == 1.5
    assert result.disturbed_delta["bus_1_kwh"] == -7.5
    assert result.energy_consumption.loc[result.energy_consumption["bus_id"] == 1, "energy_kwhkm"].iloc[0] == 1.5
    assert result.delay_info[2] == 30
    assert result.trips.loc[result.trips["bus_id"] == 2, "time_begin"].iloc[0] == "07:30"


def test_persistent_energy_event_is_applied_until_end_and_exposes_only_event_edges():
    scenario = {
        "scenario_id": "energy_plus_50_window",
        "scenario_family": "energy_pct",
        "scenario_level": 50,
        "disturbance_sign": 1,
        "target_scope": "global",
        "start_timestep": 5,
        "end_timestep": 7,
    }
    prices = pd.DataFrame({"timestep": range(1, 49), "spot_market": [0.1] * 48})
    trips = pd.DataFrame(
        {
            "trip_id": range(1, 9),
            "bus_id": range(1, 9),
            "time_begin": ["07:00"] * 8,
            "time_end": ["20:00"] * 8,
            "energy_kwhkm": [1.0] * 8,
            "average_velocity_kmh": [12.0] * 8,
        }
    )
    plan = _plan()

    onset = apply_disturbances(
        scenarios=[scenario], timestep=5, prices=prices, trips=trips, realtime_plan=plan
    )
    assert onset.energy_multipliers[1] == pytest.approx(1.5)
    assert onset.disturbed_delta["bus_1_kwh"] == pytest.approx(-7.5)
    assert onset.event_status["energy"]["onset_pending"] is True

    plan.loc[plan["timestep"] == 4, ["reoptimized", "trigger_type"]] = [True, "energy_disturbance"]
    continuing = apply_disturbances(
        scenarios=[scenario], timestep=6, prices=prices, trips=trips, realtime_plan=plan
    )
    assert continuing.energy_multipliers[1] == pytest.approx(1.5)
    assert continuing.energy_consumption.loc[
        continuing.energy_consumption["bus_id"] == 1, "energy_kwhkm"
    ].iloc[0] == pytest.approx(1.5)
    assert continuing.disturbed_delta["bus_1_kwh"] == pytest.approx(-5.0)
    assert continuing.event_status["energy"]["active_accounted"] is True

    recovery = apply_disturbances(
        scenarios=[scenario], timestep=8, prices=prices, trips=trips, realtime_plan=plan
    )
    assert recovery.energy_multipliers[1] == pytest.approx(1.0)
    assert recovery.disturbed_delta["bus_1_kwh"] == pytest.approx(-5.0 / 1.5, abs=1e-4)
    assert recovery.event_status["energy"]["recovery_pending"] is True

    plan.loc[plan["timestep"] == 7, ["reoptimized", "trigger_type"]] = [True, "energy_recovery"]
    recovered = apply_disturbances(
        scenarios=[scenario], timestep=9, prices=prices, trips=trips, realtime_plan=plan
    )
    assert recovered.event_status["energy"]["recovery_pending"] is False


def test_persistent_delay_does_not_look_recovered_immediately_after_reoptimization():
    scenario = {
        "scenario_id": "delay_plus_30_window",
        "scenario_family": "delay_minutes",
        "scenario_level": 30,
        "disturbance_sign": 1,
        "target_scope": "global",
        "start_timestep": 5,
        "end_timestep": 7,
    }
    prices = pd.DataFrame({"timestep": range(1, 49), "spot_market": [0.1] * 48})
    trips = pd.DataFrame(
        {
            "trip_id": range(1, 9),
            "bus_id": range(1, 9),
            "time_begin": ["07:00"] * 8,
            "time_end": ["20:00"] * 8,
            "energy_kwhkm": [1.0] * 8,
            "average_velocity_kmh": [12.0] * 8,
        }
    )
    plan = _plan()
    plan.loc[plan["timestep"] == 4, ["reoptimized", "trigger_type"]] = [True, "delay"]

    continuing = apply_disturbances(
        scenarios=[scenario], timestep=6, prices=prices, trips=trips, realtime_plan=plan
    )
    assert continuing.delay_info[1] == 0
    assert continuing.delay_removal_active is False
    assert continuing.trips.loc[continuing.trips["bus_id"] == 1, "time_end"].iloc[0] == "20:30"
    assert continuing.event_status["delay"]["active_accounted"] is True

    recovery = apply_disturbances(
        scenarios=[scenario], timestep=8, prices=prices, trips=trips, realtime_plan=plan
    )
    assert recovery.delay_info[1] == -30
    assert recovery.delay_removal_active is True
    assert recovery.event_status["delay"]["recovery_pending"] is True

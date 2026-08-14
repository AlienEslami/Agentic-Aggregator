from __future__ import annotations

import app_rt
import pytest


def _lexicographic_test_context(target_kwh: float) -> dict:
    return {
        "buses": [
            {
                "bus_id": 1,
                "bus_kwh": 100.0,
                "initial_soc_rt": 0.5,
                "availability_status": "available",
            }
        ],
        "chargers": [
            {
                "charger_id": 1,
                "alpha_by_step": [10.0, 10.0],
            }
        ],
        "trips": [
            {
                "trip_id": 1,
                "planned_bus_id": 1,
                "energy_per_step": 0.0,
                "start_rt": 2,
                "end_rt": 3,
                "remaining_energy_need": 0.0,
                "interruption_allowed": True,
                "active_now": False,
            }
        ],
        "prices": {
            "buy": [1.0, 1.0],
            "sell": [0.5, 0.5],
            "spot": [0.8, 0.8],
        },
        "current_timestep": 1,
        "timestep_hours": 0.5,
        "v2g_enabled": False,
        "operational_requirements": [
            {
                "priority_id": "TEST-PRIORITY",
                "objective": "frontload_site_charging",
                "affected_buses": [1],
                "timestep_start": 1,
                "timestep_end": 1,
                "target_value": target_kwh,
                "target_unit": "kwh",
            }
        ],
    }


def test_soft_priority_uses_lexicographic_stages_without_penalty(monkeypatch):
    monkeypatch.setenv("RT_SOLVER_ORDER", "appsi_highs,highs")
    model, metadata = app_rt.solve_rt_rescheduling(
        _lexicographic_test_context(target_kwh=10.0)
    )

    assert model is not None
    assert metadata["optimization_strategy"] == "lexicographic_soft_operational_priority"
    assert metadata["lexicographic_priority_applied"] is True
    assert metadata["lexicographic_optimality_proven"] is True
    assert [stage["stage"] for stage in metadata["lexicographic_stages"]] == [
        "service_feasibility",
        "operator_priority_violation",
        "economic_dispatch",
    ]
    assert metadata["minimum_operational_priority_slack"] == pytest.approx(0.0)
    assert list(model.operational_priority_slack.values())[0].value == pytest.approx(
        0.0
    )
    assert model.w_buy[1].value == pytest.approx(10.0)
    assert "operational_priority_slack" not in str(model.obj.expr)


def test_physically_impossible_soft_priority_returns_minimum_shortfall(monkeypatch):
    monkeypatch.setenv("RT_SOLVER_ORDER", "appsi_highs,highs")
    model, metadata = app_rt.solve_rt_rescheduling(
        _lexicographic_test_context(target_kwh=20.0)
    )

    assert model is not None
    assert metadata["lexicographic_optimality_proven"] is True
    assert metadata["minimum_operational_priority_slack"] == pytest.approx(10.0)
    assert list(model.operational_priority_slack.values())[0].value == pytest.approx(
        10.0
    )
    assert model.w_buy[1].value == pytest.approx(10.0)


def test_no_operator_requirement_keeps_single_stage_baseline(monkeypatch):
    monkeypatch.setenv("RT_SOLVER_ORDER", "appsi_highs,highs")
    context = _lexicographic_test_context(target_kwh=10.0)
    context["operational_requirements"] = []

    model, metadata = app_rt.solve_rt_rescheduling(context)

    assert model is not None
    assert metadata["optimization_strategy"] == "baseline_weighted_service_and_economic"
    assert metadata["lexicographic_priority_applied"] is False
    assert metadata["lexicographic_optimality_proven"] is None
    assert [stage["stage"] for stage in metadata["lexicographic_stages"]] == [
        "baseline_weighted_objective"
    ]
    assert metadata["solver_telemetry"]["solve_stage_count"] == 1
    assert model.w_buy[1].value == pytest.approx(0.0)


def test_trip_ending_at_current_timestep_is_not_added_to_remaining_horizon():
    input_data = {
        "buses": [{"bus_id": 1, "bus_kwh": 365, "initial_soc": 50}],
        "chargers": [{"charger_id": 1, "charger_kw": 200}],
        "trip_time": [
            {
                "trip_id": 1,
                "bus_id": 1,
                "time_begin": "07:00",
                "time_end": "21:00",
                "energy_kwhkm": 1,
                "average_velocity_kmh": 12,
            }
        ],
        "energy_consumption": [
            {
                "trip_id": 1,
                "bus_id": 1,
                "time_begin": "07:00",
                "time_end": "21:00",
                "energy_kwhkm": 1,
                "average_velocity_kmh": 12,
            }
        ],
        "prices": [{"timestep": t, "spot_market": 0.1} for t in range(1, 49)],
        "realtime_state": [{"bus_id": 1, "current_energy_kwh": 200}],
    }
    data = app_rt.build_dataframes(input_data)
    context = app_rt.build_rt_context(data, {}, current_timestep=43, disturbances=[])
    assert context["trips"] == []


def test_no_remaining_trips_is_a_controlled_noop():
    context = {
        "buses": [{"bus_id": 1}],
        "chargers": [{"charger_id": 1}],
        "trips": [],
        "prices": {"spot": [0.1]},
        "timestep_hours": 0.5,
        "v2g_enabled": True,
    }
    model, metadata = app_rt.solve_rt_rescheduling(context)
    assert model is None
    assert metadata == {"solver_status": "skipped/no_remaining_trips"}


def test_remaining_horizon_prices_keep_absolute_timestep_alignment():
    input_data = {
        "buses": [{"bus_id": 1, "bus_kwh": 365, "initial_soc": 50}],
        "chargers": [{"charger_id": 1, "charger_kw": 200}],
        "trip_time": [{
            "trip_id": 1,
            "bus_id": 1,
            "time_begin": "07:00",
            "time_end": "21:00",
            "energy_kwhkm": 1,
            "average_velocity_kmh": 12,
        }],
        "prices": [
            {"timestep": t, "spot_market": t / 1000}
            for t in range(27, 49)
        ],
        "realtime_state": [{"bus_id": 1, "current_energy_kwh": 200}],
    }
    guidance = {
        "buy_multipliers": [1 + t / 1000 for t in range(27, 49)],
        "sell_multipliers": [0.5 + t / 1000 for t in range(27, 49)],
    }
    context = app_rt.build_rt_context(
        app_rt.build_dataframes(input_data),
        guidance,
        current_timestep=27,
        disturbances=[],
    )

    assert context["full_horizon_steps"] == 48
    assert len(context["prices"]["spot"]) == 22
    assert context["prices"]["spot"][0] == pytest.approx(0.027)
    assert context["prices"]["spot"][-1] == pytest.approx(0.048)
    assert context["prices"]["buy_multipliers"] == pytest.approx(
        guidance["buy_multipliers"]
    )
    assert context["trips"]


def test_preapplied_delay_metadata_does_not_shift_trips_twice():
    input_data = {
        "buses": [{"bus_id": 1, "bus_kwh": 365, "initial_soc": 50}],
        "chargers": [{"charger_id": 1, "charger_kw": 200}],
        "trip_time": [{
            "trip_id": 1,
            "bus_id": 1,
            "time_begin": "07:30",
            "time_end": "21:30",
            "energy_kwhkm": 1,
            "average_velocity_kmh": 12,
        }],
        "prices": [{"timestep": t, "spot_market": 0.1} for t in range(1, 49)],
        "realtime_state": [{"bus_id": 1, "current_energy_kwh": 200}],
        "timestep_minutes": 30,
    }
    context = app_rt.build_rt_context(
        app_rt.build_dataframes(input_data),
        {},
        current_timestep=1,
        disturbances=[{
            "bus_id": [1],
            "delay_minutes": 30,
            "disturbance_type": "late",
            "already_applied": True,
        }],
    )
    assert context["trips"][0]["start_abs"] == 16


def test_direct_api_delay_supports_multiple_target_buses():
    input_data = {
        "buses": [
            {"bus_id": bus, "bus_kwh": 365, "initial_soc": 50}
            for bus in (1, 2)
        ],
        "chargers": [{"charger_id": 1, "charger_kw": 200}],
        "trip_time": [
            {
                "trip_id": bus,
                "bus_id": bus,
                "time_begin": "07:00",
                "time_end": "21:00",
                "energy_kwhkm": 1,
                "average_velocity_kmh": 12,
            }
            for bus in (1, 2)
        ],
        "prices": [{"timestep": t, "spot_market": 0.1} for t in range(1, 49)],
        "realtime_state": [
            {"bus_id": bus, "current_energy_kwh": 200} for bus in (1, 2)
        ],
        "timestep_minutes": 30,
    }
    context = app_rt.build_rt_context(
        app_rt.build_dataframes(input_data),
        {},
        current_timestep=1,
        disturbances=[{
            "bus_id": [1, 2],
            "delay_minutes": 30,
            "disturbance_type": "late",
        }],
    )
    assert [trip["start_abs"] for trip in context["trips"]] == [16, 16]

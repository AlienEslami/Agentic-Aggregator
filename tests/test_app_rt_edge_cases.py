from __future__ import annotations

import app_rt
import pytest


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

from __future__ import annotations

import pandas as pd

from agentic_workflow.context import build_context, build_observation
from agentic_workflow.disturbances import DisturbanceApplication
from agentic_workflow.io import DayAheadReference


def test_plan_energy_source_preserves_workbook_status_and_delay():
    plan = pd.DataFrame([
        {"timestep": 0, **{f"bus_{bus}_kwh": 100.0 for bus in range(1, 9)}}
    ])
    workbook_state = pd.DataFrame([
        {
            "bus_id": bus,
            "current_energy_kwh": 200.0,
            "operation_status": "in_trip" if bus == 1 else "idle",
            "delay_minutes": 5 if bus == 1 else 0,
        }
        for bus in range(1, 9)
    ])
    disturbance = DisturbanceApplication(
        scenarios=[],
        base_prices=pd.DataFrame(),
        prices=pd.DataFrame(),
        trips=pd.DataFrame(),
        energy_consumption=pd.DataFrame(),
        energy_multipliers={},
        disturbed_delta={},
        undisturbed_delta={},
        delay_info={1: 30},
        delay_removal_active=False,
        optimizer_disturbances=[],
    )
    observation = build_observation(
        timestep=1,
        realtime_plan=plan,
        disturbance=disturbance,
        workbook_state=workbook_state,
        state_source="plan",
    )
    assert observation[0]["current_energy_kwh"] == 100.0
    assert observation[0]["operation_status"] == "in_trip"
    assert observation[0]["delay_minutes"] == 35


def test_interval_deviation_uses_active_plan_not_misaligned_forecast_workbook():
    plan = pd.DataFrame(
        [
            {"timestep": 0, "w_buy": 0.0, "w_sell": 0.0, "bus_1_kwh": 100.0},
            {"timestep": 1, "w_buy": 0.0, "w_sell": 0.0, "bus_1_kwh": 90.0},
        ]
    )
    disturbance = DisturbanceApplication(
        scenarios=[],
        base_prices=pd.DataFrame([{"timestep": 2, "spot_market": 0.1}]),
        prices=pd.DataFrame([{"timestep": 2, "spot_market": 0.1}]),
        trips=pd.DataFrame(),
        energy_consumption=pd.DataFrame(),
        energy_multipliers={},
        disturbed_delta={"bus_1_kwh": -10.0},
        undisturbed_delta={"bus_1_kwh": -10.0},
        delay_info={},
        delay_removal_active=False,
        optimizer_disturbances=[],
    )
    reference = DayAheadReference(
        mode="selfish",
        run_timestamp=None,
        plan=plan,
        summary={
            "aggregator_revenue": 0.0,
            "buy_multipliers": [1.05],
            "sell_multipliers": [0.8],
        },
    )

    context = build_context(
        mode="selfish",
        timestep=2,
        observation=[
            {
                "bus_id": 1,
                "current_energy_kwh": 90.0,
                "operation_status": "in_trip",
                "delay_minutes": 0,
            }
        ],
        realtime_plan=plan,
        day_ahead=reference,
        forecast_prices=pd.DataFrame([{"timestep": 2, "spot_market": 0.1}]),
        # Deliberately incompatible values: these must not define the executed
        # interval reference.
        forecast_energy=pd.DataFrame(
            [
                {"timestep": 0, "bus_1_kwh": 1000.0},
                {"timestep": 1, "bus_1_kwh": 500.0},
            ]
        ),
        disturbance=disturbance,
        price_history={},
        context_history=[],
    )

    deviation = context["deviations"][0]
    assert deviation["forecast_interval_delta_kwh"] == -10.0
    assert deviation["energy_delta_deviation_pct"] == 0.0
    assert deviation["interval_delta_reference"] == "active_realtime_plan"
    assert context["deviation_summary"]["has_energy_disturbance"] is False

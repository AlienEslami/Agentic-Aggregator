from __future__ import annotations

import pandas as pd

from agentic_workflow.context import build_observation
from agentic_workflow.disturbances import DisturbanceApplication


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

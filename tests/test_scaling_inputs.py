import pandas as pd

from agentic_workflow.io import bus_columns, bus_ids_from_frame
from agentic_workflow.state import WorkflowState


def test_bus_discovery_and_plan_application_support_16_buses():
    plan = pd.DataFrame(
        [
            {
                "timestep": step,
                "w_buy": 0.0,
                "w_sell": 0.0,
                **{f"bus_{bus}_kwh": 100.0 for bus in range(1, 17)},
                "reoptimized": None,
                "trigger_type": None,
                "buy_multipliers": None,
                "sell_multipliers": None,
                "intraday_prices": None,
            }
            for step in range(2)
        ]
    )
    assert bus_ids_from_frame(plan) == list(range(1, 17))
    assert len(bus_columns(plan)) == 16
    state = WorkflowState(
        realtime_plan=plan,
        forecast_prices=pd.DataFrame(),
        forecast_energy=plan[["timestep", *bus_columns(plan)]].copy(),
    )
    from agentic_workflow.models import PricingDecision, TriggerDecision

    state.apply_optimized_plan(
        timestep=1,
        trigger=TriggerDecision(
            action="optimize", reasoning="scale", confidence=1.0,
            trigger_type="deviation", flagged_buses=[]
        ),
        pricing=PricingDecision(
            buy_multipliers=[1.05, 1.05], sell_multipliers=[0.8, 0.8],
            reasoning="scale", confidence=1.0
        ),
        result={
            "w_buy": [1.0, 2.0], "w_sell": [0.0, 0.0],
            "energy": [[100.0 + bus, 101.0 + bus] for bus in range(16)],
        },
        intraday_prices=[],
    )
    assert state.realtime_plan.loc[0, "bus_16_kwh"] == 115.0
    assert state.forecast_energy.loc[0, "bus_16_kwh"] == 115.0

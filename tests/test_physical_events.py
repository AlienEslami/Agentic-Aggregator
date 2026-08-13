from __future__ import annotations

import pandas as pd
import pytest

from agentic_workflow.disturbances import DisturbanceApplication
from agentic_workflow.models import NoticeInterpretation, NoticeParameterUpdates
from agentic_workflow.notices import NoticeRecord
from agentic_workflow.physical_events import (
    advance_realized_energy,
    advance_physical_notice_truth,
    apply_realized_energy_to_observation,
    realize_notice_consequences,
    settle_realized_step,
)


def _notice(phase: str, updates: NoticeParameterUpdates) -> NoticeRecord:
    truth = NoticeInterpretation(
        event_id="OPS-TEST",
        source_type="driver_chat",
        event_type="combined",
        phase=phase,
        affected_buses=[2],
        affected_chargers=[1],
        effective_timestep=10,
        updates=updates,
    )
    return NoticeRecord(
        notice_id=f"OPS-TEST-{phase}",
        scenario_id="physical_test",
        event_id="OPS-TEST",
        source_type="driver_chat",
        wording_variant="uncertain_chat",
        report_timestep=10,
        text="Public text contains no canonical object.",
        canonical=truth,
    )


def test_physical_truth_lifecycle_is_separate_and_recovery_clears_it() -> None:
    active = {}
    warning = _notice("warning", NoticeParameterUpdates())
    onset = _notice(
        "onset",
        NoticeParameterUpdates(
            delay_minutes_by_bus={2: 20},
            energy_multiplier_by_bus={2: 1.2},
            unavailable_chargers=[1],
        ),
    )
    recovery = _notice("recovery", NoticeParameterUpdates())

    assert advance_physical_notice_truth([warning], active) is None
    truth = advance_physical_notice_truth([onset], active)
    assert truth is not None
    assert truth.updates.energy_multiplier_by_bus == {2: 1.2}
    assert advance_physical_notice_truth([recovery], active) is None
    assert active == {}


def test_physical_truth_changes_only_observable_consequences() -> None:
    base = DisturbanceApplication(
        scenarios=[],
        base_prices=pd.DataFrame({"timestep": [10], "spot_market": [0.1]}),
        prices=pd.DataFrame({"timestep": [10], "spot_market": [0.1]}),
        trips=pd.DataFrame({"bus_id": [2], "energy_kwhkm": [1.0]}),
        energy_consumption=pd.DataFrame({"bus_id": [2], "energy_kwhkm": [1.0]}),
        energy_multipliers={2: 1.0},
        disturbed_delta={"bus_2_kwh": -10.0},
        undisturbed_delta={"bus_2_kwh": -10.0},
        delay_info={2: 5},
        delay_removal_active=False,
        optimizer_disturbances=[],
    )
    truth = _notice(
        "onset",
        NoticeParameterUpdates(
            delay_minutes_by_bus={2: 20},
            energy_multiplier_by_bus={2: 1.2},
        ),
    ).canonical

    realized = realize_notice_consequences(base, truth)

    assert realized.disturbed_delta["bus_2_kwh"] == -12.0
    assert realized.delay_info[2] == 25
    pd.testing.assert_frame_equal(realized.trips, base.trips)
    pd.testing.assert_frame_equal(realized.energy_consumption, base.energy_consumption)
    assert realized.optimizer_disturbances == []

    # Once the active plan already contains the same multiplier, the physical
    # layer must not multiply the planned consumption a second time.
    assumed = truth.model_copy(update={"phase": "persistence"})
    accounted = realize_notice_consequences(base, truth, assumed)
    assert accounted.disturbed_delta["bus_2_kwh"] == -10.0
    recovered = realize_notice_consequences(base, None, assumed)
    assert recovered.disturbed_delta["bus_2_kwh"] == pytest.approx(
        -10 / 1.2, abs=1e-4
    )


def test_ex_post_settlement_caps_actions_using_hidden_physical_capacity() -> None:
    plan = pd.DataFrame(
        {
            "timestep": [0],
            "w_buy": [300.0],
            "w_sell": [0.0],
            "bus_1_kwh": [100.0],
            "bus_2_kwh": [100.0],
        }
    )
    chargers = pd.DataFrame(
        {"charger_id": [1, 2], "charger_kw": [200.0, 200.0]}
    )
    truth = _notice(
        "onset", NoticeParameterUpdates(unavailable_chargers=[1])
    ).canonical
    row = settle_realized_step(
        timestep=1,
        realtime_plan=plan,
        observation=[
            {"bus_id": 1, "current_energy_kwh": 100.0},
            {"bus_id": 2, "current_energy_kwh": 100.0},
        ],
        spot_price=0.1,
        buy_multiplier=1.2,
        sell_multiplier=0.8,
        chargers=chargers,
        physical_truth=truth,
        planner_assumption=None,
    )

    assert row["physical_charger_capacity_kwh"] == 100.0
    assert row["realized_buy_kwh"] == 100.0
    assert row["curtailed_buy_kwh"] == 200.0
    assert row["realized_pto_cost"] == pytest.approx(12.0)
    assert row["realized_aggregator_revenue"] == pytest.approx(2.0)
    assert row["charger_availability_mismatch_count"] == 1
    assert row["event_model_mismatch"] is True


def test_realized_energy_accumulates_unmodelled_consumption() -> None:
    plan = pd.DataFrame(
        {
            "timestep": [0, 1, 2],
            "w_buy": [0.0, 0.0, 0.0],
            "w_sell": [0.0, 0.0, 0.0],
            "bus_1_kwh": [100.0, 90.0, 80.0],
        }
    )
    chargers = pd.DataFrame({"charger_id": [1], "charger_kw": [200.0]})
    base = DisturbanceApplication(
        scenarios=[],
        base_prices=pd.DataFrame(),
        prices=pd.DataFrame(),
        trips=pd.DataFrame(),
        energy_consumption=pd.DataFrame(),
        energy_multipliers={1: 1.2},
        disturbed_delta={"bus_1_kwh": -12.0},
        undisturbed_delta={"bus_1_kwh": -10.0},
        delay_info={},
        delay_removal_active=False,
        optimizer_disturbances=[],
    )
    actual = {}
    advance_realized_energy(
        timestep=2,
        realtime_plan=plan,
        disturbance=base,
        chargers=chargers,
        physical_truth=None,
        realized_energy_by_bus=actual,
    )
    assert actual == {1: 88.0}
    advance_realized_energy(
        timestep=3,
        realtime_plan=plan,
        disturbance=base,
        chargers=chargers,
        physical_truth=None,
        realized_energy_by_bus=actual,
    )
    assert actual == {1: 76.0}
    observation = [{"bus_id": 1, "current_energy_kwh": 80.0}]
    apply_realized_energy_to_observation(observation, actual)
    assert observation[0]["current_energy_kwh"] == 76.0


def test_realized_energy_reflects_curtailed_charging() -> None:
    plan = pd.DataFrame(
        {
            "timestep": [0, 1],
            "w_buy": [0.0, 200.0],
            "w_sell": [0.0, 0.0],
            "bus_1_kwh": [100.0, 120.0],
            "bus_2_kwh": [100.0, 120.0],
        }
    )
    chargers = pd.DataFrame(
        {"charger_id": [1, 2], "charger_kw": [200.0, 200.0]}
    )
    truth = _notice(
        "onset", NoticeParameterUpdates(unavailable_chargers=[1])
    ).canonical
    base = DisturbanceApplication(
        scenarios=[],
        base_prices=pd.DataFrame(),
        prices=pd.DataFrame(),
        trips=pd.DataFrame(),
        energy_consumption=pd.DataFrame(),
        energy_multipliers={},
        disturbed_delta={"bus_1_kwh": 20.0, "bus_2_kwh": 20.0},
        undisturbed_delta={"bus_1_kwh": 20.0, "bus_2_kwh": 20.0},
        delay_info={},
        delay_removal_active=False,
        optimizer_disturbances=[],
    )
    actual = {}
    advance_realized_energy(
        timestep=2,
        realtime_plan=plan,
        disturbance=base,
        chargers=chargers,
        physical_truth=truth,
        realized_energy_by_bus=actual,
    )
    assert actual == {1: 110.0, 2: 110.0}

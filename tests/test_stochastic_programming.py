from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agentic_workflow.stochastic_programming import (
    StochasticScenario,
    apply_future_updates,
    build_extensive_form,
    scenarios_from_definitions,
    validate_scenarios,
)
from agentic_workflow.stochastic_benchmark import (
    EventRecedingStochasticAgentBackend,
    load_stochastic_case,
)


def _context() -> dict:
    return {
        "buses": [
            {
                "bus_id": 1,
                "physical_bus_id": 1,
                "bus_kwh": 100.0,
                "initial_soc": 0.8,
                "initial_soc_rt": 0.8,
                "availability_status": "available",
                "operation_status": "idle",
                "current_trip_id": 0,
                "reassignable": True,
            }
        ],
        "chargers": [
            {
                "charger_id": 1,
                "charger_kw": 20.0,
                "alpha": 10.0,
                "alpha_by_step": [10.0, 10.0, 10.0],
            }
        ],
        "trips": [
            {
                "trip_id": 1,
                "planned_bus_id": 1,
                "route_id": 1,
                "start_abs": 2,
                "end_abs": 3,
                "start_rt": 2,
                "end_rt": 3,
                "active_now": False,
                "remaining_active_steps": 1,
                "energy_per_step": 5.0,
                "remaining_energy_need": 5.0,
                "trip_progress_status": "pending",
                "interruption_allowed": True,
                "status": "scheduled",
            }
        ],
        "prices": {
            "spot": [1.0, 2.0, 3.0],
            "buy": [1.1, 2.2, 3.3],
            "sell": [0.8, 1.6, 2.4],
            "buy_multipliers": [1.1, 1.1, 1.1],
            "sell_multipliers": [0.8, 0.8, 0.8],
            "boundaries": [1, 2, 3],
        },
        "current_timestep": 1,
        "full_horizon_steps": 3,
        "timestep_minutes": 30,
        "timestep_hours": 0.5,
        "v2g_enabled": True,
        "price_guidance": {},
        "operational_requirements": [],
    }


def _scenarios() -> tuple[StochasticScenario, StochasticScenario]:
    base = _context()
    high = apply_future_updates(base, reveal_timestep=2, price_multiplier=1.5)
    return (
        StochasticScenario("reference", 0.5, base),
        StochasticScenario("high_price", 0.5, high),
    )


def test_future_updates_do_not_change_pre_reveal_data():
    base = _context()
    changed = apply_future_updates(
        base,
        reveal_timestep=2,
        price_multiplier=1.5,
        charger_power_multipliers={1: 0.0},
    )
    assert changed["prices"]["spot"] == [1.0, 3.0, 4.5]
    assert changed["chargers"][0]["alpha_by_step"] == [10.0, 0.0, 0.0]
    assert base["prices"]["spot"] == [1.0, 2.0, 3.0]
    assert base["chargers"][0]["alpha_by_step"] == [10.0, 10.0, 10.0]


def test_scenario_probabilities_must_sum_to_one():
    first, second = _scenarios()
    with pytest.raises(ValueError, match="sum to 1"):
        validate_scenarios(
            (
                StochasticScenario(first.scenario_id, 0.4, first.context),
                StochasticScenario(second.scenario_id, 0.4, second.context),
            ),
            reveal_timestep=2,
        )


def test_pre_reveal_truth_must_be_common():
    first, second = _scenarios()
    invalid = copy.deepcopy(second.context)
    invalid["prices"]["spot"][0] = 99.0
    with pytest.raises(ValueError, match="pre-reveal timestep 1"):
        validate_scenarios(
            (first, StochasticScenario("invalid", 0.5, invalid)),
            reveal_timestep=2,
        )


def test_extensive_form_reuses_rt_model_and_adds_nonanticipativity():
    model, items = build_extensive_form(_scenarios(), reveal_timestep=2)
    assert [item.scenario_id for item in items] == ["reference", "high_price"]
    assert len(model.nonanticipativity) > 0
    assert model.scenario["reference"].obj.active is False
    assert model.scenario["high_price"].obj.active is False
    assert model.obj.active is True
    assert int(model._stochastic_reveal_timestep) == 2


def test_trip_changes_cannot_rewrite_pre_reveal_service():
    base = _context()
    base["trips"][0]["start_rt"] = 1
    with pytest.raises(ValueError, match="starts before revelation"):
        apply_future_updates(
            base,
            reveal_timestep=2,
            trip_energy_multipliers={1: 1.2},
        )


def test_active_trip_return_can_be_delayed_after_revelation():
    base = _context()
    base["trips"][0]["start_rt"] = 1
    base["trips"][0]["end_rt"] = 3
    changed = apply_future_updates(
        base,
        reveal_timestep=2,
        trip_return_delay_minutes={1: 30},
    )
    assert changed["trips"][0]["start_rt"] == 1
    assert changed["trips"][0]["end_rt"] == 4


def test_trip_delay_must_align_to_model_clock():
    with pytest.raises(ValueError, match="30-minute model grid"):
        apply_future_updates(
            _context(),
            reveal_timestep=2,
            trip_return_delay_minutes={1: 75},
        )


def test_json_definitions_support_bounded_charger_outage():
    scenarios = scenarios_from_definitions(
        _context(),
        (
            {"scenario_id": "nominal", "probability": 0.5},
            {
                "scenario_id": "outage",
                "probability": 0.5,
                "future_updates": {
                    "charger_power_windows": [
                        {
                            "charger_ids": [1],
                            "timestep_start": 2,
                            "timestep_end": 2,
                            "multiplier": 0.0,
                        }
                    ]
                },
            },
        ),
        reveal_timestep=2,
    )
    assert scenarios[0].context["chargers"][0]["alpha_by_step"] == [10.0, 10.0, 10.0]
    assert scenarios[1].context["chargers"][0]["alpha_by_step"] == [10.0, 0.0, 10.0]


def test_frozen_stochastic_protocol_has_defensible_case_sets():
    root = Path(__file__).resolve().parents[1]
    protocol = json.loads(
        (root / "inputs/revision/stochastic_benchmark_protocol_v1.json").read_text(
            encoding="utf-8"
        )
    )
    cases = {case["case_id"]: case for case in protocol["cases"]}
    assert set(cases) == {
        "aw_route6_late_return",
        "aw_charger_bank_shutdown",
        "aw_combined_evening",
    }
    for case in cases.values():
        assert sum(item["probability"] for item in case["scenarios"]) == pytest.approx(1.0)
    assert len(cases["aw_charger_bank_shutdown"]["scenarios"]) == 1
    assert "deterministic-collapse" in cases["aw_charger_bank_shutdown"]["interpretation"]
    assert protocol["information_contract"]["canonical_hidden_truth"] is False


def test_full_day_stochastic_protocol_has_causal_information_stages():
    root = Path(__file__).resolve().parents[1]
    protocol_path = root / "inputs/revision/stochastic_benchmark_protocol_v3.json"
    from agentic_workflow.stochastic_benchmark import load_stochastic_protocol

    protocol = load_stochastic_protocol(protocol_path)
    cases = {case["case_id"]: case for case in protocol["cases"]}

    assert protocol["information_contract"]["external_llm"] is False
    assert protocol["method"]["solver_time_limit_seconds"] == 300
    assert protocol["method"]["operational_feasibility_first"] is True
    assert len(cases["aw_combined_evening"]["decision_stages"]) == 2
    for case in cases.values():
        for stage in case["decision_stages"]:
            assert stage["first_executable_timestep"] == (
                stage["decision_observation_timestep"] + 1
            )
            assert stage["first_recourse_timestep"] > stage[
                "first_executable_timestep"
            ]
            assert sum(
                scenario["probability"] for scenario in stage["scenarios"]
            ) == pytest.approx(1.0)

    route = load_stochastic_case(protocol_path, "aw_route6_late_return")
    backend = EventRecedingStochasticAgentBackend(route)
    decision = backend.trigger({"timestep": 6})
    assert decision.action == "optimize"
    assert "confirmed_delay_range" in decision.reasoning
    assert backend.trigger({"timestep": 7}).action == "skip"

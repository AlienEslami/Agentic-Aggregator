from __future__ import annotations

import json

import pandas as pd
import pytest

from agentic_workflow.agents import (
    CompositeAgentBackend,
    OpenAIAgentBackend,
    apply_trigger_confidence_threshold,
    build_pricing_reference,
    create_experiment_backend,
)
from agentic_workflow.config import WorkflowConfig
from agentic_workflow.models import TriggerDecision
from scripts.build_scaling_inputs import depot_b_prices, depot_b_trips, replicate_rows
from scripts.run_revision_sensitivity import build_specs as build_sensitivity_specs
from scripts.run_scaling_study import build_specs as build_scaling_specs
from scripts.run_evaluator_ablation import validate_protocol as validate_evaluator_protocol
from scripts.validate_revision_package import REQUIRED_FILES


def test_trigger_confidence_threshold_holds_only_low_confidence_optimization():
    decision = TriggerDecision(
        action="optimize",
        reasoning="model action",
        confidence=0.69,
        trigger_type="service_notice",
        flagged_buses=[6],
    )
    held = apply_trigger_confidence_threshold(decision, 0.70)
    assert held.action == "skip"
    assert held.trigger_type == "none"
    assert held.flagged_buses == []
    assert apply_trigger_confidence_threshold(decision, 0.50) == decision


def test_pricing_guidance_spread_changes_without_changing_average_or_hard_bounds():
    context = {
        "mode": "altruistic",
        "remaining_timesteps": 3,
        "intraday_prices": {
            "prices": [
                {"price_zone": "cheap"},
                {"price_zone": "transition"},
                {"price_zone": "expensive"},
            ]
        },
    }
    narrow = build_pricing_reference(context, "narrow")
    base = build_pricing_reference(context, "base")
    wide = build_pricing_reference(context, "wide")
    for reference in (narrow, base, wide):
        assert reference["hard_economic_bounds_changed"] is False
        assert reference["status"] == "optional_context_not_constraint"
    for side in ("buy", "sell"):
        key = f"{side}_multipliers"
        means = [
            sum(reference["current_horizon"][key]) / 3
            for reference in (narrow, base, wide)
        ]
        assert means[0] == pytest.approx(means[1])
        assert means[1] == pytest.approx(means[2])
        ranges = [
            max(reference["current_horizon"][key])
            - min(reference["current_horizon"][key])
            for reference in (narrow, base, wide)
        ]
        assert ranges[0] < ranges[1] < ranges[2]


def test_pricing_guidance_preserves_average_for_unbalanced_horizon():
    context = {
        "mode": "altruistic",
        "remaining_timesteps": 5,
        "intraday_prices": {
            "prices": [
                {"price_zone": "cheap"},
                {"price_zone": "cheap"},
                {"price_zone": "cheap"},
                {"price_zone": "transition"},
                {"price_zone": "expensive"},
            ]
        },
    }
    references = [
        build_pricing_reference(context, variant)
        for variant in ("narrow", "base", "wide")
    ]
    for side in ("buy", "sell"):
        means = [
            reference["current_horizon"][f"{side}_summary"]["arithmetic_mean"]
            for reference in references
        ]
        assert means[0] == pytest.approx(means[1])
        assert means[1] == pytest.approx(means[2])


def test_new_deterministic_pricing_name_and_pricing_only_arm(monkeypatch):
    class StubOpenAIBackend:
        def __init__(self, model, *, allow_deterministic_trigger_fallback=True):
            self.model = model
            self.allow_deterministic_trigger_fallback = allow_deterministic_trigger_fallback
            self.call_records = []

    monkeypatch.setattr("agentic_workflow.agents.OpenAIAgentBackend", StubOpenAIBackend)
    deterministic = create_experiment_backend(
        "deterministic_pricing_substitution", "rule", "test-model"
    )
    pricing_only = create_experiment_backend("pricing_agent_only", "rule", "test-model")
    assert isinstance(deterministic, CompositeAgentBackend)
    assert isinstance(pricing_only, CompositeAgentBackend)
    assert isinstance(pricing_only.pricing_backend, StubOpenAIBackend)


def test_sensitivity_and_scaling_factorial_sizes_are_prespecified():
    sensitivity = build_sensitivity_specs(5)
    assert sum(spec.family == "trigger" for spec in sensitivity) == 25
    assert sum(spec.family == "pricing" for spec in sensitivity) == 30
    scaling = build_scaling_specs(3)
    assert len(scaling) == 4 * 2 * 2 * 3
    assert {spec.configuration for spec in scaling} == {
        "rule_text_event_trigger",
        "full_agentic",
    }


def test_depot_b_transform_is_distinct_and_scaling_ids_are_contiguous():
    buses = pd.DataFrame({"bus_id": range(1, 9), "bus_kwh": [365] * 8})
    scaled = replicate_rows(buses, 4, bus_column="bus_id")
    assert scaled["bus_id"].tolist() == list(range(1, 33))

    trips = pd.DataFrame(
        {
            "trip_id": range(1, 9),
            "bus_id": range(1, 9),
            "time_begin": ["06:00"] * 8,
            "time_end": ["20:00"] * 8,
            "energy_kwhkm": [1.0] * 8,
        }
    )
    transformed = depot_b_trips(trips)
    assert transformed["bus_id"].tolist() == list(range(1, 9))
    assert transformed["time_begin"].tolist() == trips["time_begin"].tolist()
    assert transformed["energy_kwhkm"].tolist() != trips["energy_kwhkm"].tolist()

    prices = pd.DataFrame({"timestep": range(1, 49), "spot_market": range(1, 49)})
    shifted = depot_b_prices(prices)
    assert shifted.loc[0, "spot_market"] == pytest.approx(45 * 1.02)


def test_evaluator_v2_protocol_has_no_currency_penalty():
    protocol = json.loads(
        (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "inputs/revision/information_and_evaluator_ablation_protocol_v2.json"
        ).read_text(encoding="utf-8")
    )
    rerun = protocol["rerun_mechanism"]
    assert rerun["name"] == "lexicographic_soft_operational_priority"
    assert rerun["arbitrary_currency_penalty"] is False
    assert rerun["stages"][-1].startswith("hold both earlier optima")
    assert protocol["planning_and_accounting"]["solver_order"] == ["gurobi"]
    assert protocol["planning_and_accounting"][
        "solver_fallback_permitted_in_final_results"
    ] is False
    assert validate_evaluator_protocol()["protocol_version"] == (
        "information_and_evaluator_ablation_v2"
    )


def test_revision_package_validator_tracks_final_v5_protocol_and_prompts():
    required_names = {path.name for path in REQUIRED_FILES}
    assert "advance_warning_ablation_protocol_v5.json" in required_names
    assert "advance_warning_ablation_protocol_v4.json" not in required_names
    assert {
        "trigger_system.txt",
        "pricing_selfish_system.txt",
        "pricing_altruistic_system.txt",
        "evaluator_system.txt",
    }.issubset(required_names)

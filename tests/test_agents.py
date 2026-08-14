from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentic_workflow.agents import (
    AgentBackend,
    CompositeAgentBackend,
    EvidenceGatedAgentBackend,
    HardCheckAgentBackend,
    NoticeOnlyAgentBackend,
    NumericalOnlyAgentBackend,
    OpenAIAgentBackend,
    RuleBasedAgentBackend,
    build_openai_trigger_payload,
    build_pricing_reference,
    create_experiment_backend,
    describe_agent_roles,
    enforce_evaluator_pricing_feedback,
    normalize_trigger_decision,
    pricing_comparison_metrics,
)
from agentic_workflow.models import (
    EvaluationDecision,
    EvaluationFeedback,
    MultiplierAdjustment,
    NULL_FEEDBACK,
    NoticeInterpretation,
    NoticeUncertaintyAssessment,
    PricingDecision,
    StructuredNoticeInterpretation,
    StructuredNoticeParameterUpdates,
    StructuredPricingDecision,
    StructuredTriggerDecision,
    TriggerDecision,
)


def _context():
    return {
        "timestep": 5,
        "remaining_timesteps": 44,
        "mode": "selfish",
        "trigger_flags": {
            "price_deviation_significant": True,
            "has_severe_delay": False,
            "delay_removal_active": False,
            "delay_sign_reversed": False,
            "has_high_energy_deviation": False,
            "multi_bus_moderate_deviation": False,
        },
        "reoptimization_history": {
            "last_reopt_trigger_type": None,
            "last_reopt_timestep": None,
        },
        "deviation_summary": {"has_energy_disturbance": False},
        "intraday_prices": {
            "prices": [
                {"timestep": timestep, "spot_market": 0.1, "price_zone": "transition"}
                for timestep in range(5, 49)
            ]
        },
    }


def test_rule_agent_trigger_and_pricing_lengths():
    backend = RuleBasedAgentBackend()
    context = _context()
    trigger = backend.trigger(context)
    assert trigger.action == "optimize"
    assert trigger.trigger_type == "price"
    pricing = backend.price(
        context,
        trigger,
        rerun_count=0,
        previous=None,
        feedback=None,
    )
    assert len(pricing.buy_multipliers) == 44
    assert len(pricing.sell_multipliers) == 44
    assert max(pricing.buy_multipliers[:6]) <= 1.10
    assert all(sell < buy for buy, sell in zip(pricing.buy_multipliers, pricing.sell_multipliers))


def test_altruistic_pricing_reference_matches_deterministic_policy_and_is_optional():
    context = _context()
    context["mode"] = "altruistic"
    context["intraday_prices"]["prices"] = [
        {"timestep": 5, "spot_market": 0.08, "price_zone": "cheap"},
        {"timestep": 6, "spot_market": 0.12, "price_zone": "transition"},
        {"timestep": 7, "spot_market": 0.20, "price_zone": "expensive"},
    ]

    reference = build_pricing_reference(context)

    assert reference["status"] == "optional_context_not_constraint"
    assert reference["current_horizon"]["buy_multipliers"] == [1.01, 1.03, 1.05]
    assert reference["current_horizon"]["sell_multipliers"] == [0.82, 0.89, 0.96]
    assert reference["current_horizon"]["buy_summary"]["arithmetic_mean"] == 1.03
    assert reference["current_horizon"]["sell_summary"]["arithmetic_mean"] == 0.89


def test_pricing_comparison_separates_average_level_and_temporal_shape():
    context = _context()
    context["mode"] = "altruistic"
    context["intraday_prices"]["prices"] = [
        {"timestep": 5, "spot_market": 0.08, "price_zone": "cheap"},
        {"timestep": 6, "spot_market": 0.12, "price_zone": "transition"},
        {"timestep": 7, "spot_market": 0.20, "price_zone": "expensive"},
    ]
    pricing = PricingDecision(
        buy_multipliers=[1.02, 1.04, 1.06],
        sell_multipliers=[0.84, 0.91, 0.98],
        reasoning="same temporal shape with a higher overall level",
        confidence=1.0,
    )

    metrics = pricing_comparison_metrics(
        context,
        pricing,
        {"w_buy": [3.0, 1.0, 0.0], "w_sell": [0.0, 1.0, 3.0]},
    )

    assert metrics["reference_is_guidance_only"] is True
    assert metrics["buy_arithmetic_mean_gap"] == pytest.approx(0.01)
    assert metrics["sell_arithmetic_mean_gap"] == pytest.approx(0.02)
    assert metrics["buy_centered_temporal_mae"] == pytest.approx(0.0)
    assert metrics["sell_centered_temporal_mae"] == pytest.approx(0.0)
    assert metrics["chosen_buy_dispatch_weighted_mean"] == pytest.approx(1.025)
    assert metrics["chosen_sell_dispatch_weighted_mean"] == pytest.approx(0.9625)


def test_trigger_guard_repairs_inconsistent_llm_skip():
    decision = TriggerDecision(
        action="skip",
        reasoning="Skip despite the price flag.",
        confidence=0.6,
        trigger_type="price",
        flagged_buses=[1],
    )
    repaired = normalize_trigger_decision(decision, _context())
    assert repaired.action == "optimize"
    assert repaired.trigger_type == "price"
    assert "safety guard" in repaired.reasoning


def test_agent_only_guard_does_not_substitute_numerical_baseline() -> None:
    decision = TriggerDecision(
        action="skip",
        reasoning="Agent judged the evidence insufficient.",
        confidence=0.6,
        trigger_type="none",
        flagged_buses=[],
    )
    guarded = normalize_trigger_decision(
        decision, _context(), allow_numerical_fallback=False
    )
    assert guarded.action == "skip"


class _CountingBackend(AgentBackend):
    def __init__(self) -> None:
        self.trigger_calls = 0

    def trigger(self, context):
        self.trigger_calls += 1
        return TriggerDecision(
            action="skip",
            reasoning="counted",
            confidence=1.0,
            trigger_type="none",
            flagged_buses=[],
        )

    def price(self, context, trigger, *, rerun_count, previous, feedback):
        return PricingDecision(
            reasoning="unused",
            buy_multipliers=[1.1],
            sell_multipliers=[0.8],
            confidence=1.0,
        )

    def evaluate(self, context, trigger, pricing, result, *, rerun_count):
        return EvaluationDecision(
            accept=True,
            reasoning="unused",
            confidence=1.0,
            feedback=NULL_FEEDBACK,
        )


def test_evidence_gate_calls_trigger_only_for_new_text_or_changed_telemetry():
    counted = _CountingBackend()
    backend = EvidenceGatedAgentBackend(counted)
    context = _context()
    context["trigger_flags"]["price_deviation_significant"] = False
    context["operational_notices"] = []
    context["numerical_event_telemetry"] = {
        "return_delay_minutes_by_bus": {},
        "charger_power_kw": {},
        "unavailable_chargers": [],
        "effective_timestep": None,
        "expected_end_timestep": None,
    }

    assert backend.trigger(context).action == "skip"
    assert counted.trigger_calls == 0
    assert backend.trigger(context).action == "skip"
    assert counted.trigger_calls == 0

    context["operational_notices"] = [{"text": "Driver reports a possible delay."}]
    backend.trigger(context)
    assert counted.trigger_calls == 1

    context["operational_notices"] = []
    backend.trigger(context)
    assert counted.trigger_calls == 1

    context["numerical_event_telemetry"]["unavailable_chargers"] = [2]
    backend.trigger(context)
    assert counted.trigger_calls == 2
    backend.trigger(context)
    assert counted.trigger_calls == 2

    context["numerical_event_telemetry"]["unavailable_chargers"] = []
    backend.trigger(context)
    assert counted.trigger_calls == 3


def test_role_ablation_factory_changes_only_the_named_role(monkeypatch):
    class StubOpenAIBackend(RuleBasedAgentBackend):
        def __init__(self, model, *, allow_deterministic_trigger_fallback=True):
            self.model = model
            self.allow_deterministic_trigger_fallback = (
                allow_deterministic_trigger_fallback
            )
            self.call_records = []

    monkeypatch.setattr(
        "agentic_workflow.agents.OpenAIAgentBackend", StubOpenAIBackend
    )
    full = create_experiment_backend("full_agentic", "rule", "test-model")
    rule_trigger = create_experiment_backend(
        "rule_parser_trigger_substitution", "rule", "test-model"
    )
    mathematical_pricing = create_experiment_backend(
        "mathematical_pricing_substitution", "rule", "test-model"
    )
    evaluator_removal = create_experiment_backend(
        "evaluator_removal", "rule", "test-model"
    )

    assert isinstance(full, EvidenceGatedAgentBackend)
    assert full.backend.allow_deterministic_trigger_fallback is False
    full_provenance = describe_agent_roles(full)
    assert full_provenance["trigger"]["evidence_gate"] is True
    assert full_provenance["pricing"]["evidence_gate"] is False
    assert full_provenance["evaluator"]["evidence_gate"] is False
    assert isinstance(rule_trigger, CompositeAgentBackend)
    assert isinstance(rule_trigger.trigger_backend, EvidenceGatedAgentBackend)
    assert type(rule_trigger.trigger_backend.backend) is RuleBasedAgentBackend
    assert rule_trigger.pricing_backend is rule_trigger.evaluator_backend
    assert isinstance(mathematical_pricing.pricing_backend, RuleBasedAgentBackend)
    assert isinstance(evaluator_removal.evaluator_backend, HardCheckAgentBackend)


def test_agent_role_provenance_records_shared_trigger_gate():
    backend = CompositeAgentBackend(
        EvidenceGatedAgentBackend(RuleBasedAgentBackend()),
        RuleBasedAgentBackend(),
        HardCheckAgentBackend(),
    )
    provenance = describe_agent_roles(backend)

    assert provenance["trigger"] == {
        "backend": "RuleBasedAgentBackend",
        "evidence_gate": True,
    }
    assert provenance["pricing"]["backend"] == "RuleBasedAgentBackend"
    assert provenance["pricing"]["evidence_gate"] is False
    assert provenance["evaluator"]["backend"] == "HardCheckAgentBackend"


def test_openai_trigger_payload_excludes_private_experiment_metadata():
    context = _context()
    context.update(
        {
            "operational_notices": [{"text": "public maintenance message"}],
            "active_scenarios": [{"scenario_id": "hidden-case-id"}],
            "event_status": {"energy": {"configured": True}},
            "canonical": {"updates": {"unavailable_chargers": [8]}},
            "physical_truth": {"unavailable_chargers": [8]},
            "wording_variant": "uncertain_chat",
            "history": [{"timestep": value} for value in range(1, 12)],
        }
    )

    payload = build_openai_trigger_payload(context)

    assert payload["operational_notices"] == context["operational_notices"]
    assert [row["timestep"] for row in payload["history"]] == [7, 8, 9, 10, 11]
    for private_key in (
        "active_scenarios",
        "event_status",
        "canonical",
        "physical_truth",
        "wording_variant",
    ):
        assert private_key not in payload


def test_openai_evaluator_receives_runtime_rerun_cap():
    captured = {}

    def fake_parse(system, user_data, schema, *, role):
        captured.update(user_data)
        assert role == "evaluator"
        return EvaluationDecision(
            accept=True,
            reasoning="cap received",
            confidence=1.0,
            feedback=NULL_FEEDBACK,
        )

    backend = OpenAIAgentBackend.__new__(OpenAIAgentBackend)
    backend._parse = fake_parse
    context = _context()
    context.update(
        {
            "maximum_reruns": 1,
            "deviations": {},
            "day_ahead_summary": {},
            "da_benchmark": {},
        }
    )
    trigger = TriggerDecision(
        action="optimize",
        reasoning="test",
        confidence=1.0,
        trigger_type="price",
        flagged_buses=[],
    )
    pricing = PricingDecision(
        buy_multipliers=[1.0] * 44,
        sell_multipliers=[0.8] * 44,
        reasoning="test",
        confidence=1.0,
    )

    backend.evaluate(
        context,
        trigger,
        pricing,
        {"energy": [[100.0]], "is_mock": False},
        rerun_count=0,
    )

    assert captured["maximum_reruns"] == 1


def test_openai_evaluator_does_not_auto_accept_negative_remaining_cost():
    def fake_parse(system, user_data, schema, *, role):
        assert role == "evaluator"
        return EvaluationDecision(
            accept=False,
            reasoning="incorrect percentage comparison against a negative benchmark",
            confidence=0.9,
            feedback=NULL_FEEDBACK,
        )

    backend = OpenAIAgentBackend.__new__(OpenAIAgentBackend)
    backend._parse = fake_parse
    backend.call_records = [{}]
    context = _context()
    context.update(
        {
            "mode": "altruistic",
            "maximum_reruns": 1,
            "deviations": {},
            "day_ahead_summary": {},
            "da_benchmark": {"da_benchmark_valid": True, "da_cost_remaining": -40},
        }
    )
    trigger = TriggerDecision(
        action="optimize",
        reasoning="test",
        confidence=1.0,
        trigger_type="price",
        flagged_buses=[],
    )
    pricing = PricingDecision(
        buy_multipliers=[1.01] * 44,
        sell_multipliers=[0.9] * 44,
        reasoning="test",
        confidence=1.0,
    )

    decision = backend.evaluate(
        context,
        trigger,
        pricing,
        {
            "energy": [[100.0]],
            "is_mock": False,
            "solver_status": "ok/optimal",
            "pto_daily_cost": -5.0,
        },
        rerun_count=0,
    )

    assert decision.accept is False
    assert "post_parse_normalization" not in backend.call_records[-1]


def test_evaluator_prompt_requires_full_day_accounting_and_no_negative_cost_guard():
    prompt = (
        Path(__file__).parents[1]
        / "agentic_workflow"
        / "prompts"
        / "evaluator_system.txt"
    ).read_text(encoding="utf-8")
    assert "projected_full_day_pto_cost" in prompt
    assert "negative remaining_horizon_pto_cost is NOT an automatic accept" in prompt
    assert "all flagged buses have |energy_deviation_pct| <= 5%" not in prompt


def test_selfish_pricing_prompt_models_margin_volume_tradeoff():
    prompt = (
        Path(__file__).parents[1]
        / "agentic_workflow"
        / "prompts"
        / "pricing_selfish_system.txt"
    ).read_text(encoding="utf-8")
    normalized_prompt = " ".join(prompt.split())
    assert "charges buses regardless of buy level" not in prompt
    assert "Extreme buy multipliers can suppress" in prompt
    assert "margin TIMES endogenous transaction volume" in prompt
    assert "Do not lower sell when there is no" in prompt
    assert "whole contiguous expensive price-zone block" in normalized_prompt
    assert "0.55–0.62" in prompt
    assert "0.82 is a reasonable" in prompt


def test_evaluator_prompt_requires_dispatch_sensitive_pricing_feedback():
    prompt = (
        Path(__file__).parents[1]
        / "agentic_workflow"
        / "prompts"
        / "evaluator_system.txt"
    ).read_text(encoding="utf-8")
    normalized_prompt = " ".join(prompt.split())
    assert "proposed_w_buy_kwh and" in prompt
    assert "do NOT lower sell" in prompt
    assert "Do not raise buy merely because revenue is low" in prompt
    assert "whole contiguous expensive price-zone block" in normalized_prompt
    assert "0.55–0.62" in prompt
    assert "0.82 is a reasonable" in prompt


def test_pricing_feedback_guard_repairs_numeric_noncompliance_only():
    previous = PricingDecision(
        buy_multipliers=[1.10, 1.18, 1.18],
        sell_multipliers=[0.58, 0.72, 0.72],
        reasoning="Initial proposal.",
        confidence=0.9,
    )
    feedback = EvaluationFeedback(
        reason="revenue_too_low",
        buy_multiplier_adjustment=None,
        sell_multiplier_adjustment=MultiplierAdjustment(
            timestep_start=8,
            timestep_end=9,
            direction="raise",
            amount=0.03,
            current_value=0.72,
            target_value=0.75,
            instruction="Raise the expensive-period sell incentive.",
        ),
        period_adjustment=None,
        priority="v2g_increase",
    )

    repaired, audit = enforce_evaluator_pricing_feedback(
        previous,
        feedback=feedback,
        mode="selfish",
        planning_start_timestep=7,
    )

    assert repaired.sell_multipliers == [0.58, 0.75, 0.75]
    assert audit == {
        "kind": "evaluator_feedback_compliance",
        "method": "enforce_explicit_direction_and_target_only",
        "adjustments": [
            {
                "side": "sell",
                "direction": "raise",
                "target_value": 0.75,
                "changed_timesteps": [8, 9],
            }
        ],
    }

    already_stronger = previous.model_copy(
        update={"sell_multipliers": [0.58, 0.77, 0.77]}
    )
    preserved, audit = enforce_evaluator_pricing_feedback(
        already_stronger,
        feedback=feedback,
        mode="selfish",
        planning_start_timestep=7,
    )
    assert preserved.sell_multipliers == [0.58, 0.77, 0.77]
    assert audit is None


def test_trigger_comparison_channels_are_isolated() -> None:
    context = _context()
    assert NoticeOnlyAgentBackend().trigger(context).action == "skip"

    context["trigger_flags"]["price_deviation_significant"] = False
    context["notice_interpretation"] = NoticeInterpretation(
        event_id="OPS-TEST",
        source_type="driver_chat",
        event_type="route_energy_change",
        phase="onset",
        affected_buses=[2],
        effective_timestep=5,
        uncertainty_details=NoticeUncertaintyAssessment(
            confidence_level=0.9,
            recommended_action="optimize",
        ),
    ).model_dump()
    assert NoticeOnlyAgentBackend().trigger(context).action == "optimize"
    assert NumericalOnlyAgentBackend().trigger(context).action == "skip"


def test_trigger_guard_normalizes_non_actionable_skip():
    context = _context()
    context["trigger_flags"]["price_deviation_significant"] = False
    decision = TriggerDecision(
        action="skip",
        reasoning="No action.",
        confidence=0.9,
        trigger_type="deviation",
        flagged_buses=[2],
    )
    repaired = normalize_trigger_decision(decision, context)
    assert repaired.trigger_type == "none"
    assert repaired.flagged_buses == []


def test_trigger_guard_blocks_duplicate_persistent_event_optimization():
    context = _context()
    context["trigger_flags"].update(
        {
            "price_deviation_significant": False,
            "same_event_already_accounted": True,
            "unexpected_discharging_buses": [],
        }
    )
    decision = TriggerDecision(
        action="optimize",
        reasoning="The same continuing event still differs from day-ahead.",
        confidence=0.9,
        trigger_type="energy_disturbance",
        flagged_buses=[1],
    )
    repaired = normalize_trigger_decision(decision, context)
    assert repaired.action == "skip"
    assert repaired.trigger_type == "none"
    assert "event-memory guard" in repaired.reasoning


def test_trigger_guard_uses_notice_event_memory_for_persistence():
    context = _context()
    context["trigger_flags"]["price_deviation_significant"] = False
    context["notice_event_memory"] = [
        {"event_id": "CHG-01", "previous_phase": "onset", "previous_timestep": 10}
    ]
    decision = TriggerDecision(
        action="optimize",
        reasoning="The charger restriction remains active.",
        confidence=0.9,
        trigger_type="charger_event",
        flagged_buses=[],
        notice_interpretation=StructuredNoticeInterpretation(
            event_id="CHG-01",
            source_type="ocpp",
            event_type="charger_fault",
            phase="persistence",
            affected_buses=[],
            affected_chargers=[2],
            effective_timestep=11,
            expected_end_timestep=14,
            uncertainty=True,
            material=True,
            updates=StructuredNoticeParameterUpdates(unavailable_chargers=[2]),
            evidence=["restriction remains"],
        ).to_domain(),
    )

    repaired = normalize_trigger_decision(decision, context)

    assert repaired.action == "skip"
    assert repaired.trigger_type == "none"
    assert "event-memory guard" in repaired.reasoning


def test_trigger_guard_maps_request_confirmation_to_skip():
    context = _context()
    context["trigger_flags"]["price_deviation_significant"] = False
    notice = NoticeInterpretation(
        event_id="OPS-101",
        source_type="driver_chat",
        event_type="service_delay",
        phase="warning",
        affected_buses=[4],
        effective_timestep=5,
        uncertainty=True,
        uncertainty_details=NoticeUncertaintyAssessment(
            confidence_level=0.45,
            provisional=True,
            recommended_action="request_confirmation",
            rationale="Field report is not confirmed.",
        ),
    )
    decision = TriggerDecision(
        action="optimize",
        reasoning="Premature optimization.",
        confidence=0.6,
        trigger_type="service_notice",
        flagged_buses=[4],
        notice_interpretation=notice,
    )
    repaired = normalize_trigger_decision(decision, context)
    assert repaired.action == "skip"
    assert repaired.trigger_type == "none"
    assert "request_confirmation" in repaired.reasoning


def test_trigger_guard_maps_confirmed_optimizer_update_to_optimize():
    context = _context()
    context["trigger_flags"]["price_deviation_significant"] = False
    notice = NoticeInterpretation(
        event_id="OPS-101",
        source_type="driver_chat",
        event_type="service_delay",
        phase="onset",
        affected_buses=[4],
        effective_timestep=5,
        uncertainty=True,
        uncertainty_details=NoticeUncertaintyAssessment(
            confidence_level=0.72,
            provisional=True,
            recommended_action="optimize",
            rationale="Confirmed material event.",
        ),
    )
    decision = TriggerDecision(
        action="skip",
        reasoning="Inconsistent skip.",
        confidence=0.6,
        trigger_type="none",
        flagged_buses=[],
        notice_interpretation=notice,
    )
    repaired = normalize_trigger_decision(decision, context)
    assert repaired.action == "optimize"
    assert repaired.trigger_type == "service_notice"
    assert repaired.flagged_buses == [4]


def test_rule_agent_triggers_energy_recovery_once():
    context = _context()
    context["trigger_flags"].update(
        {
            "price_deviation_significant": False,
            "energy_recovery_active": True,
            "energy_event_buses": [1, 2, 3],
        }
    )
    trigger = RuleBasedAgentBackend().trigger(context)
    assert trigger.action == "optimize"
    assert trigger.trigger_type == "energy_recovery"
    assert trigger.flagged_buses == [1, 2, 3]


def test_rule_agent_only_treats_precomputed_unexpected_discharging_as_trigger():
    context = _context()
    context["trigger_flags"].update(
        {
            "price_deviation_significant": False,
            "reported_discharging_buses": [4],
            "unexpected_discharging_buses": [],
        }
    )
    assert RuleBasedAgentBackend().trigger(context).action == "skip"

    context["trigger_flags"]["unexpected_discharging_buses"] = [4]
    trigger = RuleBasedAgentBackend().trigger(context)
    assert trigger.action == "optimize"
    assert trigger.trigger_type == "deviation"
    assert trigger.flagged_buses == [4]


def test_gpt_56_chat_parse_uses_low_cost_reasoning_baseline():
    captured = {}
    parsed = TriggerDecision(
        action="skip",
        reasoning="test",
        confidence=1,
        trigger_type="none",
        flagged_buses=[],
    )

    class FakeCompletions:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))]
            )

    backend = OpenAIAgentBackend.__new__(OpenAIAgentBackend)
    backend.model = "gpt-5.6-sol"
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    assert backend._parse("system", {"value": 1}, TriggerDecision) == parsed
    assert captured["model"] == "gpt-5.6-sol"
    assert captured["reasoning_effort"] == "low"


def test_openai_parse_records_schema_failure_and_retry():
    parsed = TriggerDecision(
        action="skip", reasoning="ok", confidence=1,
        trigger_type="none", flagged_buses=[]
    )

    class FlakyCompletions:
        calls = 0

        def parse(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("invalid structured output")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    parsed=parsed, refusal=None, content='{"action":"skip"}'
                ))]
            )

    backend = OpenAIAgentBackend.__new__(OpenAIAgentBackend)
    backend.model = "test-model"
    backend.call_records = []
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FlakyCompletions())
    )
    assert backend._parse("system", {}, TriggerDecision, role="trigger") == parsed
    assert [record["schema_valid"] for record in backend.call_records] == [False, True]
    assert [record["attempt"] for record in backend.call_records] == [1, 2]


def test_openai_pricing_normalizes_mismatched_transport_lengths():
    parsed = StructuredPricingDecision(
        buy_multipliers=[1.03, 1.04],
        sell_multipliers=[0.82],
        reasoning="valid content with a length-only transport defect",
        confidence=0.9,
    )

    class FakeCompletions:
        def parse(self, **kwargs):
            assert kwargs["response_format"] is StructuredPricingDecision
            request = json.loads(kwargs["messages"][1]["content"])
            guidance = request["pricing_reference_guidance"]
            assert guidance["status"] == "optional_context_not_constraint"
            assert guidance["current_horizon"]["buy_summary"]["arithmetic_mean"] == 1.1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    parsed=parsed,
                    refusal=None,
                    content='{"buy_multipliers":[1.03,1.04],"sell_multipliers":[0.82]}',
                ))]
            )

    backend = OpenAIAgentBackend.__new__(OpenAIAgentBackend)
    backend.model = "test-model"
    backend.call_records = []
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    context = _context()
    context.update(
        {
            "remaining_timesteps": 3,
            "remaining_hours": 1.5,
            "realtime_state": {},
            "deviations": {},
            "day_ahead_summary": {},
        }
    )

    decision = backend.price(
        context,
        TriggerDecision(
            action="optimize",
            reasoning="test",
            confidence=1,
            trigger_type="service_notice",
            flagged_buses=[],
        ),
        rerun_count=0,
        previous=None,
        feedback=None,
    )

    assert decision.buy_multipliers == [1.03, 1.04, 1.04]
    assert decision.sell_multipliers == [0.82, 0.82, 0.82]
    assert backend.call_records[-1]["post_parse_normalization"] == {
        "kind": "pricing_array_length",
        "expected_length": 3,
        "actual_lengths": {"buy": 2, "sell": 1},
        "method": "truncate_or_extend_last_value",
    }


def test_openai_trigger_schema_uses_only_closed_objects():
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(StructuredTriggerDecision)

    def assert_closed_objects(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
            for child in value.values():
                assert_closed_objects(child)
        elif isinstance(value, list):
            for child in value:
                assert_closed_objects(child)

    assert_closed_objects(schema)


def test_structured_trigger_converts_update_lists_to_domain_maps():
    structured = StructuredTriggerDecision(
        action="optimize",
        reasoning="Charger 2 fault onset.",
        confidence=0.95,
        trigger_type="charger_event",
        flagged_buses=[],
        notice_interpretation=StructuredNoticeInterpretation(
            event_id="chg_2_fault",
            source_type="ocpp",
            event_type="charger_fault",
            phase="onset",
            affected_buses=[],
            affected_chargers=[2],
            effective_timestep=10,
            expected_end_timestep=14,
            uncertainty=False,
            material=True,
            updates=StructuredNoticeParameterUpdates(
                charger_power_kw=[{"asset_id": 2, "value": 0.0}],
                unavailable_chargers=[2],
            ),
            evidence=["connector 2 unavailable"],
        ),
    )

    decision = structured.to_domain()
    assert decision.notice_interpretation is not None
    assert decision.notice_interpretation.updates.charger_power_kw == {2: 0.0}
    assert decision.notice_interpretation.updates.unavailable_chargers == [2]


def test_openai_parse_flattens_detailed_usage_and_cache_aware_cost():
    parsed = TriggerDecision(
        action="skip", reasoning="ok", confidence=1,
        trigger_type="none", flagged_buses=[]
    )
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "total_tokens": 110,
        "prompt_tokens_details": {
            "cached_tokens": 20,
            "cache_write_tokens": 30,
        },
        "completion_tokens_details": {"reasoning_tokens": 4},
    }

    class FakeCompletions:
        def parse(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    parsed=parsed, refusal=None, content='{"action":"skip"}'
                ))],
                usage=SimpleNamespace(model_dump=lambda: usage),
            )

    backend = OpenAIAgentBackend.__new__(OpenAIAgentBackend)
    backend.model = "gpt-5.6-luna"
    backend.call_records = []
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    assert backend._parse("system", {}, TriggerDecision, role="trigger") == parsed
    record = backend.call_records[0]
    assert record["input_tokens"] == 100
    assert record["cached_input_tokens"] == 20
    assert record["cache_write_tokens"] == 30
    assert record["uncached_input_tokens"] == 50
    assert record["reasoning_tokens"] == 4
    assert record["total_tokens"] == 110
    assert record["approximate_cost_usd"] == 0.0000299

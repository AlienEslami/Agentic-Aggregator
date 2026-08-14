from __future__ import annotations

from types import SimpleNamespace

from agentic_workflow.agents import (
    AgentBackend,
    EvidenceGatedAgentBackend,
    NoticeOnlyAgentBackend,
    NumericalOnlyAgentBackend,
    OpenAIAgentBackend,
    RuleBasedAgentBackend,
    normalize_trigger_decision,
)
from agentic_workflow.models import (
    EvaluationDecision,
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

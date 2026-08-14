from agentic_workflow.agents import build_openai_trigger_payload
from agentic_workflow.evaluation import assess_priority, frozen_priority_parse
from agentic_workflow.models import OperationalPriority


def test_trigger_payload_never_exposes_preinterpreted_or_canonical_information():
    payload = build_openai_trigger_payload(
        {
            "timestep": 6,
            "operational_notices": [{"text": "Bus 6 may return late."}],
            "notice_interpretation": {"event_type": "service_delay"},
            "notice_flags": {"same_event_already_accounted": False},
            "benchmark_canonical_priorities": [{"objective": "hidden"}],
            "history": list(range(10)),
        }
    )
    assert payload["operational_notices"]
    assert payload["history"] == [5, 6, 7, 8, 9]
    assert "notice_interpretation" not in payload
    assert "notice_flags" not in payload
    assert "benchmark_canonical_priorities" not in payload


def test_frozen_text_evaluator_uses_declared_extra_reserve_default():
    priority = frozen_priority_parse(
        [
            {
                "notice_id": "N1",
                "text": "Bus 8 has warnings. Keep some extra charge available tonight if possible.",
            }
        ],
        planning_start_timestep=20,
    )
    assert priority is not None
    assert priority.objective == "preserve_bus_reserve"
    assert priority.affected_buses == [8]
    assert priority.timestep_start == 37
    assert priority.timestep_end == 48
    assert priority.target_value == 0.30
    assert priority.default_policy_applied is True


def test_priority_assessment_scores_schedule_without_llm_arithmetic():
    priority = OperationalPriority(
        priority_id="P1",
        objective="preserve_bus_reserve",
        affected_buses=[1],
        timestep_start=37,
        timestep_end=39,
        target_value=0.30,
        target_unit="soc_fraction",
    )
    result = {
        "remaining_horizon_start": 37,
        "remaining_horizon_end": 39,
        "energy": [[120.0, 100.0, 130.0]],
    }
    assessment = assess_priority(
        result, priority, battery_capacity_kwh_by_bus={1: 365.0}
    )
    assert assessment is not None
    assert assessment.applicable is True
    assert assessment.satisfied is False
    assert assessment.measured_value == round(100.0 / 365.0, 6)

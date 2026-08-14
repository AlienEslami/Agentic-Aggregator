import json
import hashlib
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from agentic_workflow.agents import RuleBasedAgentBackend
from agentic_workflow.models import (
    NoticeInterpretation,
    NoticeParameterUpdates,
    UncertainParameterEstimate,
    TriggerDecision,
)
from agentic_workflow.notices import (
    NoticeRecord,
    NoticeSeries,
    apply_notice_updates,
    frozen_rule_parse,
    normalize_notice_clock_timesteps,
    resolve_notice_coreferences,
)
from agentic_workflow.runner import WorkflowRunner
from agentic_workflow.state import WorkflowState
from agentic_workflow.trigger_evaluation import (
    build_notice_only_context,
    reference_action,
    score_trigger_decision,
)
from agentic_workflow.uncertainty import select_operational_value


DATA = Path(__file__).parents[1] / "inputs" / "revision" / "trigger_notices.json"
V3_DATA = Path(__file__).parents[1] / "inputs" / "revision" / "trigger_notices_v3.json"


def test_public_clock_window_has_one_deterministic_timestep_mapping():
    interpretation = NoticeInterpretation(
        event_id="AW-BANK",
        source_type="combined",
        event_type="charger_fault",
        phase="onset",
        effective_timestep=7,
        expected_end_timestep=11,
    )
    normalized = normalize_notice_clock_timesteps(
        interpretation,
        {
            "operational_notices": [
                {
                    "event_id": "AW-BANK",
                    "text": "Isolate the south row from 03:30 to 05:00.",
                }
            ]
        },
    )
    assert normalized.effective_timestep == 8
    assert normalized.expected_end_timestep == 10


def test_non_aligned_clock_window_includes_every_overlapping_half_hour():
    interpretation = NoticeInterpretation(
        event_id="WINDOW",
        source_type="combined",
        event_type="charger_fault",
        phase="onset",
        effective_timestep=1,
        expected_end_timestep=1,
    )
    normalized = normalize_notice_clock_timesteps(
        interpretation,
        {
            "operational_notices": [
                {"event_id": "WINDOW", "text": "Restriction from 03:45 to 05:15."}
            ]
        },
    )
    assert (normalized.effective_timestep, normalized.expected_end_timestep) == (8, 11)


def test_end_of_day_clock_window_maps_to_final_timestep():
    interpretation = NoticeInterpretation(
        event_id="AW-ROUTE6",
        source_type="driver_chat",
        event_type="service_delay",
        phase="warning",
        effective_timestep=44,
        expected_end_timestep=None,
    )
    normalized = normalize_notice_clock_timesteps(
        interpretation,
        {
            "operational_notices": [
                {
                    "event_id": "AW-ROUTE6",
                    "text": (
                        "Dispatch has not confirmed the 21:30-to-end-of-day "
                        "control window."
                    ),
                }
            ]
        },
    )
    assert (normalized.effective_timestep, normalized.expected_end_timestep) == (
        44,
        48,
    )


def test_followup_inherits_normalized_window_but_recovery_does_not():
    active = {
        "event_id": "AW-BANK",
        "effective_timestep": 8,
        "expected_end_timestep": 10,
    }
    persistence = NoticeInterpretation(
        event_id="AW-BANK",
        source_type="combined",
        event_type="charger_fault",
        phase="persistence",
        effective_timestep=6,
        expected_end_timestep=11,
    )
    normalized = normalize_notice_clock_timesteps(
        persistence, {"active_operational_events": [active]}
    )
    assert (normalized.effective_timestep, normalized.expected_end_timestep) == (8, 10)

    recovery = persistence.model_copy(
        update={"phase": "recovery", "effective_timestep": 11, "expected_end_timestep": 11}
    )
    assert normalize_notice_clock_timesteps(
        recovery, {"active_operational_events": [active]}
    ) == recovery


def test_revision_dataset_has_aligned_lifecycle_and_wording_variants():
    series = NoticeSeries(DATA)
    explicit = series.at(
        10, scenario_ids=("svc_route4_detour",), wording_variant="explicit"
    )
    indirect = series.at(
        10, scenario_ids=("svc_route4_detour",), wording_variant="indirect"
    )
    operational = series.at(
        10, scenario_ids=("svc_route4_detour",), wording_variant="operational"
    )
    stable = series.at(
        15, scenario_ids=("svc_route4_detour",), wording_variant="explicit"
    )
    assert len(explicit) == len(indirect) == len(operational) == len(stable) == 1
    assert explicit[0].canonical.phase == "onset"
    assert stable[0].canonical.phase == "stable"
    assert stable[0].canonical.material is False


def test_revision_dataset_v2_is_frozen_at_120_decisions():
    series = NoticeSeries(DATA)
    manifest = json.loads(
        DATA.with_name("trigger_dataset_manifest.json").read_text(encoding="utf-8")
    )
    assert len(series.records) == 120
    assert {record.wording_variant for record in series.records} == {
        "explicit",
        "indirect",
        "operational",
    }
    assert manifest["dataset_version"] == "trigger_notices_v2"
    assert manifest["decision_count"] == 120
    assert manifest["method_input_excludes"] == [
        "canonical",
        "scenario_id",
        "wording_variant",
    ]


def test_uncertainty_chat_v3_is_complete_and_scenario_clustered():
    series = NoticeSeries(V3_DATA)
    manifest = json.loads(
        V3_DATA.with_name("trigger_dataset_manifest_v3.json").read_text(
            encoding="utf-8"
        )
    )
    split = json.loads(
        V3_DATA.with_name("trigger_split_v3.json").read_text(encoding="utf-8")
    )
    assert len(series.records) == 192
    assert {record.wording_variant for record in series.records} == {
        "clean",
        "single_message",
        "driver_chat",
        "uncertain_chat",
    }
    assert {record.uncertainty_case for record in series.records} == {
        "warning",
        "onset",
        "persistence",
        "severity_change",
        "recovery",
        "stable",
    }
    assert sum(record.benchmark_split == "development" for record in series.records) == 96
    assert sum(record.benchmark_split == "test" for record in series.records) == 96
    assert set(split["development"]).isdisjoint(split["test"])
    assert manifest["dataset_version"] == "trigger_uncertainty_chat_v3"
    assert manifest["decision_count"] == 192
    for name, expected in manifest["sha256"].items():
        actual = hashlib.sha256(V3_DATA.with_name(name).read_bytes()).hexdigest()
        assert actual == expected


def test_v3_public_payload_hides_split_case_and_truth():
    record = NoticeSeries(V3_DATA).records[0]
    payload = record.public_dict()
    assert record.benchmark_split == "development"
    assert record.uncertainty_case == "warning"
    for hidden in (
        "canonical",
        "scenario_id",
        "wording_variant",
        "benchmark_split",
        "uncertainty_case",
    ):
        assert hidden not in payload


def test_frozen_uncertainty_policy_selects_risk_aware_bounds():
    assert select_operational_value("delay_minutes", 20, 30, "optimize") == (
        30,
        "conservative_upper",
    )
    assert select_operational_value("energy_multiplier", 1.08, 1.12, "optimize") == (
        1.12,
        "conservative_upper",
    )
    assert select_operational_value("charger_power_kw", 60, 90, "optimize") == (
        60,
        "conservative_lower",
    )
    assert select_operational_value("delay_minutes", 20, 30, "wait") == (
        None,
        "no_update_pending_confirmation",
    )


def test_uncertain_parameter_estimate_rejects_out_of_range_selection():
    with pytest.raises(ValidationError):
        UncertainParameterEstimate(
            parameter="delay_minutes",
            asset_id=4,
            lower_bound=20,
            upper_bound=30,
            selected_value=35,
            unit="minutes",
            selection_policy="conservative_upper",
        )


def test_v3_lifecycle_maps_uncertainty_to_optimizer_updates_completely():
    series = NoticeSeries(V3_DATA)
    records = {
        record.uncertainty_case: record.canonical
        for record in series.records
        if record.scenario_id == "v3_route4_detour"
        and record.wording_variant == "driver_chat"
    }
    warning = records["warning"]
    onset = records["onset"]
    persistence = records["persistence"]
    severity = records["severity_change"]
    recovery = records["recovery"]
    stable = records["stable"]
    assert warning.uncertainty_details.recommended_action == "request_confirmation"
    assert warning.updates == NoticeParameterUpdates()
    assert onset.updates.delay_minutes_by_bus == {4: 30}
    assert onset.updates.energy_multiplier_by_bus == {4: 1.12}
    assert onset.uncertainty_details.provisional is True
    assert persistence.uncertainty_details.recommended_action == "wait"
    assert persistence.updates == onset.updates
    assert severity.updates.delay_minutes_by_bus == {4: 40}
    assert severity.updates.energy_multiplier_by_bus == {4: 1.18}
    assert recovery.updates.delay_minutes_by_bus == {4: 0}
    assert recovery.updates.energy_multiplier_by_bus == {4: 1.0}
    assert stable.material is False
    assert stable.affected_buses == []


def test_v3_heldout_chat_contains_fragmentation_conflict_and_irrelevant_noise():
    record = NoticeSeries(V3_DATA).at(
        12,
        scenario_ids=("v3_bus8_charger8",),
        wording_variant="uncertain_chat",
    )[0]
    lowered = record.text.lower()
    assert "scratch the previous figures" in lowered
    assert "stale dashboard" in lowered
    assert "unrelated" in lowered
    assert "driver lounge" in lowered


def test_public_notice_payload_excludes_experimental_labels_and_truth():
    record = NoticeSeries(DATA).at(
        10, scenario_ids=("svc_route4_detour",), wording_variant="explicit"
    )[0]
    public = record.public_dict()
    assert "scenario_id" not in public
    assert "wording_variant" not in public
    assert "canonical" not in public
    assert public["text"]


def test_frozen_parser_extracts_service_and_charger_updates():
    service = NoticeRecord(
        notice_id="s",
        scenario_id="s",
        event_id="SVC",
        source_type="service_alert",
        wording_variant="explicit",
        report_timestep=10,
        text="Route 4, bus 4, is delayed 25 minutes and energy consumption increases 10 percent.",
    )
    parsed_service = frozen_rule_parse(service, {4: 4})
    assert parsed_service.affected_buses == [4]
    assert parsed_service.updates.delay_minutes_by_bus == {4: 25}
    assert parsed_service.updates.energy_multiplier_by_bus == {4: 1.1}

    charger = NoticeRecord(
        notice_id="c",
        scenario_id="c",
        event_id="CHG",
        source_type="ocpp",
        wording_variant="explicit",
        report_timestep=10,
        text="Charger 5 remains in service but is derated to 75 kW.",
    )
    parsed_charger = frozen_rule_parse(charger)
    assert parsed_charger.affected_chargers == [5]
    assert parsed_charger.updates.charger_power_kw == {5: 75.0}


def test_frozen_parser_recognizes_operational_vocabulary_fairly():
    service = NoticeSeries(DATA).at(
        10, scenario_ids=("svc_route4_detour",), wording_variant="operational"
    )[0]
    parsed_service = frozen_rule_parse(service, {4: 4})
    assert parsed_service.affected_buses == [4]
    assert parsed_service.updates.delay_minutes_by_bus == {4: 25}
    assert parsed_service.updates.energy_multiplier_by_bus == {4: 1.1}

    charger = NoticeSeries(DATA).at(
        10, scenario_ids=("chg_2_fault",), wording_variant="operational"
    )[0]
    parsed_charger = frozen_rule_parse(charger)
    assert parsed_charger.affected_chargers == [2]
    assert parsed_charger.updates.unavailable_chargers == [2]


def test_rule_baseline_resolves_same_event_coreferences_without_truth():
    series = NoticeSeries(DATA)
    onset_record = series.at(
        10, scenario_ids=("combined_bus8_charger8",), wording_variant="operational"
    )[0]
    onset = frozen_rule_parse(onset_record, {8: 8})
    active = {onset.event_id: onset.model_dump()}
    severity_record = series.at(
        12, scenario_ids=("combined_bus8_charger8",), wording_variant="operational"
    )[0]
    severity = resolve_notice_coreferences(
        severity_record,
        frozen_rule_parse(severity_record, {8: 8}),
        active,
    )

    assert severity.affected_buses == [8]
    assert severity.affected_chargers == [8]
    assert severity.updates.delay_minutes_by_bus == {8: 28}
    assert severity.updates.charger_power_kw == {8: 25.0}
    assert "same_event_memory_v1" in severity.evidence


def test_notice_only_context_never_exposes_hidden_benchmark_labels():
    record = NoticeSeries(DATA).records[0]
    context = build_notice_only_context(record)
    serialized = json.dumps(context)
    assert "canonical" not in serialized
    assert "scenario_id" not in serialized
    assert "wording_variant" not in serialized
    assert context["operational_notices"] == [record.public_dict()]


def test_trigger_scoring_reports_raw_action_and_exact_update_correctness():
    record = NoticeSeries(DATA).at(
        10, scenario_ids=("chg_2_fault",), wording_variant="explicit"
    )[0]
    decision = TriggerDecision(
        action="optimize",
        reasoning="Charger 2 is unavailable.",
        confidence=1.0,
        trigger_type="charger_event",
        flagged_buses=[],
        notice_interpretation=record.canonical,
    )
    scores = score_trigger_decision(decision, record.canonical)
    assert reference_action(record.canonical) == "optimize"
    assert scores["action_correct"] is True
    assert scores["updates_correct"] is True


def test_notice_updates_change_common_optimizer_inputs_without_mutating_sources():
    chargers = pd.DataFrame(
        {"charger_id": [1, 2], "charger_kw": [200.0, 200.0]}
    )
    trips = pd.DataFrame(
        {"trip_id": [1], "bus_id": [1], "time_begin": ["06:00"], "time_end": ["08:00"], "energy_kwhkm": [1.0]}
    )
    interpretation = NoticeInterpretation(
        event_id="COM",
        source_type="combined",
        event_type="combined",
        phase="onset",
        affected_buses=[1],
        affected_chargers=[2],
        effective_timestep=10,
        updates=NoticeParameterUpdates(
            delay_minutes_by_bus={1: 30},
            energy_multiplier_by_bus={1: 1.2},
            unavailable_chargers=[2],
        ),
    )
    revised_chargers, revised_trips, revised_energy = apply_notice_updates(
        interpretation,
        chargers=chargers,
        trips=trips,
        energy_consumption=trips,
    )
    assert revised_chargers["charger_id"].tolist() == [1]
    assert revised_trips.iloc[0]["time_begin"] == "06:30"
    assert revised_energy.iloc[0]["energy_kwhkm"] == 1.2
    assert chargers["charger_id"].tolist() == [1, 2]


def test_rule_trigger_optimizes_material_notice_and_skips_accounted_persistence():
    notice = NoticeInterpretation(
        event_id="CHG",
        source_type="ocpp",
        event_type="charger_fault",
        phase="onset",
        affected_chargers=[2],
        effective_timestep=10,
        updates=NoticeParameterUpdates(unavailable_chargers=[2]),
    )
    context = {
        "timestep": 10,
        "remaining_timesteps": 39,
        "trigger_flags": {},
        "reoptimization_history": {
            "last_reopt_trigger_type": None,
            "last_reopt_timestep": None,
        },
        "notice_interpretation": notice.model_dump(),
        "notice_flags": {"same_event_already_accounted": False},
        "deviation_summary": {},
    }
    triggered = RuleBasedAgentBackend().trigger(context)
    assert triggered.action == "optimize"
    assert triggered.trigger_type == "charger_event"
    context["notice_flags"]["same_event_already_accounted"] = True
    skipped = RuleBasedAgentBackend().trigger(context)
    assert skipped.action == "skip"


def test_active_notice_updates_persist_for_independent_reoptimization_until_recovery():
    active = NoticeInterpretation(
        event_id="CHG",
        source_type="ocpp",
        event_type="charger_fault",
        phase="onset",
        affected_chargers=[2],
        effective_timestep=10,
        updates=NoticeParameterUpdates(unavailable_chargers=[2]),
    )
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner.state = WorkflowState(
        realtime_plan=pd.DataFrame(),
        forecast_prices=pd.DataFrame(),
        forecast_energy=pd.DataFrame(),
        active_notice_interpretations={"CHG": active.model_dump()},
    )
    unrelated = TriggerDecision(
        action="optimize", reasoning="price", confidence=1.0,
        trigger_type="price", flagged_buses=[]
    )
    effective = runner._effective_notice_interpretation(unrelated)
    assert effective.updates.unavailable_chargers == [2]

    recovery = active.model_copy(update={
        "phase": "recovery",
        "effective_timestep": 14,
        "updates": NoticeParameterUpdates(charger_power_kw={2: 200.0}),
    })
    recovering_trigger = unrelated.model_copy(update={
        "trigger_type": "charger_event",
        "notice_interpretation": recovery,
    })
    effective_recovery = runner._effective_notice_interpretation(recovering_trigger)
    assert effective_recovery.updates.unavailable_chargers == []
    assert effective_recovery.updates.charger_power_kw == {2: 200.0}


def test_future_charger_window_and_return_delay_change_only_stated_inputs():
    chargers = pd.DataFrame(
        {
            "charger_id": [1, 2],
            "max_power_kw": [200.0, 200.0],
        }
    )
    trips = pd.DataFrame(
        {
            "trip_id": [1],
            "bus_id": [6],
            "time_begin": ["06:30"],
            "time_end": ["21:30"],
        }
    )
    energy = pd.DataFrame({"bus_id": [6], "energy_kwhkm": [1.0]})
    interpretation = NoticeInterpretation(
        event_id="FUTURE",
        source_type="combined",
        event_type="combined",
        phase="onset",
        affected_buses=[6],
        affected_chargers=[2],
        effective_timestep=8,
        expected_end_timestep=10,
        updates=NoticeParameterUpdates(
            return_delay_minutes_by_bus={6: 90},
            unavailable_chargers=[2],
        ),
    )

    revised_chargers, revised_trips, _ = apply_notice_updates(
        interpretation,
        chargers=chargers,
        trips=trips,
        energy_consumption=energy,
    )

    schedule = revised_chargers.loc[
        revised_chargers["charger_id"] == 2, "power_schedule_kw"
    ].iloc[0]
    assert schedule[6] == 200.0
    assert schedule[7:10] == [0.0, 0.0, 0.0]
    assert schedule[10] == 200.0
    assert revised_trips.loc[0, "time_begin"] == "06:30"
    assert revised_trips.loc[0, "time_end"] == "23:00"

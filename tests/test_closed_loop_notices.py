from __future__ import annotations

import json

from agentic_workflow.notices import NoticeSeries
from agentic_workflow.physical_events import PhysicalEventSeries
from scripts.build_closed_loop_notice_cases import (
    MANIFEST,
    OUTPUT,
    PHYSICAL_OUTPUT,
    build,
    sha256,
)


def test_advance_warning_cases_are_reproducible_and_complete() -> None:
    expected_notices, expected_physical = build()
    notices = json.loads(OUTPUT.read_text(encoding="utf-8"))
    physical = json.loads(PHYSICAL_OUTPUT.read_text(encoding="utf-8"))["events"]
    assert notices == json.loads(json.dumps(expected_notices))
    assert physical == json.loads(json.dumps(expected_physical))
    assert len({row["scenario_id"] for row in notices}) == 3
    assert len(physical) == 3
    assert all(
        row["report_timestep"] < row["canonical"]["effective_timestep"]
        for row in notices
        if row["canonical"]["phase"] in {"warning", "onset"}
    )

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["notice_sha256"] == sha256(OUTPUT)
    assert manifest["physical_event_sha256"] == sha256(PHYSICAL_OUTPUT)
    assert manifest["comparison"] == ["agent", "rule_text", "numerical", "oracle"]
    assert manifest["version"] == "advance_warning_benchmark_v2"
    assert manifest["calibration"]["route_delay_minutes_selected"] == 90
    assert manifest["calibration"]["unavailable_chargers_selected"] == [6, 7, 8]


def test_hidden_truth_is_separate_from_public_trigger_payload() -> None:
    series = NoticeSeries(OUTPUT)
    public = series.records[0].public_dict()
    assert "canonical" not in public
    assert "scenario_id" not in public
    assert "physical_onset_timestep" not in public
    physical = PhysicalEventSeries(PHYSICAL_OUTPUT)
    assert physical.truth_at(7, scenario_ids=("aw_charger_bank_shutdown",)) is None
    truth = physical.truth_at(8, scenario_ids=("aw_charger_bank_shutdown",))
    assert truth is not None
    assert truth.updates.unavailable_chargers == [6, 7, 8]

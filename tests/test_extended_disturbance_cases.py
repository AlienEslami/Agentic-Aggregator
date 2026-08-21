from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agentic_workflow.io import load_disturbances
from agentic_workflow.notices import NoticeSeries
from agentic_workflow.physical_events import PhysicalEventSeries
from scripts.build_extended_disturbance_cases import (
    CLUSTERED_BUSES,
    CLUSTERED_EFFECTIVE,
    ENERGY_BUSES,
    ENERGY_EFFECTIVE,
    ENERGY_MULTIPLIER,
    EXTENDED_PROTOCOL_OUTPUT,
    MANIFEST_OUTPUT,
    NOTICE_OUTPUT,
    PHYSICAL_OUTPUT,
    multistep_price_scenarios,
)

REVISION = Path(__file__).resolve().parents[1] / "inputs" / "revision"
V1_NOTICES = REVISION / "advance_warning_notices_v1.json"
V1_PHYSICAL = REVISION / "advance_warning_physical_events_v1.json"
V1_MANIFEST = REVISION / "advance_warning_manifest_v1.json"
NEW_CASES = ("aw_clustered_late_returns", "aw_extended_energy_shift")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_v1_dataset_is_untouched_by_the_extension():
    manifest = load(V1_MANIFEST)

    assert manifest["notice_sha256"] == hashlib.sha256(
        V1_NOTICES.read_bytes()
    ).hexdigest()
    assert manifest["physical_event_sha256"] == hashlib.sha256(
        V1_PHYSICAL.read_bytes()
    ).hexdigest()


def test_v2_contains_every_v1_notice_unchanged():
    v1 = load(V1_NOTICES)
    v2 = load(NOTICE_OUTPUT)

    assert v2[: len(v1)] == v1
    assert len(v2) > len(v1)


def test_new_cases_announce_before_the_event_becomes_physical():
    series = NoticeSeries(NOTICE_OUTPUT)

    for case in NEW_CASES:
        records = [row for row in series.records if row.scenario_id == case]
        phases = [row.canonical.phase for row in records]
        assert phases[0] == "warning"
        assert "onset" in phases
        assert "persistence" in phases
        warning = records[0]
        # The warning has to arrive strictly before the physical effect, or the
        # case would not test advance information at all.
        assert warning.report_timestep < warning.canonical.effective_timestep
        # A warning must not carry a committed update; only the onset does.
        assert not warning.canonical.updates.return_delay_minutes_by_bus
        assert not warning.canonical.updates.energy_multiplier_by_bus


def test_clustered_case_delays_several_buses_in_one_window():
    truth = PhysicalEventSeries(PHYSICAL_OUTPUT).truth_at(
        CLUSTERED_EFFECTIVE + 1, scenario_ids=("aw_clustered_late_returns",)
    )

    assert truth is not None
    delays = dict(truth.updates.return_delay_minutes_by_bus)
    assert delays == {int(bus): value for bus, value in CLUSTERED_BUSES.items()}
    assert len(delays) >= 3


@pytest.mark.parametrize("timestep", [ENERGY_EFFECTIVE, 30, 48])
def test_energy_shift_persists_to_the_end_of_the_horizon(timestep):
    truth = PhysicalEventSeries(PHYSICAL_OUTPUT).truth_at(
        timestep, scenario_ids=("aw_extended_energy_shift",)
    )

    assert truth is not None
    multipliers = dict(truth.updates.energy_multiplier_by_bus)
    assert multipliers == {int(bus): ENERGY_MULTIPLIER for bus in ENERGY_BUSES}


def test_energy_shift_is_not_active_before_its_effective_timestep():
    truth = PhysicalEventSeries(PHYSICAL_OUTPUT).truth_at(
        ENERGY_EFFECTIVE - 1, scenario_ids=("aw_extended_energy_shift",)
    )

    assert truth is None


def test_price_escalation_is_monotone_and_contiguous():
    frame = multistep_price_scenarios()
    steps = frame[frame["scenario_id"].str.startswith("price_step_up")]
    steps = steps.sort_values("start_timestep")

    levels = list(steps["scenario_level"])
    assert levels == sorted(levels)
    assert len(levels) >= 3
    assert set(steps["scenario_family"]) == {"price_pct"}
    # Contiguous windows: each step begins where the previous one ended.
    ends = list(steps["end_timestep"])
    starts = list(steps["start_timestep"])
    assert all(start == end + 1 for start, end in zip(starts[1:], ends[:-1]))


def test_price_scenarios_load_through_the_workflow_loader(tmp_path):
    # The workbook itself is a build artifact and stays out of version control,
    # so the contract under test is that what the builder emits round-trips
    # through the workflow loader, not that a generated file happens to exist.
    workbook = tmp_path / "scenarios.xlsx"
    multistep_price_scenarios().to_excel(
        workbook, sheet_name="scenarios", index=False
    )

    rows = load_disturbances(
        workbook, ("price_step_up_1", "price_step_up_2", "price_step_up_3")
    )

    assert [row["scenario_level"] for row in rows] == [25, 50, 75]
    assert all(row["scenario_family"] == "price_pct" for row in rows)


def test_extended_manifest_records_the_horizon_limitation():
    manifest = load(MANIFEST_OUTPUT)

    assert manifest["frozen_v1_cases_unchanged"] is True
    assert manifest["notice_sha256"] == hashlib.sha256(
        NOTICE_OUTPUT.read_bytes()
    ).hexdigest()
    assert "multi-day" in manifest["horizon_limitation"]
    recorded = {case["scenario_id"] for case in manifest["cases"]}
    assert set(NEW_CASES).issubset(recorded)


def test_extended_protocol_is_separate_and_has_consistent_run_counts():
    protocol = load(EXTENDED_PROTOCOL_OUTPUT)
    design = protocol["design"]

    assert protocol["protocol_version"] == "advance_warning_ablation_extended_v1"
    assert design["cases"] == list(NEW_CASES)
    assert design["case_mode_cells"] == 4
    assert design["planned_runs"] == 80
    assert design["nonagentic_stack_baseline"]["planned_runs"] == 4
    assert protocol["controls"]["solver_time_limit_seconds"] == 300

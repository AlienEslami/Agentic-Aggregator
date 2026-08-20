from __future__ import annotations

import json

import pandas as pd

from scripts.run_index import merge_run_rows, read_run_index, write_run_index


def row(run_id: str, **extra):
    return {"run_id": run_id, "status": "complete", **extra}


def test_filtered_rerun_keeps_the_episodes_it_did_not_touch(tmp_path):
    write_run_index(
        tmp_path,
        [row("a"), row("b"), row("c")],
        stem="scaling_runs",
    )

    # A run filtered down to one episode must not erase the other two.
    merged = write_run_index(tmp_path, [row("b", note="rerun")], stem="scaling_runs")

    assert [item["run_id"] for item in merged] == ["a", "b", "c"]
    frame = pd.read_csv(tmp_path / "scaling_runs.csv")
    assert len(frame) == 3
    assert set(frame["run_id"]) == {"a", "b", "c"}


def test_a_freshly_indexed_row_supersedes_the_recorded_one(tmp_path):
    write_run_index(tmp_path, [row("a", realized_pto_cost=10.0)], stem="matrix_runs")

    write_run_index(tmp_path, [row("a", realized_pto_cost=20.0)], stem="matrix_runs")

    frame = pd.read_csv(tmp_path / "matrix_runs.csv")
    assert len(frame) == 1
    assert frame.iloc[0]["realized_pto_cost"] == 20.0


def test_json_sidecar_matches_the_csv(tmp_path):
    write_run_index(tmp_path, [row("a"), row("b")], stem="scaling_runs")
    write_run_index(tmp_path, [row("c")], stem="scaling_runs")

    payload = json.loads((tmp_path / "scaling_runs.json").read_text(encoding="utf-8"))

    assert [item["run_id"] for item in payload] == ["a", "b", "c"]
    assert len(payload) == len(pd.read_csv(tmp_path / "scaling_runs.csv"))


def test_rows_without_an_identifier_are_kept_rather_than_collapsed():
    merged = merge_run_rows([{"status": "complete"}], [{"status": "complete"}])

    assert len(merged) == 2


def test_missing_or_unreadable_index_reads_as_empty(tmp_path):
    assert read_run_index(tmp_path / "absent.csv") == []

    broken = tmp_path / "broken.csv"
    broken.write_text("not,a,valid\nindex\n", encoding="utf-8")
    assert read_run_index(broken) == []


def test_new_root_writes_cleanly(tmp_path):
    target = tmp_path / "fresh"

    merged = write_run_index(target, [row("a")], stem="scaling_runs")

    assert [item["run_id"] for item in merged] == ["a"]
    assert (target / "scaling_runs.csv").exists()

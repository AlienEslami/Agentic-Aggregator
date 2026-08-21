from __future__ import annotations

import json

import pandas as pd

from scripts import analyze_multiday_solver_audit as audit_script


def test_build_audit_preserves_time_limited_incumbent(monkeypatch, tmp_path):
    workbook = tmp_path / "day_3.xlsx"
    workbook.write_bytes(b"signed test workbook")
    pd.DataFrame(
        [
            {
                "episode_id": "md_nominal__selfish__scheduled_daily_replan__r001",
                "condition": "nominal",
                "mode": "selfish",
                "configuration": "oracle_event_trigger",
                "method": "scheduled_daily_replan",
                "day": 3,
                "case_id": "md_nominal_day3",
                "workbook": str(workbook),
                "run_signature_sha256": "a" * 64,
            }
        ]
    ).to_csv(tmp_path / "multiday_days.csv", index=False)
    (tmp_path / "multiday_manifest.json").write_text(
        json.dumps(
            {
                "controls": {
                    "solver_mip_gap": 0.02,
                    "solver_time_limit_seconds": 300.0,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit_script.pd,
        "read_excel",
        lambda *_args, **_kwargs: pd.DataFrame(
            [
                {
                    "timestep": 1,
                    "solver_name": "gurobi",
                    "solver_status": "ok/optimal",
                    "solver_relative_gap": 0.019,
                    "solver_fallback_errors": "[]",
                },
                {
                    "timestep": 1,
                    "solver_name": "gurobi",
                    "solver_status": "aborted/maxtimelimit/incumbent",
                    "solver_relative_gap": 0.055,
                    "solver_fallback_errors": "[]",
                },
            ]
        ),
    )

    audit, summary = audit_script.build_audit(tmp_path)

    assert audit["configured_gap_met"].tolist() == [True, False]
    assert audit["solver_fallback_used"].tolist() == [False, False]
    assert int(summary.iloc[0]["optimizer_attempts"]) == 2
    assert int(summary.iloc[0]["configured_gap_met_attempts"]) == 1
    assert int(summary.iloc[0]["time_limited_feasible_incumbent_attempts"]) == 1
    assert float(summary.iloc[0]["maximum_solver_relative_gap"]) == 0.055


def test_fallback_used_handles_empty_and_material_values():
    assert audit_script._fallback_used(None) is False
    assert audit_script._fallback_used("[]") is False
    assert audit_script._fallback_used([]) is False
    assert audit_script._fallback_used('["highs failed"]') is True

"""Publish compact optimizer-status evidence from the multi-day workbooks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = (
    ROOT / "results" / "revision" / "multiday_charger_derating_v1"
)
GAP_TOLERANCE = 1e-9


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _path(value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _fallback_used(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    if bool(pd.isna(value)):
        return False
    return str(value).strip() not in {"", "[]"}


def build_audit(output_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    day_index = pd.read_csv(output_root / "multiday_days.csv")
    manifest = json.loads(
        (output_root / "multiday_manifest.json").read_text(encoding="utf-8")
    )
    gap_target = float(manifest["controls"]["solver_mip_gap"])
    time_limit = float(manifest["controls"]["solver_time_limit_seconds"])
    rows: list[dict[str, Any]] = []
    for day in day_index.itertuples(index=False):
        workbook = _path(day.workbook)
        if not workbook.exists():
            raise FileNotFoundError(f"Missing indexed workbook: {workbook}")
        attempts = pd.read_excel(workbook, sheet_name="optimization_attempts")
        for attempt_number, attempt in enumerate(
            attempts.to_dict(orient="records"), start=1
        ):
            status = str(attempt.get("solver_status") or "")
            gap = pd.to_numeric(attempt.get("solver_relative_gap"), errors="coerce")
            gap_value = None if pd.isna(gap) else float(gap)
            fallback_used = _fallback_used(attempt.get("solver_fallback_errors"))
            target_met = bool(
                status == "ok/optimal"
                and gap_value is not None
                and gap_value <= gap_target + GAP_TOLERANCE
                and not fallback_used
            )
            rows.append(
                {
                    "episode_id": day.episode_id,
                    "condition": day.condition,
                    "mode": day.mode,
                    "configuration": day.configuration,
                    "method": day.method,
                    "day": int(day.day),
                    "case_id": day.case_id,
                    "attempt_number": attempt_number,
                    "timestep": attempt.get("timestep"),
                    "solver_name": attempt.get("solver_name"),
                    "solver_status": status,
                    "solver_relative_gap": gap_value,
                    "configured_mip_gap": gap_target,
                    "configured_time_limit_seconds": time_limit,
                    "configured_gap_met": target_met,
                    "solver_wall_seconds": attempt.get("solver_wall_seconds"),
                    "solver_lower_bound": attempt.get("solver_lower_bound"),
                    "solver_upper_bound": attempt.get("solver_upper_bound"),
                    "solver_fallback_used": fallback_used,
                    "workbook": _display_path(workbook),
                    "workbook_sha256": sha256(workbook),
                    "run_signature_sha256": day.run_signature_sha256,
                }
            )

    audit = pd.DataFrame(rows)
    if audit.empty:
        raise ValueError("No optimizer attempts were found in the indexed workbooks")
    status_counts = audit["solver_status"].value_counts().to_dict()
    summary = pd.DataFrame(
        [
            {
                "daily_workbooks_indexed": int(len(day_index)),
                "optimizer_attempts": int(len(audit)),
                "gurobi_attempts": int(audit["solver_name"].eq("gurobi").sum()),
                "configured_gap_met_attempts": int(
                    audit["configured_gap_met"].sum()
                ),
                "time_limited_feasible_incumbent_attempts": int(
                    audit["solver_status"]
                    .eq("aborted/maxtimelimit/incumbent")
                    .sum()
                ),
                "fallback_attempts": int(audit["solver_fallback_used"].sum()),
                "maximum_solver_relative_gap": float(
                    audit["solver_relative_gap"].max()
                ),
                "status_counts": json.dumps(status_counts, sort_keys=True),
            }
        ]
    )
    return audit, summary


def write_outputs(output_root: Path) -> tuple[Path, Path, Path]:
    audit, summary = build_audit(output_root)
    audit_path = output_root / "multiday_solver_audit.csv"
    summary_path = output_root / "multiday_solver_audit_summary.csv"
    json_path = output_root / "multiday_solver_audit_summary.json"
    audit.to_csv(audit_path, index=False, lineterminator="\n")
    summary.to_csv(summary_path, index=False, lineterminator="\n")
    json_path.write_text(
        summary.to_json(orient="records", indent=2) + "\n", encoding="utf-8"
    )
    return audit_path, summary_path, json_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = _path(args.output_root)
    paths = write_outputs(output_root)
    summary = pd.read_csv(paths[1]).iloc[0]
    print(
        f"wrote {len(paths)} solver-audit artifacts; "
        f"attempts={int(summary.optimizer_attempts)}, "
        f"gap_target_met={int(summary.configured_gap_met_attempts)}, "
        f"time_limited={int(summary.time_limited_feasible_incumbent_attempts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

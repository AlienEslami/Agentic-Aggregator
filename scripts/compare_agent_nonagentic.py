#!/usr/bin/env python3
"""Compare repeated full-Agent episodes with the matched full no-AI loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "aw_route6_late_return",
    "aw_charger_bank_shutdown",
    "aw_combined_evening",
)
MODES = ("selfish", "altruistic")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _truthy(value: Any) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _feasible(row: pd.Series) -> bool:
    return (
        str(row.get("status")) == "complete"
        and int(row.get("timesteps_completed") or 0) == 48
        and float(row.get("maximum_reserve_shortfall_kwh") or 0.0) <= 1e-6
        and int(row.get("reserve_violation_timesteps") or 0) == 0
        and float(row.get("minimum_observed_soc_fraction") or 0.0) >= 0.2 - 1e-9
        and float(row.get("terminal_minimum_soc_fraction") or 0.0) >= 0.2 - 1e-9
    )


def _retention_compliant(row: pd.Series, mode: str) -> bool:
    return mode != "altruistic" or _truthy(
        row.get("baseline_revenue_retention_compliant")
    )


def build_comparison(
    agent_runs: pd.DataFrame,
    baseline_runs: pd.DataFrame,
    *,
    tie_tolerance: float = 0.001,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for case in CASES:
        for mode in MODES:
            agent = agent_runs.loc[
                (agent_runs["configuration"] == "full_agentic")
                & (agent_runs["case"] == case)
                & (agent_runs["mode"] == mode)
            ].copy()
            baseline = baseline_runs.loc[
                (baseline_runs["configuration"] == "full_deterministic")
                & (baseline_runs["case"] == case)
                & (baseline_runs["mode"] == mode)
            ].copy()
            if len(agent) != 5:
                raise ValueError(
                    f"Expected five full-Agent repetitions for {case}/{mode}; "
                    f"found {len(agent)}"
                )
            if len(baseline) != 1:
                raise ValueError(
                    f"Expected one deterministic baseline for {case}/{mode}; "
                    f"found {len(baseline)}"
                )

            metric = (
                "realized_aggregator_revenue"
                if mode == "selfish"
                else "realized_pto_cost"
            )
            agent_values = pd.to_numeric(agent[metric], errors="raise")
            baseline_value = float(baseline.iloc[0][metric])
            agent_feasible = agent.apply(_feasible, axis=1)
            baseline_feasible = _feasible(baseline.iloc[0])
            agent_floor = agent.apply(
                lambda row: _retention_compliant(row, mode), axis=1
            )
            baseline_floor = _retention_compliant(baseline.iloc[0], mode)
            eligible = (
                baseline_feasible
                and bool(agent_feasible.all())
                and baseline_floor
                and bool(agent_floor.all())
            )
            gain_by_repetition = (
                agent_values - baseline_value
                if mode == "selfish"
                else baseline_value - agent_values
            )
            agent_mean = float(agent_values.mean())
            mean_gain = (
                agent_mean - baseline_value
                if mode == "selfish"
                else baseline_value - agent_mean
            )

            if bool(agent_feasible.all()) and not baseline_feasible:
                outcome = "agent_win_on_feasibility"
            elif baseline_feasible and not bool(agent_feasible.all()):
                outcome = "agent_loss_on_feasibility"
            elif mode == "altruistic" and bool(agent_floor.all()) and not baseline_floor:
                outcome = "agent_win_on_retention"
            elif mode == "altruistic" and baseline_floor and not bool(agent_floor.all()):
                outcome = "agent_loss_on_retention"
            elif not eligible:
                outcome = "comparison_ineligible"
            elif mean_gain > tie_tolerance:
                outcome = "agent_win"
            elif mean_gain < -tie_tolerance:
                outcome = "agent_loss"
            else:
                outcome = "tie"

            rows.append(
                {
                    "case": case,
                    "mode": mode,
                    "metric": metric,
                    "agent_runs": int(len(agent)),
                    "baseline_runs": int(len(baseline)),
                    "agent_mean": agent_mean,
                    "agent_median": float(agent_values.median()),
                    "agent_min": float(agent_values.min()),
                    "agent_max": float(agent_values.max()),
                    "baseline_value": baseline_value,
                    "economic_comparison_eligible": eligible,
                    "mode_aligned_gain": mean_gain if eligible else None,
                    "gain_percent_of_baseline": (
                        100.0 * mean_gain / abs(baseline_value)
                        if eligible and abs(baseline_value) > 1e-12
                        else None
                    ),
                    "outcome": outcome,
                    "agent_wins": int((gain_by_repetition > tie_tolerance).sum())
                    if eligible
                    else None,
                    "ties": int((gain_by_repetition.abs() <= tie_tolerance).sum())
                    if eligible
                    else None,
                    "agent_losses": int((gain_by_repetition < -tie_tolerance).sum())
                    if eligible
                    else None,
                    "agent_feasible_rate": float(agent_feasible.mean()),
                    "baseline_feasible": baseline_feasible,
                    "agent_floor_rate": float(agent_floor.mean()),
                    "baseline_floor": baseline_floor,
                }
            )
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selfish-agent-runs",
        type=Path,
        default=Path("results/revision/selfish_5rep/matrix_runs.csv"),
    )
    parser.add_argument(
        "--altruistic-agent-runs",
        type=Path,
        default=Path(
            "results/revision/altruistic_50pct_retention_5rep/matrix_runs.csv"
        ),
    )
    parser.add_argument("--baseline-runs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tie-tolerance", type=float, default=0.001)
    args = parser.parse_args(argv)

    paths = {
        "selfish_agent_runs": args.selfish_agent_runs,
        "altruistic_agent_runs": args.altruistic_agent_runs,
        "baseline_runs": args.baseline_runs,
    }
    paths = {
        name: path if path.is_absolute() else ROOT / path
        for name, path in paths.items()
    }
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    agent = pd.concat(
        [
            pd.read_csv(paths["selfish_agent_runs"]),
            pd.read_csv(paths["altruistic_agent_runs"]),
        ],
        ignore_index=True,
    )
    baseline = pd.read_csv(paths["baseline_runs"])
    comparison = build_comparison(
        agent, baseline, tie_tolerance=args.tie_tolerance
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(output_dir / "agent_vs_full_no_ai_comparison.csv", index=False)
    combined = pd.concat(
        [
            agent.loc[agent["configuration"] == "full_agentic"],
            baseline.loc[baseline["configuration"] == "full_deterministic"],
        ],
        ignore_index=True,
    )
    combined.to_csv(output_dir / "agentic_vs_nonagentic_runs.csv", index=False)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_revision(),
        "tie_tolerance": args.tie_tolerance,
        "positive_mode_aligned_gain_favors": "full_agentic",
        "sources": {
            name: {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": _sha256(path),
            }
            for name, path in paths.items()
        },
        "rows": comparison.to_dict(orient="records"),
    }
    (output_dir / "agent_vs_full_no_ai_comparison.json").write_text(
        json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(comparison.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

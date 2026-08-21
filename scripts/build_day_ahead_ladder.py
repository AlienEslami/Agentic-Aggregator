"""Freeze the day-ahead comparison ladder in one reproducible pass.

The ladder separates three effects that the original S1-to-S4 progression
conflated: the value of optimization, the value of V2G, and the value of
agentic coordination. It runs the three non-agentic day-ahead scenarios under
fixed solver settings and records their provenance.

Basis note: values are settled on the optimizer-native basis: interval ``t``
energy is paired with interval ``t`` tariffs.  The legacy manuscript's
SOC-derived values used the previous interval tariff because of an off-by-one
reporting error; ``scripts/reconcile_day_ahead_costs.py`` documents it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCENARIOS = (
    (
        "S1_dumb_charging",
        "run_dumb_charging.py",
        [],
        "uncontrolled charging, no V2G; earliest-charging tie-break",
    ),
    (
        "S2_smart_no_v2g",
        "run_no_v2g_optimization.py",
        [],
        "optimized charging, no V2G, spot passthrough",
    ),
    (
        "S2p5_v2g_fixed_margin",
        "run_nonagentic_v2g_optimization.py",
        ["--tariff-policy", "fixed_margin", "--tariffs-file", "data/inputs/aggregator_tariffs.xlsx"],
        "V2G with a constant regulated tariff band and no agents",
    ),
    (
        "S2p5_v2g_passthrough",
        "run_nonagentic_v2g_optimization.py",
        ["--tariff-policy", "passthrough"],
        "V2G at spot on both sides: no aggregator margin, a no-aggregator counterfactual",
    ),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/inputs/case_study_inputs.xlsx")
    parser.add_argument("--spot-prices-file", default="data/inputs/spot_prices.xlsx")
    parser.add_argument(
        "--output-root", type=Path, default=Path("results/revision/day_ahead_ladder")
    )
    parser.add_argument(
        "--mip-gap",
        default="0.000001",
        help=(
            "Gap for the priced day-ahead models. The passthrough scenario is "
            "degenerate at a zero tariff spread and may hit the time limit at a "
            "tight gap; it falls back to --passthrough-mip-gap."
        ),
    )
    parser.add_argument("--passthrough-mip-gap", default="0.04")
    parser.add_argument("--solver-order", default="gurobi")
    args = parser.parse_args()

    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    output_root.mkdir(parents=True, exist_ok=True)
    summary_workbook = output_root / "day_ahead_ladder_comparison.xlsx"
    if not summary_workbook.exists():
        summary_workbook.write_bytes((ROOT / args.input).read_bytes())

    rows = []
    for name, script, extra, description in SCENARIOS:
        result_path = output_root / f"{name}.json"
        environment = dict(os.environ)
        environment["DA_SOLVER_ORDER"] = args.solver_order
        environment["DA_DUMB_CHARGING_MIP_GAP"] = "0.0"
        environment["DA_SOLVER_MIP_GAP"] = (
            args.passthrough_mip_gap if "passthrough" in name else args.mip_gap
        )
        command = [
            sys.executable,
            script,
            "--input",
            args.input,
            "--spot-prices-file",
            args.spot_prices_file,
            "--output",
            str(result_path),
            "--summary-workbook",
            str(summary_workbook),
            *extra,
        ]
        print(f"[{name}] {' '.join(command[1:3])} ...")
        subprocess.run(command, cwd=ROOT, check=True, env=environment)
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        rows.append(
            {
                "scenario": name,
                "description": description,
                "mip_gap": environment["DA_SOLVER_MIP_GAP"],
                "pto_daily_cost": payload["pto_daily_cost"],
                "total_kwh_bought": payload["total_kwh_bought"],
                "total_kwh_sold": payload.get("total_kwh_sold", 0.0),
                "result_file": result_path.name,
                "result_sha256": sha256(result_path),
            }
        )

    baseline = next(r for r in rows if r["scenario"] == "S1_dumb_charging")
    smart = next(r for r in rows if r["scenario"] == "S2_smart_no_v2g")
    manifest = {
        "ladder_version": "day_ahead_ladder_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"),
        "git_worktree_clean": not (git("status", "--porcelain") or ""),
        "basis": "optimizer-native same-interval settlement",
        "basis_definition": (
            "Timestep t energy is settled at timestep t price and multipliers."
        ),
        "solver": {
            "order": args.solver_order,
            "priced_mip_gap": args.mip_gap,
            "passthrough_mip_gap": args.passthrough_mip_gap,
            "dumb_charging_mip_gap": "0.0",
            "tie_break": "earliest charging over the benchmark-optimal face",
        },
        "inputs": {
            "case_study": args.input,
            "case_study_sha256": sha256(ROOT / args.input),
            "spot_prices": args.spot_prices_file,
            "spot_prices_sha256": sha256(ROOT / args.spot_prices_file),
        },
        "scenarios": rows,
        "optimization_saving_vs_dumb_charging_pct": round(
            100 * (1 - smart["pto_daily_cost"] / baseline["pto_daily_cost"]), 4
        ),
    }
    manifest_path = output_root / "ladder_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    width = max(len(r["scenario"]) for r in rows)
    print()
    print(f"{'scenario'.ljust(width)}  {'PTO cost':>10}  {'bought':>8}  {'sold':>7}")
    for row in rows:
        print(
            f"{row['scenario'].ljust(width)}  {row['pto_daily_cost']:10.4f}  "
            f"{row['total_kwh_bought']:8.1f}  {row['total_kwh_sold']:7.1f}"
        )
    print()
    print(
        f"optimization saving vs dumb charging: "
        f"{manifest['optimization_saving_vs_dumb_charging_pct']:.2f}%"
    )
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Record that the corrected S1 baseline is solver-invariant.

The manuscript states that before the earliest-charging tie-break the
dumb-charging baseline varied by roughly ten percent between solvers, and that
with the tie-break Gurobi and HiGHS return the same value. This script is the
artifact behind that sentence: it solves the S1 scenario with each available
solver at a zero gap and writes the costs, together with input hashes and git
provenance, so the claim is auditable rather than anecdotal.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOLVERS = ("gurobi", "appsi_highs")
INPUT = ROOT / "data" / "inputs" / "case_study_inputs.xlsx"
SPOT = ROOT / "data" / "inputs" / "spot_prices.xlsx"
OUTPUT_ROOT = ROOT / "results" / "revision" / "s1_solver_invariance_v1"


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
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for solver in SOLVERS:
        result_path = OUTPUT_ROOT / f"s1_{solver}.json"
        summary_copy = OUTPUT_ROOT / f"summary_{solver}.xlsx"
        summary_copy.write_bytes(INPUT.read_bytes())
        environment = dict(os.environ)
        environment["DA_SOLVER_ORDER"] = solver
        environment["DA_DUMB_CHARGING_MIP_GAP"] = "0.0"
        subprocess.run(
            [
                sys.executable,
                "run_dumb_charging.py",
                "--input", str(INPUT),
                "--spot-prices-file", str(SPOT),
                "--output", str(result_path),
                "--summary-workbook", str(summary_copy),
            ],
            cwd=ROOT,
            check=True,
            env=environment,
        )
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        rows.append(
            {
                "solver": solver,
                "pto_daily_cost": payload["pto_daily_cost"],
                "total_kwh_bought": payload["total_kwh_bought"],
                "result_sha256": sha256(result_path),
            }
        )
        summary_copy.unlink()

    costs = [row["pto_daily_cost"] for row in rows]
    manifest = {
        "artifact": "s1_solver_invariance_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"),
        # -uno: the artifact's own freshly written outputs are untracked at
        # run time and must not count against provenance; what matters is that
        # no tracked file differs from the recorded commit.
        "git_tracked_files_clean": not (git("status", "--porcelain", "-uno") or ""),
        "tie_break": "earliest charging over the benchmark-optimal face, gap 0",
        "inputs": {
            "case_study_sha256": sha256(INPUT),
            "spot_prices_sha256": sha256(SPOT),
        },
        "solvers": rows,
        "max_absolute_cost_difference": round(max(costs) - min(costs), 9),
        "claim_backed": (
            "With the earliest-charging tie-break, every available solver "
            "returns the same S1 baseline cost."
        ),
    }
    (OUTPUT_ROOT / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    for row in rows:
        print(f"{row['solver']:12s} {row['pto_daily_cost']:.6f}")
    print(f"diferenca maxima: {manifest['max_absolute_cost_difference']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

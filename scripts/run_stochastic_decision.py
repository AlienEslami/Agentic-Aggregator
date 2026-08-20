#!/usr/bin/env python3
"""Run one auditable rolling two-stage stochastic decision point.

The input payload uses the same contract as ``app_rt.py``.  Scenario sets are
kept in a separate frozen JSON file so the benchmark cannot inspect canonical
hidden truth while it is solving.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app_rt  # noqa: E402
from agentic_workflow.stochastic_programming import (  # noqa: E402
    scenarios_from_definitions,
    solve_two_stage_stochastic,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
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


def _localize_updates(
    definitions: list[dict[str, Any]], current_timestep: int
) -> list[dict[str, Any]]:
    localized = json.loads(json.dumps(definitions))
    for definition in localized:
        updates = definition.get("future_updates") or {}
        if "price_multiplier_end_absolute_timestep" in updates:
            updates["price_multiplier_end_timestep"] = (
                int(updates.pop("price_multiplier_end_absolute_timestep"))
                - current_timestep
                + 1
            )
        for window in updates.get("charger_power_windows") or []:
            if "absolute_timestep_start" in window:
                window["timestep_start"] = (
                    int(window.pop("absolute_timestep_start"))
                    - current_timestep
                    + 1
                )
            if "absolute_timestep_end" in window:
                window["timestep_end"] = (
                    int(window.pop("absolute_timestep_end"))
                    - current_timestep
                    + 1
                )
    return localized


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--solver", default="gurobi")
    parser.add_argument("--time-limit", type=float, default=300.0)
    parser.add_argument("--mip-gap", type=float, default=0.02)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    cases = {str(item["case_id"]): item for item in protocol["cases"]}
    if args.case not in cases:
        raise SystemExit(f"Unknown stochastic case: {args.case}")
    case = cases[args.case]
    current_timestep = int(payload.get("current_timestep", 1))
    reveal_absolute = int(case["reveal_absolute_timestep"])
    reveal_local = reveal_absolute - current_timestep + 1

    data = app_rt.build_dataframes(payload["input"])
    base_context = app_rt.build_rt_context(
        data,
        payload.get("price_guidance", {}),
        current_timestep,
        payload.get("disturbances", []),
    )
    definitions = _localize_updates(case["scenarios"], current_timestep)
    scenarios = scenarios_from_definitions(
        base_context, definitions, reveal_timestep=reveal_local
    )
    manifest = {
        "protocol_version": protocol["protocol_version"],
        "case_id": args.case,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_revision(),
        "payload_sha256": _sha256(args.payload),
        "protocol_sha256": _sha256(args.protocol),
        "current_timestep": current_timestep,
        "reveal_absolute_timestep": reveal_absolute,
        "reveal_local_timestep": reveal_local,
        "scenario_ids": [scenario.scenario_id for scenario in scenarios],
        "scenario_probabilities": {
            scenario.scenario_id: scenario.probability for scenario in scenarios
        },
        "external_llm_used": False,
        "canonical_hidden_truth_used": False,
    }
    if args.dry_run:
        result = {"status": "validated_dry_run"}
    else:
        result = solve_two_stage_stochastic(
            scenarios,
            reveal_timestep=reveal_local,
            solver_name=args.solver,
            time_limit_seconds=args.time_limit,
            mip_gap=args.mip_gap,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"manifest": manifest, "result": result}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "status": result["status"]}))


if __name__ == "__main__":
    main()

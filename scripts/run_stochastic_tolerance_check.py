#!/usr/bin/env python3
"""Diagnostic re-solve of the stochastic benchmark at a 0.1% MIP gap.

The published stochastic results run at the frozen 2% gap. This check re-runs
the deterministic altruistic cells at a much tighter gap to verify that the
sub-tolerance differences reported as equivalent in the manuscript are not
artifacts of the solver tolerance. It is a diagnostic outside the frozen
protocol and its outputs live in their own folder; the published v4 artifacts
are not touched.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_workflow.config import WorkflowConfig
from agentic_workflow.runner import WorkflowRunner
from agentic_workflow.stochastic_benchmark import (
    EventRecedingStochasticAgentBackend,
    EventRecedingStochasticOptimizerBackend,
    load_stochastic_case,
    load_stochastic_protocol,
)


def _load_v4_module():
    spec = importlib.util.spec_from_file_location(
        "run_stochastic_closed_loop", ROOT / "scripts" / "run_stochastic_closed_loop.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


V4 = _load_v4_module()
PROTOCOL = ROOT / "inputs" / "revision" / "stochastic_benchmark_protocol_v4.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/revision/tolerance_check_v1/stochastic"),
    )
    parser.add_argument("--mip-gap", type=float, default=0.001)
    parser.add_argument("--time-limit", type=float, default=300.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    output_root = (
        args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    )
    os.environ["RT_SOLVER_ORDER"] = "gurobi"
    os.environ["RT_SOLVER_TIME_LIMIT"] = str(args.time_limit)
    os.environ["RT_SOLVER_MIP_GAP"] = str(args.mip_gap)

    rows: list[dict[str, Any]] = []
    for case_id in V4.CASES:
        case = load_stochastic_case(PROTOCOL, case_id)
        workbook = output_root / case_id / "altruistic" / "stochastic_programming.xlsx"
        if args.force or not workbook.exists():
            config = WorkflowConfig(
                state_workbook=ROOT / "inputs" / "State.xlsx",
                forecast_workbook=ROOT / "inputs" / "Forecasted.xlsx",
                spot_prices_workbook=ROOT / "inputs" / "SpotPrices.xlsx",
                realtime_states=ROOT / "inputs" / "realtime_states",
                intraday_prices=ROOT / "inputs" / "intraday_prices",
                disturbance_workbook=(
                    ROOT / "inputs" / "rt_disturbance_scenarios_multiple.xlsx"
                ),
                output_workbook=workbook,
                notices_file=(
                    ROOT / "inputs" / "revision" / "advance_warning_notices_v1.json"
                ),
                physical_events_file=(
                    ROOT
                    / "inputs"
                    / "revision"
                    / "advance_warning_physical_events_v1.json"
                ),
                notice_scenario_ids=(case_id,),
                notice_variant="uncertain_chat",
                mode="altruistic",
                altruistic_revenue_retention_fraction=0.5,
                scenario_ids=("rt_none",),
                start_timestep=1,
                end_timestep=48,
                agent_backend="rule",
                experiment_configuration="stochastic_programming",  # type: ignore[arg-type]
                notice_path="none",
                realize_notice_truth=True,
                optimizer_backend="direct",
                max_reruns=1,
                checkpoint_every=48,
                metadata={
                    "tolerance_check": "true",
                    "tolerance_check_mip_gap": str(args.mip_gap),
                    "reference_protocol_version": "event_receding_two_stage_stochastic_v4",
                    "external_llm_used": "false",
                },
            )
            WorkflowRunner(
                config,
                agents=EventRecedingStochasticAgentBackend(case),
                optimizer=EventRecedingStochasticOptimizerBackend(
                    case,
                    solver_name="gurobi",
                    time_limit_seconds=args.time_limit,
                    mip_gap=args.mip_gap,
                ),
            ).run()
        tight = V4._summary(workbook)
        published = V4._summary(
            ROOT
            / "results"
            / "revision"
            / "stochastic_v4"
            / case_id
            / "altruistic"
            / "stochastic_programming.xlsx"
        )
        row = {
            "case": case_id,
            "mode": "altruistic",
            "published_gap": 0.02,
            "check_gap": args.mip_gap,
            "published_realized_pto_cost": float(published["realized_pto_cost"]),
            "check_realized_pto_cost": float(tight["realized_pto_cost"]),
            "pto_cost_delta": float(tight["realized_pto_cost"])
            - float(published["realized_pto_cost"]),
            "published_realized_aggregator_revenue": float(
                published["realized_aggregator_revenue"]
            ),
            "check_realized_aggregator_revenue": float(
                tight["realized_aggregator_revenue"]
            ),
        }
        rows.append(row)
        print(
            f"{case_id}: published {row['published_realized_pto_cost']:.6f} "
            f"vs 0.1%-gap {row['check_realized_pto_cost']:.6f} "
            f"(delta {row['pto_cost_delta']:+.6f})",
            flush=True,
        )

    output_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output_root / "tolerance_check.csv", index=False)
    (output_root / "manifest.json").write_text(
        json.dumps(
            {
                "purpose": (
                    "diagnostic re-solve of the deterministic stochastic cells at "
                    "a 0.1% MIP gap, supporting the equivalence labels of the "
                    "manuscript comparison tables"
                ),
                "reference_results": "results/revision/stochastic_v4",
                "git_commit": V4._git_revision(),
                "generated_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

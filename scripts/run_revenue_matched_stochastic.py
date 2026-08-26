#!/usr/bin/env python3
"""Run the revenue-matched (v5) stochastic benchmark for the selfish mode.

For every advance-warning case, each tariff posture in the frozen candidate
grid is executed as a complete deterministic episode. The posture is then
selected ex ante, by the projected full-day aggregator revenue of the first
accepted optimization decision (an expectation over the frozen scenario set
plus the settled prefix), and the selected episode's realized outcome is the
benchmark result. Altruistic cells are inherited from v4, whose objective is
already matched to that mode.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import shutil
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
from agentic_workflow.revenue_matched_stochastic import (
    PostureShiftAgentBackend,
    posture_grid,
    selection_record,
)
from agentic_workflow.stochastic_benchmark import (
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
CASES = V4.CASES
PROTOCOL = ROOT / "inputs" / "revision" / "stochastic_benchmark_protocol_v5.json"


def _candidate_tag(candidate: dict[str, float]) -> str:
    buy = f"{candidate['buy_shift']:+.2f}".replace("+", "p").replace("-", "m").replace(".", "")
    sell = f"{candidate['sell_shift']:+.2f}".replace("+", "p").replace("-", "m").replace(".", "")
    return f"buy{buy}_sell{sell}"


def _first_accepted_projection(workbook: Path) -> dict[str, Any]:
    attempts = pd.read_excel(workbook, sheet_name="optimization_attempts")
    accepted = attempts.loc[attempts["accepted"].astype(bool)]
    if accepted.empty:
        raise RuntimeError(f"No accepted optimization decision in {workbook}")
    first = accepted.sort_values("timestep").iloc[0]
    return {
        "decision_timestep": int(first["timestep"]),
        "projected_full_day_aggregator_revenue": float(
            first["projected_full_day_aggregator_revenue"]
        ),
        "projected_full_day_pto_cost": float(first["projected_full_day_pto_cost"]),
    }


def _run_episode(
    *,
    case_id: str,
    case: dict[str, Any],
    workbook: Path,
    candidate: dict[str, float],
    protocol: dict[str, Any],
    protocol_path: Path,
    time_limit: float,
    mip_gap: float,
) -> None:
    config = WorkflowConfig(
        state_workbook=ROOT / "inputs" / "State.xlsx",
        forecast_workbook=ROOT / "inputs" / "Forecasted.xlsx",
        spot_prices_workbook=ROOT / "inputs" / "SpotPrices.xlsx",
        realtime_states=ROOT / "inputs" / "realtime_states",
        intraday_prices=ROOT / "inputs" / "intraday_prices",
        disturbance_workbook=ROOT / "inputs" / "rt_disturbance_scenarios_multiple.xlsx",
        output_workbook=workbook,
        notices_file=ROOT / "inputs" / "revision" / "advance_warning_notices_v1.json",
        physical_events_file=(
            ROOT / "inputs" / "revision" / "advance_warning_physical_events_v1.json"
        ),
        notice_scenario_ids=(case_id,),
        notice_variant="uncertain_chat",
        mode="selfish",
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
            "stochastic_protocol_version": protocol["protocol_version"],
            "stochastic_protocol_sha256": V4._sha256(protocol_path),
            "external_llm_used": "false",
            "revenue_matched_buy_shift": str(candidate["buy_shift"]),
            "revenue_matched_sell_shift": str(candidate["sell_shift"]),
        },
    )
    WorkflowRunner(
        config,
        agents=PostureShiftAgentBackend(
            case,
            buy_shift=candidate["buy_shift"],
            sell_shift=candidate["sell_shift"],
        ),
        optimizer=EventRecedingStochasticOptimizerBackend(
            case,
            solver_name="gurobi",
            time_limit_seconds=time_limit,
            mip_gap=mip_gap,
        ),
    ).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    parser.add_argument(
        "--output-root", type=Path, default=Path("results/revision/stochastic_v5")
    )
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--time-limit", type=float, default=300.0)
    parser.add_argument("--mip-gap", type=float, default=0.02)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    protocol_path = (
        args.protocol if args.protocol.is_absolute() else ROOT / args.protocol
    )
    protocol = load_stochastic_protocol(protocol_path)
    frozen_limit = float(protocol["method"]["solver_time_limit_seconds"])
    frozen_gap = float(protocol["method"]["solver_mip_gap"])
    if abs(args.time_limit - frozen_limit) > 1e-12:
        raise SystemExit(
            f"--time-limit must match the frozen protocol ({frozen_limit:g} seconds)"
        )
    if abs(args.mip_gap - frozen_gap) > 1e-12:
        raise SystemExit(f"--mip-gap must match the frozen protocol ({frozen_gap:g})")
    if protocol["information_contract"]["external_llm"] is not False:
        raise SystemExit("Stochastic protocol must explicitly disable external LLM use")
    if protocol["method"]["applies_to_modes"] != ["selfish"]:
        raise SystemExit("The v5 revenue-matched protocol applies to selfish mode only")

    import os

    os.environ["RT_SOLVER_ORDER"] = "gurobi"
    os.environ["RT_SOLVER_TIME_LIMIT"] = str(args.time_limit)
    os.environ["RT_SOLVER_MIP_GAP"] = str(args.mip_gap)

    output_root = (
        args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    )
    selected_cases = tuple(args.case or CASES)
    candidates = posture_grid(protocol)
    rows: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []

    for case_id in selected_cases:
        case = load_stochastic_case(protocol_path, case_id)
        evaluated: list[dict[str, Any]] = []
        for candidate in candidates:
            tag = _candidate_tag(candidate)
            workbook = (
                output_root
                / "selection"
                / case_id
                / tag
                / "stochastic_programming.xlsx"
            )
            if args.force or not workbook.exists():
                _run_episode(
                    case_id=case_id,
                    case=case,
                    workbook=workbook,
                    candidate=candidate,
                    protocol=protocol,
                    protocol_path=protocol_path,
                    time_limit=args.time_limit,
                    mip_gap=args.mip_gap,
                )
            projection = _first_accepted_projection(workbook)
            audit = V4._gurobi_audit(workbook)
            if audit["solver_names"] != ["gurobi"]:
                raise RuntimeError(
                    f"Non-Gurobi solver provenance in {workbook}: {audit['solver_names']}"
                )
            summary = V4._summary(workbook)
            if int(summary.get("llm_request_attempts") or 0) != 0:
                raise RuntimeError(f"Unexpected LLM request recorded in {workbook}")
            evaluated.append(
                {
                    **candidate,
                    "tag": tag,
                    **projection,
                    "realized_aggregator_revenue": float(
                        summary.get("realized_aggregator_revenue") or 0.0
                    ),
                    "workbook": str(workbook.relative_to(ROOT)).replace("\\", "/"),
                }
            )
            print(
                f"{case_id} {tag}: projected revenue "
                f"{projection['projected_full_day_aggregator_revenue']:.6f}",
                flush=True,
            )
        winner = max(
            evaluated,
            key=lambda row: (
                row["projected_full_day_aggregator_revenue"],
                -row["buy_shift"],
                -abs(row["sell_shift"]),
            ),
        )
        selections.append(
            selection_record(case_id=case_id, candidates=evaluated, winner=winner)
        )
        final_workbook = output_root / case_id / "selfish" / "stochastic_programming.xlsx"
        final_workbook.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / winner["workbook"], final_workbook)
        summary = V4._summary(final_workbook)
        audit = V4._gurobi_audit(final_workbook)
        rows.append(
            V4._comparison_row(
                case=case_id, mode="selfish", stochastic=summary, workbook=final_workbook
            )
            | audit
            | {
                "selected_buy_shift": winner["buy_shift"],
                "selected_sell_shift": winner["sell_shift"],
            }
        )
        print(
            f"{case_id}/selfish: {rows[-1]['outcome']} "
            f"(Agent={rows[-1]['agent_mean']:.6f}, "
            f"stochastic={rows[-1]['stochastic_value']:.6f}, "
            f"posture buy {winner['buy_shift']:+.2f} / sell {winner['sell_shift']:+.2f})",
            flush=True,
        )

    output_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(
        output_root / "agent_vs_stochastic_comparison.csv", index=False
    )
    (output_root / "posture_selection.json").write_text(
        json.dumps(selections, indent=2) + "\n", encoding="utf-8"
    )
    protocol_dependencies = [protocol_path]
    raw = json.loads(protocol_path.read_text(encoding="utf-8"))
    while raw.get("base_protocol_file"):
        dependency = protocol_path.parent / str(raw["base_protocol_file"])
        protocol_dependencies.append(dependency)
        raw = json.loads(dependency.read_text(encoding="utf-8"))
    manifest = {
        "protocol_version": protocol["protocol_version"],
        "protocol_file": str(protocol_path.relative_to(ROOT)).replace("\\", "/"),
        "protocol_dependency_sha256": {
            str(path.relative_to(ROOT)).replace("\\", "/"): V4._sha256(path)
            for path in protocol_dependencies
        },
        "git_commit": V4._git_revision(),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_grid_size": len(candidates),
        "cases": list(selected_cases),
        "modes": ["selfish"],
        "altruistic_results_policy": (
            "inherited from results/revision/stochastic_v4, whose objective is "
            "already matched to the altruistic mode"
        ),
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

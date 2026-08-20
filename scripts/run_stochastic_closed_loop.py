#!/usr/bin/env python3
"""Run and compare the no-API stochastic programmer over full-day episodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
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


PROTOCOL = ROOT / "inputs" / "revision" / "stochastic_benchmark_protocol_v3.json"
CASES = (
    "aw_route6_late_return",
    "aw_charger_bank_shutdown",
    "aw_combined_evening",
)
MODES = ("selfish", "altruistic")
AGENT_RESULT_FILES = {
    "selfish": ROOT / "results" / "revision" / "selfish_5rep" / "matrix_runs.csv",
    "altruistic": (
        ROOT
        / "results"
        / "revision"
        / "altruistic_50pct_retention_5rep"
        / "matrix_runs.csv"
    ),
}


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


def _summary(workbook: Path) -> dict[str, Any]:
    return pd.read_excel(workbook, sheet_name="run_summary").iloc[0].to_dict()


def _gurobi_audit(workbook: Path) -> dict[str, Any]:
    attempts = pd.read_excel(workbook, sheet_name="optimization_attempts")
    solver_names = sorted(
        attempts.get("solver_name", pd.Series(dtype=str))
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )
    statuses = sorted(
        attempts.get("solver_status", pd.Series(dtype=str))
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )
    return {
        "solver_names": solver_names,
        "solver_statuses": statuses,
        "solver_wall_seconds": float(
            pd.to_numeric(
                attempts.get("solver_wall_seconds", pd.Series(dtype=float)),
                errors="coerce",
            ).sum()
        ),
        "optimizer_attempts": int(len(attempts)),
    }


def _operationally_feasible(row: dict[str, Any]) -> bool:
    return (
        row.get("status") == "complete"
        and int(row.get("timesteps_completed") or 0) == 48
        and float(row.get("maximum_reserve_shortfall_kwh") or 0.0) <= 1e-6
        and int(row.get("reserve_violation_timesteps") or 0) == 0
        and float(row.get("minimum_observed_soc_fraction") or 0.0) >= 0.2 - 1e-9
        and float(row.get("terminal_minimum_soc_fraction") or 0.0) >= 0.2 - 1e-9
    )


def _agent_rows(mode: str, case: str) -> pd.DataFrame:
    frame = pd.read_csv(AGENT_RESULT_FILES[mode])
    return frame.loc[
        (frame["configuration"] == "full_agentic")
        & (frame["case"] == case)
        & (frame["mode"] == mode)
    ].copy()


def _comparison_row(
    *, case: str, mode: str, stochastic: dict[str, Any], workbook: Path
) -> dict[str, Any]:
    agents = _agent_rows(mode, case)
    if len(agents) != 5:
        raise ValueError(
            f"Expected five Agent repetitions for {case}/{mode}; found {len(agents)}"
        )
    agent_feasible = agents.apply(
        lambda row: _operationally_feasible(row.to_dict()), axis=1
    )
    agent_all_feasible = bool(agent_feasible.all())
    stochastic_feasible = _operationally_feasible(stochastic)
    agent_retention = pd.Series([True] * len(agents), index=agents.index)
    if mode == "altruistic":
        agent_retention = agents[
            "baseline_revenue_retention_compliant"
        ].fillna(False).astype(bool)
    stochastic_retention = bool(
        stochastic.get("baseline_revenue_retention_compliant")
    )
    metric = (
        "realized_aggregator_revenue"
        if mode == "selfish"
        else "realized_pto_cost"
    )
    agent_values = pd.to_numeric(agents[metric], errors="raise")
    agent_mean = float(agent_values.mean())
    stochastic_value = float(stochastic[metric])
    agent_advantage = (
        agent_mean - stochastic_value
        if mode == "selfish"
        else stochastic_value - agent_mean
    )
    tolerance = 0.001
    if agent_all_feasible and not stochastic_feasible:
        outcome = "agent_win_on_feasibility"
    elif stochastic_feasible and not agent_all_feasible:
        outcome = "agent_loss_on_feasibility"
    elif not stochastic_feasible and not agent_all_feasible:
        outcome = "both_infeasible"
    elif mode == "altruistic" and bool(agent_retention.all()) and not stochastic_retention:
        outcome = "agent_win_on_retention"
    elif mode == "altruistic" and stochastic_retention and not bool(agent_retention.all()):
        outcome = "agent_loss_on_retention"
    elif mode == "altruistic" and not stochastic_retention and not bool(agent_retention.all()):
        outcome = "both_retention_noncompliant"
    elif agent_advantage > tolerance:
        outcome = "agent_win"
    elif agent_advantage < -tolerance:
        outcome = "agent_loss"
    else:
        outcome = "tie"
    return {
        "case": case,
        "mode": mode,
        "primary_metric": metric,
        "better_direction": "higher" if mode == "selfish" else "lower",
        "agent_repetitions": int(len(agents)),
        "agent_feasible_repetitions": int(agent_feasible.sum()),
        "agent_retention_compliant_repetitions": int(agent_retention.sum()),
        "stochastic_operationally_feasible": stochastic_feasible,
        "agent_mean": agent_mean,
        "agent_median": float(agent_values.median()),
        "agent_min": float(agent_values.min()),
        "agent_max": float(agent_values.max()),
        "stochastic_value": stochastic_value,
        "agent_advantage_absolute": agent_advantage,
        "agent_advantage_percent_of_stochastic": (
            100.0 * agent_advantage / abs(stochastic_value)
            if abs(stochastic_value) > 1e-12
            else None
        ),
        "outcome": outcome,
        "stochastic_retention_compliant": stochastic_retention,
        "stochastic_realized_aggregator_revenue": stochastic.get(
            "realized_aggregator_revenue"
        ),
        "stochastic_realized_pto_cost": stochastic.get("realized_pto_cost"),
        "stochastic_realized_grid_net_cost": stochastic.get(
            "realized_grid_net_cost"
        ),
        "stochastic_optimizer_calls": stochastic.get("optimizer_calls"),
        "stochastic_llm_request_attempts": stochastic.get("llm_request_attempts"),
        "stochastic_llm_total_tokens": stochastic.get("llm_total_tokens"),
        "stochastic_llm_approximate_cost_usd": stochastic.get(
            "llm_approximate_cost_usd"
        ),
        "stochastic_workbook": str(workbook.relative_to(ROOT)).replace("\\", "/"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    parser.add_argument("--output-root", type=Path, default=Path("results/revision/stochastic_v3"))
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--mode", action="append", choices=MODES)
    parser.add_argument("--time-limit", type=float, default=300.0)
    parser.add_argument("--mip-gap", type=float, default=0.02)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    protocol_path = args.protocol if args.protocol.is_absolute() else ROOT / args.protocol
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

    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    selected_cases = tuple(args.case or CASES)
    selected_modes = tuple(args.mode or MODES)
    rows: list[dict[str, Any]] = []
    os.environ["RT_SOLVER_ORDER"] = "gurobi"
    os.environ["RT_SOLVER_TIME_LIMIT"] = str(args.time_limit)
    os.environ["RT_SOLVER_MIP_GAP"] = str(args.mip_gap)

    for case_id in selected_cases:
        case = load_stochastic_case(protocol_path, case_id)
        for mode in selected_modes:
            workbook = output_root / case_id / mode / "stochastic_programming.xlsx"
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
                    mode=mode,
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
                        "stochastic_protocol_sha256": _sha256(protocol_path),
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
            summary = _summary(workbook)
            audit = _gurobi_audit(workbook)
            if audit["solver_names"] != ["gurobi"]:
                raise RuntimeError(
                    f"Non-Gurobi solver provenance in {workbook}: {audit['solver_names']}"
                )
            if int(summary.get("llm_request_attempts") or 0) != 0:
                raise RuntimeError(f"Unexpected LLM request recorded in {workbook}")
            rows.append(
                _comparison_row(
                    case=case_id,
                    mode=mode,
                    stochastic=summary,
                    workbook=workbook,
                )
                | audit
            )
            print(
                f"{case_id}/{mode}: {rows[-1]['outcome']} "
                f"({rows[-1]['primary_metric']} Agent={rows[-1]['agent_mean']:.6f}, "
                f"stochastic={rows[-1]['stochastic_value']:.6f})",
                flush=True,
            )

    output_root.mkdir(parents=True, exist_ok=True)
    comparison = pd.DataFrame(rows)
    comparison.to_csv(output_root / "agent_vs_stochastic_comparison.csv", index=False)
    protocol_dependencies = [protocol_path]
    raw_protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if raw_protocol.get("base_protocol_file"):
        protocol_dependencies.append(
            protocol_path.parent / str(raw_protocol["base_protocol_file"])
        )
    manifest = {
        "protocol_version": protocol["protocol_version"],
        "protocol_file": str(protocol_path.relative_to(ROOT)).replace("\\", "/"),
        "protocol_sha256": _sha256(protocol_path),
        "protocol_dependencies": {
            str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path)
            for path in protocol_dependencies
        },
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_revision(),
        "external_llm_used": False,
        "api_key_accessed": False,
        "solver": {
            "name": "gurobi",
            "time_limit_seconds": args.time_limit,
            "mip_gap": args.mip_gap,
        },
        "comparison_note": (
            "Positive agent_advantage values favor the Agent. Historical Agent "
            "solves terminated ok/optimal before their 60-second cap, so that cap "
            "was nonbinding; stochastic runs use the current 300-second protocol."
        ),
        "rows": rows,
    }
    (output_root / "agent_vs_stochastic_comparison.json").write_text(
        json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

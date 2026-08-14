from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_scaling_inputs import build_instance, sha256
from scripts.run_closed_loop_trigger_comparison import SUMMARY_COLUMNS, command_for


PROTOCOL = ROOT / "inputs" / "revision" / "scaling_and_second_depot_protocol_v1.json"
NOTICE_FILE = ROOT / "inputs" / "revision" / "advance_warning_notices_v1.json"
PHYSICAL_FILE = (
    ROOT / "inputs" / "revision" / "advance_warning_physical_events_v1.json"
)
INSTANCES = (
    ("depot_a", 8),
    ("depot_a", 16),
    ("depot_a", 32),
    ("depot_b", 8),
)
CONFIGURATIONS = ("rule_text_event_trigger", "full_agentic")
MODES = ("selfish", "altruistic")


def git_revision() -> str | None:
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


def git_clean() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, check=True,
            capture_output=True, text=True
        )
        return not result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return False


@dataclass(frozen=True, slots=True)
class ScalingSpec:
    depot: str
    fleet_size: int
    mode: str
    configuration: str
    repetition: int

    @property
    def instance(self) -> str:
        return f"{self.depot}_{self.fleet_size}"

    @property
    def run_id(self) -> str:
        return (
            f"{self.instance}__{self.mode}__{self.configuration}__"
            f"r{self.repetition:03d}"
        )

    @property
    def uses_external_llm(self) -> bool:
        return self.configuration == "full_agentic"


def build_specs(repetitions: int = 3) -> list[ScalingSpec]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    return [
        ScalingSpec(depot, fleet_size, mode, configuration, repetition)
        for depot, fleet_size in INSTANCES
        for mode in MODES
        for configuration in CONFIGURATIONS
        for repetition in range(1, repetitions + 1)
    ]


def output_path(output_root: Path, spec: ScalingSpec) -> Path:
    return (
        output_root
        / "runs"
        / spec.instance
        / spec.mode
        / f"{spec.configuration}_rep_{spec.repetition:03d}.xlsx"
    )


def run_command(
    spec: ScalingSpec, generated_root: Path, output_root: Path, model: str
) -> list[str]:
    instance = generated_root / spec.instance
    return command_for(
        configuration=spec.configuration,
        case="aw_combined_evening",
        variant="uncertain_chat",
        mode=spec.mode,
        start=1,
        end=48,
        model=model,
        output=output_path(output_root, spec),
        state_workbook=instance / "State.xlsx",
        forecast_workbook=instance / "Forecasted.xlsx",
        spot_prices=instance / "SpotPrices.xlsx",
        realtime_states=instance / "realtime_states",
        intraday_prices=instance / "intraday_prices",
        disturbances=instance / "rt_disturbance_scenarios_multiple.xlsx",
        max_reruns=0,
    )


def mean_numeric(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None


def maximum_numeric(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.max()) if not values.empty else None


def operationally_feasible(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("status") == "complete"
        and float(summary.get("maximum_reserve_shortfall_kwh") or 0.0) <= 1e-6
        and int(summary.get("reserve_violation_timesteps") or 0) == 0
        and float(summary.get("minimum_observed_soc_fraction") or 0.0) >= 0.2 - 1e-6
        and float(summary.get("terminal_minimum_soc_fraction") or 0.0) >= 0.2 - 1e-6
    )


def read_row(
    spec: ScalingSpec, output_root: Path, generated_root: Path, model: str
) -> dict[str, Any]:
    workbook = output_path(output_root, spec)
    summary = pd.read_excel(workbook, sheet_name="run_summary").iloc[0].to_dict()
    attempts = pd.read_excel(workbook, sheet_name="optimization_attempts")
    instance_manifest = generated_root / spec.instance / "instance_manifest.json"
    solvers = (
        sorted(attempts["solver_name"].dropna().astype(str).unique().tolist())
        if "solver_name" in attempts
        else []
    )
    fallback_errors = (
        attempts["solver_fallback_errors"].dropna().astype(str).tolist()
        if "solver_fallback_errors" in attempts
        else []
    )
    fallback_errors = [value for value in fallback_errors if value not in {"", "[]"}]
    if solvers != ["gurobi"] or fallback_errors:
        raise ValueError(
            "Final scaling evidence requires Gurobi with no fallback; "
            f"refusing to index {workbook}"
        )
    return {
        **asdict(spec),
        "run_id": spec.run_id,
        "instance": spec.instance,
        "model": model if spec.uses_external_llm else "not_used",
        "workbook": str(workbook.relative_to(ROOT)),
        "workbook_sha256": sha256(workbook),
        "instance_manifest_sha256": sha256(instance_manifest),
        "operationally_feasible": operationally_feasible(summary),
        "mode_aligned_economic_score": (
            float(summary.get("realized_aggregator_revenue") or 0.0)
            if spec.mode == "selfish"
            else -float(summary.get("realized_pto_cost") or 0.0)
        ),
        "optimizer_latency_seconds_mean": mean_numeric(
            attempts, "optimizer_latency_seconds"
        ),
        "optimizer_process_cpu_seconds_mean": mean_numeric(
            attempts, "optimizer_process_cpu_seconds"
        ),
        "solver_wall_seconds_mean": mean_numeric(attempts, "solver_wall_seconds"),
        "solver_wall_seconds_max": maximum_numeric(attempts, "solver_wall_seconds"),
        "solver_model_variables_max": maximum_numeric(attempts, "model_variables"),
        "solver_model_constraints_max": maximum_numeric(attempts, "model_constraints"),
        "solver_branch_and_bound_nodes_max": maximum_numeric(
            attempts, "solver_branch_and_bound_nodes"
        ),
        "solver_iterations_max": maximum_numeric(attempts, "solver_iterations"),
        "solver_relative_gap_max": maximum_numeric(attempts, "solver_relative_gap"),
        "solver_names": json.dumps(solvers),
        "solver_fallback_used": False,
        **{column: summary.get(column) for column in SUMMARY_COLUMNS},
    }


def write_index(output_root: Path, rows: list[dict[str, Any]]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output_root / "scaling_runs.csv", index=False)
    (output_root / "scaling_runs.json").write_text(
        json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run separated workflow/LLM/optimizer scaling and Depot B tests."
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("results/revision/scaling_v1")
    )
    parser.add_argument(
        "--generated-input-root",
        type=Path,
        default=Path("results/revision/generated_inputs"),
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--solver-order", default="gurobi")
    parser.add_argument("--solver-time-limit", type=float, default=60.0)
    parser.add_argument("--solver-mip-gap", type=float, default=0.02)
    parser.add_argument("--configuration", action="append", choices=CONFIGURATIONS)
    parser.add_argument("--instance", action="append", choices=[f"{d}_{n}" for d, n in INSTANCES])
    parser.add_argument("--mode", action="append", choices=MODES)
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--require-clean-git", action="store_true")
    parser.add_argument("--max-approximate-api-cost-usd", type=float)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    generated_root = (
        args.generated_input_root
        if args.generated_input_root.is_absolute()
        else ROOT / args.generated_input_root
    )
    selected_configs = set(args.configuration or CONFIGURATIONS)
    selected_instances = set(args.instance or [f"{d}_{n}" for d, n in INSTANCES])
    selected_modes = set(args.mode or MODES)
    specs = [
        spec
        for spec in build_specs(args.repetitions)
        if spec.configuration in selected_configs
        and spec.instance in selected_instances
        and spec.mode in selected_modes
    ]
    if args.require_clean_git and not git_clean():
        raise SystemExit("Refusing execution because the Git worktree is not clean")
    if not args.dry_run and any(spec.uses_external_llm for spec in specs):
        if not args.allow_external_llm:
            raise SystemExit(
                "Agent scaling runs require --allow-external-llm to record authorization."
            )
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is required for Agent scaling runs")
    if args.max_approximate_api_cost_usd is not None and args.max_approximate_api_cost_usd <= 0:
        raise SystemExit("--max-approximate-api-cost-usd must be positive")
    if args.solver_order.strip().lower() != "gurobi":
        raise SystemExit("Final scaling runs require --solver-order gurobi")
    if args.solver_time_limit <= 0:
        raise SystemExit("--solver-time-limit must be positive")
    if not 0 <= args.solver_mip_gap < 1:
        raise SystemExit("--solver-mip-gap must be in [0,1)")

    if not args.dry_run:
        for depot, fleet_size in INSTANCES:
            if f"{depot}_{fleet_size}" in selected_instances:
                build_instance(
                    depot=depot,
                    fleet_size=fleet_size,
                    output_root=generated_root,
                    force=args.force,
                )

    rows: list[dict[str, Any]] = []
    commands: list[list[str]] = []
    for index, spec in enumerate(specs, start=1):
        command = run_command(spec, generated_root, output_root, args.model)
        commands.append(command)
        if args.dry_run:
            print(subprocess.list2cmdline(command))
            continue
        spent = sum(float(row.get("llm_approximate_cost_usd") or 0.0) for row in rows)
        if (
            args.max_approximate_api_cost_usd is not None
            and spent >= args.max_approximate_api_cost_usd
        ):
            write_index(output_root, rows)
            raise SystemExit(
                f"Approved approximate API-cost ceiling reached: USD {spent:.4f}"
            )
        workbook = output_path(output_root, spec)
        workbook.parent.mkdir(parents=True, exist_ok=True)
        if args.force or not workbook.exists():
            environment = os.environ.copy()
            environment.update(
                {
                    "RT_SOLVER_ORDER": args.solver_order,
                    "RT_SOLVER_TIME_LIMIT": str(args.solver_time_limit),
                    "RT_SOLVER_MIP_GAP": str(args.solver_mip_gap),
                }
            )
            subprocess.run(command, cwd=ROOT, check=True, env=environment)
        rows.append(read_row(spec, output_root, generated_root, args.model))
        write_index(output_root, rows)
        print(f"[{index}/{len(specs)}] indexed {spec.run_id}", flush=True)

    manifest = {
        "protocol_version": "scaling_and_second_depot_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_revision(),
        "git_worktree_clean": git_clean(),
        "protocol_file": str(PROTOCOL.relative_to(ROOT)),
        "protocol_sha256": sha256(PROTOCOL),
        "notice_sha256": sha256(NOTICE_FILE),
        "physical_event_sha256": sha256(PHYSICAL_FILE),
        "model": args.model,
        "solver_settings": {
            "order": args.solver_order,
            "time_limit_seconds": args.solver_time_limit,
            "mip_gap": args.solver_mip_gap,
        },
        "external_llm_authorized": bool(args.allow_external_llm),
        "canonical_hidden_truth_sent_to_openai": False,
        "planned_runs": len(specs),
        "indexed_runs": len(rows),
        "specs": [asdict(spec) | {"run_id": spec.run_id} for spec in specs],
        "commands": [subprocess.list2cmdline(command) for command in commands],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "scaling_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

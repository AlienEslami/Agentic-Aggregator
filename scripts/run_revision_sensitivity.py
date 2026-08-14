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

from agentic_workflow.experiment_controls import (
    PRICING_GUIDANCE_VARIANTS,
    TRIGGER_CONFIDENCE_LEVELS,
    TRIGGER_PROMPT_VARIANTS,
)
from scripts.run_closed_loop_trigger_comparison import SUMMARY_COLUMNS, command_for


PROTOCOL = ROOT / "inputs" / "revision" / "revision_sensitivity_protocol_v1.json"
TRIGGER_DATA = ROOT / "inputs" / "revision" / "trigger_notices_v3.json"
NOTICE_DATA = ROOT / "inputs" / "revision" / "advance_warning_notices_v1.json"
PHYSICAL_DATA = (
    ROOT / "inputs" / "revision" / "advance_warning_physical_events_v1.json"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return not result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return False


@dataclass(frozen=True, slots=True)
class SensitivitySpec:
    family: str
    arm: str
    repetition: int
    prompt_variant: str = "baseline"
    confidence_level: str = "base"
    confidence_threshold: float = 0.70
    pricing_guidance_variant: str = "base"
    mode: str = "selfish"

    @property
    def run_id(self) -> str:
        return f"{self.family}__{self.arm}__{self.mode}__r{self.repetition:03d}"


def build_specs(repetitions: int = 5) -> list[SensitivitySpec]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    unique: dict[tuple[Any, ...], SensitivitySpec] = {}
    for prompt_variant in TRIGGER_PROMPT_VARIANTS:
        for repetition in range(1, repetitions + 1):
            spec = SensitivitySpec(
                family="trigger",
                arm=f"prompt_{prompt_variant}",
                repetition=repetition,
                prompt_variant=prompt_variant,
                confidence_level="base",
                confidence_threshold=TRIGGER_CONFIDENCE_LEVELS["base"],
            )
            unique[(spec.family, spec.prompt_variant, spec.confidence_threshold, repetition)] = spec
    for level, threshold in TRIGGER_CONFIDENCE_LEVELS.items():
        for repetition in range(1, repetitions + 1):
            spec = SensitivitySpec(
                family="trigger",
                arm=f"threshold_{level}",
                repetition=repetition,
                prompt_variant="baseline",
                confidence_level=level,
                confidence_threshold=threshold,
            )
            unique.setdefault(
                (spec.family, spec.prompt_variant, spec.confidence_threshold, repetition),
                spec,
            )
    specs = list(unique.values())
    for mode in ("selfish", "altruistic"):
        for guidance in PRICING_GUIDANCE_VARIANTS:
            for repetition in range(1, repetitions + 1):
                specs.append(
                    SensitivitySpec(
                        family="pricing",
                        arm=f"guidance_{guidance}",
                        repetition=repetition,
                        pricing_guidance_variant=guidance,
                        mode=mode,
                    )
                )
    return specs


def trigger_paths(output_root: Path, spec: SensitivitySpec) -> tuple[Path, str]:
    directory = output_root / "trigger" / spec.arm
    label = f"{spec.run_id}_trigger_agent"
    return directory, label


def pricing_path(output_root: Path, spec: SensitivitySpec) -> Path:
    return output_root / "pricing" / spec.mode / spec.arm / f"{spec.run_id}.xlsx"


def trigger_command(
    spec: SensitivitySpec, output_root: Path, model: str
) -> list[str]:
    directory, label = trigger_paths(output_root, spec)
    return [
        sys.executable,
        "scripts/evaluate_trigger_agent.py",
        "--notices",
        str(TRIGGER_DATA.relative_to(ROOT)),
        "--output-dir",
        str(directory),
        "--backend",
        "openai",
        "--model",
        model,
        "--split",
        "test",
        "--label",
        label,
        "--trigger-prompt-variant",
        spec.prompt_variant,
        "--trigger-confidence-threshold",
        str(spec.confidence_threshold),
    ]


def pricing_command(
    spec: SensitivitySpec, output_root: Path, model: str
) -> list[str]:
    return command_for(
        configuration="pricing_agent_only",
        case="aw_combined_evening",
        variant="uncertain_chat",
        mode=spec.mode,
        start=1,
        end=48,
        model=model,
        output=pricing_path(output_root, spec),
        pricing_guidance_variant=spec.pricing_guidance_variant,
    )


def operationally_feasible(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("status") == "complete"
        and float(summary.get("maximum_reserve_shortfall_kwh") or 0.0) <= 1e-6
        and int(summary.get("reserve_violation_timesteps") or 0) == 0
        and float(summary.get("minimum_observed_soc_fraction") or 0.0) >= 0.2 - 1e-6
        and float(summary.get("terminal_minimum_soc_fraction") or 0.0) >= 0.2 - 1e-6
    )


def read_trigger_row(
    spec: SensitivitySpec, output_root: Path, model: str
) -> dict[str, Any]:
    directory, label = trigger_paths(output_root, spec)
    summary_path = directory / f"{label}_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = summary.get("metrics") or {}
    usage = summary.get("usage") or {}
    return {
        **asdict(spec),
        "run_id": spec.run_id,
        "model": model,
        "artifact": str(summary_path.relative_to(ROOT)),
        "artifact_sha256": sha256(summary_path),
        **metrics,
        **usage,
    }


def read_pricing_row(
    spec: SensitivitySpec, output_root: Path, model: str
) -> dict[str, Any]:
    workbook = pricing_path(output_root, spec)
    summary = pd.read_excel(workbook, sheet_name="run_summary").iloc[0].to_dict()
    attempts = pd.read_excel(workbook, sheet_name="optimization_attempts")
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
            "Final sensitivity evidence requires Gurobi with no fallback; "
            f"refusing to index {workbook}"
        )
    if "selected_for_execution" in attempts:
        selected = attempts[attempts["selected_for_execution"].fillna(False).astype(bool)]
        if selected.empty:
            selected = attempts
    else:
        selected = attempts

    def mean(column: str) -> float | None:
        if column not in selected:
            return None
        values = pd.to_numeric(selected[column], errors="coerce").dropna()
        return float(values.mean()) if not values.empty else None

    return {
        **asdict(spec),
        "run_id": spec.run_id,
        "model": model,
        "artifact": str(workbook.relative_to(ROOT)),
        "artifact_sha256": sha256(workbook),
        "solver_names": json.dumps(solvers),
        "solver_fallback_used": False,
        "operationally_feasible": operationally_feasible(summary),
        "mode_aligned_economic_score": (
            float(summary.get("realized_aggregator_revenue") or 0.0)
            if spec.mode == "selfish"
            else -float(summary.get("realized_pto_cost") or 0.0)
        ),
        "chosen_buy_arithmetic_mean": mean("chosen_buy_arithmetic_mean"),
        "chosen_sell_arithmetic_mean": mean("chosen_sell_arithmetic_mean"),
        "buy_arithmetic_mean_gap": mean("buy_arithmetic_mean_gap"),
        "sell_arithmetic_mean_gap": mean("sell_arithmetic_mean_gap"),
        "buy_centered_temporal_mae": mean("buy_centered_temporal_mae"),
        "sell_centered_temporal_mae": mean("sell_centered_temporal_mae"),
        **{column: summary.get(column) for column in SUMMARY_COLUMNS},
    }


def write_index(output_root: Path, rows: list[dict[str, Any]]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output_root / "sensitivity_runs.csv", index=False)
    (output_root / "sensitivity_runs.json").write_text(
        json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the prespecified one-factor revision sensitivity study."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/revision/sensitivity_v1"),
    )
    parser.add_argument("--component", choices=("all", "trigger", "pricing"), default="all")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--require-clean-git", action="store_true")
    parser.add_argument("--max-approximate-api-cost-usd", type=float)
    parser.add_argument("--solver-order", default="gurobi")
    parser.add_argument("--solver-time-limit", type=float, default=60.0)
    parser.add_argument("--solver-mip-gap", type=float, default=0.02)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    specs = [
        spec
        for spec in build_specs(args.repetitions)
        if args.component == "all" or spec.family == args.component
    ]
    if args.require_clean_git and not git_clean():
        raise SystemExit("Refusing execution because the Git worktree is not clean")
    if not args.dry_run:
        if not args.allow_external_llm:
            raise SystemExit(
                "Sensitivity runs require --allow-external-llm to record authorization."
            )
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is required for sensitivity runs")
    if args.max_approximate_api_cost_usd is not None and args.max_approximate_api_cost_usd <= 0:
        raise SystemExit("--max-approximate-api-cost-usd must be positive")
    if args.solver_order.strip().lower() != "gurobi":
        raise SystemExit("Final sensitivity runs require --solver-order gurobi")
    if args.solver_time_limit <= 0:
        raise SystemExit("--solver-time-limit must be positive")
    if not 0 <= args.solver_mip_gap < 1:
        raise SystemExit("--solver-mip-gap must be in [0, 1)")

    solver_environment = os.environ.copy()
    solver_environment.update(
        {
            "RT_SOLVER_ORDER": args.solver_order,
            "RT_SOLVER_TIME_LIMIT": str(args.solver_time_limit),
            "RT_SOLVER_MIP_GAP": str(args.solver_mip_gap),
        }
    )

    rows: list[dict[str, Any]] = []
    commands: list[list[str]] = []
    for index, spec in enumerate(specs, start=1):
        command = (
            trigger_command(spec, output_root, args.model)
            if spec.family == "trigger"
            else pricing_command(spec, output_root, args.model)
        )
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
        if spec.family == "trigger":
            directory, label = trigger_paths(output_root, spec)
            artifact = directory / f"{label}_summary.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            if args.force or not artifact.exists():
                subprocess.run(command, cwd=ROOT, check=True, env=solver_environment)
            row = read_trigger_row(spec, output_root, args.model)
        else:
            artifact = pricing_path(output_root, spec)
            artifact.parent.mkdir(parents=True, exist_ok=True)
            if args.force or not artifact.exists():
                subprocess.run(command, cwd=ROOT, check=True, env=solver_environment)
            row = read_pricing_row(spec, output_root, args.model)
        rows.append(row)
        write_index(output_root, rows)
        print(f"[{index}/{len(specs)}] indexed {spec.run_id}", flush=True)

    manifest = {
        "protocol_version": "revision_sensitivity_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_revision(),
        "git_worktree_clean": git_clean(),
        "protocol_file": str(PROTOCOL.relative_to(ROOT)),
        "protocol_sha256": sha256(PROTOCOL),
        "input_hashes": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (TRIGGER_DATA, NOTICE_DATA, PHYSICAL_DATA)
        },
        "model": args.model,
        "external_llm_authorized": bool(args.allow_external_llm),
        "canonical_hidden_truth_sent_to_openai": False,
        "solver": {
            "order": args.solver_order,
            "time_limit_seconds": args.solver_time_limit,
            "mip_gap": args.solver_mip_gap,
            "fallback_allowed": False,
        },
        "planned_runs": len(specs),
        "indexed_runs": len(rows),
        "specs": [asdict(spec) | {"run_id": spec.run_id} for spec in specs],
        "commands": [subprocess.list2cmdline(command) for command in commands],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "sensitivity_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

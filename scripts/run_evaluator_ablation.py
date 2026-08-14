from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
NOTICE_FILE = ROOT / "inputs" / "revision" / "evaluator_priority_notices_v1.json"
PHYSICAL_FILE = ROOT / "inputs" / "revision" / "advance_warning_physical_events_v1.json"
PROTOCOL_FILE = (
    ROOT / "inputs" / "revision" / "information_and_evaluator_ablation_protocol_v2.json"
)
CASES = (
    "aw_route6_late_return",
    "aw_charger_bank_shutdown",
    "aw_combined_evening",
)
MODES = ("selfish", "altruistic")
CONFIGURATIONS = (
    "agent_evaluator_raw_text",
    "rule_text_evaluator",
    "structured_evaluator_oracle",
    "evaluator_removal_control",
)
LLM_CONFIGURATION = "agent_evaluator_raw_text"


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


def require_gurobi_only(workbook: Path) -> None:
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
            "Final Evaluator evidence requires Gurobi with no fallback; "
            f"refusing to index {workbook}"
        )


def validate_protocol() -> dict[str, object]:
    protocol = json.loads(PROTOCOL_FILE.read_text(encoding="utf-8"))
    rerun = protocol.get("rerun_mechanism") or {}
    if rerun.get("name") != "lexicographic_soft_operational_priority":
        raise ValueError("Evaluator protocol must use staged lexicographic priorities")
    if rerun.get("arbitrary_currency_penalty") is not False:
        raise ValueError("Evaluator protocol must explicitly disable currency penalties")
    if int((protocol.get("repetitions") or {}).get("planned_runs") or 0) != 48:
        raise ValueError("Evaluator protocol planned_runs must equal 48")
    return protocol


def command_for(
    *, case: str, mode: str, configuration: str, model: str, output: Path
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "agentic_workflow",
        "--state-workbook",
        "inputs/State.xlsx",
        "--forecast-workbook",
        "inputs/Forecasted.xlsx",
        "--spot-prices",
        "inputs/SpotPrices.xlsx",
        "--realtime-states",
        "inputs/realtime_states",
        "--intraday-prices",
        "inputs/intraday_prices",
        "--disturbances",
        "inputs/rt_disturbance_scenarios_multiple.xlsx",
        "--scenario",
        "rt_none",
        "--notices-file",
        str(NOTICE_FILE.relative_to(ROOT)),
        "--physical-events-file",
        str(PHYSICAL_FILE.relative_to(ROOT)),
        "--notice-scenario",
        case,
        "--notice-variant",
        "uncertain_chat",
        "--configuration",
        configuration,
        "--realize-notice-truth",
        "--mode",
        mode,
        "--start-timestep",
        "1",
        "--end-timestep",
        "48",
        "--max-reruns",
        "1",
        "--checkpoint-every",
        "48",
        "--optimizer-backend",
        "direct",
        "--model",
        model,
        "--output",
        str(output),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the controlled evaluator ablation with fixed structured Trigger "
            "and deterministic Pricing roles."
        )
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--mode", action="append", choices=MODES)
    parser.add_argument("--configuration", action="append", choices=CONFIGURATIONS)
    parser.add_argument("--agent-repetitions", type=int, default=5)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--solver-order", default="gurobi")
    parser.add_argument("--solver-time-limit", type=float, default=60.0)
    parser.add_argument("--solver-mip-gap", type=float, default=0.02)
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--require-clean-git", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    validate_protocol()

    cases = tuple(args.case or CASES)
    modes = tuple(args.mode or MODES)
    configurations = tuple(args.configuration or CONFIGURATIONS)
    if args.agent_repetitions < 1:
        raise ValueError("--agent-repetitions must be positive")
    if args.solver_order.strip().lower() != "gurobi":
        raise ValueError("Final Evaluator runs require --solver-order gurobi")
    if args.solver_time_limit <= 0:
        raise ValueError("--solver-time-limit must be positive")
    if not 0 <= args.solver_mip_gap < 1:
        raise ValueError("--solver-mip-gap must be in [0, 1)")
    if args.require_clean_git and not git_clean():
        raise ValueError("Refusing execution because the Git worktree is not clean")
    if LLM_CONFIGURATION in configurations and not args.dry_run:
        if not args.allow_external_llm:
            raise ValueError(
                "The Agent evaluator requires --allow-external-llm to record "
                "authorization for sending synthetic public messages and numerical context."
            )
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required for the Agent evaluator")

    specs: list[dict[str, object]] = []
    for case in cases:
        for mode in modes:
            for configuration in configurations:
                repetitions = (
                    args.agent_repetitions
                    if configuration == LLM_CONFIGURATION
                    else 1
                )
                for repetition in range(1, repetitions + 1):
                    output = (
                        args.output_root
                        / case
                        / mode
                        / f"{configuration}_rep_{repetition:03d}.xlsx"
                    )
                    specs.append(
                        {
                            "case": case,
                            "mode": mode,
                            "configuration": configuration,
                            "repetition": repetition,
                            "output": str(output),
                        }
                    )
                    command = command_for(
                        case=case,
                        mode=mode,
                        configuration=configuration,
                        model=args.model,
                        output=output,
                    )
                    if args.dry_run:
                        print(subprocess.list2cmdline(command))
                        continue
                    if output.exists() and not args.force:
                        print(f"Reusing {output}")
                        require_gurobi_only(output)
                        continue
                    output.parent.mkdir(parents=True, exist_ok=True)
                    environment = os.environ.copy()
                    environment.update(
                        {
                            "RT_SOLVER_ORDER": args.solver_order,
                            "RT_SOLVER_TIME_LIMIT": str(args.solver_time_limit),
                            "RT_SOLVER_MIP_GAP": str(args.solver_mip_gap),
                        }
                    )
                    subprocess.run(command, cwd=ROOT, check=True, env=environment)
                    require_gurobi_only(output)

    args.output_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "protocol": "controlled_evaluator_ablation_v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_revision(),
        "git_worktree_clean": git_clean(),
        "information_contract": {
            "common_trigger": "canonical structured notice; fixed across all arms",
            "common_pricing": "deterministic price-zone policy; fixed across all arms",
            "agent_evaluator": "raw public message only",
            "rule_evaluator": "same raw public message through frozen parser",
            "structured_oracle": "canonical structured priority; labelled oracle only",
            "removal": "solver and feasibility hard checks; ignores soft priority",
            "canonical_priority_sent_to_llm": False,
            "common_physical_truth": True,
            "causal_settlement": True,
        },
        "comparison_order": [
            "optimizer usability",
            "canonical operator-priority compliance",
            "projected full-day economics among compliant schedules",
        ],
        "inputs": {
            "protocol": str(PROTOCOL_FILE.relative_to(ROOT)),
            "protocol_sha256": sha256(PROTOCOL_FILE),
            "notices": str(NOTICE_FILE.relative_to(ROOT)),
            "notices_sha256": sha256(NOTICE_FILE),
            "physical_events": str(PHYSICAL_FILE.relative_to(ROOT)),
            "physical_events_sha256": sha256(PHYSICAL_FILE),
        },
        "model": args.model,
        "external_llm_authorized": bool(args.allow_external_llm),
        "canonical_hidden_truth_sent_to_openai": False,
        "solver_settings": {
            "order": args.solver_order,
            "time_limit_seconds": args.solver_time_limit,
            "mip_gap": args.solver_mip_gap,
            "fallback_permitted_in_final_results": False,
        },
        "specs": specs,
    }
    (args.output_root / "evaluator_ablation_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

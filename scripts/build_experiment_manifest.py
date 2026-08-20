from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_workflow.config import (
    DEFAULT_CACHED_INPUT_USD_PER_MILLION,
    DEFAULT_CACHE_WRITE_MULTIPLIER,
    DEFAULT_INPUT_USD_PER_MILLION,
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_USD_PER_MILLION,
    DEFAULT_REASONING_EFFORT,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a non-secret revision experiment manifest.")
    parser.add_argument("--output", type=Path, default=Path("results/revision/experiment_manifest.json"))
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--model", default="not-used")
    parser.add_argument("--notice-scenario", action="append", default=[])
    parser.add_argument("--notice-variant", default="explicit")
    args = parser.parse_args()
    tracked = [
        *sorted((ROOT / "agentic_workflow" / "prompts").glob("*.txt")),
        ROOT / "inputs" / "revision" / "trigger_scenarios.json",
        ROOT / "inputs" / "revision" / "trigger_notices.json",
        ROOT / "inputs" / "revision" / "trigger_dataset_manifest.json",
        ROOT / "inputs" / "revision" / "trigger_scenarios_v3.json",
        ROOT / "inputs" / "revision" / "trigger_notices_v3.json",
        ROOT / "inputs" / "revision" / "trigger_dataset_manifest_v3.json",
        ROOT / "inputs" / "revision" / "trigger_split_v3.json",
        ROOT / "inputs" / "revision" / "uncertainty_chat_mapping_v3.md",
        ROOT / "inputs" / "revision" / "advance_warning_notices_v1.json",
        ROOT / "inputs" / "revision" / "advance_warning_physical_events_v1.json",
        ROOT / "inputs" / "revision" / "advance_warning_manifest_v1.json",
        ROOT / "inputs" / "revision" / "advance_warning_ablation_protocol_v8.json",
        ROOT / "inputs" / "revision" / "information_and_evaluator_ablation_protocol_v3.json",
        ROOT / "inputs" / "revision" / "revision_sensitivity_protocol_v2.json",
        ROOT / "inputs" / "revision" / "scaling_and_second_depot_protocol_v2.json",
        ROOT / "inputs" / "revision" / "stochastic_benchmark_protocol_v2.json",
        ROOT / "inputs" / "revision" / "stochastic_benchmark_protocol_v3.json",
        ROOT / "inputs" / "revision" / "stochastic_benchmark_protocol_v4.json",
        ROOT / "inputs" / "revision" / "independent_validation_checklist_v1.md",
        ROOT / "requirements-lock.txt",
        ROOT / "requirements-dev-lock.txt",
        ROOT / "agentic_workflow" / "notices.py",
        ROOT / "agentic_workflow" / "trigger_evaluation.py",
        ROOT / "agentic_workflow" / "uncertainty.py",
        ROOT / "agentic_workflow" / "config.py",
        ROOT / "agentic_workflow" / "agents.py",
        ROOT / "agentic_workflow" / "runner.py",
        ROOT / "agentic_workflow" / "state.py",
        ROOT / "agentic_workflow" / "physical_events.py",
        ROOT / "agentic_workflow" / "telemetry.py",
        ROOT / "scripts" / "evaluate_trigger_notices.py",
        ROOT / "scripts" / "evaluate_trigger_agent.py",
        ROOT / "scripts" / "build_uncertain_chat_dataset.py",
        ROOT / "scripts" / "compare_trigger_evaluations.py",
        ROOT / "scripts" / "build_closed_loop_notice_cases.py",
        ROOT / "scripts" / "run_closed_loop_trigger_comparison.py",
        ROOT / "scripts" / "run_advance_warning_matrix.py",
        ROOT / "scripts" / "analyze_advance_warning_matrix.py",
        ROOT / "scripts" / "run_revision_sensitivity.py",
        ROOT / "scripts" / "analyze_revision_sensitivity.py",
        ROOT / "scripts" / "build_scaling_inputs.py",
        ROOT / "scripts" / "run_scaling_study.py",
        ROOT / "scripts" / "analyze_scaling_study.py",
        ROOT / "scripts" / "run_stochastic_decision.py",
        ROOT / "scripts" / "run_stochastic_closed_loop.py",
        ROOT / "scripts" / "validate_revision_package.py",
        ROOT / "agentic_workflow" / "stochastic_programming.py",
        ROOT / "agentic_workflow" / "stochastic_benchmark.py",
        ROOT / "app.py",
        ROOT / "app_rt.py",
    ]
    packages = {}
    for name in (
        "flask",
        "gurobipy",
        "highspy",
        "httpx",
        "numpy",
        "openai",
        "openpyxl",
        "pandas",
        "pydantic",
        "pyomo",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True
        ).stdout.strip()
        git_dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=ROOT, check=True,
                capture_output=True, text=True
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        git_commit = None
        git_dirty = None
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "git_worktree_dirty": git_dirty,
        "configuration": args.configuration,
        "model": args.model,
        "notice_scenarios": args.notice_scenario,
        "notice_variant": args.notice_variant,
        "llm_settings": {
            "temperature": "provider/model default; no temperature parameter sent",
            "seed": "not set; repeated calls quantify nondeterminism",
            "reasoning_effort": (
                DEFAULT_REASONING_EFFORT
                if str(args.model).startswith("gpt-5.6")
                else "provider/model default"
            ),
            "max_structured_output_attempts": 3,
            "cost_rates_usd_per_million": {
                "input": (
                    DEFAULT_INPUT_USD_PER_MILLION
                    if args.model == DEFAULT_MODEL
                    else os.environ.get("OPENAI_INPUT_USD_PER_MILLION", "not set")
                ),
                "cached_input": (
                    DEFAULT_CACHED_INPUT_USD_PER_MILLION
                    if args.model == DEFAULT_MODEL
                    else os.environ.get(
                        "OPENAI_CACHED_INPUT_USD_PER_MILLION", "not set"
                    )
                ),
                "cache_write": (
                    DEFAULT_INPUT_USD_PER_MILLION * DEFAULT_CACHE_WRITE_MULTIPLIER
                    if args.model == DEFAULT_MODEL
                    else os.environ.get(
                        "OPENAI_CACHE_WRITE_USD_PER_MILLION", "not set"
                    )
                ),
                "output": (
                    DEFAULT_OUTPUT_USD_PER_MILLION
                    if args.model == DEFAULT_MODEL
                    else os.environ.get("OPENAI_OUTPUT_USD_PER_MILLION", "not set")
                ),
            },
        },
        "solver_settings": {
            "order": os.environ.get("RT_SOLVER_ORDER", "gurobi,appsi_highs,highs,cbc,glpk"),
            "time_limit_seconds": os.environ.get("RT_SOLVER_TIME_LIMIT", "300"),
            "mip_gap": os.environ.get("RT_SOLVER_MIP_GAP", "0.02"),
            "lexicographic_mip_gap": os.environ.get(
                "RT_LEXICOGRAPHIC_MIP_GAP", "0.0"
            ),
            "lexicographic_abs_tolerance": os.environ.get(
                "RT_LEXICOGRAPHIC_ABS_TOLERANCE", "1e-6"
            ),
            "lexicographic_rel_tolerance": os.environ.get(
                "RT_LEXICOGRAPHIC_REL_TOLERANCE", "1e-8"
            ),
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "packages": packages,
        },
        "dependency_locks": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (
                ROOT / "requirements-lock.txt",
                ROOT / "requirements-dev-lock.txt",
            )
            if path.exists()
        },
        "files": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
            for path in tracked
            if path.exists()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()

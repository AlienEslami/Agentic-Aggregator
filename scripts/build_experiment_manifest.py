from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
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
        ROOT / "agentic_workflow" / "notices.py",
        ROOT / "agentic_workflow" / "trigger_evaluation.py",
        ROOT / "agentic_workflow" / "uncertainty.py",
        ROOT / "agentic_workflow" / "config.py",
        ROOT / "agentic_workflow" / "agents.py",
        ROOT / "agentic_workflow" / "runner.py",
        ROOT / "agentic_workflow" / "state.py",
        ROOT / "agentic_workflow" / "telemetry.py",
        ROOT / "scripts" / "evaluate_trigger_notices.py",
        ROOT / "scripts" / "evaluate_trigger_agent.py",
        ROOT / "scripts" / "build_uncertain_chat_dataset.py",
        ROOT / "scripts" / "compare_trigger_evaluations.py",
        ROOT / "app.py",
        ROOT / "app_rt.py",
    ]
    packages = {}
    for name in ("openai", "pydantic", "pandas", "pyomo", "highspy", "openpyxl"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
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
            "time_limit_seconds": os.environ.get("RT_SOLVER_TIME_LIMIT", "60"),
            "mip_gap": os.environ.get("RT_SOLVER_MIP_GAP", "0.02"),
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "packages": packages,
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

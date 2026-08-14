from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTICE_FILE = ROOT / "inputs" / "revision" / "evaluator_priority_notices_v1.json"
PHYSICAL_FILE = ROOT / "inputs" / "revision" / "advance_warning_physical_events_v1.json"
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
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cases = tuple(args.case or CASES)
    modes = tuple(args.mode or MODES)
    configurations = tuple(args.configuration or CONFIGURATIONS)
    if args.agent_repetitions < 1:
        raise ValueError("--agent-repetitions must be positive")
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
                        continue
                    output.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(command, cwd=ROOT, check=True)

    args.output_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "protocol": "controlled_evaluator_ablation_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
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
            "notices": str(NOTICE_FILE.relative_to(ROOT)),
            "notices_sha256": sha256(NOTICE_FILE),
            "physical_events": str(PHYSICAL_FILE.relative_to(ROOT)),
            "physical_events_sha256": sha256(PHYSICAL_FILE),
        },
        "model": args.model,
        "specs": specs,
    }
    (args.output_root / "evaluator_ablation_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

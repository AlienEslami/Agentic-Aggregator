from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_workflow.agents import create_agent_backend
from agentic_workflow.config import DEFAULT_MODEL
from agentic_workflow.experiment_controls import TRIGGER_PROMPT_VARIANTS
from agentic_workflow.notices import NoticeSeries
from agentic_workflow.telemetry import summarize_agent_calls
from agentic_workflow.trigger_evaluation import evaluate_notice_sequences


def _json_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return value


def _metrics(frame: pd.DataFrame) -> dict[str, float]:
    columns = [
        column
        for column in frame.columns
        if (column.startswith("raw_") or column.startswith("effective_"))
        and column.endswith("_correct")
    ]
    metrics = {column: float(frame[column].mean()) for column in columns}
    if {"reference_action", "raw_action", "effective_action"}.issubset(frame.columns):
        reference_optimize = frame["reference_action"].eq("optimize")
        for prefix in ("raw", "effective"):
            optimized = frame[f"{prefix}_action"].eq("optimize")
            metrics[f"{prefix}_optimize_rate"] = float(optimized.mean())
            metrics[f"{prefix}_false_optimization_rate"] = float(
                (optimized & ~reference_optimize).mean()
            )
            metrics[f"{prefix}_missed_optimization_rate"] = float(
                (~optimized & reference_optimize).mean()
            )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score raw and guarded Trigger-Agent decisions on frozen notice sequences."
    )
    parser.add_argument(
        "--notices",
        type=Path,
        default=Path("inputs/revision/trigger_notices_v3.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results/revision"))
    parser.add_argument("--backend", choices=("openai", "rule"), default="openai")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--trigger-prompt-variant",
        choices=TRIGGER_PROMPT_VARIANTS,
        default="baseline",
    )
    parser.add_argument(
        "--trigger-confidence-threshold", type=float, default=0.0
    )
    parser.add_argument("--mode", choices=("selfish", "altruistic"), default="selfish")
    parser.add_argument("--scenario", action="append", dest="scenario_ids")
    parser.add_argument(
        "--variant",
        action="append",
        dest="variants",
    )
    parser.add_argument(
        "--split",
        choices=("development", "test", "all"),
        default="test",
        help="Evaluate the frozen held-out test split by default.",
    )
    parser.add_argument("--label", default="trigger_agent")
    args = parser.parse_args()

    series = NoticeSeries(args.notices)
    selected_scenarios = set(args.scenario_ids or [])
    selected_variants = set(args.variants or [])
    records = [
        record
        for record in series.records
        if (not selected_scenarios or record.scenario_id in selected_scenarios)
        and (not selected_variants or record.wording_variant in selected_variants)
        and (args.split == "all" or record.benchmark_split == args.split)
    ]
    backend = create_agent_backend(
        args.backend,
        args.model,
        trigger_prompt_variant=args.trigger_prompt_variant,
        trigger_confidence_threshold=args.trigger_confidence_threshold,
    )
    rows, calls = evaluate_notice_sequences(records, backend, mode=args.mode)
    if not rows:
        raise ValueError("No canonical notice records matched the requested filters")

    frame = pd.DataFrame(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    decisions_path = args.output_dir / f"{args.label}_decisions.csv"
    calls_path = args.output_dir / f"{args.label}_calls.jsonl"
    summary_path = args.output_dir / f"{args.label}_summary.json"
    frame.apply(lambda column: column.map(_json_cell)).to_csv(decisions_path, index=False)
    calls_path.write_text(
        "".join(json.dumps(call, default=str, separators=(",", ":")) + "\n" for call in calls),
        encoding="utf-8",
    )
    summary = {
        "backend": args.backend,
        "model": args.model,
        "mode": args.mode,
        "trigger_prompt_variant": args.trigger_prompt_variant,
        "trigger_confidence_threshold": args.trigger_confidence_threshold,
        "confidence_interpretation": (
            "model-reported confidence is treated as an uncalibrated deployment "
            "score; low/base/high thresholds are sensitivity arms"
        ),
        "n_decisions": len(frame),
        "n_sequences": int(frame[["scenario_id", "wording_variant"]].drop_duplicates().shape[0]),
        "guard_rate": float(frame["guard_applied"].mean()),
        "metrics": _metrics(frame),
        "by_wording_variant": {
            variant: _metrics(group)
            for variant, group in frame.groupby("wording_variant", sort=True)
        },
        "by_uncertainty_case": {
            case: _metrics(group)
            for case, group in frame.groupby("uncertainty_case", sort=True)
        },
        "by_benchmark_split": {
            split: _metrics(group)
            for split, group in frame.groupby("benchmark_split", sort=True)
        },
        "usage": summarize_agent_calls(calls),
        "local_resources": {
            "wall_seconds": float(frame["local_wall_seconds"].sum()),
            "process_cpu_seconds": float(frame["local_process_cpu_seconds"].sum()),
            "peak_rss_mb": float(frame["local_peak_rss_mb"].max()),
            "provider_compute_note": (
                "Token counts and latency are logged; provider-side FLOPs, GPU energy, "
                "and carbon emissions are not exposed by the API."
            ),
        },
        "method_input_excludes": [
            "canonical",
            "scenario_id",
            "wording_variant",
            "benchmark_split",
            "uncertainty_case",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


METRICS = (
    "operationally_feasible",
    "mode_aligned_economic_score",
    "run_wall_seconds",
    "run_process_cpu_seconds",
    "run_peak_rss_mb",
    "llm_latency_seconds",
    "llm_total_tokens",
    "llm_approximate_cost_usd",
    "optimizer_latency_seconds_mean",
    "optimizer_process_cpu_seconds_mean",
    "solver_wall_seconds_mean",
    "solver_wall_seconds_max",
    "solver_model_variables_max",
    "solver_model_constraints_max",
    "solver_branch_and_bound_nodes_max",
    "solver_iterations_max",
)


def seed_for(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def interval(values: np.ndarray, label: str, iterations: int) -> tuple[float, float]:
    if not len(values):
        return np.nan, np.nan
    rng = np.random.default_rng(seed_for(label))
    means = rng.choice(values, size=(iterations, len(values)), replace=True).mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize scaling and second-depot runs.")
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    args = parser.parse_args(argv)
    frame = pd.read_csv(args.runs)
    rows: list[dict[str, object]] = []
    groups = ["depot", "fleet_size", "mode", "configuration"]
    for keys, group in frame.groupby(groups, sort=True):
        for metric in METRICS:
            if metric not in group:
                continue
            values = pd.to_numeric(group[metric], errors="coerce").dropna().to_numpy(dtype=float)
            low, high = interval(
                values, "|".join(map(str, (*keys, metric))), args.bootstrap_iterations
            )
            rows.append(
                {
                    **dict(zip(groups, keys)),
                    "metric": metric,
                    "n": len(values),
                    "mean": float(values.mean()) if len(values) else None,
                    "standard_deviation": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
                    "bootstrap_ci95_low": low,
                    "bootstrap_ci95_high": high,
                }
            )
    summary = pd.DataFrame(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_dir / "scaling_summary.csv", index=False)
    (args.output_dir / "scaling_summary.json").write_text(
        summary.to_json(orient="records", indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "analysis_protocol.json").write_text(
        json.dumps(
            {
                "bootstrap_iterations": args.bootstrap_iterations,
                "bootstrap_unit": "complete timing repetition",
                "optimizer_and_llm_metrics_reported_separately": True,
                "no_pooling_across_depot_fleet_mode_or_method": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

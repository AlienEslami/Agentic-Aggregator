from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def seed_for(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def bootstrap_mean(values: pd.Series, label: str, iterations: int) -> tuple[float, float]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if clean.size == 0:
        return np.nan, np.nan
    rng = np.random.default_rng(seed_for(label))
    draws = rng.choice(clean, size=(iterations, clean.size), replace=True).mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze revision sensitivity runs.")
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    args = parser.parse_args(argv)
    frame = pd.read_csv(args.runs)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    metric_map = {
        "trigger": [
            "effective_action_correct",
            "effective_false_optimization_rate",
            "effective_missed_optimization_rate",
            "effective_phase_correct",
            "effective_updates_correct",
            "effective_uncertainty_recommended_action_correct",
            "llm_total_tokens",
            "llm_latency_seconds",
            "llm_approximate_cost_usd",
        ],
        "pricing": [
            "operationally_feasible",
            "mode_aligned_economic_score",
            "buy_arithmetic_mean_gap",
            "sell_arithmetic_mean_gap",
            "buy_centered_temporal_mae",
            "sell_centered_temporal_mae",
            "llm_total_tokens",
            "llm_latency_seconds",
            "llm_approximate_cost_usd",
        ],
    }
    rows: list[dict[str, object]] = []
    for family, metrics in metric_map.items():
        family_frame = frame[frame["family"].eq(family)]
        group_columns = ["family", "arm", "mode"]
        for keys, group in family_frame.groupby(group_columns, sort=True, dropna=False):
            for metric in metrics:
                if metric not in group:
                    continue
                values = pd.to_numeric(group[metric], errors="coerce").dropna()
                low, high = bootstrap_mean(
                    values, "|".join(map(str, (*keys, metric))), args.bootstrap_iterations
                )
                rows.append(
                    {
                        **dict(zip(group_columns, keys)),
                        "metric": metric,
                        "n": int(values.size),
                        "mean": float(values.mean()) if not values.empty else None,
                        "standard_deviation": (
                            float(values.std(ddof=1)) if values.size > 1 else 0.0
                        ),
                        "bootstrap_ci95_low": low,
                        "bootstrap_ci95_high": high,
                    }
                )
    summary = pd.DataFrame(rows)
    summary.to_csv(args.output_dir / "sensitivity_summary.csv", index=False)
    (args.output_dir / "sensitivity_summary.json").write_text(
        summary.to_json(orient="records", indent=2) + "\n", encoding="utf-8"
    )
    protocol = {
        "bootstrap_iterations": args.bootstrap_iterations,
        "bootstrap_unit": "complete stochastic repetition",
        "pooling": "no pooling across sensitivity family, arm, or mode",
    }
    (args.output_dir / "analysis_protocol.json").write_text(
        json.dumps(protocol, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

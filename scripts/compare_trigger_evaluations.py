from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


METRICS = {
    "action": ("raw_action_correct", "action_correct"),
    "phase": ("raw_phase_correct", "phase_correct"),
    "updates": ("raw_updates_correct", "updates_correct"),
    "uncertainty_estimates": (
        "raw_uncertainty_estimates_correct",
        "uncertainty_estimates_correct",
    ),
    "uncertainty_recommendation": (
        "raw_uncertainty_recommended_action_correct",
        "uncertainty_recommended_action_correct",
    ),
}


def _boolean(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )
    if normalized.isna().any():
        invalid = sorted(series[normalized.isna()].astype(str).unique().tolist())
        raise ValueError(f"Expected boolean score values, found: {invalid}")
    return normalized.astype(bool)


def _cluster_bootstrap(
    frame: pd.DataFrame,
    agent_column: str,
    rule_column: str,
    *,
    repetitions: int,
    seed: int,
) -> tuple[float, float]:
    grouped = [group for _, group in frame.groupby(["scenario_id", "wording_variant"])]
    if not grouped:
        raise ValueError("Cannot bootstrap an empty paired evaluation")
    if repetitions <= 0:
        raise ValueError("Bootstrap repetitions must be positive")
    rng = np.random.default_rng(seed)
    sequence_differences = np.array(
        [
            group[agent_column].mean() - group[rule_column].mean()
            for group in grouped
        ],
        dtype=float,
    )
    sample_indices = rng.integers(
        0, len(grouped), size=(repetitions, len(grouped))
    )
    differences = sequence_differences[sample_indices].mean(axis=1)
    low, high = np.quantile(differences, [0.025, 0.975])
    return float(low), float(high)


def _exact_mcnemar(agent_correct: pd.Series, rule_correct: pd.Series) -> dict[str, float | int]:
    agent_only = int((agent_correct & ~rule_correct).sum())
    rule_only = int((rule_correct & ~agent_correct).sum())
    discordant = agent_only + rule_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, value)
            for value in range(0, min(agent_only, rule_only) + 1)
        ) / (2**discordant)
        p_value = min(1.0, 2 * tail)
    return {
        "agent_correct_rule_wrong": agent_only,
        "rule_correct_agent_wrong": rule_only,
        "discordant_pairs": discordant,
        "two_sided_exact_p_value": p_value,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a paired Agent-versus-rule Trigger report.")
    parser.add_argument(
        "--agent",
        type=Path,
        default=Path("results/revision/trigger_agent_v3_test_decisions.csv"),
    )
    parser.add_argument(
        "--rule",
        type=Path,
        default=Path("results/revision/stateful_rule_v3_test_scores.csv"),
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("results/revision/trigger_agent_vs_rule_v3_test"),
    )
    parser.add_argument("--bootstrap-repetitions", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=26_062_380)
    args = parser.parse_args()

    agent = pd.read_csv(args.agent)
    rule = pd.read_csv(args.rule)
    keys = [
        "notice_id",
        "scenario_id",
        "wording_variant",
        "benchmark_split",
        "uncertainty_case",
        "reference_action",
    ]
    paired = agent.merge(rule, on=keys, how="inner", validate="one_to_one", suffixes=("_agent", "_rule"))
    if len(paired) != len(agent) or len(paired) != len(rule):
        raise ValueError("Agent and rule files do not contain identical paired decisions")

    metric_summary = {}
    for offset, (name, (agent_source, rule_source)) in enumerate(METRICS.items()):
        agent_column = f"paired_agent_{name}"
        rule_column = f"paired_rule_{name}"
        paired[agent_column] = _boolean(paired[agent_source])
        paired[rule_column] = _boolean(paired[rule_source])
        low, high = _cluster_bootstrap(
            paired,
            agent_column,
            rule_column,
            repetitions=args.bootstrap_repetitions,
            seed=args.seed + offset,
        )
        agent_accuracy = float(paired[agent_column].mean())
        rule_accuracy = float(paired[rule_column].mean())
        metric_summary[name] = {
            "agent_accuracy": agent_accuracy,
            "rule_accuracy": rule_accuracy,
            "paired_difference": agent_accuracy - rule_accuracy,
            "paired_difference_percentage_points": 100 * (agent_accuracy - rule_accuracy),
            "sequence_cluster_bootstrap_95pct_ci": [low, high],
            "sequence_cluster_bootstrap_95pct_ci_percentage_points": [100 * low, 100 * high],
        }

    action_agent = paired["paired_agent_action"]
    action_rule = paired["paired_rule_action"]
    report = {
        "n_decisions": len(paired),
        "n_sequences": int(
            paired[["scenario_id", "wording_variant"]].drop_duplicates().shape[0]
        ),
        "benchmark_split": sorted(paired["benchmark_split"].unique().tolist()),
        "bootstrap_repetitions": args.bootstrap_repetitions,
        "bootstrap_seed": args.seed,
        "metrics": metric_summary,
        "action_discordance": _exact_mcnemar(action_agent, action_rule),
        "by_wording_variant": {},
        "by_uncertainty_case": {},
        "interpretation": (
            "Bootstrap confidence intervals resample complete scenario/wording lifecycle "
            "sequences. The exact McNemar result is supplementary because individual "
            "decisions within a lifecycle are not independent."
        ),
    }
    for group_field in ("wording_variant", "uncertainty_case"):
        destination = report[f"by_{group_field}"]
        for group_name, group in paired.groupby(group_field, sort=True):
            destination[str(group_name)] = {
                name: {
                    "agent_accuracy": float(group[f"paired_agent_{name}"].mean()),
                    "rule_accuracy": float(group[f"paired_rule_{name}"].mean()),
                    "paired_difference": float(
                        group[f"paired_agent_{name}"].mean()
                        - group[f"paired_rule_{name}"].mean()
                    ),
                }
                for name in METRICS
            }

    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    paired.to_csv(args.output_prefix.with_suffix(".paired.csv"), index=False)
    args.output_prefix.with_suffix(".summary.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PRIMARY_BASELINES = (
    (
        "agent_vs_rule_text",
        "rule_text_event_trigger",
        "Does LLM interpretation improve on a frozen stateful text rule?",
    ),
    (
        "agent_vs_numerical",
        "numerical_event_trigger",
        "Does advance unstructured information improve on causal sensor triggering?",
    ),
    (
        "agent_vs_oracle",
        "oracle_event_trigger",
        "How close is the Agent to perfectly structured advance information?",
    ),
)
ABLATION_COMPARATORS = (
    (
        "llm_trigger_contribution",
        "rule_parser_trigger_substitution",
        "What changes when the LLM Trigger is replaced by the frozen rule parser?",
    ),
    (
        "llm_pricing_contribution",
        "mathematical_pricing_substitution",
        "What changes when LLM pricing is replaced by mathematical pricing?",
    ),
    (
        "llm_evaluator_contribution",
        "evaluator_removal",
        "What changes when the LLM evaluator is removed?",
    ),
)
PAIR_COLUMNS = (
    "contrast",
    "question",
    "case",
    "variant",
    "mode",
    "repetition",
    "candidate_configuration",
    "baseline_configuration",
    "candidate_safe",
    "baseline_safe",
    "safety_outcome",
    "valid_economic_comparison",
    "mode_aligned_economic_gain",
    "aggregator_revenue_delta",
    "pto_cost_reduction",
    "candidate_llm_total_tokens",
    "candidate_llm_approximate_cost_usd",
)
CONTRAST_SUMMARY_COLUMNS = (
    "contrast",
    "question",
    "case",
    "variant",
    "mode",
    "n_pairs",
    "candidate_safe_rate",
    "baseline_safe_rate",
    "net_safety_advantage_rate",
    "candidate_only_safe_count",
    "baseline_only_safe_count",
    "both_safe_count",
    "neither_safe_count",
    "comparable_safe_pairs",
    "mode_aligned_economic_gain_mean",
    "mode_aligned_economic_gain_std",
    "mode_aligned_economic_gain_ci95_low",
    "mode_aligned_economic_gain_ci95_high",
    "economic_wins",
    "economic_ties",
    "economic_losses",
    "candidate_llm_total_tokens",
    "candidate_llm_approximate_cost_usd",
)
ABLATION_SUMMARY_COLUMNS = (
    "contrast",
    "question",
    "case",
    "variant",
    "mode",
    "candidate_configuration",
    "baseline_configuration",
    "candidate_runs",
    "baseline_runs",
    "candidate_safe_rate",
    "baseline_safe_rate",
    "net_safety_advantage_rate",
    "candidate_safe_economic_runs",
    "baseline_safe_economic_runs",
    "candidate_mode_aligned_score_mean_safe_only",
    "baseline_mode_aligned_score_mean_safe_only",
    "mode_aligned_economic_gain_mean",
    "mode_aligned_economic_gain_ci95_low",
    "mode_aligned_economic_gain_ci95_high",
    "candidate_llm_total_tokens",
    "baseline_llm_total_tokens",
    "candidate_llm_approximate_cost_usd",
    "baseline_llm_approximate_cost_usd",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def annotate_runs(
    frame: pd.DataFrame,
    *,
    reserve_tolerance_kwh: float = 1e-6,
    minimum_soc_fraction: float = 0.2,
) -> pd.DataFrame:
    required = {
        "configuration",
        "case",
        "variant",
        "mode",
        "repetition",
        "status",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("matrix_runs.csv is missing columns: " + ", ".join(sorted(missing)))

    result = frame.copy()
    complete = result["status"].astype(str).eq("complete")
    shortfall = numeric(result, "maximum_reserve_shortfall_kwh")
    violations = numeric(result, "reserve_violation_timesteps")
    minimum_soc = numeric(result, "minimum_observed_soc_fraction")
    terminal_soc = numeric(result, "terminal_minimum_soc_fraction")
    result["safety_feasible"] = (
        complete
        & shortfall.le(reserve_tolerance_kwh)
        & violations.eq(0)
        & minimum_soc.ge(minimum_soc_fraction - reserve_tolerance_kwh)
        & terminal_soc.ge(minimum_soc_fraction - reserve_tolerance_kwh)
    )
    result["economic_metric"] = np.where(
        result["mode"].eq("selfish"),
        "realized_aggregator_revenue",
        "realized_pto_cost",
    )
    revenue = numeric(result, "realized_aggregator_revenue")
    pto_cost = numeric(result, "realized_pto_cost")
    result["mode_aligned_economic_value"] = np.where(
        result["mode"].eq("selfish"), revenue, pto_cost
    )
    # Higher is always better: revenue in selfish mode, negative PTO cost in
    # altruistic mode. This is used only after both paired runs pass safety.
    result["mode_aligned_economic_score"] = np.where(
        result["mode"].eq("selfish"), revenue, -pto_cost
    )
    return result


def seed_for(parts: Iterable[Any]) -> int:
    text = "|".join(str(part) for part in parts)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def mean_std_ci(
    values: Iterable[Any],
    *,
    seed: int,
    bootstrap_iterations: int,
) -> tuple[float | None, float | None, float | None, float | None]:
    array = pd.to_numeric(pd.Series(list(values)), errors="coerce").dropna().to_numpy(float)
    if not len(array):
        return None, None, None, None
    mean = float(np.mean(array))
    std = float(np.std(array, ddof=1)) if len(array) > 1 else None
    if len(array) < 2 or bootstrap_iterations < 1:
        return mean, std, None, None
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(array), size=(bootstrap_iterations, len(array)))
    sampled_means = array[indices].mean(axis=1)
    low, high = np.quantile(sampled_means, [0.025, 0.975])
    return mean, std, float(low), float(high)


def build_method_summary(
    runs: pd.DataFrame, *, bootstrap_iterations: int
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    group_columns = [
        "case",
        "variant",
        "mode",
        "configuration",
        "method",
        "run_family",
    ]
    metric_columns = (
        "realized_aggregator_revenue",
        "realized_pto_cost",
        "realized_grid_net_cost",
        "maximum_reserve_shortfall_kwh",
        "minimum_observed_soc_fraction",
        "terminal_minimum_soc_fraction",
        "optimizer_calls",
        "llm_total_tokens",
        "llm_latency_seconds",
        "llm_approximate_cost_usd",
        "run_wall_seconds",
        "run_process_cpu_seconds",
        "run_peak_rss_mb",
        "mode_aligned_economic_score",
    )
    for keys, group in runs.groupby(group_columns, dropna=False, sort=True):
        row = dict(zip(group_columns, keys))
        complete = group["status"].astype(str).eq("complete")
        safe = group["safety_feasible"].astype(bool)
        row.update(
            {
                "n_runs": len(group),
                "n_complete": int(complete.sum()),
                "n_safe": int(safe.sum()),
                "complete_rate": float(complete.mean()),
                "safety_rate": float(safe.mean()),
            }
        )
        for metric in metric_columns:
            values = numeric(group, metric)
            mean, std, low, high = mean_std_ci(
                values,
                seed=seed_for((*keys, metric)),
                bootstrap_iterations=bootstrap_iterations,
            )
            row[f"{metric}_mean"] = mean
            row[f"{metric}_std"] = std
            row[f"{metric}_ci95_low"] = low
            row[f"{metric}_ci95_high"] = high
        safe_scores = numeric(group.loc[safe], "mode_aligned_economic_score")
        row["mode_aligned_economic_score_mean_safe_only"] = (
            float(safe_scores.mean()) if safe_scores.notna().any() else None
        )
        rows.append(row)
    return pd.DataFrame(rows)


def choose_baseline(group: pd.DataFrame, repetition: int) -> pd.Series | None:
    exact = group[numeric(group, "repetition").eq(repetition)]
    if not exact.empty:
        return exact.iloc[0]
    if not group.empty:
        return group.iloc[0]
    return None


def pair_row(
    *,
    contrast: str,
    question: str,
    candidate: pd.Series,
    baseline: pd.Series,
) -> dict[str, Any]:
    candidate_safe = bool(candidate["safety_feasible"])
    baseline_safe = bool(baseline["safety_feasible"])
    if candidate_safe and baseline_safe:
        outcome = "both_safe"
    elif candidate_safe:
        outcome = "candidate_only_safe"
    elif baseline_safe:
        outcome = "baseline_only_safe"
    else:
        outcome = "neither_safe"
    valid_economic = candidate_safe and baseline_safe
    candidate_score = float(candidate["mode_aligned_economic_score"])
    baseline_score = float(baseline["mode_aligned_economic_score"])
    revenue_delta = float(candidate["realized_aggregator_revenue"]) - float(
        baseline["realized_aggregator_revenue"]
    )
    pto_cost_reduction = float(baseline["realized_pto_cost"]) - float(
        candidate["realized_pto_cost"]
    )
    return {
        "contrast": contrast,
        "question": question,
        "case": candidate["case"],
        "variant": candidate["variant"],
        "mode": candidate["mode"],
        "repetition": int(candidate["repetition"]),
        "candidate_configuration": candidate["configuration"],
        "baseline_configuration": baseline["configuration"],
        "candidate_safe": candidate_safe,
        "baseline_safe": baseline_safe,
        "safety_outcome": outcome,
        "valid_economic_comparison": valid_economic,
        "mode_aligned_economic_gain": (
            candidate_score - baseline_score if valid_economic else None
        ),
        "aggregator_revenue_delta": revenue_delta,
        "pto_cost_reduction": pto_cost_reduction,
        "candidate_llm_total_tokens": float(candidate.get("llm_total_tokens") or 0),
        "candidate_llm_approximate_cost_usd": float(
            candidate.get("llm_approximate_cost_usd") or 0
        ),
    }


def build_primary_pairs(runs: pd.DataFrame) -> pd.DataFrame:
    agents = runs[runs["configuration"].eq("agent_trigger_only")]
    rows: list[dict[str, Any]] = []
    for _, candidate in agents.iterrows():
        for contrast, baseline_configuration, question in PRIMARY_BASELINES:
            baseline_group = runs[
                runs["configuration"].eq(baseline_configuration)
                & runs["case"].eq(candidate["case"])
                & runs["variant"].eq(candidate["variant"])
                & runs["mode"].eq(candidate["mode"])
            ]
            baseline = choose_baseline(baseline_group, int(candidate["repetition"]))
            if baseline is not None:
                rows.append(
                    pair_row(
                        contrast=contrast,
                        question=question,
                        candidate=candidate,
                        baseline=baseline,
                    )
                )
    return pd.DataFrame(rows, columns=PAIR_COLUMNS)


def independent_mean_difference_ci(
    candidate_values: Iterable[Any],
    baseline_values: Iterable[Any],
    *,
    seed: int,
    bootstrap_iterations: int,
) -> tuple[float | None, float | None, float | None]:
    candidate = (
        pd.to_numeric(pd.Series(list(candidate_values)), errors="coerce")
        .dropna()
        .to_numpy(float)
    )
    baseline = (
        pd.to_numeric(pd.Series(list(baseline_values)), errors="coerce")
        .dropna()
        .to_numpy(float)
    )
    if not len(candidate) or not len(baseline):
        return None, None, None
    difference = float(candidate.mean() - baseline.mean())
    if bootstrap_iterations < 1 or len(candidate) < 2 or len(baseline) < 2:
        return difference, None, None
    rng = np.random.default_rng(seed)
    candidate_indices = rng.integers(
        0, len(candidate), size=(bootstrap_iterations, len(candidate))
    )
    baseline_indices = rng.integers(
        0, len(baseline), size=(bootstrap_iterations, len(baseline))
    )
    differences = (
        candidate[candidate_indices].mean(axis=1)
        - baseline[baseline_indices].mean(axis=1)
    )
    low, high = np.quantile(differences, [0.025, 0.975])
    return difference, float(low), float(high)


def build_ablation_contrasts(
    runs: pd.DataFrame, *, bootstrap_iterations: int
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cells = runs[["case", "variant", "mode"]].drop_duplicates()
    for cell in cells.itertuples(index=False):
        common = (
            runs["case"].eq(cell.case)
            & runs["variant"].eq(cell.variant)
            & runs["mode"].eq(cell.mode)
        )
        candidate = runs[common & runs["configuration"].eq("full_agentic")]
        if candidate.empty:
            continue
        for contrast, baseline_configuration, question in ABLATION_COMPARATORS:
            baseline = runs[
                common & runs["configuration"].eq(baseline_configuration)
            ]
            if baseline.empty:
                continue
            candidate_safe = candidate[candidate["safety_feasible"].astype(bool)]
            baseline_safe = baseline[baseline["safety_feasible"].astype(bool)]
            difference, low, high = independent_mean_difference_ci(
                numeric(candidate_safe, "mode_aligned_economic_score"),
                numeric(baseline_safe, "mode_aligned_economic_score"),
                seed=seed_for(
                    (contrast, cell.case, cell.variant, cell.mode)
                ),
                bootstrap_iterations=bootstrap_iterations,
            )
            candidate_scores = numeric(
                candidate_safe, "mode_aligned_economic_score"
            )
            baseline_scores = numeric(baseline_safe, "mode_aligned_economic_score")
            rows.append(
                {
                    "contrast": contrast,
                    "question": question,
                    "case": cell.case,
                    "variant": cell.variant,
                    "mode": cell.mode,
                    "candidate_configuration": "full_agentic",
                    "baseline_configuration": baseline_configuration,
                    "candidate_runs": len(candidate),
                    "baseline_runs": len(baseline),
                    "candidate_safe_rate": float(candidate["safety_feasible"].mean()),
                    "baseline_safe_rate": float(baseline["safety_feasible"].mean()),
                    "net_safety_advantage_rate": float(
                        candidate["safety_feasible"].mean()
                        - baseline["safety_feasible"].mean()
                    ),
                    "candidate_safe_economic_runs": len(candidate_safe),
                    "baseline_safe_economic_runs": len(baseline_safe),
                    "candidate_mode_aligned_score_mean_safe_only": (
                        float(candidate_scores.mean())
                        if candidate_scores.notna().any()
                        else None
                    ),
                    "baseline_mode_aligned_score_mean_safe_only": (
                        float(baseline_scores.mean())
                        if baseline_scores.notna().any()
                        else None
                    ),
                    "mode_aligned_economic_gain_mean": difference,
                    "mode_aligned_economic_gain_ci95_low": low,
                    "mode_aligned_economic_gain_ci95_high": high,
                    "candidate_llm_total_tokens": float(
                        numeric(candidate, "llm_total_tokens").sum()
                    ),
                    "baseline_llm_total_tokens": float(
                        numeric(baseline, "llm_total_tokens").sum()
                    ),
                    "candidate_llm_approximate_cost_usd": float(
                        numeric(candidate, "llm_approximate_cost_usd").sum()
                    ),
                    "baseline_llm_approximate_cost_usd": float(
                        numeric(baseline, "llm_approximate_cost_usd").sum()
                    ),
                }
            )
    return pd.DataFrame(rows, columns=ABLATION_SUMMARY_COLUMNS)


def summarize_pairs(
    pairs: pd.DataFrame,
    *,
    bootstrap_iterations: int,
    economic_tie_tolerance: float = 1e-3,
) -> pd.DataFrame:
    if pairs.empty:
        return pd.DataFrame(columns=CONTRAST_SUMMARY_COLUMNS)
    rows: list[dict[str, Any]] = []
    group_columns = ["contrast", "question", "case", "variant", "mode"]
    for keys, group in pairs.groupby(group_columns, dropna=False, sort=True):
        outcomes = group["safety_outcome"].value_counts()
        valid = group[group["valid_economic_comparison"].astype(bool)]
        gains = numeric(valid, "mode_aligned_economic_gain")
        mean, std, low, high = mean_std_ci(
            gains,
            seed=seed_for(keys),
            bootstrap_iterations=bootstrap_iterations,
        )
        rows.append(
            {
                **dict(zip(group_columns, keys)),
                "n_pairs": len(group),
                "candidate_safe_rate": float(group["candidate_safe"].mean()),
                "baseline_safe_rate": float(group["baseline_safe"].mean()),
                "net_safety_advantage_rate": float(
                    group["candidate_safe"].mean() - group["baseline_safe"].mean()
                ),
                "candidate_only_safe_count": int(outcomes.get("candidate_only_safe", 0)),
                "baseline_only_safe_count": int(outcomes.get("baseline_only_safe", 0)),
                "both_safe_count": int(outcomes.get("both_safe", 0)),
                "neither_safe_count": int(outcomes.get("neither_safe", 0)),
                "comparable_safe_pairs": len(valid),
                "mode_aligned_economic_gain_mean": mean,
                "mode_aligned_economic_gain_std": std,
                "mode_aligned_economic_gain_ci95_low": low,
                "mode_aligned_economic_gain_ci95_high": high,
                "economic_wins": int(gains.gt(economic_tie_tolerance).sum()),
                "economic_ties": int(gains.abs().le(economic_tie_tolerance).sum()),
                "economic_losses": int(gains.lt(-economic_tie_tolerance).sum()),
                "candidate_llm_total_tokens": float(
                    numeric(group, "candidate_llm_total_tokens").sum()
                ),
                "candidate_llm_approximate_cost_usd": float(
                    numeric(group, "candidate_llm_approximate_cost_usd").sum()
                ),
            }
        )
    return pd.DataFrame(rows, columns=CONTRAST_SUMMARY_COLUMNS)


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create safety-first paired CSV/JSON summaries from the advance-warning "
            "experiment matrix."
        )
    )
    parser.add_argument(
        "--runs",
        type=Path,
        default=Path("results/revision/closed_loop/matrix_runs.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/revision/advance_warning_analysis"),
    )
    parser.add_argument("--reserve-tolerance-kwh", type=float, default=1e-6)
    parser.add_argument("--minimum-soc-fraction", type=float, default=0.2)
    parser.add_argument(
        "--economic-tie-tolerance",
        type=float,
        default=1e-3,
        help="Absolute mode-aligned score difference counted as an economic tie.",
    )
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    args = parser.parse_args()

    runs_path = args.runs if args.runs.is_absolute() else ROOT / args.runs
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    if not runs_path.exists():
        raise SystemExit(f"Run index not found: {runs_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    runs = annotate_runs(
        pd.read_csv(runs_path),
        reserve_tolerance_kwh=args.reserve_tolerance_kwh,
        minimum_soc_fraction=args.minimum_soc_fraction,
    )
    method_summary = build_method_summary(
        runs, bootstrap_iterations=args.bootstrap_iterations
    )
    primary_pairs = build_primary_pairs(runs)
    primary_summary = summarize_pairs(
        primary_pairs,
        bootstrap_iterations=args.bootstrap_iterations,
        economic_tie_tolerance=args.economic_tie_tolerance,
    )
    ablation_runs = runs[
        runs["configuration"].isin(
            [
                "full_agentic",
                "rule_parser_trigger_substitution",
                "mathematical_pricing_substitution",
                "evaluator_removal",
            ]
        )
    ].copy()
    ablation_summary = build_ablation_contrasts(
        runs, bootstrap_iterations=args.bootstrap_iterations
    )

    outputs = {
        "run_level_annotated.csv": runs,
        "method_summary.csv": method_summary,
        "primary_paired_runs.csv": primary_pairs,
        "primary_contrasts.csv": primary_summary,
        "ablation_run_level.csv": ablation_runs,
        "ablation_contrasts.csv": ablation_summary,
    }
    for filename, frame in outputs.items():
        frame.to_csv(output_dir / filename, index=False)

    summary = {
        "protocol_version": "advance_warning_analysis_v2",
        "source": {
            "path": str(runs_path),
            "sha256": sha256(runs_path),
            "rows": len(runs),
        },
        "safety_rule": {
            "complete_run_required": True,
            "maximum_reserve_shortfall_kwh": args.reserve_tolerance_kwh,
            "reserve_violation_timesteps": 0,
            "minimum_observed_soc_fraction": args.minimum_soc_fraction,
            "terminal_minimum_soc_fraction": args.minimum_soc_fraction,
        },
        "economic_rule": {
            "selfish": "higher realized aggregator revenue is better",
            "altruistic": "lower realized PTO cost is better",
            "paired_economic_effect_reported_only_when_both_runs_are_safe": True,
            "absolute_tie_tolerance": args.economic_tie_tolerance,
        },
        "uncertainty": {
            "method": "nonparametric bootstrap over repeated runs within each case-mode cell",
            "iterations": args.bootstrap_iterations,
            "single_deterministic_runs_have_no_confidence_interval": True,
        },
        "primary_contrasts": records(primary_summary),
        "ablation_contrasts": records(ablation_summary),
    }
    (output_dir / "analysis_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote safety-first analysis to {output_dir}")
    if primary_pairs.empty:
        print("No Agent Trigger repetitions are indexed yet; primary contrasts are empty.")
    if ablation_summary.empty:
        print("No role-ablation runs are indexed yet; ablation contrasts are empty.")


if __name__ == "__main__":
    main()

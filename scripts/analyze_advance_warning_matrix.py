from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OBJECTIVE_TIE_TOLERANCE = 1e-3
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
        "What changes when LLM pricing is replaced by the deterministic price-zone heuristic?",
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
    "candidate_evaluator_acceptance_rate",
    "baseline_evaluator_acceptance_rate",
    "candidate_forced_selection_rate",
    "baseline_forced_selection_rate",
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
SECONDARY_OUTCOME_METRICS = (
    (
        "downward_flexibility_cheap_buy_kwh",
        "Cheap-period charging (downward flexibility delivered)",
        "higher",
    ),
    (
        "upward_flexibility_expensive_sell_kwh",
        "Expensive-period V2G export (upward flexibility delivered)",
        "higher",
    ),
    (
        "price_aligned_flexibility_kwh",
        "Cheap charging plus expensive V2G export",
        "higher",
    ),
    (
        "cheap_period_buy_share",
        "Share of charging completed in cheap-price intervals",
        "higher",
    ),
    (
        "expensive_period_sell_share",
        "Share of V2G export completed in expensive-price intervals",
        "higher",
    ),
    (
        "energy_weighted_average_buy_grid_price",
        "Energy-weighted grid price when charging",
        "lower",
    ),
    (
        "energy_weighted_average_sell_grid_price",
        "Energy-weighted grid price when exporting",
        "higher",
    ),
    (
        "peak_net_import_kwh_per_interval",
        "Maximum realized net import in one 30-minute interval",
        "lower",
    ),
    (
        "battery_throughput_proxy_kwh",
        "Grid-side charge plus discharge energy (cycling proxy)",
        "descriptive",
    ),
)
SECONDARY_CONTRAST_COLUMNS = (
    "contrast",
    "question",
    "case",
    "variant",
    "mode",
    "candidate_configuration",
    "baseline_configuration",
    "metric",
    "metric_label",
    "preferred_direction",
    "candidate_feasible_runs",
    "baseline_feasible_runs",
    "candidate_mean_feasible_only",
    "baseline_mean_feasible_only",
    "raw_candidate_minus_baseline",
    "raw_difference_ci95_low",
    "raw_difference_ci95_high",
    "benefit_aligned_difference",
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


def settlement_secondary_outcomes(settlement: pd.DataFrame) -> dict[str, Any]:
    """Compute transparent price-aligned grid-flexibility outcomes for one run."""

    required = {
        "spot_price",
        "realized_buy_kwh",
        "realized_sell_kwh",
    }
    missing = required - set(settlement.columns)
    if missing:
        raise ValueError(
            "ex_post_settlement is missing columns: " + ", ".join(sorted(missing))
        )
    prices = pd.to_numeric(settlement["spot_price"], errors="coerce")
    buy = pd.to_numeric(settlement["realized_buy_kwh"], errors="coerce").fillna(0.0)
    sell = pd.to_numeric(settlement["realized_sell_kwh"], errors="coerce").fillna(0.0)
    if prices.isna().any() or settlement.empty:
        raise ValueError("ex_post_settlement must contain a spot price for every interval")

    price_minimum = float(prices.min())
    price_maximum = float(prices.max())
    spread = price_maximum - price_minimum
    cheap_threshold = price_minimum + spread / 3.0
    expensive_threshold = price_minimum + 2.0 * spread / 3.0
    cheap = prices.le(cheap_threshold + 1e-12)
    expensive = prices.ge(expensive_threshold - 1e-12)

    total_buy = float(buy.sum())
    total_sell = float(sell.sum())
    cheap_buy = float(buy.loc[cheap].sum())
    expensive_sell = float(sell.loc[expensive].sum())
    net_import = buy - sell

    return {
        "cheap_price_threshold": cheap_threshold,
        "expensive_price_threshold": expensive_threshold,
        "downward_flexibility_cheap_buy_kwh": cheap_buy,
        "upward_flexibility_expensive_sell_kwh": expensive_sell,
        "price_aligned_flexibility_kwh": cheap_buy + expensive_sell,
        "cheap_period_buy_share": cheap_buy / total_buy if total_buy > 0 else np.nan,
        "expensive_period_sell_share": (
            expensive_sell / total_sell if total_sell > 0 else np.nan
        ),
        "energy_weighted_average_buy_grid_price": (
            float((prices * buy).sum() / total_buy) if total_buy > 0 else np.nan
        ),
        "energy_weighted_average_sell_grid_price": (
            float((prices * sell).sum() / total_sell) if total_sell > 0 else np.nan
        ),
        "peak_net_import_kwh_per_interval": float(net_import.max()),
        "peak_net_import_kw": float(net_import.max()) * 2.0,
        "peak_net_export_kwh_per_interval": float((-net_import).clip(lower=0).max()),
        "battery_throughput_proxy_kwh": total_buy + total_sell,
        "settlement_realized_buy_kwh": total_buy,
        "settlement_realized_sell_kwh": total_sell,
    }


def enrich_with_secondary_outcomes(
    runs: pd.DataFrame, *, repository_root: Path = ROOT
) -> pd.DataFrame:
    """Attach interval-derived secondary outcomes without altering source workbooks."""

    if "workbook" not in runs:
        raise ValueError("matrix_runs.csv must contain a workbook column")
    rows: list[dict[str, Any]] = []
    for run in runs.itertuples(index=False):
        workbook = Path(str(run.workbook))
        if not workbook.is_absolute():
            workbook = repository_root / workbook
        if not workbook.exists():
            raise FileNotFoundError(f"Run workbook not found: {workbook}")
        outcomes = settlement_secondary_outcomes(
            pd.read_excel(workbook, sheet_name="ex_post_settlement")
        )
        indexed_buy = float(getattr(run, "realized_buy_kwh", np.nan))
        indexed_sell = float(getattr(run, "realized_sell_kwh", np.nan))
        outcomes["settlement_reconciliation_ok"] = bool(
            np.isclose(outcomes["settlement_realized_buy_kwh"], indexed_buy, atol=1e-6)
            and np.isclose(
                outcomes["settlement_realized_sell_kwh"], indexed_sell, atol=1e-6
            )
        )
        rows.append(outcomes)
    return pd.concat(
        [runs.reset_index(drop=True), pd.DataFrame(rows).reset_index(drop=True)], axis=1
    )


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
        "realized_buy_kwh",
        "realized_sell_kwh",
        "downward_flexibility_cheap_buy_kwh",
        "upward_flexibility_expensive_sell_kwh",
        "price_aligned_flexibility_kwh",
        "cheap_period_buy_share",
        "expensive_period_sell_share",
        "energy_weighted_average_buy_grid_price",
        "energy_weighted_average_sell_grid_price",
        "peak_net_import_kwh_per_interval",
        "peak_net_import_kw",
        "peak_net_export_kwh_per_interval",
        "battery_throughput_proxy_kwh",
        "maximum_reserve_shortfall_kwh",
        "minimum_observed_soc_fraction",
        "terminal_minimum_soc_fraction",
        "optimizer_calls",
        "evaluator_accepted_optimizer_calls",
        "forced_optimizer_selections",
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
            candidate_decisions = numeric(candidate, "optimize_decisions").sum()
            baseline_decisions = numeric(baseline, "optimize_decisions").sum()
            candidate_evaluator_accepts = numeric(
                candidate, "evaluator_accepted_optimizer_calls"
            ).sum()
            baseline_evaluator_accepts = numeric(
                baseline, "evaluator_accepted_optimizer_calls"
            ).sum()
            candidate_forced = numeric(
                candidate, "forced_optimizer_selections"
            ).sum()
            baseline_forced = numeric(
                baseline, "forced_optimizer_selections"
            ).sum()
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
                    "candidate_evaluator_acceptance_rate": (
                        float(candidate_evaluator_accepts / candidate_decisions)
                        if candidate_decisions
                        else None
                    ),
                    "baseline_evaluator_acceptance_rate": (
                        float(baseline_evaluator_accepts / baseline_decisions)
                        if baseline_decisions
                        else None
                    ),
                    "candidate_forced_selection_rate": (
                        float(candidate_forced / candidate_decisions)
                        if candidate_decisions
                        else None
                    ),
                    "baseline_forced_selection_rate": (
                        float(baseline_forced / baseline_decisions)
                        if baseline_decisions
                        else None
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


def build_secondary_outcome_contrasts(
    runs: pd.DataFrame, *, bootstrap_iterations: int
) -> pd.DataFrame:
    """Compare full-agent outcomes with each single-role substitution.

    Only runs meeting realized operational feasibility criteria enter these
    outcome comparisons. Raw differences are always full Agent minus baseline;
    benefit-aligned differences reverse the sign for metrics where lower is
    preferable and remain blank for descriptive-only throughput.
    """

    rows: list[dict[str, Any]] = []
    cells = runs[["case", "variant", "mode"]].drop_duplicates()
    for cell in cells.itertuples(index=False):
        common = (
            runs["case"].eq(cell.case)
            & runs["variant"].eq(cell.variant)
            & runs["mode"].eq(cell.mode)
        )
        candidate = runs[
            common
            & runs["configuration"].eq("full_agentic")
            & runs["safety_feasible"].astype(bool)
        ]
        if candidate.empty:
            continue
        for contrast, baseline_configuration, question in ABLATION_COMPARATORS:
            baseline = runs[
                common
                & runs["configuration"].eq(baseline_configuration)
                & runs["safety_feasible"].astype(bool)
            ]
            if baseline.empty:
                continue
            for metric, label, preferred_direction in SECONDARY_OUTCOME_METRICS:
                difference, low, high = independent_mean_difference_ci(
                    numeric(candidate, metric),
                    numeric(baseline, metric),
                    seed=seed_for(
                        (
                            "secondary",
                            contrast,
                            cell.case,
                            cell.variant,
                            cell.mode,
                            metric,
                        )
                    ),
                    bootstrap_iterations=bootstrap_iterations,
                )
                if difference is None or preferred_direction == "descriptive":
                    benefit_aligned = None
                elif preferred_direction == "higher":
                    benefit_aligned = difference
                else:
                    benefit_aligned = -difference
                rows.append(
                    {
                        "contrast": contrast,
                        "question": question,
                        "case": cell.case,
                        "variant": cell.variant,
                        "mode": cell.mode,
                        "candidate_configuration": "full_agentic",
                        "baseline_configuration": baseline_configuration,
                        "metric": metric,
                        "metric_label": label,
                        "preferred_direction": preferred_direction,
                        "candidate_feasible_runs": len(candidate),
                        "baseline_feasible_runs": len(baseline),
                        "candidate_mean_feasible_only": float(
                            numeric(candidate, metric).mean()
                        ),
                        "baseline_mean_feasible_only": float(
                            numeric(baseline, metric).mean()
                        ),
                        "raw_candidate_minus_baseline": difference,
                        "raw_difference_ci95_low": low,
                        "raw_difference_ci95_high": high,
                        "benefit_aligned_difference": benefit_aligned,
                    }
                )
    return pd.DataFrame(rows, columns=SECONDARY_CONTRAST_COLUMNS)


def _feedback_reason(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("reason")
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed.get("reason") if isinstance(parsed, dict) else None


def build_evaluator_attempt_audit(
    runs: pd.DataFrame, *, repository_root: Path = ROOT
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read saved attempt summaries and detect evaluator/rerun inconsistencies."""

    attempt_frames: list[pd.DataFrame] = []
    metadata_columns = [
        "run_id",
        "configuration",
        "case",
        "variant",
        "mode",
        "repetition",
        "workbook",
    ]
    for run in runs.itertuples(index=False):
        workbook = Path(str(run.workbook))
        if not workbook.is_absolute():
            workbook = repository_root / workbook
        attempts = pd.read_excel(workbook, sheet_name="optimization_attempts")
        if attempts.empty:
            continue
        for column in metadata_columns:
            attempts[column] = getattr(run, column)
        attempts["feedback_reason"] = attempts.get(
            "feedback", pd.Series(index=attempts.index, dtype=object)
        ).map(_feedback_reason)
        attempts["rejected_negative_pto_cost_violation"] = (
            attempts["mode"].eq("altruistic")
            & pd.to_numeric(attempts.get("pto_daily_cost"), errors="coerce").lt(0)
            & ~attempts.get("evaluator_accepted", False).astype(bool)
            & ~attempts.get("is_mock", False).astype(bool)
        )
        attempts["rejection_feedback_no_op_on_current_schedule"] = (
            attempts["feedback_reason"].eq("cost_too_high")
            & pd.to_numeric(attempts.get("total_kwh_bought"), errors="coerce")
            .fillna(0.0)
            .abs()
            .le(1e-9)
        )
        attempt_frames.append(attempts)
    if not attempt_frames:
        return pd.DataFrame(), pd.DataFrame()

    attempt_audit = pd.concat(attempt_frames, ignore_index=True, sort=False)
    decisions: list[dict[str, Any]] = []
    group_columns = [
        "run_id",
        "configuration",
        "case",
        "variant",
        "mode",
        "repetition",
        "workbook",
        "timestep",
    ]
    for keys, group in attempt_audit.groupby(group_columns, sort=True, dropna=False):
        group = group.sort_values("attempt")
        mode = str(group["mode"].iloc[0])
        objective_column = (
            "aggregator_revenue" if mode == "selfish" else "pto_daily_cost"
        )
        objective = pd.to_numeric(group[objective_column], errors="coerce")
        solver_status = group.get(
            "solver_status", pd.Series("", index=group.index)
        ).astype(str).str.lower()
        usable = (
            ~group.get("is_mock", False).astype(bool)
            & ~solver_status.isin({"infeasible", "error", "unknown", "mock"})
            & objective.notna()
        )
        usable_group = group.loc[usable]
        usable_objective = objective.loc[usable]
        if usable_group.empty:
            best_index = None
        elif mode == "selfish":
            best_index = usable_objective.idxmax()
        else:
            best_index = usable_objective.idxmin()

        selected_group = group[group.get("accepted", False).astype(bool)]
        selected_index = selected_group.index[0] if not selected_group.empty else None
        evaluator_accepted_group = group[
            group.get("evaluator_accepted", False).astype(bool)
        ]
        evaluator_accepted_index = (
            evaluator_accepted_group.index[0]
            if not evaluator_accepted_group.empty
            else None
        )

        best_value = float(objective.loc[best_index]) if best_index is not None else None
        selected_value = (
            float(objective.loc[selected_index]) if selected_index is not None else None
        )
        selected_is_best = bool(
            best_index is not None
            and selected_index is not None
            and np.isclose(
                best_value,
                selected_value,
                rtol=0.0,
                atol=OBJECTIVE_TIE_TOLERANCE,
            )
        )
        accepted_rerun_worse = False
        if evaluator_accepted_index is not None:
            accepted_attempt = int(group.loc[evaluator_accepted_index, "attempt"])
            earlier = group[
                group["attempt"].lt(accepted_attempt) & usable
            ]
            if not earlier.empty:
                accepted_value = float(objective.loc[evaluator_accepted_index])
                earlier_values = pd.to_numeric(
                    earlier[objective_column], errors="coerce"
                )
                accepted_rerun_worse = bool(
                    earlier_values.max()
                    > accepted_value + OBJECTIVE_TIE_TOLERANCE
                    if mode == "selfish"
                    else earlier_values.min()
                    < accepted_value - OBJECTIVE_TIE_TOLERANCE
                )
        if best_value is None or selected_value is None:
            objective_regret = None
        elif mode == "selfish":
            objective_regret = best_value - selected_value
        else:
            objective_regret = selected_value - best_value

        decisions.append(
            {
                **dict(zip(group_columns, keys)),
                "attempt_count": len(group),
                "objective_metric": objective_column,
                "best_usable_attempt": (
                    int(group.loc[best_index, "attempt"])
                    if best_index is not None
                    else None
                ),
                "selected_attempt": (
                    int(group.loc[selected_index, "attempt"])
                    if selected_index is not None
                    else None
                ),
                "evaluator_accepted_attempt": (
                    int(group.loc[evaluator_accepted_index, "attempt"])
                    if evaluator_accepted_index is not None
                    else None
                ),
                "best_usable_objective": best_value,
                "selected_objective": selected_value,
                "selected_is_best_usable_objective": selected_is_best,
                "selected_objective_regret": objective_regret,
                "accepted_rerun_worse_than_earlier_usable_attempt": accepted_rerun_worse,
                "rejected_negative_pto_cost_count": int(
                    group["rejected_negative_pto_cost_violation"].sum()
                ),
                "no_op_rejection_feedback_count": int(
                    group["rejection_feedback_no_op_on_current_schedule"].sum()
                ),
            }
        )
    return attempt_audit, pd.DataFrame(decisions)


def _flatten_multiplier_vectors(values: Iterable[Any]) -> np.ndarray:
    flattened: list[float] = []
    for value in values:
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, list):
            flattened.extend(
                float(item) for item in parsed if item is not None
            )
    return np.asarray(flattened, dtype=float)


def build_pricing_multiplier_summary(attempts: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if attempts.empty:
        return pd.DataFrame()
    for (configuration, mode), group in attempts.groupby(
        ["configuration", "mode"], sort=True
    ):
        for scope, scoped in (
            ("all_attempts", group),
            ("initial_attempts", group[group["attempt"].eq(1)]),
        ):
            buy = _flatten_multiplier_vectors(scoped["buy_multipliers"])
            sell = _flatten_multiplier_vectors(scoped["sell_multipliers"])
            row = {
                    "configuration": configuration,
                    "mode": mode,
                    "scope": scope,
                    "optimizer_attempts": len(scoped),
                    "buy_multiplier_values": len(buy),
                    "buy_multiplier_min": float(buy.min()) if len(buy) else None,
                    "buy_multiplier_mean": float(buy.mean()) if len(buy) else None,
                    "buy_multiplier_max": float(buy.max()) if len(buy) else None,
                    "sell_multiplier_values": len(sell),
                    "sell_multiplier_min": float(sell.min()) if len(sell) else None,
                    "sell_multiplier_mean": float(sell.mean()) if len(sell) else None,
                    "sell_multiplier_max": float(sell.max()) if len(sell) else None,
                }
            for metric in (
                "reference_buy_arithmetic_mean",
                "chosen_buy_arithmetic_mean",
                "buy_arithmetic_mean_gap",
                "reference_sell_arithmetic_mean",
                "chosen_sell_arithmetic_mean",
                "sell_arithmetic_mean_gap",
                "buy_centered_temporal_mae",
                "sell_centered_temporal_mae",
                "chosen_buy_dispatch_weighted_mean",
                "chosen_sell_dispatch_weighted_mean",
            ):
                if metric in scoped.columns:
                    values = pd.to_numeric(scoped[metric], errors="coerce").dropna()
                    row[f"mean_{metric}"] = float(values.mean()) if len(values) else None
            rows.append(row)
    return pd.DataFrame(rows)


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
            "Create feasibility-first paired CSV/JSON summaries from the advance-warning "
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
    runs = enrich_with_secondary_outcomes(runs)
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
    secondary_outcome_contrasts = build_secondary_outcome_contrasts(
        runs, bootstrap_iterations=args.bootstrap_iterations
    )
    evaluator_attempts, evaluator_decision_audit = build_evaluator_attempt_audit(runs)
    pricing_multiplier_summary = build_pricing_multiplier_summary(
        evaluator_attempts
    )

    outputs = {
        "run_level_annotated.csv": runs,
        "method_summary.csv": method_summary,
        "primary_paired_runs.csv": primary_pairs,
        "primary_contrasts.csv": primary_summary,
        "ablation_run_level.csv": ablation_runs,
        "ablation_contrasts.csv": ablation_summary,
        "secondary_outcome_contrasts.csv": secondary_outcome_contrasts,
        "evaluator_attempt_audit.csv": evaluator_attempts,
        "evaluator_decision_audit.csv": evaluator_decision_audit,
        "pricing_multiplier_summary.csv": pricing_multiplier_summary,
    }
    for filename, frame in outputs.items():
        frame.to_csv(output_dir / filename, index=False)

    summary = {
        "protocol_version": "advance_warning_analysis_v4",
        "source": {
            "path": str(runs_path),
            "sha256": sha256(runs_path),
            "rows": len(runs),
        },
        "realized_operational_feasibility_rule": {
            "complete_run_required": True,
            "maximum_reserve_shortfall_kwh": args.reserve_tolerance_kwh,
            "reserve_violation_timesteps": 0,
            "minimum_observed_soc_fraction": args.minimum_soc_fraction,
            "terminal_minimum_soc_fraction": args.minimum_soc_fraction,
        },
        "economic_rule": {
            "selfish": "higher realized aggregator revenue is better",
            "altruistic": "lower realized PTO cost is better",
            "paired_economic_effect_reported_only_when_both_runs_are_realized_operationally_feasible": True,
            "absolute_tie_tolerance": args.economic_tie_tolerance,
        },
        "secondary_outcome_rule": {
            "cheap_price_zone": "spot price in the lower third of the realized daily min-max range",
            "expensive_price_zone": "spot price in the upper third of the realized daily min-max range",
            "downward_flexibility": "realized charging energy in cheap-price intervals",
            "upward_flexibility": "realized V2G export energy in expensive-price intervals",
            "battery_throughput_proxy": "realized grid-side charging plus V2G export energy; descriptive cycling proxy, not a degradation model",
            "secondary_contrasts_use_realized_operationally_feasible_runs_only": True,
            "all_settlement_totals_reconcile_with_matrix_index": bool(
                runs["settlement_reconciliation_ok"].all()
            ),
        },
        "evaluator_audit": {
            "acceptance_rate_denominator": "optimize decisions",
            "forced_selection_reported_separately": True,
            "rerun_cap_is_not_automatic_evaluator_acceptance": True,
            "decision_groups": len(evaluator_decision_audit),
            "accepted_rerun_worse_than_earlier_usable_attempts": int(
                evaluator_decision_audit.get(
                    "accepted_rerun_worse_than_earlier_usable_attempt",
                    pd.Series(dtype=bool),
                ).sum()
            ),
            "selected_schedules_not_best_usable_objective": int(
                (~evaluator_decision_audit.get(
                    "selected_is_best_usable_objective",
                    pd.Series(dtype=bool),
                ).astype(bool)).sum()
            ),
            "rejected_negative_pto_cost_attempts": int(
                evaluator_attempts.get(
                    "rejected_negative_pto_cost_violation", pd.Series(dtype=bool)
                ).sum()
            ),
            "no_op_rejection_feedback_attempts": int(
                evaluator_attempts.get(
                    "rejection_feedback_no_op_on_current_schedule",
                    pd.Series(dtype=bool),
                ).sum()
            ),
        },
        "uncertainty": {
            "method": "nonparametric bootstrap over repeated runs within each case-mode cell",
            "iterations": args.bootstrap_iterations,
            "single_deterministic_runs_have_no_confidence_interval": True,
        },
        "primary_contrasts": records(primary_summary),
        "ablation_contrasts": records(ablation_summary),
        "secondary_outcome_contrasts": records(secondary_outcome_contrasts),
        "pricing_multiplier_summary": records(pricing_multiplier_summary),
    }
    (output_dir / "analysis_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote feasibility-first analysis to {output_dir}")
    if primary_pairs.empty:
        print("No Agent Trigger repetitions are indexed yet; primary contrasts are empty.")
    if ablation_summary.empty:
        print("No role-ablation runs are indexed yet; ablation contrasts are empty.")


if __name__ == "__main__":
    main()

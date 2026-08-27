"""Build manuscript-ready CSV tables and figures from frozen revision results."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "paper_outputs" / "revision"
TABLES = OUTPUT / "tables"
FIGURES = OUTPUT / "figures"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(frame: pd.DataFrame, name: str) -> Path:
    path = TABLES / name
    frame.to_csv(path, index=False, lineterminator="\n")
    return path


def aggregator_revenue(payload: dict) -> float:
    buy = np.asarray(payload["w_buy"], dtype=float)
    sell = np.asarray(payload["w_sell"], dtype=float)
    grid_buy = np.asarray(payload["S_buy"], dtype=float)
    grid_sell = np.asarray(payload["S_sell"], dtype=float)
    policy = payload["pricing_policy"]
    buy_multiplier = float(policy["buy_multiplier"])
    sell_multiplier = float(policy["sell_multiplier"])
    return float(
        np.sum(buy * grid_buy * (buy_multiplier - 1.0))
        + np.sum(sell * grid_sell * (1.0 - sell_multiplier))
    )


def day_ahead_table() -> pd.DataFrame:
    reconciliation_path = (
        ROOT
        / "results/revision/day_ahead_reconciliation_v1/day_ahead_cost_reconciliation.csv"
    )
    reconciliation = pd.read_csv(reconciliation_path).set_index("strategy")
    ladder_root = ROOT / "results/revision/day_ahead_ladder_v1"
    ladder_manifest = json.loads((ladder_root / "ladder_manifest.json").read_text())
    ladder = {row["scenario"]: row for row in ladder_manifest["scenarios"]}
    fixed = json.loads((ladder_root / "S2p5_v2g_fixed_margin.json").read_text())
    passthrough = json.loads((ladder_root / "S2p5_v2g_passthrough.json").read_text())

    def reconciled(strategy: str, label: str, v2g: str, ai_layer: str) -> dict:
        row = reconciliation.loc[strategy]
        return {
            "id": strategy,
            "strategy": label,
            "v2g": v2g,
            "ai_layer": ai_layer,
            "pto_cost_eur_per_day": row["recommended_cost_eur"],
            "aggregator_revenue_eur_per_day": row["recommended_revenue_eur"],
            "bought_kwh_per_day": row["recommended_bought_kwh"],
            "sold_kwh_per_day": row["recommended_sold_kwh"],
            "reporting_basis": "same-interval optimizer settlement",
        }

    rows = [
        reconciled("S1", "Dumb charging", "off", "none"),
        reconciled("S2", "Smart charging, no V2G", "off", "none"),
        {
            "id": "S2.5a",
            "strategy": "Smart V2G, fixed 1.05/0.80 tariff multipliers",
            "v2g": "on",
            "ai_layer": "none",
            "pto_cost_eur_per_day": ladder["S2p5_v2g_fixed_margin"]["pto_daily_cost"],
            "aggregator_revenue_eur_per_day": aggregator_revenue(fixed),
            "bought_kwh_per_day": ladder["S2p5_v2g_fixed_margin"]["total_kwh_bought"],
            "sold_kwh_per_day": ladder["S2p5_v2g_fixed_margin"]["total_kwh_sold"],
            "reporting_basis": "same-interval optimizer settlement",
        },
        {
            "id": "S2.5b",
            "strategy": "Smart V2G, spot passthrough",
            "v2g": "on",
            "ai_layer": "none",
            "pto_cost_eur_per_day": ladder["S2p5_v2g_passthrough"]["pto_daily_cost"],
            "aggregator_revenue_eur_per_day": aggregator_revenue(passthrough),
            "bought_kwh_per_day": ladder["S2p5_v2g_passthrough"]["total_kwh_bought"],
            "sold_kwh_per_day": ladder["S2p5_v2g_passthrough"]["total_kwh_sold"],
            "reporting_basis": "same-interval optimizer settlement",
        },
        reconciled("S3", "Profit-based agentic aggregator", "on", "Pricing + Evaluator"),
        reconciled("S4", "Operational agentic aggregator", "on", "Pricing + Evaluator"),
    ]
    return pd.DataFrame(rows)


def trigger_table() -> pd.DataFrame:
    inputs = [
        (
            ROOT / "results/revision/extended_disturbances_v3/analysis/method_summary.csv",
            {
                "aw_extended_energy_shift": "Sustained route-energy shift",
            },
        ),
        (
            ROOT / "results/revision/price_escalation_v3/analysis/method_summary.csv",
            {"aw_route6_late_return": "Route warning + price escalation"},
        ),
    ]
    pieces = []
    for path, labels in inputs:
        frame = pd.read_csv(path)
        frame = frame.loc[frame["case"].isin(labels)].copy()
        frame["case_label"] = frame["case"].map(labels)
        pieces.append(frame)
    recoverable = pd.read_csv(
        ROOT / "results/revision/recoverable_cluster_v1/matrix_runs.csv"
    )
    recoverable["operationally_feasible"] = (
        recoverable["status"].eq("complete")
        & recoverable["timesteps_completed"].eq(48)
        & recoverable["maximum_reserve_shortfall_kwh"].le(1e-6)
        & recoverable["reserve_violation_timesteps"].eq(0)
        & recoverable["minimum_observed_soc_fraction"].ge(0.20 - 1e-12)
        & recoverable["terminal_minimum_soc_fraction"].ge(0.20 - 1e-12)
    )
    recoverable = (
        recoverable.groupby(["case", "mode", "method"], as_index=False)
        .agg(
            n_runs=("run_id", "size"),
            n_feasible=("operationally_feasible", "sum"),
            feasibility_rate=("operationally_feasible", "mean"),
            realized_pto_cost_mean=("realized_pto_cost", "mean"),
            realized_aggregator_revenue_mean=(
                "realized_aggregator_revenue",
                "mean",
            ),
            maximum_reserve_shortfall_kwh_mean=(
                "maximum_reserve_shortfall_kwh",
                "mean",
            ),
        )
    )
    recoverable["case_label"] = "Recoverable clustered late returns"
    pieces.append(recoverable)
    frame = pd.concat(pieces, ignore_index=True)
    return frame[[
        "case", "case_label", "mode", "method", "n_runs", "n_feasible",
        "feasibility_rate", "realized_pto_cost_mean",
        "realized_aggregator_revenue_mean", "maximum_reserve_shortfall_kwh_mean",
    ]].sort_values(["case_label", "mode", "method"])


def prompt_table() -> pd.DataFrame:
    source = pd.read_csv(
        ROOT / "results/revision/sensitivity_v2/analysis/sensitivity_summary.csv"
    )
    wanted = {
        "effective_action_correct",
        "effective_false_optimization_rate",
        "effective_missed_optimization_rate",
        "operationally_feasible",
        "mode_aligned_economic_score",
        "buy_arithmetic_mean_gap",
        "sell_arithmetic_mean_gap",
        "llm_total_tokens",
        "llm_approximate_cost_usd",
    }
    source = source.loc[source["metric"].isin(wanted)].copy()
    pivot = source.pivot_table(
        index=["family", "arm", "mode", "n"],
        columns="metric",
        values="mean",
        aggfunc="first",
    ).reset_index()
    pivot.columns.name = None
    return pivot


def scaling_table() -> pd.DataFrame:
    frame = pd.read_csv(ROOT / "results/revision/scaling_v2/scaling_runs.csv")
    grouped = frame.groupby(
        ["depot", "fleet_size", "configuration"], as_index=False
    ).agg(
        runs=("run_id", "count"),
        feasibility_rate=("operationally_feasible", "mean"),
        workflow_wall_seconds_mean=("run_wall_seconds", "mean"),
        llm_latency_seconds_mean=("llm_latency_seconds", "mean"),
        llm_tokens_total=("llm_total_tokens", "sum"),
        llm_cost_usd_total=("llm_approximate_cost_usd", "sum"),
        solver_wall_seconds_mean=("solver_wall_seconds_mean", "mean"),
        solver_wall_seconds_max=("solver_wall_seconds_max", "max"),
        model_variables_max=("solver_model_variables_max", "max"),
        model_constraints_max=("solver_model_constraints_max", "max"),
        peak_rss_mb_mean=("run_peak_rss_mb", "mean"),
    )
    return grouped


def multiday_table() -> pd.DataFrame:
    return pd.read_csv(
        ROOT
        / "results/revision/multiday_charger_derating_v1/multiday_method_summary.csv"
    )


def multiday_episode_effects_table() -> pd.DataFrame:
    return pd.read_csv(
        ROOT
        / "results/revision/multiday_charger_derating_v1/multiday_episode_effects.csv"
    )


def multiday_daily_effects_table() -> pd.DataFrame:
    return pd.read_csv(
        ROOT
        / "results/revision/multiday_charger_derating_v1/multiday_daily_effects.csv"
    )


def multiday_solver_audit_table() -> pd.DataFrame:
    return pd.read_csv(
        ROOT
        / "results/revision/multiday_charger_derating_v1/multiday_solver_audit.csv"
    )


def multiday_solver_audit_summary_table() -> pd.DataFrame:
    return pd.read_csv(
        ROOT
        / "results/revision/multiday_charger_derating_v1/multiday_solver_audit_summary.csv"
    )


def multiday_time_limit_episode_table() -> pd.DataFrame:
    return pd.read_csv(
        ROOT
        / "results/revision/multiday_nominal_selfish_900s_v1/multiday_episodes.csv"
    )


def multiday_time_limit_solver_audit_table() -> pd.DataFrame:
    return pd.read_csv(
        ROOT
        / "results/revision/multiday_nominal_selfish_900s_v1/multiday_solver_audit.csv"
    )


def multiday_time_limit_solver_audit_summary_table() -> pd.DataFrame:
    return pd.read_csv(
        ROOT
        / "results/revision/multiday_nominal_selfish_900s_v1/multiday_solver_audit_summary.csv"
    )


def make_trigger_figure(frame: pd.DataFrame) -> list[Path]:
    methods = ["agent", "rule_text", "numerical", "oracle"]
    labels = {"agent": "Agent", "rule_text": "Text rule", "numerical": "Numerical", "oracle": "Oracle"}
    colors = {"agent": "#2166AC", "rule_text": "#67A9CF", "numerical": "#BDBDBD", "oracle": "#B2182B"}
    cases = ["Recoverable clustered late returns", "Sustained route-energy shift", "Route warning + price escalation"]
    modes = ["selfish", "altruistic"]
    x = np.arange(len(modes), dtype=float)
    width = 0.19
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 4.7), sharey=True)
    for case, ax in zip(cases, axes):
        for offset, method in enumerate(methods):
            values = []
            ns = []
            for mode in modes:
                row = frame.loc[(frame.case_label == case) & (frame["mode"] == mode) & (frame.method == method)]
                values.append(float(row.iloc[0].feasibility_rate))
                ns.append(int(row.iloc[0].n_runs))
            positions = x + (offset - 1.5) * width
            bars = ax.bar(positions, values, width, label=labels[method], color=colors[method], edgecolor="white")
            for bar, n in zip(bars, ns):
                y = max(0.035, bar.get_height() + 0.025)
                ax.text(bar.get_x() + bar.get_width() / 2, y, f"n={n}", ha="center", va="bottom", fontsize=7, rotation=90)
        ax.set_title(case, fontsize=10)
        ax.set_xticks(x, [mode.capitalize() for mode in modes], fontsize=9)
        ax.axhline(1.0, color="#444444", linewidth=0.7, linestyle="--")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#E6E6E6", linewidth=0.7)
    axes[0].set_ylim(0, 1.18)
    axes[0].set_ylabel("Operational feasibility rate")
    fig.legend(
        [plt.Rectangle((0, 0), 1, 1, color=colors[method]) for method in methods],
        [labels[method] for method in methods],
        ncol=4,
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.04),
    )
    fig.tight_layout()
    paths = [FIGURES / "trigger_feasibility.png", FIGURES / "trigger_feasibility.pdf"]
    fig.savefig(paths[0], dpi=220, bbox_inches="tight")
    fig.savefig(paths[1], bbox_inches="tight")
    plt.close(fig)
    return paths


def _scaling_terms(frame, configuration, sizes):
    """Split mean episode wall time into the three terms it conflates."""
    subset = frame.loc[frame.configuration == configuration].set_index("fleet_size")
    solver = np.array([subset.loc[s, "solver_wall_seconds_mean"] for s in sizes])
    agent = np.array([subset.loc[s, "llm_latency_seconds_mean"] for s in sizes])
    total = np.array([subset.loc[s, "workflow_wall_seconds_mean"] for s in sizes])
    return solver, agent, total - solver - agent, total


DECISION_INTERVAL_S = 30 * 60
SCALING_SIZES = [8, 16, 32]
SCALING_ARMS = ["rule_text_event_trigger", "full_agentic"]
SCALING_LABELS = {
    "full_agentic": "Full agentic",
    "rule_text_event_trigger": "Text-rule baseline",
}
SCALING_SHORT = {"full_agentic": "agentic", "rule_text_event_trigger": "rule"}
COL_OVERHEAD = "#D9D9D9"
COL_SOLVER = "#E08214"
COL_AGENT = "#2166AC"
COL_RULE = "#8C8C8C"


def _draw_decomposition(ax, depot) -> None:
    width, offset = 0.34, 0.185
    centers, minor_labels = [], []
    for slot, arm in enumerate(SCALING_ARMS):
        solver, agent, overhead, total = _scaling_terms(depot, arm, SCALING_SIZES)
        x = np.arange(len(SCALING_SIZES)) + (offset if slot else -offset)
        first = slot == 0
        ax.bar(x, overhead, width, color=COL_OVERHEAD,
               label="Data handling (both arms)" if first else None)
        ax.bar(x, solver, width, bottom=overhead, color=COL_SOLVER,
               label="Optimizer" if first else None)
        ax.bar(x, agent, width, bottom=overhead + solver, color=COL_AGENT,
               label="Agent latency" if first else None)
        for xi, ti in zip(x, total):
            ax.text(xi, ti + 1.6, f"{ti:.0f}", ha="center", va="bottom", fontsize=8)
        centers.extend(x)
        minor_labels.extend([SCALING_SHORT[arm]] * len(SCALING_SIZES))

    ax.set_xticks(np.arange(len(SCALING_SIZES)))
    ax.set_xticklabels(SCALING_SIZES)
    ax.tick_params(axis="x", which="major", length=0, pad=18)
    ax.set_xticks(centers, minor=True)
    ax.set_xticklabels(minor_labels, minor=True, fontsize=7.5, color="#4D4D4D")
    ax.tick_params(axis="x", which="minor", length=0)
    ax.set_ylabel("Mean episode wall time (s)")
    ax.set_xlabel("Fleet size (buses), Depot A")
    ax.set_ylim(0, 95)
    ax.legend(frameon=False, fontsize=8, loc="upper left")


GROWTH_TERMS = [
    ("Optimizer\ntime", "solver_wall_seconds_mean", COL_SOLVER),
    ("Model size\n(variables)", "model_variables_max", "#9E9E9E"),
    ("Tokens", "llm_tokens_total", "#7FB3D5"),
    ("Agent\nlatency", "llm_latency_seconds_mean", COL_AGENT),
]


def _draw_growth(ax, depot) -> None:
    """Percent growth of each term relative to the eight-bus instance."""
    agentic = depot.loc[depot.configuration == "full_agentic"].set_index("fleet_size")
    width, offset = 0.34, 0.185
    x = np.arange(len(GROWTH_TERMS))

    for slot, size in enumerate([16, 32]):
        heights, mults = [], []
        for _, column, _ in GROWTH_TERMS:
            base = float(agentic.loc[8, column])
            ratio = float(agentic.loc[size, column]) / base
            heights.append(100 * (ratio - 1.0))
            mults.append(ratio)
        pos = x + (offset if slot else -offset)
        colors = [c for _, _, c in GROWTH_TERMS]
        ax.bar(pos, heights, width, color=colors,
               alpha=1.0 if slot else 0.45,
               label=f"{size} buses" if False else None)
        for xi, hi, mi in zip(pos, heights, mults):
            ax.text(xi, hi + 55, f"\u00d7{mi:.2f}" if mi < 3 else f"\u00d7{mi:.1f}",
                    ha="center", va="bottom", fontsize=7.5,
                    fontweight="bold" if slot else "normal",
                    color="#333333" if slot else "#777777")

    ax.axhline(0, color="#9E9E9E", linewidth=1)
    ax.set_xticks(x)
    ax.set_xticklabels([label for label, _, _ in GROWTH_TERMS], fontsize=8)
    ax.set_ylabel("Growth over the 8-bus instance (%)")
    ax.set_xlabel("Relative to Depot A at 8 buses")
    ax.set_ylim(-60, 2450)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor="#8C8C8C", alpha=0.45, label="16 buses"),
                       Patch(facecolor="#8C8C8C", label="32 buses")],
              frameon=False, fontsize=8, loc="upper right")


def _draw_budget(ax, depot) -> None:
    for arm in SCALING_ARMS:
        subset = depot.loc[depot.configuration == arm]
        ax.plot(subset.fleet_size, subset.workflow_wall_seconds_mean,
                marker="o", linewidth=2, label=SCALING_LABELS[arm],
                color=COL_AGENT if arm == "full_agentic" else COL_RULE)
    ax.axhline(DECISION_INTERVAL_S, linestyle="--", linewidth=1.3, color="#B2182B")
    ax.text(8, DECISION_INTERVAL_S * 1.12, "30-min decision interval",
            fontsize=8, color="#B2182B", va="bottom")

    _, _, _, total = _scaling_terms(depot, "full_agentic", SCALING_SIZES)
    share = 100 * total[-1] / DECISION_INTERVAL_S
    ax.annotate(f"{total[-1]:.0f} s = {share:.1f}% of the interval",
                xy=(32, total[-1]), xytext=(15.5, total[-1] * 2.9), fontsize=8,
                color="#333333",
                arrowprops=dict(arrowstyle="-", linewidth=0.8, color="#999999"))

    ax.set_yscale("log")
    ax.set_ylim(4, 6000)
    ax.set_ylabel("Mean episode wall time (s), log scale")
    ax.set_xlabel("Fleet size (buses), Depot A")
    ax.set_xticks(SCALING_SIZES)
    ax.legend(frameon=False, fontsize=8, loc="lower right", ncol=1)


def _style(ax) -> None:
    ax.grid(color="#E6E6E6", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)


def make_scaling_figure(frame: pd.DataFrame) -> list[Path]:
    depot = frame.loc[frame.depot == "depot_a"].sort_values("fleet_size")
    paths: list[Path] = []

    for stem, draw in (("scalability_decomposition", _draw_decomposition),
                       ("scalability_growth", _draw_growth),
                       ("scalability_budget", _draw_budget)):
        fig, ax = plt.subplots(figsize=(5.4, 4.3))
        draw(ax, depot)
        _style(ax)
        fig.tight_layout()
        for suffix in (".pdf", ".png"):
            path = FIGURES / f"{stem}{suffix}"
            fig.savefig(path, dpi=220 if suffix == ".png" else None,
                        bbox_inches="tight")
            paths.append(path)
        plt.close(fig)

    # Combined raster: the response letter embeds one image per figure.
    fig, axes = plt.subplots(1, 3, figsize=(15.0, 4.3))
    _draw_decomposition(axes[0], depot)
    _draw_growth(axes[1], depot)
    _draw_budget(axes[2], depot)
    for ax, tag in zip(axes, ("(a)", "(b)", "(c)")):
        _style(ax)
        ax.set_title(tag, loc="left", fontsize=10, fontweight="bold")
    fig.tight_layout()
    combined = FIGURES / "scalability_latency.png"
    fig.savefig(combined, dpi=220, bbox_inches="tight")
    plt.close(fig)
    paths.append(combined)
    return paths


def main() -> int:
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []

    day_ahead = day_ahead_table()
    trigger = trigger_table()
    prompt = prompt_table()
    scaling = scaling_table()
    multiday = multiday_table()
    multiday_episode_effects = multiday_episode_effects_table()
    multiday_daily_effects = multiday_daily_effects_table()
    multiday_solver_audit = multiday_solver_audit_table()
    multiday_solver_audit_summary = multiday_solver_audit_summary_table()
    multiday_time_limit_episodes = multiday_time_limit_episode_table()
    multiday_time_limit_solver_audit = multiday_time_limit_solver_audit_table()
    multiday_time_limit_solver_audit_summary = (
        multiday_time_limit_solver_audit_summary_table()
    )
    artifacts.extend([
        write_csv(day_ahead, "day_ahead_strategy_comparison.csv"),
        write_csv(trigger, "trigger_feasibility_comparison.csv"),
        write_csv(prompt, "prompt_sensitivity.csv"),
        write_csv(scaling, "scalability.csv"),
        write_csv(multiday, "multiday_charger_derating.csv"),
        write_csv(multiday_episode_effects, "multiday_charger_derating_effects.csv"),
        write_csv(multiday_daily_effects, "multiday_charger_derating_daily_effects.csv"),
        write_csv(multiday_solver_audit, "multiday_solver_audit.csv"),
        write_csv(multiday_solver_audit_summary, "multiday_solver_audit_summary.csv"),
        write_csv(multiday_time_limit_episodes, "multiday_time_limit_sensitivity_episodes.csv"),
        write_csv(multiday_time_limit_solver_audit, "multiday_time_limit_sensitivity_solver_audit.csv"),
        write_csv(multiday_time_limit_solver_audit_summary, "multiday_time_limit_sensitivity_solver_audit_summary.csv"),
    ])

    evaluator = pd.read_csv(
        ROOT / "results/revision/evaluator_v3/analysis/evaluator_ablation_summary.csv"
    )
    evaluator = evaluator.loc[evaluator["case"].isin(["aw_route6_late_return", "aw_combined_evening"])]
    artifacts.append(write_csv(evaluator, "evaluator_ablation.csv"))

    stochastic = pd.read_csv(
        ROOT / "results/revision/stochastic_v4/agent_vs_stochastic_comparison.csv"
    )
    artifacts.append(write_csv(stochastic, "stochastic_benchmark.csv"))

    no_ai = pd.read_csv(
        ROOT / "results/revision/nonagentic_baseline_v8_confirmatory/agent_vs_full_no_ai_comparison.csv"
    )
    artifacts.append(write_csv(no_ai, "full_no_ai_comparison.csv"))

    reviewer_map = pd.DataFrame([
        ["R1.1/R2.1", "Why agentic AI is needed", "Separate numerical, text-rule, oracle, stochastic, and full-no-AI controls; isolate heterogeneous information value", "trigger_feasibility_comparison.csv; stochastic_benchmark.csv; full_no_ai_comparison.csv"],
        ["R1.2", "Deployment logic", "Deterministic timestep map, causal realized settlement, trigger evidence gate, and frozen protocol", "Methods text and protocol manifests"],
        ["R1.3/R2.3", "Beyond event triggering / if-then logic", "Advance-warning text cases and matched rule-text/numerical triggers", "trigger_feasibility_comparison.csv; trigger_feasibility figure"],
        ["R1.4/R2.4", "Component and full-no-AI ablations", "Trigger, Pricing, Evaluator, V2G-only, and full deterministic baselines", "evaluator_ablation.csv; full_no_ai_comparison.csv; day_ahead_strategy_comparison.csv"],
        ["R1.5/R2.5", "Prompt randomness and sensitivity", "55-run one-factor study: 25 Trigger repetitions plus 30 Pricing episodes", "prompt_sensitivity.csv"],
        ["R1.6", "Broader disturbances", "Sustained energy, recoverable clustered delays, price escalation, charger, route, combined, and a chained three-day persistent derating with a matched no-derating daily-replanning control", "trigger_feasibility_comparison.csv; multiday_charger_derating.csv; multiday_charger_derating_effects.csv; multiday_charger_derating_daily_effects.csv; multiday_solver_audit_summary.csv; multiday_time_limit_sensitivity_solver_audit_summary.csv"],
        ["R1.7", "Economic interpretation", "Selfish revenue, altruistic full-day PTO cost, 50% baseline revenue-retention floor, and V2G ladder", "day_ahead_strategy_comparison.csv; stochastic_benchmark.csv"],
        ["R1.8", "Reproducibility", "Frozen prompts, hashes, run indexes, attempt-level solver status/gap provenance, time-limit sensitivity, tokens, latency, CPU, and memory", "artifact manifest; experiment manifests; multiday_solver_audit.csv; multiday_time_limit_sensitivity_solver_audit.csv"],
        ["R1.9/R2.6", "Scalability", "48 runs across 8/16/32 buses and a second depot; Agent and text-rule controls; three repetitions per mode", "scalability.csv; scalability figure"],
        ["R2.2", "Stochastic benchmark", "Two-stage stochastic recourse benchmark in all six primary case-mode cells", "stochastic_benchmark.csv"],
    ], columns=["review_comment", "issue", "implemented_response", "primary_artifact"])
    artifacts.append(write_csv(reviewer_map, "reviewer_evidence_map.csv"))

    artifacts.extend(make_trigger_figure(trigger))
    artifacts.extend(make_scaling_figure(scaling))

    sources = [
        ROOT / "results/revision/day_ahead_reconciliation_v1/day_ahead_cost_reconciliation.csv",
        ROOT / "results/revision/day_ahead_ladder_v1/ladder_manifest.json",
        ROOT / "results/revision/extended_disturbances_v3/analysis/method_summary.csv",
        ROOT / "results/revision/recoverable_cluster_v1/matrix_runs.csv",
        ROOT / "results/revision/price_escalation_v3/analysis/method_summary.csv",
        ROOT / "results/revision/multiday_charger_derating_v1/multiday_method_summary.csv",
        ROOT / "results/revision/multiday_charger_derating_v1/multiday_episode_effects.csv",
        ROOT / "results/revision/multiday_charger_derating_v1/multiday_daily_effects.csv",
        ROOT / "results/revision/multiday_charger_derating_v1/multiday_solver_audit.csv",
        ROOT / "results/revision/multiday_charger_derating_v1/multiday_solver_audit_summary.csv",
        ROOT / "results/revision/multiday_nominal_selfish_900s_v1/multiday_episodes.csv",
        ROOT / "results/revision/multiday_nominal_selfish_900s_v1/multiday_solver_audit.csv",
        ROOT / "results/revision/multiday_nominal_selfish_900s_v1/multiday_solver_audit_summary.csv",
        ROOT / "results/revision/evaluator_v3/analysis/evaluator_ablation_summary.csv",
        ROOT / "results/revision/sensitivity_v2/analysis/sensitivity_summary.csv",
        ROOT / "results/revision/stochastic_v4/agent_vs_stochastic_comparison.csv",
        ROOT / "results/revision/nonagentic_baseline_v8_confirmatory/agent_vs_full_no_ai_comparison.csv",
        ROOT / "results/revision/scaling_v2/scaling_runs.csv",
    ]
    manifest = {
        "artifact_set": "revision_manuscript_assets_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sources": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in sources],
        "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in artifacts],
        "notes": [
            "Rates report the actual repetition counts shown in the tables.",
            "Agent arms generally have n=5; deterministic comparators generally have n=1.",
            "No inferential claim is made from n=1 deterministic comparator cells.",
        ],
    }
    manifest_path = OUTPUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(artifacts)} manuscript artifacts and {manifest_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

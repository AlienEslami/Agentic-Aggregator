from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
NOTICE_FILE = ROOT / "inputs" / "revision" / "evaluator_priority_notices_v1.json"


def _json(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def canonical_by_case() -> dict[str, dict[str, Any]]:
    rows = json.loads(NOTICE_FILE.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("canonical_priority") and row["scenario_id"] not in result:
            result[row["scenario_id"]] = row["canonical_priority"]
    return result


def interpretation_scores(
    interpreted: dict[str, Any] | None, canonical: dict[str, Any]
) -> dict[str, Any]:
    if interpreted is None:
        return {
            "priority_detected": False,
            "objective_correct": False,
            "assets_correct": False,
            "window_correct": False,
            "target_correct": False,
            "interpretation_exact": False,
        }
    objective = interpreted.get("objective") == canonical.get("objective")
    assets = sorted(interpreted.get("affected_buses") or []) == sorted(
        canonical.get("affected_buses") or []
    )
    window = (
        interpreted.get("timestep_start") == canonical.get("timestep_start")
        and interpreted.get("timestep_end") == canonical.get("timestep_end")
    )
    target = (
        interpreted.get("target_unit") == canonical.get("target_unit")
        and abs(
            float(interpreted.get("target_value") or 0)
            - float(canonical.get("target_value") or 0)
        )
        <= 1e-6
    )
    return {
        "priority_detected": True,
        "objective_correct": objective,
        "assets_correct": assets,
        "window_correct": window,
        "target_correct": target,
        "interpretation_exact": objective and assets and window and target,
    }


def selected_attempt(workbook: Path) -> dict[str, Any] | None:
    attempts = pd.read_excel(workbook, sheet_name="optimization_attempts")
    if attempts.empty:
        return None
    selected = attempts[attempts["selected_for_execution"].fillna(False).astype(bool)]
    row = selected.iloc[-1] if not selected.empty else attempts.iloc[-1]
    return row.to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    output_root = args.output_root or args.input_root / "analysis"
    output_root.mkdir(parents=True, exist_ok=True)
    canonical = canonical_by_case()

    rows: list[dict[str, Any]] = []
    for workbook in sorted(args.input_root.rglob("*.xlsx")):
        parts = workbook.relative_to(args.input_root).parts
        if len(parts) < 3:
            continue
        case, mode = parts[0], parts[1]
        configuration = workbook.stem.rsplit("_rep_", 1)[0]
        repetition = int(workbook.stem.rsplit("_rep_", 1)[1])
        attempt = selected_attempt(workbook)
        if attempt is None or case not in canonical:
            continue
        interpreted = _json(attempt.get("interpreted_operational_priority"))
        record = {
            "case": case,
            "mode": mode,
            "configuration": configuration,
            "repetition": repetition,
            "canonical_priority_satisfied": attempt.get(
                "canonical_priority_satisfied"
            ),
            "canonical_priority_compliance_gap": attempt.get(
                "canonical_priority_compliance_gap"
            ),
            "projected_full_day_pto_cost": attempt.get(
                "projected_full_day_pto_cost"
            ),
            "projected_full_day_aggregator_revenue": attempt.get(
                "projected_full_day_aggregator_revenue"
            ),
            "optimization_strategy": attempt.get("optimization_strategy"),
            "lexicographic_priority_applied": attempt.get(
                "lexicographic_priority_applied"
            ),
            "lexicographic_optimality_proven": attempt.get(
                "lexicographic_optimality_proven"
            ),
            "minimum_operational_priority_slack": attempt.get(
                "minimum_operational_priority_slack"
            ),
            **interpretation_scores(interpreted, canonical[case]),
        }
        record["mode_aligned_score"] = (
            float(record["projected_full_day_aggregator_revenue"])
            if mode == "selfish"
            else -float(record["projected_full_day_pto_cost"])
        )
        rows.append(record)

    runs = pd.DataFrame(rows)
    runs.to_csv(output_root / "evaluator_ablation_runs.csv", index=False)
    if runs.empty:
        raise ValueError(f"No completed evaluator-ablation workbooks under {args.input_root}")

    summary = (
        runs.groupby(["case", "mode", "configuration"], dropna=False)
        .agg(
            runs=("configuration", "size"),
            priority_detection_rate=("priority_detected", "mean"),
            interpretation_exact_rate=("interpretation_exact", "mean"),
            canonical_compliance_rate=("canonical_priority_satisfied", "mean"),
            compliance_gap_mean=("canonical_priority_compliance_gap", "mean"),
            minimum_operational_priority_slack_mean=(
                "minimum_operational_priority_slack",
                "mean",
            ),
            lexicographic_optimality_proven_rate=(
                "lexicographic_optimality_proven",
                "mean",
            ),
            projected_full_day_pto_cost_mean=("projected_full_day_pto_cost", "mean"),
            projected_full_day_aggregator_revenue_mean=(
                "projected_full_day_aggregator_revenue",
                "mean",
            ),
            mode_aligned_score_mean=("mode_aligned_score", "mean"),
        )
        .reset_index()
    )
    summary.to_csv(output_root / "evaluator_ablation_summary.csv", index=False)

    comparisons: list[dict[str, Any]] = []
    for (case, mode), group in summary.groupby(["case", "mode"]):
        indexed = group.set_index("configuration")
        if "agent_evaluator_raw_text" not in indexed.index:
            continue
        agent = indexed.loc["agent_evaluator_raw_text"]
        for baseline_name in (
            "rule_text_evaluator",
            "structured_evaluator_oracle",
            "evaluator_removal_control",
        ):
            if baseline_name not in indexed.index:
                continue
            baseline = indexed.loc[baseline_name]
            comparisons.append(
                {
                    "case": case,
                    "mode": mode,
                    "candidate": "agent_evaluator_raw_text",
                    "baseline": baseline_name,
                    "compliance_rate_delta": float(
                        agent["canonical_compliance_rate"]
                        - baseline["canonical_compliance_rate"]
                    ),
                    "interpretation_exact_rate_delta": float(
                        agent["interpretation_exact_rate"]
                        - baseline["interpretation_exact_rate"]
                    ),
                    "projected_full_day_pto_cost_delta": float(
                        agent["projected_full_day_pto_cost_mean"]
                        - baseline["projected_full_day_pto_cost_mean"]
                    ),
                    "mode_aligned_score_delta": float(
                        agent["mode_aligned_score_mean"]
                        - baseline["mode_aligned_score_mean"]
                    ),
                    "agent_compliant": bool(
                        agent["canonical_compliance_rate"] >= 1.0 - 1e-9
                    ),
                    "baseline_compliant": bool(
                        baseline["canonical_compliance_rate"] >= 1.0 - 1e-9
                    ),
                    "incremental_full_day_pto_cost_of_compliance": (
                        float(
                            agent["projected_full_day_pto_cost_mean"]
                            - baseline["projected_full_day_pto_cost_mean"]
                        )
                        if baseline_name == "evaluator_removal_control"
                        and agent["canonical_compliance_rate"] >= 1.0 - 1e-9
                        else None
                    ),
                    "mode_aligned_regret_vs_structured_oracle": (
                        float(
                            baseline["mode_aligned_score_mean"]
                            - agent["mode_aligned_score_mean"]
                        )
                        if baseline_name == "structured_evaluator_oracle"
                        and agent["canonical_compliance_rate"] >= 1.0 - 1e-9
                        and baseline["canonical_compliance_rate"] >= 1.0 - 1e-9
                        else None
                    ),
                }
            )
    pd.DataFrame(comparisons).to_csv(
        output_root / "evaluator_ablation_comparisons.csv", index=False
    )
    print(f"Wrote evaluator-ablation analysis to {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

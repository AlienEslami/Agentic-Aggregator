"""Reconcile the legacy day-ahead table with optimizer-native settlement.

The original manuscript values can be reproduced only by applying interval
``t-1`` tariffs to interval ``t`` energy.  The optimizer objective, workbook
summaries, and real-time settlement all use the same interval for energy and
tariff.  This script documents both calculations and emits the corrected
values that should be used in the revision.
"""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "paper_outputs" / "day_ahead" / "table_06"
LADDER_ROOT = ROOT / "results" / "revision" / "day_ahead_ladder_v1"
OUTPUT_ROOT = ROOT / "results" / "revision" / "day_ahead_reconciliation_v1"

PAPER_VALUES = {
    "S1": {"cost": 218.1013869209809, "revenue": 0.0},
    "S2": {"cost": 130.47060490463215, "revenue": 0.0},
    "S3": {"cost": 140.58607785909376, "revenue": 20.304325815497062},
    "S4": {"cost": 118.9083476503984, "revenue": 2.3895928820060446},
}

SOURCES = {
    "S1": ("S1_dumb_charging_no_v2g.xlsx", "dumb_charging_no_v2g", None),
    "S2": ("S2_smart_charging_no_v2g.xlsx", "smart_charging_no_v2g", None),
    "S3": ("S3_profit_based_aggregator.xlsx", "selfish", "few-shot-cot"),
    "S4": ("S4_operational_based_aggregator.xlsx", "altruistic", "few-shot-cot"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_vector(value: Any) -> list[float]:
    parsed = ast.literal_eval(str(value))
    return [float(item) for item in parsed]


def settle(
    plan: pd.DataFrame,
    spot: list[float],
    buy_multipliers: list[float],
    sell_multipliers: list[float],
    *,
    tariff_shift: int = 0,
) -> tuple[float, float]:
    """Return PTO cost and aggregator revenue for a tariff alignment.

    ``tariff_shift=-1`` reproduces the legacy paper calculation: interval 0
    is clamped to tariff 0 and every later flow uses the prior tariff.
    """

    cost = 0.0
    revenue = 0.0
    for index, row in plan.reset_index(drop=True).iterrows():
        tariff_index = max(0, min(len(spot) - 1, index + tariff_shift))
        buy = float(row["w_buy"])
        sell = float(row["w_sell"])
        grid_price = spot[tariff_index]
        buy_price = grid_price * buy_multipliers[tariff_index]
        sell_price = grid_price * sell_multipliers[tariff_index]
        cost += buy * buy_price - sell * sell_price
        revenue += buy * (buy_price - grid_price) + sell * (grid_price - sell_price)
    return cost, revenue


def source_row(strategy: str, spot: list[float]) -> dict[str, Any]:
    filename, mode, prompt = SOURCES[strategy]
    workbook = SOURCE_ROOT / filename
    summary = pd.read_excel(workbook, sheet_name="day_ahead_summary")
    selected = summary.loc[summary["mode"].astype(str) == mode]
    if prompt is not None:
        selected = selected.loc[selected["prompt_paradign"].astype(str) == prompt]
    if len(selected) != 1:
        raise ValueError(f"Expected exactly one summary row for {strategy}; found {len(selected)}")
    summary_row = selected.iloc[0]
    plan = pd.read_excel(workbook, sheet_name="day_ahead_plan").sort_values("timestep")
    buy_multipliers = parse_vector(summary_row["buy_multipliers"])
    sell_multipliers = parse_vector(summary_row["sell_multipliers"])
    native_cost, native_revenue = settle(
        plan, spot, buy_multipliers, sell_multipliers, tariff_shift=0
    )
    prior_cost, prior_revenue = settle(
        plan, spot, buy_multipliers, sell_multipliers, tariff_shift=-1
    )
    expected = PAPER_VALUES[strategy]
    if abs(native_cost - float(summary_row["pto_daily_cost"])) > 1e-6:
        raise ValueError(f"Native calculation disagrees with {strategy} workbook summary")
    if abs(native_revenue - float(summary_row["aggregator_revenue"])) > 1e-6:
        raise ValueError(f"Native revenue disagrees with {strategy} workbook summary")
    if abs(prior_cost - expected["cost"]) > 1e-6:
        raise ValueError(f"Prior-interval calculation does not reproduce {strategy} paper cost")
    if abs(prior_revenue - expected["revenue"]) > 1e-6:
        raise ValueError(f"Prior-interval calculation does not reproduce {strategy} paper revenue")
    return {
        "strategy": strategy,
        "source_workbook": str(workbook.relative_to(ROOT)),
        "mode": mode,
        "prompt_paradigm": prompt or "not_applicable",
        "paper_reported_cost_eur": expected["cost"],
        "prior_interval_recomputed_cost_eur": prior_cost,
        "native_same_interval_cost_eur": native_cost,
        "paper_minus_native_cost_eur": expected["cost"] - native_cost,
        "paper_reported_revenue_eur": expected["revenue"],
        "prior_interval_recomputed_revenue_eur": prior_revenue,
        "native_same_interval_revenue_eur": native_revenue,
        "bought_kwh": float(plan["w_buy"].sum()),
        "sold_kwh": float(plan["w_sell"].sum()),
        "paper_value_reproduced_by_prior_interval_tariff": True,
        "source_sha256": sha256(workbook),
    }


def main() -> int:
    spot_path = ROOT / "data" / "inputs" / "spot_prices.xlsx"
    spot_frame = pd.read_excel(spot_path, sheet_name="Spot Prices").sort_values("timestep")
    spot = pd.to_numeric(spot_frame["spot_market"], errors="raise").astype(float).tolist()
    if len(spot) != 48:
        raise ValueError(f"Expected 48 spot prices; found {len(spot)}")

    rows = [source_row(strategy, spot) for strategy in SOURCES]
    ladder = json.loads((LADDER_ROOT / "ladder_manifest.json").read_text(encoding="utf-8"))
    corrected = {item["scenario"]: item for item in ladder["scenarios"]}
    recommendations = {
        "S1": corrected["S1_dumb_charging"],
        "S2": corrected["S2_smart_no_v2g"],
    }
    for row in rows:
        strategy = row["strategy"]
        if strategy in recommendations:
            item = recommendations[strategy]
            row["recommended_cost_eur"] = float(item["pto_daily_cost"])
            row["recommended_revenue_eur"] = 0.0
            row["recommended_bought_kwh"] = float(item["total_kwh_bought"])
            row["recommended_sold_kwh"] = float(item["total_kwh_sold"])
            row["recommendation_source"] = (
                "corrected day-ahead ladder; S1 includes earliest-charging tie-break"
            )
        else:
            row["recommended_cost_eur"] = row["native_same_interval_cost_eur"]
            row["recommended_revenue_eur"] = row["native_same_interval_revenue_eur"]
            row["recommended_bought_kwh"] = row["bought_kwh"]
            row["recommended_sold_kwh"] = row["sold_kwh"]
            row["recommendation_source"] = "optimizer-native workbook summary"

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_ROOT / "day_ahead_cost_reconciliation.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False, lineterminator="\n")
    payload = {
        "reconciliation_version": "day_ahead_reconciliation_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "finding": (
            "Legacy manuscript values apply the previous interval tariff to the "
            "current interval energy. Optimizer-native same-interval settlement "
            "is the corrected reporting basis. The optimizer is unchanged."
        ),
        "time_definition": (
            "Timestep t energy is settled at timestep t price and multipliers; "
            "timestep 1 denotes 00:00-00:30."
        ),
        "spot_price_sha256": sha256(spot_path),
        "ladder_manifest_sha256": sha256(LADDER_ROOT / "ladder_manifest.json"),
        "rows": rows,
    }
    json_path = OUTPUT_ROOT / "day_ahead_cost_reconciliation.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(pd.DataFrame(rows)[[
        "strategy", "paper_reported_cost_eur", "native_same_interval_cost_eur",
        "recommended_cost_eur", "recommended_revenue_eur"
    ]].to_string(index=False))
    print(f"wrote {csv_path.relative_to(ROOT)}")
    print(f"wrote {json_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

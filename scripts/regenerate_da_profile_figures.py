"""Regenerate the day-ahead profile figures from repository-tracked sources.

The submitted figures for aggregate net power and average fleet SOC were
produced from the pre-correction S1 workbook, whose schedule depended on which
solver happened to be used. The manuscript now reports the corrected S1
baseline (earliest-charging tie-break, terminal average SOC 42.38%), so the
figures must show the same schedule the text describes.

Sources, all under version control:

- S1: ``results/revision/day_ahead_ladder_v1/S1_dumb_charging.json`` (the
  corrected, solver-invariant schedule);
- S2, S3, S4: the ``day_ahead_plan`` sheets of the table_06 workbooks in
  ``paper_outputs/day_ahead/`` (their schedules are unchanged by the
  accounting correction, which touched tariff pairing, not energy);
- bus capacities: ``data/inputs/case_study_inputs.xlsx``.

The drawing style replicates the original generator: same series colors, same
axes, same legend placement.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "paper_outputs" / "revision" / "figures"

CHARGE_EFF = 0.90
DISCHARGE_EFF = 0.90
EVENT_THRESHOLD_KWH = 20.0
TIMESTEP_HOURS = 0.5

SERIES_COLORS = {
    "Dumb charging": "#4C78A8",
    "Smart no V2G": "#54A24B",
    "Profit-based": "#E45756",
    "Operational-based": "#72B7B2",
}

WORKBOOKS = {
    "Smart no V2G": (
        ROOT / "paper_outputs" / "day_ahead" / "table_06" / "S2_smart_charging_no_v2g.xlsx",
        "smart_charging_no_v2g",
    ),
    "Profit-based": (
        ROOT / "paper_outputs" / "day_ahead" / "table_06" / "S3_profit_based_aggregator.xlsx",
        "selfish",
    ),
    "Operational-based": (
        ROOT / "paper_outputs" / "day_ahead" / "table_06" / "S4_operational_based_aggregator.xlsx",
        "altruistic",
    ),
}
S1_JSON = ROOT / "results" / "revision" / "day_ahead_ladder_v1" / "S1_dumb_charging.json"


def bus_energy_matrix_from_workbook(path: Path, mode: str) -> list[list[float]]:
    plan = pd.read_excel(path, sheet_name="day_ahead_plan")
    if "mode" in plan.columns:
        plan = plan[plan["mode"].astype(str).str.lower() == mode]
    plan = plan.sort_values("timestep")
    columns = sorted(
        (c for c in plan.columns if str(c).startswith("bus_") and str(c).endswith("_kwh")),
        key=lambda c: int(str(c).split("_")[1]),
    )
    return [[float(v) for v in plan[c]] for c in columns]


def bus_energy_matrix_from_s1() -> list[list[float]]:
    payload = json.loads(S1_JSON.read_text(encoding="utf-8"))
    return [[float(v) for v in bus] for bus in payload["energy"]]


def avg_soc_series(energy: list[list[float]], capacities: list[float]) -> list[float]:
    steps = len(energy[0])
    return [
        sum(energy[b][t] / capacities[b] * 100.0 for b in range(len(energy))) / len(energy)
        for t in range(steps)
    ]


def net_power_series(energy: list[list[float]]) -> list[float]:
    steps = len(energy[0])
    values = [0.0]
    for t in range(1, steps):
        diffs = [energy[b][t] - energy[b][t - 1] for b in range(len(energy))]
        battery_gain = sum(d for d in diffs if d > EVENT_THRESHOLD_KWH)
        battery_export = sum(-d for d in diffs if d < -EVENT_THRESHOLD_KWH)
        grid_buy_kwh = battery_gain / CHARGE_EFF
        grid_sell_kwh = battery_export * DISCHARGE_EFF
        values.append((grid_buy_kwh - grid_sell_kwh) / TIMESTEP_HOURS)
    return values


def draw_line_chart(path: Path, title: str, series: dict[str, list[float]], y_label: str) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 5.0))
    for label, line in series.items():
        ax.plot(range(len(line)), line, label=label, color=SERIES_COLORS[label], linewidth=1.6)
    ax.set_title(title)
    ax.set_xlabel("Timestep")
    ax.set_ylabel(y_label)
    ax.set_xlim(0, max(len(line) for line in series.values()) - 1)
    ax.set_xticks([0, 12, 24, 36, 47])
    ax.set_xticklabels(["0", "12", "24", "36", "48"])
    ax.grid(True, color="#d9e0e6", linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=3, frameon=False)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    buses = pd.read_excel(ROOT / "data" / "inputs" / "case_study_inputs.xlsx", sheet_name="Buses")
    capacities = [float(v) for v in buses.sort_values("bus_id")["bus_kwh"]]

    energies = {"Dumb charging": bus_energy_matrix_from_s1()}
    for label, (path, mode) in WORKBOOKS.items():
        energies[label] = bus_energy_matrix_from_workbook(path, mode)

    draw_line_chart(
        OUTPUT / "da_power_profiles_4panel.pdf",
        "DA aggregate net power profiles",
        {label: net_power_series(matrix) for label, matrix in energies.items()},
        "Net power (kW)",
    )
    draw_line_chart(
        OUTPUT / "da_energy_profiles_4panel.pdf",
        "DA average fleet SOC profiles",
        {label: avg_soc_series(matrix, capacities) for label, matrix in energies.items()},
        "Average SOC (%)",
    )

    for label, matrix in energies.items():
        terminal = avg_soc_series(matrix, capacities)[-1]
        print(f"{label:20s} SOC terminal médio: {terminal:6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

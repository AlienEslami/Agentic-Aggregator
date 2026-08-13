from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import pytest

from agentic_workflow.io import WorkbookSeries, load_day_ahead_reference, planned_row_for_observation


def test_day_ahead_mode_selection_and_observation_index(tmp_path):
    workbook = tmp_path / "state.xlsx"
    summaries = pd.DataFrame(
        [
            {
                "run_timestamp": "2026-01-01T00:00:00Z",
                "mode": "selfish",
                "pto_daily_cost": 10,
                "aggregator_revenue": 1,
                "buy_multipliers": "[1.1]",
                "sell_multipliers": "[0.7]",
                "avg_grid_price": 0.1,
            },
            {
                "run_timestamp": "2026-01-02T00:00:00Z",
                "mode": "selfish",
                "pto_daily_cost": 9,
                "aggregator_revenue": 2,
                "buy_multipliers": "[1.2]",
                "sell_multipliers": "[0.8]",
                "avg_grid_price": 0.1,
            },
        ]
    )
    rows = []
    for run in summaries["run_timestamp"]:
        for timestep in range(48):
            row = {
                "run_timestamp": run,
                "mode": "selfish",
                "timestep": timestep,
                "w_buy": timestep,
                "w_sell": 0,
            }
            row.update({f"bus_{bus}_kwh": 100 + timestep for bus in range(1, 9)})
            rows.append(row)
    with pd.ExcelWriter(workbook) as writer:
        summaries.to_excel(writer, sheet_name="day_ahead_summary", index=False)
        pd.DataFrame(rows).to_excel(writer, sheet_name="day_ahead_plan", index=False)

    selected = load_day_ahead_reference(workbook, "selfish")
    assert selected.run_timestamp == "2026-01-02T00:00:00Z"
    assert selected.summary["aggregator_revenue"] == 2
    assert planned_row_for_observation(selected.plan, 48)["timestep"] == 47


def test_workbook_series_reads_zip(tmp_path):
    archive_path = tmp_path / "series.zip"
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"timestep": [48], "spot_market": [0.2]}).to_excel(
            writer, sheet_name="Prices", index=False
        )
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("prices/intraday_prices_t48.xlsx", buffer.getvalue())
    series = WorkbookSeries(archive_path)
    assert series.timesteps == (48,)
    assert series.read_sheet(48, "Prices").iloc[0]["spot_market"] == 0.2


def test_day_ahead_timestamp_mismatch_does_not_mix_plan_runs(tmp_path):
    workbook = tmp_path / "state.xlsx"
    with pd.ExcelWriter(workbook) as writer:
        pd.DataFrame([{"mode": "selfish", "run_timestamp": "2026-01-02T00:00:00Z"}]).to_excel(
            writer, sheet_name="day_ahead_summary", index=False
        )
        pd.DataFrame([{
            "mode": "selfish",
            "run_timestamp": "2026-01-01T00:00:00Z",
            "timestep": 0,
        }]).to_excel(writer, sheet_name="day_ahead_plan", index=False)
    with pytest.raises(ValueError, match="run_timestamp"):
        load_day_ahead_reference(workbook, "selfish")

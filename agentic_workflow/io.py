from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZipFile

import pandas as pd


TIMESTEP_RE = re.compile(r"(\d+)(?=\.xlsx$)", re.IGNORECASE)
BUS_COLUMNS = [f"bus_{bus_id}_kwh" for bus_id in range(1, 9)]
PLAN_COLUMNS = ["timestep", "w_buy", "w_sell", *BUS_COLUMNS]
BUS_COLUMN_RE = re.compile(r"^bus_(\d+)_kwh$")


def bus_columns(frame: pd.DataFrame) -> list[str]:
    return sorted(
        (str(column) for column in frame.columns if BUS_COLUMN_RE.match(str(column))),
        key=lambda column: int(BUS_COLUMN_RE.match(column).group(1)),
    )


def bus_ids_from_frame(frame: pd.DataFrame) -> list[int]:
    return [int(BUS_COLUMN_RE.match(column).group(1)) for column in bus_columns(frame)]


def _clean_scalar(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def dataframe_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {str(key): _clean_scalar(value) for key, value in record.items()}
        for record in frame.to_dict(orient="records")
    ]


def parse_json_list(value: Any, default: list[float] | None = None) -> list[float]:
    if isinstance(value, list):
        return [float(item) for item in value]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return list(default or [])
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError(f"Expected a JSON list, received: {value!r}")
        return [float(item) for item in parsed]
    raise TypeError(f"Unsupported list value: {type(value).__name__}")


class WorkbookSeries:
    """Read timestep workbooks from either a directory or a ZIP archive."""

    def __init__(self, source: Path):
        self.source = source
        self._members: dict[int, str | Path] = {}
        if source.is_dir():
            candidates: Iterable[Path] = source.rglob("*.xlsx")
            for path in candidates:
                timestep = self._parse_timestep(path.name)
                if timestep is not None:
                    self._members[timestep] = path
        elif source.suffix.lower() == ".zip":
            with ZipFile(source) as archive:
                for name in archive.namelist():
                    if name.lower().endswith(".xlsx"):
                        timestep = self._parse_timestep(Path(name).name)
                        if timestep is not None:
                            self._members[timestep] = name
        else:
            raise ValueError(f"Expected a directory or ZIP archive: {source}")
        if not self._members:
            raise ValueError(f"No timestep workbooks found in {source}")

    @staticmethod
    def _parse_timestep(filename: str) -> int | None:
        match = TIMESTEP_RE.search(filename)
        return int(match.group(1)) if match else None

    @property
    def timesteps(self) -> tuple[int, ...]:
        return tuple(sorted(self._members))

    def _excel_source(self, timestep: int) -> Path | BytesIO:
        member = self._members.get(timestep)
        if member is None:
            raise KeyError(f"No workbook for timestep {timestep} in {self.source}")
        if isinstance(member, Path):
            return member
        with ZipFile(self.source) as archive:
            return BytesIO(archive.read(member))

    def read_sheet(self, timestep: int, sheet_name: str) -> pd.DataFrame:
        return pd.read_excel(self._excel_source(timestep), sheet_name=sheet_name)

    def read_sheets(self, timestep: int, sheet_names: Iterable[str]) -> dict[str, pd.DataFrame]:
        source = self._excel_source(timestep)
        return pd.read_excel(source, sheet_name=list(sheet_names))


@dataclass(slots=True)
class DayAheadReference:
    mode: str
    run_timestamp: str | None
    plan: pd.DataFrame
    summary: dict[str, Any]


def load_day_ahead_reference(state_workbook: Path, mode: str) -> DayAheadReference:
    summary = pd.read_excel(state_workbook, sheet_name="day_ahead_summary")
    plan = pd.read_excel(state_workbook, sheet_name="day_ahead_plan")
    summary = summary.loc[summary["mode"].astype(str).str.lower() == mode]
    if summary.empty:
        raise ValueError(f"No day_ahead_summary row for mode={mode!r}")
    if "run_timestamp" in summary.columns:
        summary = summary.assign(_ts=pd.to_datetime(summary["run_timestamp"], errors="coerce"))
        selected = summary.sort_values("_ts", na_position="first").iloc[-1]
    else:
        selected = summary.iloc[-1]
    run_timestamp = _clean_scalar(selected.get("run_timestamp"))
    filtered_plan = plan.loc[plan["mode"].astype(str).str.lower() == mode].copy()
    if run_timestamp is not None and "run_timestamp" in filtered_plan.columns:
        selected_ts = pd.to_datetime(run_timestamp, errors="coerce", utc=True)
        if pd.notna(selected_ts):
            plan_ts = pd.to_datetime(filtered_plan["run_timestamp"], errors="coerce", utc=True)
            exact = filtered_plan.loc[plan_ts == selected_ts]
        else:
            exact = filtered_plan.loc[
                filtered_plan["run_timestamp"].astype(str) == str(run_timestamp)
            ]
        if exact.empty:
            raise ValueError(
                f"No day_ahead_plan rows match run_timestamp={run_timestamp!r} for mode={mode!r}"
            )
        filtered_plan = exact
    filtered_plan = filtered_plan.sort_values("timestep").reset_index(drop=True)
    if filtered_plan.empty:
        raise ValueError(f"No day_ahead_plan rows for mode={mode!r}")
    summary_record = {str(key): _clean_scalar(value) for key, value in selected.items() if key != "_ts"}
    summary_record["buy_multipliers"] = parse_json_list(summary_record.get("buy_multipliers"))
    summary_record["sell_multipliers"] = parse_json_list(summary_record.get("sell_multipliers"))
    return DayAheadReference(
        mode=mode,
        run_timestamp=str(run_timestamp) if run_timestamp is not None else None,
        plan=filtered_plan,
        summary=summary_record,
    )


def load_forecast_tables(
    forecast_workbook: Path,
    spot_prices_workbook: Path | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    forecast_energy = pd.read_excel(forecast_workbook, sheet_name="Forecasted Energy")
    if spot_prices_workbook is not None:
        prices = pd.read_excel(spot_prices_workbook, sheet_name="Spot Prices")
    else:
        prices = pd.read_excel(forecast_workbook, sheet_name="Forecasted")
    prices = prices[["timestep", "spot_market"]].dropna(subset=["timestep", "spot_market"])
    prices["timestep"] = prices["timestep"].astype(int)
    dynamic_bus_columns = bus_columns(forecast_energy)
    if not dynamic_bus_columns:
        raise ValueError("Forecasted Energy contains no bus_<id>_kwh columns")
    energy_columns = ["timestep", *dynamic_bus_columns]
    forecast_energy = forecast_energy[energy_columns].dropna(subset=["timestep"]).copy()
    forecast_energy["timestep"] = forecast_energy["timestep"].astype(int)
    for column in dynamic_bus_columns:
        forecast_energy[column] = pd.to_numeric(forecast_energy[column], errors="coerce")
    if forecast_energy[dynamic_bus_columns].isna().any().any():
        raise ValueError("Forecasted Energy contains missing or non-numeric bus energy values")
    return (
        prices.sort_values("timestep").reset_index(drop=True),
        forecast_energy.sort_values("timestep").reset_index(drop=True),
    )


def load_disturbances(path: Path, selected_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    frame = pd.read_excel(path, sheet_name="scenarios")
    if selected_ids:
        selected = set(selected_ids)
        frame = frame.loc[frame["scenario_id"].astype(str).isin(selected)]
        missing = selected - set(frame["scenario_id"].astype(str))
        if missing:
            raise ValueError(f"Unknown scenario IDs: {sorted(missing)}")
    records = dataframe_records(frame)
    for record in records:
        target = record.get("target_bus_id")
        if isinstance(target, str):
            try:
                record["target_bus_id"] = json.loads(target)
            except json.JSONDecodeError:
                pass
    return records


def initialize_realtime_plan(reference: DayAheadReference) -> pd.DataFrame:
    frame = reference.plan.copy()
    dynamic_bus_columns = bus_columns(frame)
    if not dynamic_bus_columns:
        raise ValueError("day_ahead_plan contains no bus_<id>_kwh columns")
    plan_columns = ["timestep", "w_buy", "w_sell", *dynamic_bus_columns]
    for column in plan_columns:
        if column not in frame:
            frame[column] = None
    frame = frame[plan_columns].copy()
    for column in ["w_buy", "w_sell", *dynamic_bus_columns]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
    for column in ["buy_multipliers", "sell_multipliers", "intraday_prices", "reoptimized", "trigger_type"]:
        frame[column] = None
    return frame.sort_values("timestep").reset_index(drop=True)


def initialize_forecast_energy(reference: DayAheadReference) -> pd.DataFrame:
    columns = ["timestep", *bus_columns(reference.plan)]
    return reference.plan[columns].sort_values("timestep").reset_index(drop=True).copy()


def planned_row_for_observation(plan: pd.DataFrame, timestep: int) -> dict[str, Any] | None:
    """Map observation timestep 1..48 to plan state index 0..47."""
    target = timestep - 1
    rows = plan.loc[plan["timestep"].astype(int) == target]
    if rows.empty:
        return None
    return {str(key): _clean_scalar(value) for key, value in rows.iloc[-1].items()}

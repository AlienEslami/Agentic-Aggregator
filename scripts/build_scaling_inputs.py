from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BASE_INPUT = ROOT / "inputs"
BASE_FLEET = 8
DEPOT_B_TIME_SHIFTS_MINUTES = (0,) * BASE_FLEET
DEPOT_B_ENERGY_FACTORS = (1.03, 0.98, 1.02, 0.97, 1.03, 0.98, 1.02, 0.97)
DEPOT_B_CHARGER_KW = (200,) * BASE_FLEET


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_sheets(path: Path) -> dict[str, pd.DataFrame]:
    return {
        name: pd.read_excel(path, sheet_name=name)
        for name in pd.ExcelFile(path).sheet_names
    }


def write_sheets(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)


def replicate_rows(
    frame: pd.DataFrame,
    factor: int,
    *,
    bus_column: str | None = None,
    trip_column: str | None = None,
    charger_column: str | None = None,
) -> pd.DataFrame:
    blocks: list[pd.DataFrame] = []
    for block in range(factor):
        copy = frame.copy()
        if bus_column and bus_column in copy:
            copy[bus_column] = pd.to_numeric(copy[bus_column], errors="coerce") + block * BASE_FLEET
        if trip_column and trip_column in copy:
            values = pd.to_numeric(copy[trip_column], errors="coerce")
            copy[trip_column] = values.where(values.isna(), values + block * BASE_FLEET)
        if charger_column and charger_column in copy:
            values = pd.to_numeric(copy[charger_column], errors="coerce")
            copy[charger_column] = values.where(values.isna(), values + block * BASE_FLEET)
        blocks.append(copy)
    return pd.concat(blocks, ignore_index=True)


def replicate_bus_columns(frame: pd.DataFrame, factor: int) -> pd.DataFrame:
    result = frame.copy()
    source_columns = [f"bus_{bus}_kwh" for bus in range(1, BASE_FLEET + 1)]
    for block in range(1, factor):
        for bus, source in enumerate(source_columns, start=1):
            if source in frame:
                result[f"bus_{block * BASE_FLEET + bus}_kwh"] = frame[source].to_numpy()
    for column in ("w_buy", "w_sell"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce") * factor
    return result


def shift_clock(value: Any, minutes: int) -> Any:
    if pd.isna(value):
        return value
    parsed = pd.to_datetime(str(value), format="%H:%M", errors="coerce")
    if pd.isna(parsed):
        return value
    total = max(0, min(24 * 60 - 30, parsed.hour * 60 + parsed.minute + minutes))
    return f"{total // 60:02d}:{total % 60:02d}"


def depot_b_trips(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    # Alter route-energy coefficients deterministically so Depot B is distinct
    # without introducing a schedule/state-identity or MIP-complexity confound.
    for index in result.index:
        shift = DEPOT_B_TIME_SHIFTS_MINUTES[index % BASE_FLEET]
        result.at[index, "time_begin"] = shift_clock(result.at[index, "time_begin"], shift)
        result.at[index, "time_end"] = shift_clock(result.at[index, "time_end"], shift)
        result.at[index, "energy_kwhkm"] = round(
            float(result.at[index, "energy_kwhkm"])
            * DEPOT_B_ENERGY_FACTORS[index % BASE_FLEET],
            6,
        )
    return result


def depot_b_prices(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "spot_market" in result:
        values = pd.to_numeric(result["spot_market"], errors="raise").to_numpy(dtype=float)
        result["spot_market"] = np.roll(values, 4) * 1.02
    return result


def transform_sheets(
    sheets: dict[str, pd.DataFrame], *, factor: int, depot: str
) -> dict[str, pd.DataFrame]:
    transformed: dict[str, pd.DataFrame] = {}
    for name, frame in sheets.items():
        result = frame.copy()
        if name == "Buses":
            result = replicate_rows(result, factor, bus_column="bus_id")
        elif name == "Chargers":
            if depot == "depot_b":
                result = result.iloc[:BASE_FLEET].copy()
                result["charger_id"] = range(1, BASE_FLEET + 1)
                result["charger_kw"] = DEPOT_B_CHARGER_KW
            result = replicate_rows(result, factor, charger_column="charger_id")
        elif name == "Trips":
            if depot == "depot_b":
                result = depot_b_trips(result)
            result = replicate_rows(
                result, factor, bus_column="bus_id", trip_column="trip_id"
            )
        elif name == "Realtime state":
            result = replicate_rows(result, factor, bus_column="bus_id")
        elif name == "Benchmark action":
            result = replicate_rows(
                result,
                factor,
                bus_column="bus_id",
                trip_column="trip_id",
                charger_column="charger_id",
            )
        elif name in {"Forecasted Energy", "day_ahead_plan", "Realtime_plan"}:
            result = replicate_bus_columns(result, factor)
        elif name in {"Prices", "Forecasted", "Spot Prices"} and depot == "depot_b":
            result = depot_b_prices(result)
        elif name == "day_ahead_summary":
            for column in ("pto_daily_cost", "aggregator_revenue"):
                if column in result:
                    result[column] = pd.to_numeric(result[column], errors="coerce") * factor
            if depot == "depot_b" and "avg_grid_price" in result:
                result["avg_grid_price"] = pd.to_numeric(
                    result["avg_grid_price"], errors="coerce"
                ) * 1.02
        transformed[name] = result
    return transformed


def build_instance(
    *, depot: str, fleet_size: int, output_root: Path, force: bool = False
) -> Path:
    if depot not in {"depot_a", "depot_b"}:
        raise ValueError("depot must be depot_a or depot_b")
    if fleet_size not in {8, 16, 32} or fleet_size % BASE_FLEET:
        raise ValueError("fleet_size must be one of 8, 16, or 32")
    if depot == "depot_b" and fleet_size != 8:
        raise ValueError("The prespecified distinct Depot B instance has 8 buses")
    factor = fleet_size // BASE_FLEET
    instance = output_root / f"{depot}_{fleet_size}"
    manifest_path = instance / "instance_manifest.json"
    if manifest_path.exists() and not force:
        return instance

    source_files = [
        BASE_INPUT / "State.xlsx",
        BASE_INPUT / "Forecasted.xlsx",
        BASE_INPUT / "SpotPrices.xlsx",
        *sorted((BASE_INPUT / "realtime_states").glob("*.xlsx")),
        *sorted((BASE_INPUT / "intraday_prices").glob("*.xlsx")),
    ]
    write_sheets(
        instance / "State.xlsx",
        transform_sheets(read_sheets(BASE_INPUT / "State.xlsx"), factor=factor, depot=depot),
    )
    write_sheets(
        instance / "Forecasted.xlsx",
        transform_sheets(read_sheets(BASE_INPUT / "Forecasted.xlsx"), factor=factor, depot=depot),
    )
    write_sheets(
        instance / "SpotPrices.xlsx",
        transform_sheets(read_sheets(BASE_INPUT / "SpotPrices.xlsx"), factor=factor, depot=depot),
    )
    for source_dir in ("realtime_states", "intraday_prices"):
        for source in sorted((BASE_INPUT / source_dir).glob("*.xlsx")):
            write_sheets(
                instance / source_dir / source.name,
                transform_sheets(read_sheets(source), factor=factor, depot=depot),
            )
    # Disturbance definitions remain common and are copied only as a generated
    # execution input. No generated workbook is tracked by Git.
    write_sheets(
        instance / "rt_disturbance_scenarios_multiple.xlsx",
        read_sheets(BASE_INPUT / "rt_disturbance_scenarios_multiple.xlsx"),
    )
    forecast = read_sheets(instance / "Forecasted.xlsx")
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "instance": instance.name,
        "depot": depot,
        "fleet_size": fleet_size,
        "replication_factor": factor,
        "bus_count": len(forecast["Buses"]),
        "charger_count": len(forecast["Chargers"]),
        "trip_count": len(forecast["Trips"]),
        "total_charger_power_kw": float(forecast["Chargers"]["charger_kw"].sum()),
        "terminal_soc_fraction": 0.20,
        "v2g_enabled": True,
        "source_hashes": {
            str(path.relative_to(ROOT)): sha256(path) for path in source_files
        },
        "transformation": (
            "linear eight-bus block replication"
            if depot == "depot_a"
            else "distinct route, energy, charger-power, and price transformations"
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return instance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate ignored, physically declared scaling/depot workbooks."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/revision/generated_inputs"),
    )
    parser.add_argument("--depot", choices=("depot_a", "depot_b"), default="depot_a")
    parser.add_argument("--fleet-size", type=int, choices=(8, 16, 32), default=8)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    instance = build_instance(
        depot=args.depot,
        fleet_size=args.fleet_size,
        output_root=output_root,
        force=args.force,
    )
    print(instance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

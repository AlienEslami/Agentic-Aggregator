from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_closed_loop_notice_cases import build
from scripts.run_closed_loop_trigger_comparison import command_for


CONFIGURATIONS = ("oracle_event_trigger", "numerical_event_trigger")


def _asset_list(asset_ids: list[int]) -> str:
    labels = [f"EVSE {asset_id}" for asset_id in asset_ids]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + f" and {labels[-1]}"


def route_candidate(
    notices: list[dict[str, Any]],
    physical: list[dict[str, Any]],
    upper_delay: int,
) -> None:
    lower_delay = max(30, upper_delay - 30)
    for notice in notices:
        if notice["scenario_id"] != "aw_route6_late_return":
            continue
        canonical = notice["canonical"]
        updates = canonical["updates"]["return_delay_minutes_by_bus"]
        if updates:
            canonical["updates"]["return_delay_minutes_by_bus"] = {
                "6": upper_delay
            }
        for estimate in canonical["uncertainty_details"]["estimates"]:
            if estimate["parameter"] == "return_delay_minutes":
                estimate["lower_bound"] = lower_delay
                estimate["upper_bound"] = upper_delay
                if estimate["selected_value"] is not None:
                    estimate["selected_value"] = upper_delay
        if notice["notice_id"] == "AW-ROUTE6-WARN":
            notice["text"] = (
                "Conditional warning - Driver 6: the evening roadworks note looks worse than "
                f"the board. Bus 6 return may be {lower_delay}-{upper_delay} minutes late; "
                "dispatch has not confirmed the 21:30-to-end-of-day control window. Request "
                "confirmation; do not replan yet."
            )
        elif notice["notice_id"] == "AW-ROUTE6-CONFIRM":
            notice["text"] = (
                f"Dispatcher: confirmed for Bus 6. Its return is expected {lower_delay} to "
                f"{upper_delay} minutes late, with the operational control window 21:30 to "
                "24:00. Use the conservative upper bound and optimize now; departure remains "
                "at its scheduled time."
            )
    event = next(
        item for item in physical if item["scenario_id"] == "aw_route6_late_return"
    )
    event["updates"]["return_delay_minutes_by_bus"] = {"6": upper_delay}


def charger_candidate(
    notices: list[dict[str, Any]],
    physical: list[dict[str, Any]],
    charger_ids: list[int],
) -> None:
    assets = _asset_list(charger_ids)
    for notice in notices:
        if notice["scenario_id"] != "aw_charger_bank_shutdown":
            continue
        canonical = notice["canonical"]
        canonical["affected_chargers"] = charger_ids
        if canonical["updates"]["unavailable_chargers"]:
            canonical["updates"]["unavailable_chargers"] = charger_ids
        if notice["notice_id"] == "AW-BANK-WARN":
            notice["text"] = (
                "Conditional warning - Maintenance chat - Lee: the south auxiliary row may "
                "need isolating for the 03:30-05:00 busbar job. Ops: which assets? Lee: the "
                f"map labels that row {assets}, not the north bank. Lockout is pending; "
                "request confirmation and do not replan yet."
            )
        elif notice["notice_id"] == "AW-BANK-RECOVER":
            notice["text"] = (
                f"Recovery confirmed: the south auxiliary-row lockout is cleared and {assets} "
                "are restored to normal service."
            )
    event = next(
        item for item in physical if item["scenario_id"] == "aw_charger_bank_shutdown"
    )
    event["updates"]["unavailable_chargers"] = charger_ids


def write_inputs(
    directory: Path,
    notices: list[dict[str, Any]],
    physical: list[dict[str, Any]],
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    notices_path = directory / "notices.json"
    physical_path = directory / "physical_events.json"
    notices_path.write_text(json.dumps(notices, indent=2) + "\n", encoding="utf-8")
    physical_path.write_text(
        json.dumps({"events": physical}, indent=2) + "\n", encoding="utf-8"
    )
    return notices_path, physical_path


def run_candidate(
    *,
    candidate_id: str,
    case: str,
    mode: str,
    notices: list[dict[str, Any]],
    physical: list[dict[str, Any]],
    output_root: Path,
    force: bool,
) -> list[dict[str, Any]]:
    candidate_dir = output_root / candidate_id
    notices_path, physical_path = write_inputs(candidate_dir, notices, physical)
    rows: list[dict[str, Any]] = []
    for configuration in CONFIGURATIONS:
        workbook = candidate_dir / f"{configuration}.xlsx"
        if force or not workbook.exists():
            command = command_for(
                configuration=configuration,
                case=case,
                variant="uncertain_chat",
                mode=mode,
                start=1,
                end=48,
                model="not_used",
                output=workbook,
            )
            command[command.index("--notices-file") + 1] = str(notices_path)
            command[command.index("--physical-events-file") + 1] = str(physical_path)
            subprocess.run(command, cwd=ROOT, check=True)
        summary = pd.read_excel(workbook, sheet_name="run_summary").iloc[0]
        reserve_shortfall = float(summary["maximum_reserve_shortfall_kwh"])
        reserve_violations = int(summary["reserve_violation_timesteps"])
        minimum_soc = float(summary["minimum_observed_soc_fraction"])
        terminal_soc = float(summary["terminal_minimum_soc_fraction"])
        rows.append(
            {
                "candidate_id": candidate_id,
                "case": case,
                "mode": mode,
                "configuration": configuration,
                "safety_feasible": bool(
                    summary["status"] == "complete"
                    and reserve_shortfall <= 1e-6
                    and reserve_violations == 0
                    and minimum_soc >= 0.2
                    and terminal_soc >= 0.2
                ),
                "maximum_reserve_shortfall_kwh": reserve_shortfall,
                "minimum_observed_soc_fraction": minimum_soc,
                "terminal_minimum_soc_fraction": terminal_soc,
                "realized_aggregator_revenue": float(
                    summary["realized_aggregator_revenue"]
                ),
                "realized_pto_cost": float(summary["realized_pto_cost"]),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministically calibrate oracle-unsafe advance-warning cells."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/revision/advance_warning_calibration"),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root

    base_notices, base_physical = build()
    rows: list[dict[str, Any]] = []
    for upper_delay in (60, 90, 120, 150):
        notices = copy.deepcopy(base_notices)
        physical = copy.deepcopy(base_physical)
        route_candidate(notices, physical, upper_delay)
        rows.extend(
            run_candidate(
                candidate_id=f"route_delay_{upper_delay}",
                case="aw_route6_late_return",
                mode="selfish",
                notices=notices,
                physical=physical,
                output_root=output_root,
                force=args.force,
            )
        )

    for charger_ids in ([7, 8], [6, 7, 8], [5, 6, 7, 8]):
        notices = copy.deepcopy(base_notices)
        physical = copy.deepcopy(base_physical)
        charger_candidate(notices, physical, list(charger_ids))
        rows.extend(
            run_candidate(
                candidate_id="charger_outage_" + "_".join(map(str, charger_ids)),
                case="aw_charger_bank_shutdown",
                mode="altruistic",
                notices=notices,
                physical=physical,
                output_root=output_root,
                force=args.force,
            )
        )

    frame = pd.DataFrame(rows)
    output_root.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_root / "calibration_summary.csv", index=False)
    (output_root / "calibration_summary.json").write_text(
        frame.to_json(orient="records", indent=2) + "\n", encoding="utf-8"
    )
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()

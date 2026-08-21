"""Build a recoverable three-day persistent charger-derating case."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_closed_loop_notice_cases import (  # noqa: E402
    _canonical,
    _estimate,
    _notice,
    sha256,
    write_lf,
)


OUTPUT_DIR = ROOT / "inputs" / "revision"
NOTICE_OUTPUT = OUTPUT_DIR / "multiday_charger_notices_v1.json"
PHYSICAL_OUTPUT = OUTPUT_DIR / "multiday_charger_physical_events_v1.json"
MANIFEST_OUTPUT = OUTPUT_DIR / "multiday_charger_manifest_v1.json"

MULTIDAY_EVENT = "MD-CHARGER-COOLING"
MULTIDAY_CHARGERS = [6, 7, 8]
NOMINAL_POWER_KW = 200.0
DERATED_POWER_KW = 100.0
MULTIDAY_DAY_CASES = (
    "md_charger_derating_day1",
    "md_charger_derating_day2",
    "md_charger_derating_day3",
)
MULTIDAY_NOMINAL_DAY_CASES = (
    "md_nominal_day1",
    "md_nominal_day2",
    "md_nominal_day3",
)


def _updates(power_kw: float) -> dict[str, Any]:
    return {
        "charger_power_kw": {
            charger: power_kw for charger in MULTIDAY_CHARGERS
        }
    }


def _estimate_power(power_kw: float) -> list[dict[str, Any]]:
    return [
        _estimate(
            "charger_power_kw",
            charger,
            power_kw,
            power_kw,
            power_kw,
            "kw",
        )
        for charger in MULTIDAY_CHARGERS
    ]


def build() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    derated = _updates(DERATED_POWER_KW)
    derated_estimates = _estimate_power(DERATED_POWER_KW)
    notices: list[dict[str, Any]] = []
    physical: list[dict[str, Any]] = []

    notices.extend(
        [
            _notice(
                notice_id="MD-CHARGER-D1-WARN",
                scenario_id=MULTIDAY_DAY_CASES[0],
                event_id=MULTIDAY_EVENT,
                report=22,
                source_type="service_alert",
                text=(
                    "Maintenance chat: the cooling loop shared by Charger 6, "
                    "Charger 7 and Charger 8 is running hot. We may have to cap "
                    "their output this afternoon, but the limit is not confirmed. "
                    "Request confirmation before changing the schedule."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="charger_derating",
                    phase="warning",
                    buses=[],
                    chargers=MULTIDAY_CHARGERS,
                    effective=31,
                    end=48,
                    recommendation="request_confirmation",
                    updates={},
                    confidence=0.58,
                    provisional=True,
                ),
            ),
            _notice(
                notice_id="MD-CHARGER-D1-CONFIRM",
                scenario_id=MULTIDAY_DAY_CASES[0],
                event_id=MULTIDAY_EVENT,
                report=24,
                source_type="service_alert",
                text=(
                    "Maintenance confirmed the temporary cooling limit. Charger "
                    "6, Charger 7 and Charger 8 will each be capped at 100 kW from "
                    "15:00 today. The limit will remain through day 2 and should "
                    "clear at 15:00 on day 3. Optimize before the cap begins."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="charger_derating",
                    phase="onset",
                    buses=[],
                    chargers=MULTIDAY_CHARGERS,
                    effective=31,
                    end=48,
                    recommendation="optimize",
                    updates=derated,
                    estimates=derated_estimates,
                    confidence=0.96,
                ),
            ),
            _notice(
                notice_id="MD-CHARGER-D1-HOLD",
                scenario_id=MULTIDAY_DAY_CASES[0],
                event_id=MULTIDAY_EVENT,
                report=40,
                source_type="service_alert",
                text=(
                    "Maintenance: the same cooling limit on Charger 6, Charger 7 "
                    "and Charger 8 remains unchanged at 100 kW each. Keep the plan."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="charger_derating",
                    phase="persistence",
                    buses=[],
                    chargers=MULTIDAY_CHARGERS,
                    effective=31,
                    end=48,
                    recommendation="wait",
                    updates=derated,
                    estimates=derated_estimates,
                    confidence=0.96,
                ),
            ),
        ]
    )
    physical.append(
        {
            "scenario_id": MULTIDAY_DAY_CASES[0],
            "event_id": MULTIDAY_EVENT,
            "effective_timestep": 31,
            "end_timestep": 48,
            "sensor_detection_timestep": 31,
            "updates": derated,
        }
    )

    notices.extend(
        [
            _notice(
                notice_id="MD-CHARGER-D2-STATUS",
                scenario_id=MULTIDAY_DAY_CASES[1],
                event_id=MULTIDAY_EVENT,
                report=1,
                source_type="service_alert",
                text=(
                    "New day-2 scheduling instruction: Charger 6, Charger 7 and "
                    "Charger 8 are each capped at 100 kW from 00:00 until 24:00. "
                    "Build and optimize today's schedule using the carried fleet "
                    "battery state."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="charger_derating",
                    phase="onset",
                    buses=[],
                    chargers=MULTIDAY_CHARGERS,
                    effective=1,
                    end=48,
                    recommendation="optimize",
                    updates=derated,
                    estimates=derated_estimates,
                    confidence=0.99,
                ),
            ),
            _notice(
                notice_id="MD-CHARGER-D2-HOLD",
                scenario_id=MULTIDAY_DAY_CASES[1],
                event_id=MULTIDAY_EVENT,
                report=26,
                source_type="service_alert",
                text=(
                    "Maintenance: no change. Charger 6, Charger 7 and Charger 8 "
                    "remain at 100 kW each for the rest of day 2. Keep the plan."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="charger_derating",
                    phase="persistence",
                    buses=[],
                    chargers=MULTIDAY_CHARGERS,
                    effective=1,
                    end=48,
                    recommendation="wait",
                    updates=derated,
                    estimates=derated_estimates,
                    confidence=0.99,
                ),
            ),
        ]
    )
    physical.append(
        {
            "scenario_id": MULTIDAY_DAY_CASES[1],
            "event_id": MULTIDAY_EVENT,
            "effective_timestep": 1,
            "end_timestep": 48,
            "sensor_detection_timestep": 1,
            "updates": derated,
        }
    )

    notices.extend(
        [
            _notice(
                notice_id="MD-CHARGER-D3-STATUS",
                scenario_id=MULTIDAY_DAY_CASES[2],
                event_id=MULTIDAY_EVENT,
                report=1,
                source_type="service_alert",
                text=(
                    "New day-3 scheduling instruction: Charger 6, Charger 7 and "
                    "Charger 8 are each capped at 100 kW from 00:00 until 15:00. "
                    "Build and optimize today's schedule using the carried fleet "
                    "battery state."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="charger_derating",
                    phase="onset",
                    buses=[],
                    chargers=MULTIDAY_CHARGERS,
                    effective=1,
                    end=30,
                    recommendation="optimize",
                    updates=derated,
                    estimates=derated_estimates,
                    confidence=0.99,
                ),
            ),
            _notice(
                notice_id="MD-CHARGER-D3-RECOVERY",
                scenario_id=MULTIDAY_DAY_CASES[2],
                event_id=MULTIDAY_EVENT,
                report=31,
                source_type="service_alert",
                text=(
                    "Recovery confirmed at 15:00. The cooling work is complete: "
                    "Charger 6, Charger 7 and Charger 8 are restored to 200 kW "
                    "each for 15:00 to 24:00. Optimize the restored schedule."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="charger_derating",
                    phase="recovery",
                    buses=[],
                    chargers=MULTIDAY_CHARGERS,
                    effective=31,
                    end=48,
                    recommendation="optimize",
                    updates=_updates(NOMINAL_POWER_KW),
                    estimates=_estimate_power(NOMINAL_POWER_KW),
                    confidence=1.0,
                ),
            ),
        ]
    )
    physical.append(
        {
            "scenario_id": MULTIDAY_DAY_CASES[2],
            "event_id": MULTIDAY_EVENT,
            "effective_timestep": 1,
            "end_timestep": 30,
            "sensor_detection_timestep": 1,
            "updates": derated,
        }
    )
    for day, scenario_id in ((2, MULTIDAY_NOMINAL_DAY_CASES[1]), (3, MULTIDAY_NOMINAL_DAY_CASES[2])):
        notices.append(
            _notice(
                notice_id=f"MD-NOMINAL-D{day}-HANDOVER",
                scenario_id=scenario_id,
                event_id=f"MD-NOMINAL-D{day}",
                report=1,
                source_type="informational",
                text=(
                    f"New day-{day} scheduling boundary: no charger, route, or "
                    "service restriction is active. Build today's nominal schedule "
                    "using the carried fleet battery state."
                ),
                canonical=_canonical(
                    event_id=f"MD-NOMINAL-D{day}",
                    source_type="informational",
                    event_type="informational",
                    phase="onset",
                    buses=[],
                    chargers=[],
                    effective=1,
                    end=48,
                    recommendation="optimize",
                    updates={},
                    confidence=1.0,
                ),
            )
        )
    return notices, physical


def main() -> None:
    notices, physical = build()
    write_lf(NOTICE_OUTPUT, json.dumps(notices, indent=2) + "\n")
    write_lf(
        PHYSICAL_OUTPUT,
        json.dumps({"events": physical}, indent=2) + "\n",
    )
    manifest = {
        "version": "multiday_charger_derating_v1",
        "frozen_date_utc": "2026-08-21",
        "design": "three-day chained rolling horizon with exact terminal battery-energy carryover",
        "fleet_size": 8,
        "disturbance": "temporary charger cooling derating",
        "affected_chargers": MULTIDAY_CHARGERS,
        "nominal_power_kw": NOMINAL_POWER_KW,
        "derated_power_kw": DERATED_POWER_KW,
        "day_windows": {"day_1": [31, 48], "day_2": [1, 48], "day_3": [1, 30]},
        "daily_case_ids": list(MULTIDAY_DAY_CASES),
        "nominal_control_daily_case_ids": list(MULTIDAY_NOMINAL_DAY_CASES),
        "notice_file": str(NOTICE_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
        "physical_event_file": str(PHYSICAL_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
        "notice_sha256": sha256(NOTICE_OUTPUT),
        "physical_event_sha256": sha256(PHYSICAL_OUTPUT),
        "selection_rule": {
            "calibrate_deterministically_before_agent_execution": True,
            "require_all_deterministic_methods_operationally_feasible": True,
            "agent_outcomes_used_for_selection": False,
        },
        "nominal_control": {
            "physical_disturbance": "none",
            "daily_replanning": "day 2 and day 3 are re-optimized at timestep 1 using exact carried battery energy",
            "purpose": "estimate incremental daily and three-day effects of the derating",
        },
        "rejected_calibration": {
            "case": "25 percent route-energy increase for Buses 1 and 4",
            "reason": "all deterministic methods violated reserve after physical SOC carryover",
            "included_in_primary_reporting": False,
        },
    }
    write_lf(MANIFEST_OUTPUT, json.dumps(manifest, indent=2) + "\n")
    print(
        f"Wrote {len(notices)} notices and {len(physical)} daily physical-event slices"
    )


if __name__ == "__main__":
    main()

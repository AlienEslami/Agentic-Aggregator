"""Build the recoverable clustered-delay and chained three-day cases.

The original clustered-delay stress case is intentionally left unchanged in
the v2 archive.  This builder creates a separate, prospectively specified case
whose inclusion criteria are operational feasibility under exact information
and a non-zero physical effect.  It also creates three daily slices of one
persistent roadwork detour for a chained rolling-horizon experiment.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
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
NOTICE_OUTPUT = OUTPUT_DIR / "followup_notices_v1.json"
PHYSICAL_OUTPUT = OUTPUT_DIR / "followup_physical_events_v1.json"
PROTOCOL_OUTPUT = OUTPUT_DIR / "advance_warning_recoverable_cluster_protocol_v1.json"
MANIFEST_OUTPUT = OUTPUT_DIR / "followup_disturbance_manifest_v1.json"
BASE_PROTOCOL_INPUT = OUTPUT_DIR / "advance_warning_ablation_protocol_v8.json"

RECOVERABLE_CASE = "aw_recoverable_clustered_late_returns"
RECOVERABLE_EVENT = "AW-RECOVERABLE-CLUSTER"
RECOVERABLE_DELAYS = {3: 60, 4: 45, 7: 60}
RECOVERABLE_EFFECTIVE = 40

MULTIDAY_EVENT = "MD-ROADWORK-DETOUR"
MULTIDAY_BUSES = [1, 4]
MULTIDAY_ENERGY_MULTIPLIER = 1.25
MULTIDAY_DAY_CASES = (
    "md_roadwork_day1",
    "md_roadwork_day2",
    "md_roadwork_day3",
)


def _energy_updates(multiplier: float) -> dict[str, Any]:
    return {
        "energy_multiplier_by_bus": {
            bus: multiplier for bus in MULTIDAY_BUSES
        }
    }


def recoverable_cluster_case() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A three-bus return-delay cluster with overnight recovery headroom."""

    pending = [
        _estimate("return_delay_minutes", bus, 30, delay, None, "minutes")
        for bus, delay in RECOVERABLE_DELAYS.items()
    ]
    confirmed = [
        _estimate("return_delay_minutes", bus, 30, delay, delay, "minutes")
        for bus, delay in RECOVERABLE_DELAYS.items()
    ]
    buses = sorted(RECOVERABLE_DELAYS)
    updates = {
        "return_delay_minutes_by_bus": dict(sorted(RECOVERABLE_DELAYS.items()))
    }
    notices = [
        _notice(
            notice_id="AW-RECOVERABLE-CLUSTER-WARN",
            scenario_id=RECOVERABLE_CASE,
            event_id=RECOVERABLE_EVENT,
            report=34,
            source_type="driver_chat",
            text=(
                "Driver 3: traffic control on the depot approach is backing up. "
                "Drivers 4 and 7 report the same queue. Returns may be 30 to 60 "
                "minutes late in the 20:00-to-end-of-day window, but dispatch has "
                "not confirmed the individual times. Request confirmation."
            ),
            canonical=_canonical(
                event_id=RECOVERABLE_EVENT,
                source_type="driver_chat",
                event_type="service_delay",
                phase="warning",
                buses=buses,
                chargers=[],
                effective=RECOVERABLE_EFFECTIVE,
                end=48,
                recommendation="request_confirmation",
                updates={},
                estimates=pending,
                confidence=0.58,
                provisional=True,
            ),
        ),
        _notice(
            notice_id="AW-RECOVERABLE-CLUSTER-CONFIRM",
            scenario_id=RECOVERABLE_CASE,
            event_id=RECOVERABLE_EVENT,
            report=36,
            source_type="combined",
            text=(
                "Dispatcher: confirmed clustered late returns. Bus 3 will return "
                "60 minutes late, Bus 4 will return 45 minutes late, and Bus 7 "
                "will return 60 minutes late. The control window is 20:00 to "
                "24:00, tomorrow's departures are unchanged, and the schedule "
                "should be optimized now."
            ),
            canonical=_canonical(
                event_id=RECOVERABLE_EVENT,
                source_type="combined",
                event_type="service_delay",
                phase="onset",
                buses=buses,
                chargers=[],
                effective=RECOVERABLE_EFFECTIVE,
                end=48,
                recommendation="optimize",
                updates=updates,
                estimates=confirmed,
                confidence=0.94,
            ),
        ),
        _notice(
            notice_id="AW-RECOVERABLE-CLUSTER-HOLD",
            scenario_id=RECOVERABLE_CASE,
            event_id=RECOVERABLE_EVENT,
            report=42,
            source_type="driver_chat",
            text=(
                "Dispatch: the same Bus 3, Bus 4 and Bus 7 late-return event is "
                "unchanged. Keep the current plan; no replan."
            ),
            canonical=_canonical(
                event_id=RECOVERABLE_EVENT,
                source_type="driver_chat",
                event_type="service_delay",
                phase="persistence",
                buses=buses,
                chargers=[],
                effective=RECOVERABLE_EFFECTIVE,
                end=48,
                recommendation="wait",
                updates=updates,
                estimates=confirmed,
                confidence=0.94,
            ),
        ),
    ]
    physical = {
        "scenario_id": RECOVERABLE_CASE,
        "event_id": RECOVERABLE_EVENT,
        "effective_timestep": RECOVERABLE_EFFECTIVE,
        "end_timestep": 48,
        "sensor_detection_timestep": RECOVERABLE_EFFECTIVE,
        "updates": updates,
    }
    return notices, physical


def multiday_roadwork_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Three daily slices of one 48-hour temporary roadwork detour.

    The detour starts at 15:00 on day 1, is active all day 2, and clears after
    15:00 on day 3.  Buses 1 and 4 use the affected western loop and consume
    25 percent more traction energy while it is active.
    """

    selected = [
        _estimate(
            "energy_multiplier",
            bus,
            MULTIDAY_ENERGY_MULTIPLIER,
            MULTIDAY_ENERGY_MULTIPLIER,
            MULTIDAY_ENERGY_MULTIPLIER,
            "multiplier",
        )
        for bus in MULTIDAY_BUSES
    ]
    updates = _energy_updates(MULTIDAY_ENERGY_MULTIPLIER)
    notices: list[dict[str, Any]] = []
    physical: list[dict[str, Any]] = []

    notices.extend(
        [
            _notice(
                notice_id="MD-ROADWORK-D1-CONFIRM",
                scenario_id=MULTIDAY_DAY_CASES[0],
                event_id=MULTIDAY_EVENT,
                report=24,
                source_type="service_alert",
                text=(
                    "Road supervisor: confirmed emergency roadwork on the western "
                    "loop. Buses 1 and 4 will consume 25 percent more traction "
                    "energy from 15:00 until 24:00 today. The detour will remain "
                    "through tomorrow and is expected to reopen at 15:00 on day 3. "
                    "Optimize today's remaining schedule now."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="route_energy_change",
                    phase="onset",
                    buses=MULTIDAY_BUSES,
                    chargers=[],
                    effective=31,
                    end=48,
                    recommendation="optimize",
                    updates=updates,
                    estimates=selected,
                    confidence=0.95,
                ),
            ),
            _notice(
                notice_id="MD-ROADWORK-D1-HOLD",
                scenario_id=MULTIDAY_DAY_CASES[0],
                event_id=MULTIDAY_EVENT,
                report=40,
                source_type="service_alert",
                text=(
                    "Road supervisor: the same western-loop restriction for Buses "
                    "1 and 4 remains unchanged. Consumption is still 25 percent "
                    "higher; keep the current plan."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="route_energy_change",
                    phase="persistence",
                    buses=MULTIDAY_BUSES,
                    chargers=[],
                    effective=31,
                    end=48,
                    recommendation="wait",
                    updates=updates,
                    estimates=selected,
                    confidence=0.95,
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
            "updates": updates,
        }
    )

    notices.extend(
        [
            _notice(
                notice_id="MD-ROADWORK-D2-STATUS",
                scenario_id=MULTIDAY_DAY_CASES[1],
                event_id=MULTIDAY_EVENT,
                report=1,
                source_type="service_alert",
                text=(
                    "Day 2 operations update: the western-loop roadwork remains "
                    "active for the full day. Buses 1 and 4 will consume 25 percent "
                    "more traction energy from 00:00 until 24:00. Optimize the new "
                    "daily schedule using the carried battery state."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="route_energy_change",
                    phase="onset",
                    buses=MULTIDAY_BUSES,
                    chargers=[],
                    effective=1,
                    end=48,
                    recommendation="optimize",
                    updates=updates,
                    estimates=selected,
                    confidence=0.98,
                ),
            ),
            _notice(
                notice_id="MD-ROADWORK-D2-HOLD",
                scenario_id=MULTIDAY_DAY_CASES[1],
                event_id=MULTIDAY_EVENT,
                report=26,
                source_type="service_alert",
                text=(
                    "Road supervisor: no change for Buses 1 and 4; consumption is "
                    "still 25 percent higher for the rest of day 2. Keep the plan."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="route_energy_change",
                    phase="persistence",
                    buses=MULTIDAY_BUSES,
                    chargers=[],
                    effective=1,
                    end=48,
                    recommendation="wait",
                    updates=updates,
                    estimates=selected,
                    confidence=0.98,
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
            "updates": updates,
        }
    )

    notices.extend(
        [
            _notice(
                notice_id="MD-ROADWORK-D3-STATUS",
                scenario_id=MULTIDAY_DAY_CASES[2],
                event_id=MULTIDAY_EVENT,
                report=1,
                source_type="service_alert",
                text=(
                    "Day 3 operations update: the western-loop roadwork remains "
                    "active until 15:00. Buses 1 and 4 will consume 25 percent "
                    "more traction energy from 00:00 through 15:00. Optimize the "
                    "new daily schedule using the carried battery state."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="route_energy_change",
                    phase="onset",
                    buses=MULTIDAY_BUSES,
                    chargers=[],
                    effective=1,
                    end=30,
                    recommendation="optimize",
                    updates=updates,
                    estimates=selected,
                    confidence=0.98,
                ),
            ),
            _notice(
                notice_id="MD-ROADWORK-D3-RECOVERY",
                scenario_id=MULTIDAY_DAY_CASES[2],
                event_id=MULTIDAY_EVENT,
                report=31,
                source_type="service_alert",
                text=(
                    "Recovery confirmed at 15:00: the western loop has reopened. "
                    "Buses 1 and 4 return to normal consumption at 1.0 x traction "
                    "energy for 15:00 to 24:00. Optimize the restored schedule."
                ),
                canonical=_canonical(
                    event_id=MULTIDAY_EVENT,
                    source_type="service_alert",
                    event_type="route_energy_change",
                    phase="recovery",
                    buses=MULTIDAY_BUSES,
                    chargers=[],
                    effective=31,
                    end=48,
                    recommendation="optimize",
                    updates=_energy_updates(1.0),
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
            "updates": updates,
        }
    )
    return notices, physical


def recoverable_protocol() -> dict[str, Any]:
    protocol = deepcopy(json.loads(BASE_PROTOCOL_INPUT.read_text(encoding="utf-8")))
    design = protocol["design"]
    design["cases"] = [RECOVERABLE_CASE]
    design["case_mode_cells"] = len(design["modes"])
    design["planned_runs"] = (
        len(design["configurations"])
        * len(design["cases"])
        * len(design["modes"])
        * int(design["repetitions_per_configuration_case_mode"])
    )
    baseline = design.get("nonagentic_stack_baseline")
    if baseline:
        baseline["planned_runs"] = (
            len(baseline["configurations"])
            * len(design["cases"])
            * len(design["modes"])
            * int(baseline["repetitions_per_configuration_case_mode"])
        )
    protocol.update(
        {
            "protocol_version": "advance_warning_recoverable_cluster_v1",
            "status": "frozen_before_execution",
            "frozen_date_utc": "2026-08-21",
            "parent_protocol": (
                "inputs/revision/advance_warning_ablation_protocol_v8.json"
            ),
            "change_reason": [
                "replace the operationally infeasible clustered-delay stress case in the primary reviewer-facing analysis",
                "select the replacement before observing Agent outcomes using exact-information operational feasibility and non-zero physical effect",
                "retain the old case and its outputs as an archived boundary stress test",
            ],
        }
    )
    protocol["controls"]["notice_dataset"] = str(
        NOTICE_OUTPUT.relative_to(ROOT)
    ).replace("\\", "/")
    protocol["controls"]["physical_event_dataset"] = str(
        PHYSICAL_OUTPUT.relative_to(ROOT)
    ).replace("\\", "/")
    protocol["controls"]["case_selection_rule"] = {
        "selected_before_agent_execution": True,
        "required_exact_information_operational_feasibility": True,
        "required_nonzero_physical_effect": True,
        "agent_economic_outcome_used_for_selection": False,
    }
    return protocol


def main() -> None:
    cluster_notices, cluster_physical = recoverable_cluster_case()
    multiday_notices, multiday_physical = multiday_roadwork_cases()
    notices = [*cluster_notices, *multiday_notices]
    physical = [cluster_physical, *multiday_physical]

    write_lf(NOTICE_OUTPUT, json.dumps(notices, indent=2) + "\n")
    write_lf(
        PHYSICAL_OUTPUT,
        json.dumps({"events": physical}, indent=2) + "\n",
    )
    write_lf(PROTOCOL_OUTPUT, json.dumps(recoverable_protocol(), indent=2) + "\n")
    manifest = {
        "version": "followup_disturbance_cases_v1",
        "frozen_date_utc": "2026-08-21",
        "notice_file": str(NOTICE_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
        "physical_event_file": str(PHYSICAL_OUTPUT.relative_to(ROOT)).replace(
            "\\", "/"
        ),
        "recoverable_cluster_protocol": str(
            PROTOCOL_OUTPUT.relative_to(ROOT)
        ).replace("\\", "/"),
        "notice_sha256": sha256(NOTICE_OUTPUT),
        "physical_event_sha256": sha256(PHYSICAL_OUTPUT),
        "recoverable_cluster_protocol_sha256": sha256(PROTOCOL_OUTPUT),
        "archived_case_excluded_from_primary_reporting": {
            "scenario_id": "aw_clustered_late_returns",
            "files_deleted": False,
            "reason": "operationally infeasible under the realized trajectory for all compared trigger methods",
        },
        "recoverable_cluster": {
            "scenario_id": RECOVERABLE_CASE,
            "buses": sorted(RECOVERABLE_DELAYS),
            "return_delay_minutes_by_bus": dict(sorted(RECOVERABLE_DELAYS.items())),
            "effective_timestep": RECOVERABLE_EFFECTIVE,
            "selection_rule": "freeze after exact-information feasibility and physical-effect calibration, before Agent execution",
        },
        "multiday": {
            "design": "three-day chained rolling horizon with realized terminal battery energy carried into the next day",
            "fleet_size": 8,
            "disturbance": "temporary western-loop roadwork detour",
            "affected_buses": MULTIDAY_BUSES,
            "energy_multiplier": MULTIDAY_ENERGY_MULTIPLIER,
            "day_windows": {
                "day_1": [31, 48],
                "day_2": [1, 48],
                "day_3": [1, 30],
            },
            "daily_case_ids": list(MULTIDAY_DAY_CASES),
            "single_144_step_lookahead": False,
        },
    }
    write_lf(MANIFEST_OUTPUT, json.dumps(manifest, indent=2) + "\n")
    print(
        f"Wrote {len(notices)} notices and {len(physical)} physical events "
        "for one recoverable cluster and one chained three-day disturbance"
    )


if __name__ == "__main__":
    main()

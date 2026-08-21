"""Build the broader real-time disturbance cases requested in review.

Reviewer 1 asked for the disturbance model to be exercised beyond the original
isolated patterns: delays clustered on several buses inside one window, a
multi-step price escalation rather than a single jump, longer route-energy
shifts, and persistence of an event across the horizon.

Nothing here modifies the frozen v1 advance-warning dataset.  This script emits
a *v2* dataset that contains the three v1 cases unchanged plus the new ones, so
published results keep validating against v1 while new runs can select v2.  It
also emits a disturbance workbook holding the multi-step price escalation,
which travels through the numerical disturbance path rather than through a
notice, because the physical-event schema carries fleet and charger updates but
no prices.

Horizon note: the harness simulates a single 48-timestep day, so "persistence"
is represented by an event that stays active from onset to the end of the
horizon and is re-announced at later timesteps.  A genuine multi-day run would
require extending the optimizer horizon and is out of scope here.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_closed_loop_notice_cases import (  # noqa: E402
    _canonical,
    _estimate,
    _notice,
    build as build_v1,
    sha256,
    write_lf,
)


OUTPUT_DIR = ROOT / "inputs" / "revision"
NOTICE_OUTPUT = OUTPUT_DIR / "advance_warning_notices_v2.json"
PHYSICAL_OUTPUT = OUTPUT_DIR / "advance_warning_physical_events_v2.json"
MANIFEST_OUTPUT = OUTPUT_DIR / "advance_warning_manifest_v2.json"
DISTURBANCE_OUTPUT = OUTPUT_DIR / "rt_disturbance_scenarios_revision_e6.xlsx"
BASE_PROTOCOL_INPUT = OUTPUT_DIR / "advance_warning_ablation_protocol_v8.json"
EXTENDED_PROTOCOL_OUTPUT = (
    OUTPUT_DIR / "advance_warning_ablation_protocol_extended_v1.json"
)

CLUSTERED_BUSES = {2: 45, 3: 60, 5: 75}
CLUSTERED_EFFECTIVE = 40
ENERGY_BUSES = [1, 4]
ENERGY_MULTIPLIER = 1.25
ENERGY_EFFECTIVE = 20


def clustered_delay_case() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Three buses returning late inside the same evening window.

    The v1 cases delay one bus at a time.  A cluster is materially different
    for the supervisory layer: the deviation indicators fire together, so the
    question is no longer whether to re-optimize but how to allocate scarce
    charger time between competing late arrivals.
    """

    estimates_pending = [
        _estimate("return_delay_minutes", bus, 30, delay, None, "minutes")
        for bus, delay in CLUSTERED_BUSES.items()
    ]
    estimates_confirmed = [
        _estimate("return_delay_minutes", bus, 30, delay, delay, "minutes")
        for bus, delay in CLUSTERED_BUSES.items()
    ]
    buses = sorted(CLUSTERED_BUSES)
    notices = [
        _notice(
            notice_id="AW-CLUSTER-WARN",
            scenario_id="aw_clustered_late_returns",
            event_id="AW-CLUSTER",
            report=34,
            source_type="driver_chat",
            text=(
                "Driver 2: the ring road is blocked northbound, we are crawling. "
                "Driver 3: same here, and 5 is behind me. Depot, expect all three "
                "of us back somewhere between half an hour and an hour and a "
                "quarter late tonight. Nothing confirmed yet."
            ),
            canonical=_canonical(
                event_id="AW-CLUSTER",
                source_type="driver_chat",
                event_type="service_delay",
                phase="warning",
                buses=buses,
                chargers=[],
                effective=CLUSTERED_EFFECTIVE,
                end=48,
                recommendation="request_confirmation",
                updates={},
                estimates=estimates_pending,
                confidence=0.55,
                provisional=True,
            ),
        ),
        _notice(
            notice_id="AW-CLUSTER-CONFIRM",
            scenario_id="aw_clustered_late_returns",
            event_id="AW-CLUSTER",
            report=36,
            source_type="combined",
            text=(
                "Dispatcher: confirmed for the three units on the ring road. Bus 2 "
                "is 45 minutes late, Bus 3 is 60 minutes late and Bus 5 is 75 "
                "minutes late, all returning inside the 20:00 to end-of-day window. "
                "Departures tomorrow are unchanged. Replan now."
            ),
            canonical=_canonical(
                event_id="AW-CLUSTER",
                source_type="combined",
                event_type="service_delay",
                phase="onset",
                buses=buses,
                chargers=[],
                effective=CLUSTERED_EFFECTIVE,
                end=48,
                recommendation="optimize",
                updates={
                    "return_delay_minutes_by_bus": dict(sorted(CLUSTERED_BUSES.items()))
                },
                estimates=estimates_confirmed,
                confidence=0.93,
            ),
        ),
        _notice(
            notice_id="AW-CLUSTER-HOLD",
            scenario_id="aw_clustered_late_returns",
            event_id="AW-CLUSTER",
            report=42,
            source_type="driver_chat",
            text=(
                "Dispatch: the same three late returns are still running to the "
                "confirmed times. No change; keep the current plan."
            ),
            canonical=_canonical(
                event_id="AW-CLUSTER",
                source_type="driver_chat",
                event_type="service_delay",
                phase="persistence",
                buses=buses,
                chargers=[],
                effective=CLUSTERED_EFFECTIVE,
                end=48,
                recommendation="wait",
                updates={
                    "return_delay_minutes_by_bus": dict(sorted(CLUSTERED_BUSES.items()))
                },
                estimates=estimates_confirmed,
                confidence=0.93,
            ),
        ),
    ]
    physical = {
        "scenario_id": "aw_clustered_late_returns",
        "event_id": "AW-CLUSTER",
        "effective_timestep": CLUSTERED_EFFECTIVE,
        "end_timestep": 48,
        "sensor_detection_timestep": CLUSTERED_EFFECTIVE,
        "updates": {
            "return_delay_minutes_by_bus": dict(sorted(CLUSTERED_BUSES.items()))
        },
    }
    return notices, physical


def extended_energy_shift_case() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A sustained route-energy increase announced early and held all day.

    The v1 cases are short and late in the horizon.  This one starts in the
    first half of the day and stays active until the end, so the supervisory
    layer has to decide once and then resist re-triggering on an unchanged
    condition — the behaviour the persistence phase is meant to probe.
    """

    estimates_pending = [
        _estimate("energy_multiplier", bus, 1.15, ENERGY_MULTIPLIER, None, "multiplier")
        for bus in ENERGY_BUSES
    ]
    estimates_confirmed = [
        _estimate(
            "energy_multiplier", bus, 1.15, ENERGY_MULTIPLIER, ENERGY_MULTIPLIER, "multiplier"
        )
        for bus in ENERGY_BUSES
    ]
    updates = {
        "energy_multiplier_by_bus": {bus: ENERGY_MULTIPLIER for bus in ENERGY_BUSES}
    }
    notices = [
        _notice(
            notice_id="AW-ENERGY-WARN",
            scenario_id="aw_extended_energy_shift",
            event_id="AW-ENERGY",
            report=14,
            source_type="service_alert",
            text=(
                "Operations: the detour on the western loop stays in place for the "
                "rest of the day. Units 1 and 4 run it. Traction use is up somewhere "
                "between fifteen and twenty-five percent; the exact figure is still "
                "being checked. Hold until we confirm."
            ),
            canonical=_canonical(
                event_id="AW-ENERGY",
                source_type="service_alert",
                event_type="route_energy_change",
                phase="warning",
                buses=ENERGY_BUSES,
                chargers=[],
                effective=ENERGY_EFFECTIVE,
                end=48,
                recommendation="request_confirmation",
                updates={},
                estimates=estimates_pending,
                confidence=0.6,
                provisional=True,
            ),
        ),
        _notice(
            notice_id="AW-ENERGY-CONFIRM",
            scenario_id="aw_extended_energy_shift",
            event_id="AW-ENERGY",
            report=16,
            source_type="service_alert",
            text=(
                "Operations: confirmed. Units 1 and 4 consume 1.25 times the planned "
                "traction energy from 10:00 until the end of service. Plan on the "
                "confirmed figure."
            ),
            canonical=_canonical(
                event_id="AW-ENERGY",
                source_type="service_alert",
                event_type="route_energy_change",
                phase="onset",
                buses=ENERGY_BUSES,
                chargers=[],
                effective=ENERGY_EFFECTIVE,
                end=48,
                recommendation="optimize",
                updates=updates,
                estimates=estimates_confirmed,
                confidence=0.9,
            ),
        ),
        _notice(
            notice_id="AW-ENERGY-HOLD-1",
            scenario_id="aw_extended_energy_shift",
            event_id="AW-ENERGY",
            report=26,
            source_type="service_alert",
            text=(
                "Operations: the western loop detour is unchanged and the traction "
                "figure still holds. No action needed."
            ),
            canonical=_canonical(
                event_id="AW-ENERGY",
                source_type="service_alert",
                event_type="route_energy_change",
                phase="persistence",
                buses=ENERGY_BUSES,
                chargers=[],
                effective=ENERGY_EFFECTIVE,
                end=48,
                recommendation="wait",
                updates=updates,
                estimates=estimates_confirmed,
                confidence=0.9,
            ),
        ),
        _notice(
            notice_id="AW-ENERGY-HOLD-2",
            scenario_id="aw_extended_energy_shift",
            event_id="AW-ENERGY",
            report=38,
            source_type="service_alert",
            text=(
                "Operations: still the same detour, same consumption. Nothing new to "
                "report tonight."
            ),
            canonical=_canonical(
                event_id="AW-ENERGY",
                source_type="service_alert",
                event_type="route_energy_change",
                phase="persistence",
                buses=ENERGY_BUSES,
                chargers=[],
                effective=ENERGY_EFFECTIVE,
                end=48,
                recommendation="wait",
                updates=updates,
                estimates=estimates_confirmed,
                confidence=0.9,
            ),
        ),
    ]
    physical = {
        "scenario_id": "aw_extended_energy_shift",
        "event_id": "AW-ENERGY",
        "effective_timestep": ENERGY_EFFECTIVE,
        "end_timestep": 48,
        "sensor_detection_timestep": ENERGY_EFFECTIVE,
        "updates": {
            "energy_multiplier_by_bus": {
                bus: ENERGY_MULTIPLIER for bus in ENERGY_BUSES
            }
        },
    }
    return notices, physical


def multistep_price_scenarios() -> pd.DataFrame:
    """A price escalation delivered in three steps instead of one jump.

    Prices reach the disturbance model through the scenarios workbook, not
    through notices, so the escalation is expressed as three overlapping
    windows that are selected together with repeated ``--scenario`` options.
    A matching de-escalation scenario is provided so the recovery branch of the
    trigger gate is exercised as well.
    """

    rows = [
        {
            "scenario_id": "price_step_up_1",
            "scenario_family": "price_pct",
            "scenario_level": 25,
            "disturbance_sign": 1,
            "target_scope": "global",
            "target_bus_id": None,
            "start_timestep": 34,
            "end_timestep": 37,
        },
        {
            "scenario_id": "price_step_up_2",
            "scenario_family": "price_pct",
            "scenario_level": 50,
            "disturbance_sign": 1,
            "target_scope": "global",
            "target_bus_id": None,
            "start_timestep": 38,
            "end_timestep": 41,
        },
        {
            "scenario_id": "price_step_up_3",
            "scenario_family": "price_pct",
            "scenario_level": 75,
            "disturbance_sign": 1,
            "target_scope": "global",
            "target_bus_id": None,
            "start_timestep": 42,
            "end_timestep": 45,
        },
        {
            "scenario_id": "price_step_down_recovery",
            "scenario_family": "price_pct",
            "scenario_level": 30,
            "disturbance_sign": -1,
            "target_scope": "global",
            "target_bus_id": None,
            "start_timestep": 46,
            "end_timestep": 48,
        },
        {
            "scenario_id": "rt_none",
            "scenario_family": "none",
            "scenario_level": 0,
            "disturbance_sign": 0,
            "target_scope": "global",
            "target_bus_id": None,
            "start_timestep": 1,
            "end_timestep": 48,
        },
    ]
    return pd.DataFrame(rows)


def extended_ablation_protocol() -> dict[str, Any]:
    """Freeze the extended cases separately from the original v8 experiment.

    The Agent prompts, optimizer, solver controls and role configurations remain
    identical to v8.  Only the declared case set and its derived run counts
    change, which prevents extended-case results from being silently pooled with
    the original three-case matrix.
    """

    protocol = deepcopy(json.loads(BASE_PROTOCOL_INPUT.read_text(encoding="utf-8")))
    cases = ["aw_clustered_late_returns", "aw_extended_energy_shift"]
    design = protocol["design"]
    modes = design["modes"]
    repetitions = int(design["repetitions_per_configuration_case_mode"])
    configurations = design["configurations"]

    protocol.update(
        {
            "protocol_version": "advance_warning_ablation_extended_v1",
            "status": "implemented_and_frozen_pending_execution",
            "frozen_date_utc": "2026-08-21",
            "supersedes_for_future_runs": None,
            "parent_protocol": (
                "inputs/revision/advance_warning_ablation_protocol_v8.json"
            ),
            "change_reason": [
                "broaden the disturbance patterns to clustered late returns and a sustained route-energy shift",
                "keep prompts, optimizer, settlement, solver controls and the 50 percent revenue-retention policy identical to v8",
                "freeze the extended cases separately so they are not silently pooled with the original v8 case matrix",
            ],
        }
    )
    design["cases"] = cases
    design["case_mode_cells"] = len(cases) * len(modes)
    design["planned_runs"] = (
        len(configurations) * len(cases) * len(modes) * repetitions
    )
    baseline = design.get("nonagentic_stack_baseline")
    if baseline is not None:
        baseline["planned_runs"] = (
            len(baseline["configurations"])
            * len(cases)
            * len(modes)
            * int(baseline["repetitions_per_configuration_case_mode"])
        )
    protocol["controls"]["notice_dataset"] = (
        "inputs/revision/advance_warning_notices_v2.json"
    )
    protocol["controls"]["physical_event_dataset"] = (
        "inputs/revision/advance_warning_physical_events_v2.json"
    )
    return protocol


def main() -> None:
    v1_notices, v1_physical = build_v1()

    clustered_notices, clustered_physical = clustered_delay_case()
    energy_notices, energy_physical = extended_energy_shift_case()

    notices = [*v1_notices, *clustered_notices, *energy_notices]
    physical = [*v1_physical, clustered_physical, energy_physical]

    write_lf(NOTICE_OUTPUT, json.dumps(notices, indent=2) + "\n")
    write_lf(PHYSICAL_OUTPUT, json.dumps({"events": physical}, indent=2) + "\n")
    write_lf(
        EXTENDED_PROTOCOL_OUTPUT,
        json.dumps(extended_ablation_protocol(), indent=2) + "\n",
    )

    scenarios = multistep_price_scenarios()
    DISTURBANCE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(DISTURBANCE_OUTPUT, engine="openpyxl") as writer:
        scenarios.to_excel(writer, sheet_name="scenarios", index=False)

    manifest = {
        "version": "advance_warning_benchmark_v2_extended",
        "supersedes_for_future_runs": (
            "inputs/revision/advance_warning_manifest_v1.json"
        ),
        "frozen_v1_cases_unchanged": True,
        "notice_file": str(NOTICE_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
        "physical_event_file": str(PHYSICAL_OUTPUT.relative_to(ROOT)).replace(
            "\\", "/"
        ),
        "disturbance_workbook": str(DISTURBANCE_OUTPUT.relative_to(ROOT)).replace(
            "\\", "/"
        ),
        "extended_ablation_protocol": str(
            EXTENDED_PROTOCOL_OUTPUT.relative_to(ROOT)
        ).replace("\\", "/"),
        "notice_sha256": sha256(NOTICE_OUTPUT),
        "physical_event_sha256": sha256(PHYSICAL_OUTPUT),
        "extended_ablation_protocol_sha256": sha256(EXTENDED_PROTOCOL_OUTPUT),
        "cases": [
            {
                "scenario_id": "aw_route6_late_return",
                "origin": "v1",
                "pattern": "single-bus late return",
            },
            {
                "scenario_id": "aw_charger_bank_shutdown",
                "origin": "v1",
                "pattern": "charger-bank failure",
            },
            {
                "scenario_id": "aw_combined_evening",
                "origin": "v1",
                "pattern": "combined delay and charger failure",
            },
            {
                "scenario_id": "aw_clustered_late_returns",
                "origin": "v2",
                "pattern": "three buses delayed inside one window",
                "buses": sorted(CLUSTERED_BUSES),
                "return_delay_minutes_by_bus": dict(sorted(CLUSTERED_BUSES.items())),
                "effective_timestep": CLUSTERED_EFFECTIVE,
            },
            {
                "scenario_id": "aw_extended_energy_shift",
                "origin": "v2",
                "pattern": (
                    "sustained route-energy increase held to the end of the horizon"
                ),
                "buses": ENERGY_BUSES,
                "energy_multiplier": ENERGY_MULTIPLIER,
                "effective_timestep": ENERGY_EFFECTIVE,
                "persistence_reports": [26, 38],
            },
        ],
        "price_scenarios": {
            "delivery": "numerical disturbance workbook, not a notice",
            "scenario_ids": [
                "price_step_up_1",
                "price_step_up_2",
                "price_step_up_3",
                "price_step_down_recovery",
            ],
            "composition": (
                "select the three step scenarios together to obtain a monotone "
                "escalation of 25, 50 and 75 percent, optionally followed by the "
                "recovery scenario"
            ),
        },
        "horizon_limitation": (
            "The harness simulates one 48-timestep day. Persistence is exercised "
            "within the horizon; a multi-day study requires extending the "
            "optimizer horizon."
        ),
    }
    write_lf(MANIFEST_OUTPUT, json.dumps(manifest, indent=2) + "\n")

    print(
        f"Wrote {len(notices)} notices and {len(physical)} physical events "
        f"({len(physical) - len(v1_physical)} new cases) plus "
        f"{len(scenarios)} disturbance scenarios and an extended protocol"
    )


if __name__ == "__main__":
    main()

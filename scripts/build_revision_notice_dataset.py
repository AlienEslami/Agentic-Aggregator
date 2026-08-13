from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "inputs" / "revision"
DATASET_VERSION = "trigger_notices_v2"


SEQUENCES = [
    {
        "scenario_id": "svc_route4_detour",
        "event_id": "SVC-01",
        "source_type": "service_alert",
        "event_type": "service_delay",
        "buses": [4],
        "chargers": [],
        "updates": {"delay_minutes_by_bus": {"4": 25}, "energy_multiplier_by_bus": {"4": 1.10}},
        "explicit": "Route block 4, served by bus 4, is detoured. Cycle time will increase 25 minutes and route energy consumption is expected to increase 10 percent.",
        "indirect": "Water-main work diverts route 4; bus 4 should allow roughly 25 extra minutes and about 10% more traction energy.",
        "operational": "Ops ticket SVC-01: water-main diversion on block 4. Unit 4 planning allowance is +25 min and 1.10x traction energy; clearance time pending.",
    },
    {
        "scenario_id": "svc_route6_closure",
        "event_id": "SVC-02",
        "source_type": "service_alert",
        "event_type": "service_delay",
        "buses": [6],
        "chargers": [],
        "updates": {"delay_minutes_by_bus": {"6": 20}, "energy_multiplier_by_bus": {"6": 1.08}},
        "explicit": "Route block 6, bus 6, is partially closed. Service delay is 20 minutes and energy consumption increases 8 percent.",
        "indirect": "Construction narrows route 6. Bus 6 may need approximately 20 minutes longer and around 8% additional energy.",
        "operational": "Dispatch SVC-02: construction restriction on block 6. Carry +20 min for unit 6 and plan at 1.08x normal traction use; end time not confirmed.",
    },
    {
        "scenario_id": "svc_route2_substitution",
        "event_id": "SVC-03",
        "source_type": "service_alert",
        "event_type": "route_energy_change",
        "buses": [2],
        "chargers": [],
        "updates": {"delay_minutes_by_bus": {"2": 15}, "energy_multiplier_by_bus": {"2": 1.12}},
        "explicit": "Bus 2 has a route substitution. Delay is 15 minutes and energy consumption increases 12 percent.",
        "indirect": "A substitute alignment applies to bus 2; allow about 15 extra minutes and roughly 12% more route energy.",
        "operational": "Control note SVC-03: substitute alignment assigned to unit 2. Use a +15 min cycle allowance and 1.12x route energy until released.",
    },
    {
        "scenario_id": "chg_2_fault",
        "event_id": "CHG-01",
        "source_type": "ocpp",
        "event_type": "charger_fault",
        "buses": [],
        "chargers": [2],
        "updates": {"unavailable_chargers": [2]},
        "explicit": "Charger 2 is faulted and unavailable. Technician timing is not confirmed.",
        "indirect": "OCPP status for charger 2 is Faulted; do not assign charging until maintenance clears it.",
        "operational": "Maintenance CHG-01: EVSE-02 locked out after an isolation alarm. ETA pending; exclude this unit from allocation.",
    },
    {
        "scenario_id": "chg_5_derating",
        "event_id": "CHG-02",
        "source_type": "ocpp",
        "event_type": "charger_derating",
        "buses": [],
        "chargers": [5],
        "updates": {"charger_power_kw": {"5": 75.0}},
        "explicit": "Charger 5 remains in service but is derated to 75 kW. Repair timing is uncertain.",
        "indirect": "Thermal alarms limit charger 5; cap the connector at 75 kW pending inspection.",
        "operational": "Maintenance CHG-02: thermal protection remains active on EVSE-05. Temporary output ceiling 75 kW; inspection window uncertain.",
    },
    {
        "scenario_id": "chg_7_fault",
        "event_id": "CHG-03",
        "source_type": "ocpp",
        "event_type": "charger_fault",
        "buses": [],
        "chargers": [7],
        "updates": {"unavailable_chargers": [7]},
        "explicit": "Charger 7 is offline and unavailable because of an isolation fault.",
        "indirect": "The protection relay has removed charger 7 from service; assignments must avoid it.",
        "operational": "Maintenance CHG-03: protection relay removed EVSE-07 from service. Keep the unit out of charger assignments pending clearance.",
    },
    {
        "scenario_id": "combined_bus3_charger3",
        "event_id": "COM-01",
        "source_type": "combined",
        "event_type": "combined",
        "buses": [3],
        "chargers": [3],
        "updates": {"delay_minutes_by_bus": {"3": 30}, "energy_multiplier_by_bus": {"3": 1.12}, "unavailable_chargers": [3]},
        "explicit": "Bus 3 is delayed 30 minutes with energy consumption up 12 percent, while charger 3 is faulted and unavailable.",
        "indirect": "A detour affects bus 3 (about 30 extra minutes and 12% more energy); charger 3 is also out of service.",
        "operational": "Joint ticket COM-01: unit 3 is on a diversion (+30 min, 1.12x traction use); EVSE-03 is locked out under the same incident.",
    },
    {
        "scenario_id": "combined_bus8_charger8",
        "event_id": "COM-02",
        "source_type": "combined",
        "event_type": "combined",
        "buses": [8],
        "chargers": [8],
        "updates": {"delay_minutes_by_bus": {"8": 20}, "charger_power_kw": {"8": 50.0}},
        "explicit": "Bus 8 is delayed 20 minutes and charger 8 is derated to 50 kW.",
        "indirect": "Allow roughly 20 extra minutes for bus 8; thermal limits cap charger 8 at 50 kW.",
        "operational": "Joint ticket COM-02: carry +20 min for unit 8. Thermal protection limits EVSE-08 to 50 kW until field release.",
    },
]

PHASES = [
    (10, "onset"),
    (11, "persistence"),
    (12, "severity_change"),
    (14, "recovery"),
    (15, "stable"),
]


def phase_text(base: str, phase: str, sequence: dict, truth: dict) -> str:
    if phase == "onset":
        return base
    if phase == "persistence":
        return "The condition continues unchanged. " + base
    if phase == "severity_change":
        details = []
        updates = truth["updates"]
        for bus, minutes in updates["delay_minutes_by_bus"].items():
            details.append(f"Bus {bus} is now delayed {minutes} minutes.")
        for bus, multiplier in updates["energy_multiplier_by_bus"].items():
            details.append(
                f"Bus {bus} energy consumption now increases {round((multiplier - 1) * 100)} percent."
            )
        for charger, power in updates["charger_power_kw"].items():
            details.append(f"Charger {charger} is now derated to {power:g} kW.")
        for charger in updates["unavailable_chargers"]:
            details.append(f"Charger {charger} is now fully unavailable.")
        return "The condition has worsened and requires re-evaluation. " + " ".join(details)
    if phase == "stable":
        return "Informational: the recovered state is stable and has no new operational impact."
    assets = []
    if sequence["buses"]:
        assets.append("buses " + ", ".join(map(str, sequence["buses"])))
    if sequence["chargers"]:
        assets.append("chargers " + ", ".join(map(str, sequence["chargers"])))
    return "Normal service is restored for " + " and ".join(assets) + "."


def operational_phase_text(base: str, phase: str, sequence: dict, truth: dict) -> str:
    """Generate realistic event-scoped updates without repeating all prior facts."""
    event_id = sequence["event_id"]
    if phase == "onset":
        return base
    if phase == "persistence":
        return (
            f"{event_id} field follow-up: no change from the last dispatch instruction; "
            "the same restrictions and planning assumptions remain in force."
        )
    if phase == "severity_change":
        details = []
        updates = truth["updates"]
        for minutes in updates["delay_minutes_by_bus"].values():
            details.append(f"replace the earlier timing allowance with {minutes} min")
        for multiplier in updates["energy_multiplier_by_bus"].values():
            details.append(f"use {multiplier:.2f}x traction energy for that block")
        for power in updates["charger_power_kw"].values():
            details.append(f"lower the same connector ceiling to {power:g} kW")
        if updates["unavailable_chargers"]:
            details.append("the same charging unit is now confirmed fully unavailable")
        return f"{event_id} dispatch correction: " + "; ".join(details) + ". Earlier values are superseded."
    if phase == "recovery":
        return (
            f"{event_id} close-out: the field restriction is cleared at the current report time. "
            "Return the affected block and charging unit, where applicable, to normal planning assumptions."
        )
    return f"{event_id} monitoring note: close-out remains valid; no new dispatch or charging restriction."


def canonical(sequence: dict, timestep: int, phase: str) -> dict:
    updates = json.loads(json.dumps(sequence["updates"]))
    if phase == "severity_change":
        updates["delay_minutes_by_bus"] = {
            bus: int(round(minutes * 1.4))
            for bus, minutes in updates.get("delay_minutes_by_bus", {}).items()
        }
        updates["energy_multiplier_by_bus"] = {
            bus: round(1 + (multiplier - 1) * 1.5, 3)
            for bus, multiplier in updates.get("energy_multiplier_by_bus", {}).items()
        }
        updates["charger_power_kw"] = {
            charger: round(power * 0.5, 3)
            for charger, power in updates.get("charger_power_kw", {}).items()
        }
    if phase in {"recovery", "stable"}:
        updates = {
            "delay_minutes_by_bus": {str(bus): 0 for bus in sequence["buses"]},
            "energy_multiplier_by_bus": {str(bus): 1.0 for bus in sequence["buses"] if "energy_multiplier_by_bus" in sequence["updates"]},
            "charger_power_kw": {str(charger): 200.0 for charger in sequence["chargers"]},
            "unavailable_chargers": [],
        }
    return {
        "event_id": sequence["event_id"],
        "source_type": sequence["source_type"],
        "event_type": sequence["event_type"],
        "phase": phase,
        "affected_buses": sequence["buses"],
        "affected_chargers": sequence["chargers"],
        "effective_timestep": timestep,
        "expected_end_timestep": 14 if phase not in {"recovery", "stable"} else None,
        "uncertainty": phase not in {"recovery", "stable"},
        "material": phase != "stable",
        "updates": {
            "delay_minutes_by_bus": updates.get("delay_minutes_by_bus", {}),
            "energy_multiplier_by_bus": updates.get("energy_multiplier_by_bus", {}),
            "charger_power_kw": updates.get("charger_power_kw", {}),
            "unavailable_chargers": updates.get("unavailable_chargers", []),
        },
        "evidence": ["canonical_scenario_v2", sequence["scenario_id"]],
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    notices = []
    canonical_rows = []
    for sequence in SEQUENCES:
        for timestep, phase in PHASES:
            truth = canonical(sequence, timestep, phase)
            canonical_rows.append({"scenario_id": sequence["scenario_id"], **truth})
            for variant in ("explicit", "indirect", "operational"):
                text = (
                    operational_phase_text(sequence[variant], phase, sequence, truth)
                    if variant == "operational"
                    else phase_text(sequence[variant], phase, sequence, truth)
                )
                notices.append(
                    {
                        "notice_id": f"{sequence['event_id']}-{phase}-{variant}",
                        "scenario_id": sequence["scenario_id"],
                        "event_id": sequence["event_id"],
                        "source_type": sequence["source_type"],
                        "wording_variant": variant,
                        "report_timestep": timestep,
                        "text": text,
                        "canonical": truth,
                    }
                )
    scenario_path = OUTPUT / "trigger_scenarios.json"
    notice_path = OUTPUT / "trigger_notices.json"
    csv_path = OUTPUT / "trigger_notices.csv"
    scenario_path.write_text(
        json.dumps(canonical_rows, indent=2), encoding="utf-8"
    )
    notice_path.write_text(
        json.dumps(notices, indent=2), encoding="utf-8"
    )
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "notice_id", "scenario_id", "event_id", "source_type", "wording_variant",
            "report_timestep", "text", "canonical",
        ])
        writer.writeheader()
        for row in notices:
            writer.writerow({**row, "canonical": json.dumps(row["canonical"], separators=(",", ":"))})
    manifest = {
        "dataset_version": DATASET_VERSION,
        "generation": "deterministic; scenario truth defined before wording variants",
        "scenario_count": len(SEQUENCES),
        "lifecycle_phases": [phase for _, phase in PHASES],
        "wording_variants": ["explicit", "indirect", "operational"],
        "decision_count": len(notices),
        "method_input_excludes": ["canonical", "scenario_id", "wording_variant"],
        "sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (scenario_path, notice_path, csv_path)
        },
    }
    (OUTPUT / "trigger_dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

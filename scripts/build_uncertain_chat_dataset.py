from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_workflow.uncertainty import select_operational_value


OUTPUT = ROOT / "inputs" / "revision"


def write_lf(path: Path, text: str) -> None:
    """Write text with LF endings on every platform.

    The dataset hashes recorded below are plain file digests, so a CRLF
    translation on Windows would silently produce a manifest that no other
    platform can verify.
    """

    path.write_text(text, encoding="utf-8", newline="\n")
DATASET_VERSION = "trigger_uncertainty_chat_v3"
VARIANTS = ("clean", "single_message", "driver_chat", "uncertain_chat")
PHASES = (
    (9, "warning"),
    (10, "onset"),
    (11, "persistence"),
    (12, "severity_change"),
    (14, "recovery"),
    (15, "stable"),
)


SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario_id": "v3_route4_detour",
        "split": "development",
        "event_id": "OPS-101",
        "base_source_type": "service_alert",
        "event_type": "service_delay",
        "buses": [4],
        "chargers": [],
        "cause": "water-main detour on route block 4",
        "primary_role": "Driver 4",
        "params": [
            ("delay_minutes", 4, "minutes", (20, 30), (30, 40)),
            ("energy_multiplier", 4, "multiplier", (1.08, 1.12), (1.12, 1.18)),
        ],
    },
    {
        "scenario_id": "v3_route6_closure",
        "split": "test",
        "event_id": "OPS-102",
        "base_source_type": "service_alert",
        "event_type": "service_delay",
        "buses": [6],
        "chargers": [],
        "cause": "lane closure on route block 6",
        "primary_role": "Driver 6",
        "params": [
            ("delay_minutes", 6, "minutes", (15, 25), (25, 35)),
            ("energy_multiplier", 6, "multiplier", (1.05, 1.10), (1.10, 1.16)),
        ],
    },
    {
        "scenario_id": "v3_bus2_energy_sensor",
        "split": "test",
        "event_id": "OPS-103",
        "base_source_type": "service_alert",
        "event_type": "route_energy_change",
        "buses": [2],
        "chargers": [],
        "cause": "substitute alignment and fluctuating energy dashboard for unit 2",
        "primary_role": "Driver 2",
        "params": [
            ("energy_multiplier", 2, "multiplier", (1.08, 1.15), (1.14, 1.22)),
        ],
    },
    {
        "scenario_id": "v3_charger2_isolation",
        "split": "development",
        "event_id": "OPS-104",
        "base_source_type": "ocpp",
        "event_type": "charger_fault",
        "buses": [],
        "chargers": [2],
        "cause": "intermittent isolation alarm on EVSE-02",
        "primary_role": "Maintenance",
        "params": [
            ("charger_unavailability_probability", 2, "probability", (0.80, 1.0), (1.0, 1.0)),
        ],
    },
    {
        "scenario_id": "v3_charger5_thermal",
        "split": "development",
        "event_id": "OPS-105",
        "base_source_type": "ocpp",
        "event_type": "charger_derating",
        "buses": [],
        "chargers": [5],
        "cause": "thermal protection on EVSE-05",
        "primary_role": "Maintenance",
        "params": [
            ("charger_power_kw", 5, "kw", (60, 90), (35, 55)),
        ],
    },
    {
        "scenario_id": "v3_charger7_relay",
        "split": "test",
        "event_id": "OPS-106",
        "base_source_type": "ocpp",
        "event_type": "charger_fault",
        "buses": [],
        "chargers": [7],
        "cause": "protection-relay indication on EVSE-07 that may be a sensor fault",
        "primary_role": "Maintenance",
        "params": [
            ("charger_unavailability_probability", 7, "probability", (0.75, 1.0), (1.0, 1.0)),
        ],
    },
    {
        "scenario_id": "v3_bus3_charger3",
        "split": "development",
        "event_id": "OPS-107",
        "base_source_type": "combined",
        "event_type": "combined",
        "buses": [3],
        "chargers": [3],
        "cause": "route diversion for unit 3 and a related EVSE-03 isolation warning",
        "primary_role": "Driver 3",
        "params": [
            ("delay_minutes", 3, "minutes", (20, 30), (30, 45)),
            ("energy_multiplier", 3, "multiplier", (1.07, 1.13), (1.12, 1.20)),
            ("charger_unavailability_probability", 3, "probability", (0.70, 1.0), (1.0, 1.0)),
        ],
    },
    {
        "scenario_id": "v3_bus8_charger8",
        "split": "test",
        "event_id": "OPS-108",
        "base_source_type": "combined",
        "event_type": "combined",
        "buses": [8],
        "chargers": [8],
        "cause": "late return for unit 8 and thermal limiting on EVSE-08",
        "primary_role": "Driver 8",
        "params": [
            ("delay_minutes", 8, "minutes", (15, 25), (25, 35)),
            ("charger_power_kw", 8, "kw", (45, 70), (20, 40)),
        ],
    },
]


PHASE_POLICY = {
    "warning": ("request_confirmation", 0.45, True),
    "onset": ("optimize", 0.72, True),
    "persistence": ("wait", 0.60, True),
    "severity_change": ("optimize", 0.88, False),
    "recovery": ("optimize", 0.96, False),
    "stable": ("wait", 1.0, False),
}


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _parameter_label(parameter: str, asset_id: int, lower: float, upper: float) -> str:
    bounds = f"{_format_number(lower)}-{_format_number(upper)}"
    if parameter == "delay_minutes":
        return f"unit {asset_id} delay {bounds} min"
    if parameter == "energy_multiplier":
        return f"unit {asset_id} traction energy {bounds}x normal"
    if parameter == "charger_power_kw":
        return f"EVSE-{asset_id:02d} available power {bounds} kW"
    return f"EVSE-{asset_id:02d} unavailability probability {bounds}"


def _range_set(scenario: dict[str, Any], phase: str) -> list[tuple[str, int, str, float, float]]:
    range_index = 4 if phase == "severity_change" else 3
    result = []
    for parameter, asset_id, unit, onset, severity in scenario["params"]:
        lower, upper = severity if range_index == 4 else onset
        result.append((parameter, asset_id, unit, float(lower), float(upper)))
    return result


def _estimates(scenario: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    recommendation, _, _ = PHASE_POLICY[phase]
    if phase == "stable":
        return []
    if phase == "recovery":
        result = []
        for parameter, asset_id, unit, _, _ in scenario["params"]:
            nominal = {
                "delay_minutes": 0.0,
                "energy_multiplier": 1.0,
                "charger_power_kw": 200.0,
                "charger_unavailability_probability": 0.0,
            }[parameter]
            result.append(
                {
                    "parameter": parameter,
                    "asset_id": asset_id,
                    "lower_bound": nominal,
                    "upper_bound": nominal,
                    "selected_value": nominal,
                    "unit": unit,
                    "selection_policy": "restored_nominal",
                }
            )
        return result
    ranges = _range_set(scenario, phase)
    result = []
    for parameter, asset_id, unit, lower, upper in ranges:
        selection_recommendation = "optimize" if phase == "persistence" else recommendation
        selected, policy = select_operational_value(
            parameter, lower, upper, selection_recommendation
        )
        result.append(
            {
                "parameter": parameter,
                "asset_id": asset_id,
                "lower_bound": lower,
                "upper_bound": upper,
                "selected_value": selected,
                "unit": unit,
                "selection_policy": policy,
            }
        )
    return result


def _updates(scenario: dict[str, Any], phase: str, estimates: list[dict[str, Any]]) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "delay_minutes_by_bus": {},
        "energy_multiplier_by_bus": {},
        "charger_power_kw": {},
        "unavailable_chargers": [],
    }
    if phase in {"warning", "stable"}:
        return updates
    for estimate in estimates:
        parameter = estimate["parameter"]
        asset = str(estimate["asset_id"])
        selected = estimate["selected_value"]
        if selected is None:
            continue
        if parameter == "delay_minutes":
            updates["delay_minutes_by_bus"][asset] = int(round(selected))
        elif parameter == "energy_multiplier":
            updates["energy_multiplier_by_bus"][asset] = selected
        elif parameter == "charger_power_kw":
            updates["charger_power_kw"][asset] = selected
        elif parameter == "charger_unavailability_probability" and selected >= 1.0:
            updates["unavailable_chargers"].append(int(asset))
    if phase == "recovery":
        updates["unavailable_chargers"] = []
    return updates


def _conflicts(phase: str) -> list[str]:
    if phase == "warning":
        return ["field_report_vs_initial_telemetry"]
    if phase == "onset":
        return ["field_estimate_vs_fluctuating_dashboard"]
    if phase == "persistence":
        return ["driver_or_maintenance_report_vs_delayed_telemetry"]
    return []


def canonical(scenario: dict[str, Any], variant: str, timestep: int, phase: str) -> dict[str, Any]:
    recommendation, confidence, provisional = PHASE_POLICY[phase]
    estimates = _estimates(scenario, phase)
    source_type = scenario["base_source_type"] if variant == "clean" else "driver_chat"
    affected_buses = [] if phase == "stable" else scenario["buses"]
    affected_chargers = [] if phase == "stable" else scenario["chargers"]
    return {
        "event_id": scenario["event_id"],
        "source_type": source_type,
        "event_type": "informational" if phase == "stable" else scenario["event_type"],
        "phase": phase,
        "affected_buses": affected_buses,
        "affected_chargers": affected_chargers,
        "effective_timestep": timestep,
        "expected_end_timestep": None,
        "uncertainty": phase in {"warning", "onset", "persistence"},
        "uncertainty_details": {
            "confidence_level": confidence,
            "provisional": provisional,
            "conflicting_evidence": _conflicts(phase),
            "estimates": estimates,
            "recommended_action": recommendation,
            "rationale": {
                "warning": "Conditional warning lacks confirmation; request confirmation without changing optimizer inputs.",
                "onset": "Material event is confirmed; apply the frozen conservative parameter policy provisionally.",
                "persistence": "No selected operational value has changed; wait and avoid duplicate optimization.",
                "severity_change": "A verified correction supersedes earlier values and requires reoptimization.",
                "recovery": "Confirmed recovery restores nominal optimizer assumptions.",
                "stable": "Post-recovery chatter contains no new operational restriction.",
            }[phase],
        },
        "material": phase != "stable",
        "updates": _updates(scenario, phase, estimates),
        "evidence": ["canonical_uncertainty_policy_v1", DATASET_VERSION],
    }


def _range_text(scenario: dict[str, Any], phase: str) -> str:
    if phase in {"recovery", "stable"}:
        return ""
    return "; ".join(
        _parameter_label(parameter, asset, lower, upper)
        for parameter, asset, _, lower, upper in _range_set(scenario, phase)
    )


def _instruction(phase: str) -> str:
    return {
        "warning": "Request confirmation and do not replan yet.",
        "onset": "Confirmed material: apply the conservative bound provisionally and replan now.",
        "persistence": "No selected value changed; keep the current plan and wait.",
        "severity_change": "Verified correction: supersede the earlier values and replan now.",
        "recovery": "Recovery confirmed: restore nominal assumptions and replan now.",
        "stable": "No new operational restriction; no replan is needed.",
    }[phase]


_NUMBER_WORDS = {
    0: "zero",
    5: "five",
    8: "eight",
    10: "ten",
    12: "twelve",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    20: "twenty",
    22: "twenty-two",
    25: "twenty-five",
    30: "thirty",
    35: "thirty-five",
    40: "forty",
    45: "forty-five",
    55: "fifty-five",
    60: "sixty",
    65: "sixty-five",
    70: "seventy",
    75: "seventy-five",
    80: "eighty",
    90: "ninety",
    100: "one hundred",
}


def _words(value: float) -> str:
    rounded = int(round(value))
    return _NUMBER_WORDS.get(rounded, str(rounded))


def _conversational_ranges(scenario: dict[str, Any], phase: str) -> str:
    phrases = []
    for parameter, asset, _, lower, upper in _range_set(scenario, phase):
        if parameter == "delay_minutes":
            phrases.append(
                f"unit {asset} is somewhere between {_words(lower)} and {_words(upper)} minutes late"
            )
        elif parameter == "energy_multiplier":
            low_pct = (lower - 1) * 100
            high_pct = (upper - 1) * 100
            phrases.append(
                f"its battery draw looks {_words(low_pct)} to {_words(high_pct)} percent above normal"
            )
        elif parameter == "charger_power_kw":
            phrases.append(
                f"that connector can deliver only {_words(lower)} to {_words(upper)} kilowatts"
            )
        else:
            phrases.append(
                f"the relay-fault likelihood runs from {_words(lower * 100)} to {_words(upper * 100)} percent"
            )
    return "; ".join(phrases)


def _heldout_chat_text(scenario: dict[str, Any], variant: str, phase: str) -> str:
    """A lexical family not used by the regex-oriented development templates."""

    event = scenario["event_id"]
    role = scenario["primary_role"]
    cause = scenario["cause"]
    current = _conversational_ranges(scenario, phase)
    confidence_pct = int(round(PHASE_POLICY[phase][1] * 100))
    if phase == "warning":
        lines = [
            f"[{event}] {role}: Heads-up only—if the {cause} is real, {current}.",
            "Telemetry desk: First screen is lagging and I cannot corroborate that yet.",
            f"Dispatcher: Confidence is about {confidence_pct} percent. Could someone verify before we touch the schedule?",
        ]
    elif phase == "onset":
        lines = [
            f"[{event}] {role}: Field check backs it up: {current}.",
            "Telemetry desk: My feed is still bouncing, so the field range and screen do not agree exactly.",
            f"Dispatcher: Use the cautious end of those figures for now; confidence {confidence_pct} percent. Treat it as live.",
        ]
    elif phase == "persistence":
        lines = [
            f"[{event}] {role}: Same issue as the last exchange; {current}.",
            "Telemetry desk: The delayed screen still shows something lower.",
            f"Dispatcher: Confidence remains {confidence_pct} percent. Leave what we already loaded alone for now.",
        ]
    elif phase == "severity_change":
        earlier = _conversational_ranges(scenario, "onset")
        lines = [
            f"[{event}] Telemetry desk: The old board still repeats: {earlier}.",
            f"{role}: Fresh field check says {current}.",
            f"Dispatcher: Scratch the previous figures; the fresh field range is authoritative at {confidence_pct} percent confidence. Publish the replacement.",
        ]
    elif phase == "recovery":
        lines = [
            f"[{event}] {role}: Field gives the all-clear on the same equipment and route.",
            "Telemetry desk: Normal readings are now corroborated.",
            f"Dispatcher: Confidence {confidence_pct} percent. Put the original planning values back into service.",
        ]
    else:
        lines = [
            f"[{event}] Depot chat: FYI after close-out, everything operational is quiet.",
            "Driver lounge: The phone charger by the coffee machine is still broken.",
            "Dispatcher: That is not a fleet asset and there is nothing for fleet dispatch to change.",
        ]
    if variant == "uncertain_chat":
        lines.insert(
            1,
            "Driver lounge: Anyone swapping the afternoon break? This is unrelated to the incident.",
        )
        lines.insert(
            -1,
            "Maintenance: One stale dashboard panel may contradict the field thread; follow the latest authorized dispatch line.",
        )
    return "\n".join(lines)


def text_for(scenario: dict[str, Any], variant: str, phase: str) -> str:
    event = scenario["event_id"]
    role = scenario["primary_role"]
    cause = scenario["cause"]
    ranges = _range_text(scenario, phase)
    confidence = PHASE_POLICY[phase][1]
    instruction = _instruction(phase)
    if scenario["split"] == "test" and variant in {"driver_chat", "uncertain_chat"}:
        return _heldout_chat_text(scenario, variant, phase)
    if phase == "warning":
        phase_fact = f"A conditional warning was raised for {cause}. {ranges}."
    elif phase == "onset":
        phase_fact = f"The event is now confirmed for {cause}. {ranges}."
    elif phase == "persistence":
        phase_fact = f"The same event remains active. Field reports persist but delayed telemetry still disagrees. {ranges}."
    elif phase == "severity_change":
        phase_fact = f"A verified correction supersedes the previous estimate for {cause}. {ranges}."
    elif phase == "recovery":
        phase_fact = f"The affected buses and chargers for {cause} are confirmed back to normal."
    else:
        phase_fact = "Post-recovery monitoring is normal; a message about a break-room charger is unrelated to fleet operations."

    if variant == "clean":
        return (
            f"Operations notice {event}. {phase_fact} Confidence {confidence:.2f}. "
            f"Field information and initial telemetry conflict where stated. {instruction}"
        )
    if variant == "single_message":
        return (
            f"{role} re {event}: {phase_fact} dashboard is a bit flaky; conf {confidence:.2f}. "
            f"{instruction}"
        )
    if variant == "driver_chat":
        return "\n".join(
            [
                f"[{event}] {role}: {phase_fact}",
                f"Telemetry desk: The dashboard is delayed and does not fully match the field estimate. Confidence {confidence:.2f}.",
                f"Dispatcher: {instruction}",
            ]
        )
    return "\n".join(
        [
            f"[{event}] {role}: {phase_fact}",
            "Depot chat: coffee machine charger is acting up again (not a fleet asset).",
            "Telemetry desk: My first screen disagrees with the field report; the feed may be stale.",
            f"Maintenance: Treat the operational confidence as {confidence:.2f}; earlier estimates may be superseded.",
            f"Dispatcher: {instruction}",
        ]
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    notices: list[dict[str, Any]] = []
    canonical_rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for variant in VARIANTS:
            for timestep, phase in PHASES:
                truth = canonical(scenario, variant, timestep, phase)
                canonical_rows.append(
                    {
                        "scenario_id": scenario["scenario_id"],
                        "benchmark_split": scenario["split"],
                        "wording_variant": variant,
                        **truth,
                    }
                )
                notices.append(
                    {
                        "notice_id": f"{scenario['event_id']}-{phase}-{variant}",
                        "scenario_id": scenario["scenario_id"],
                        "event_id": scenario["event_id"],
                        "source_type": truth["source_type"],
                        "wording_variant": variant,
                        "benchmark_split": scenario["split"],
                        "uncertainty_case": phase,
                        "report_timestep": timestep,
                        "text": text_for(scenario, variant, phase),
                        "canonical": truth,
                    }
                )

    scenario_path = OUTPUT / "trigger_scenarios_v3.json"
    notice_path = OUTPUT / "trigger_notices_v3.json"
    csv_path = OUTPUT / "trigger_notices_v3.csv"
    split_path = OUTPUT / "trigger_split_v3.json"
    manifest_path = OUTPUT / "trigger_dataset_manifest_v3.json"
    mapping_path = OUTPUT / "uncertainty_chat_mapping_v3.md"
    write_lf(scenario_path, json.dumps(canonical_rows, indent=2))
    write_lf(notice_path, json.dumps(notices, indent=2))
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            lineterminator="\n",
            fieldnames=[
                "notice_id",
                "scenario_id",
                "event_id",
                "source_type",
                "wording_variant",
                "benchmark_split",
                "uncertainty_case",
                "report_timestep",
                "text",
                "canonical",
            ],
        )
        writer.writeheader()
        for row in notices:
            writer.writerow(
                {**row, "canonical": json.dumps(row["canonical"], separators=(",", ":"))}
            )
    split = {
        "split_unit": "scenario lifecycle; variants from one physical event never cross splits",
        "development": [item["scenario_id"] for item in SCENARIOS if item["split"] == "development"],
        "test": [item["scenario_id"] for item in SCENARIOS if item["split"] == "test"],
    }
    write_lf(split_path, json.dumps(split, indent=2))
    manifest = {
        "dataset_version": DATASET_VERSION,
        "generation": "deterministic synthetic conversations; no personal data or real driver messages",
        "scenario_count": len(SCENARIOS),
        "lifecycle_phases": [phase for _, phase in PHASES],
        "wording_variants": list(VARIANTS),
        "decision_count": len(notices),
        "development_decisions": sum(item["benchmark_split"] == "development" for item in notices),
        "test_decisions": sum(item["benchmark_split"] == "test" for item in notices),
        "split_policy": "scenario-clustered 50/50 split; test scenarios frozen before evaluation",
        "freeze_sequence": "uncertainty policy and stateful rule grammar fixed before held-out Agent evaluation",
        "uncertainty_policy": {
            "delay_minutes": "conservative upper bound",
            "energy_multiplier": "conservative upper bound",
            "charger_power_kw": "conservative lower bound",
            "charger_fault": "unavailable only after confirmation",
            "wait_or_request_confirmation": "no optimizer update",
        },
        "method_input_excludes": [
            "canonical",
            "scenario_id",
            "wording_variant",
            "benchmark_split",
            "uncertainty_case",
        ],
        "sha256": {
            path.name: sha256(path)
            for path in (
                scenario_path,
                notice_path,
                csv_path,
                split_path,
                mapping_path,
            )
            if path.exists()
        },
    }
    write_lf(manifest_path, json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

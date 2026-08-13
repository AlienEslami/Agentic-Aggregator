from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "inputs" / "revision" / "advance_warning_notices_v1.json"
PHYSICAL_OUTPUT = (
    ROOT / "inputs" / "revision" / "advance_warning_physical_events_v1.json"
)
MANIFEST = ROOT / "inputs" / "revision" / "advance_warning_manifest_v1.json"


def _uncertainty(
    *,
    recommendation: str,
    confidence: float,
    provisional: bool,
    estimates: list[dict[str, Any]] | None = None,
    conflicts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "confidence_level": confidence,
        "provisional": provisional,
        "conflicting_evidence": conflicts or [],
        "estimates": estimates or [],
        "recommended_action": recommendation,
        "rationale": (
            "Use the confirmed advance information now."
            if recommendation == "optimize"
            else "Wait for operational confirmation."
            if recommendation == "request_confirmation"
            else "The active plan already contains this unchanged information."
        ),
    }


def _estimate(
    parameter: str,
    asset_id: int,
    lower: float,
    upper: float,
    selected: float | None,
    unit: str,
) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "asset_id": asset_id,
        "lower_bound": lower,
        "upper_bound": upper,
        "selected_value": selected,
        "unit": unit,
        "selection_policy": (
            "no_update_pending_confirmation"
            if selected is None
            else "conservative_upper"
            if parameter in {"delay_minutes", "return_delay_minutes", "energy_multiplier"}
            else "conservative_lower"
        ),
    }


def _canonical(
    *,
    event_id: str,
    source_type: str,
    event_type: str,
    phase: str,
    buses: list[int],
    chargers: list[int],
    effective: int,
    end: int,
    recommendation: str,
    updates: dict[str, Any],
    estimates: list[dict[str, Any]] | None = None,
    confidence: float = 1.0,
    provisional: bool = False,
    material: bool = True,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "source_type": source_type,
        "event_type": event_type,
        "phase": phase,
        "affected_buses": buses,
        "affected_chargers": chargers,
        "effective_timestep": effective,
        "expected_end_timestep": end,
        "uncertainty": bool(estimates),
        "uncertainty_details": _uncertainty(
            recommendation=recommendation,
            confidence=confidence,
            provisional=provisional,
            estimates=estimates,
        ),
        "material": material,
        "updates": {
            "delay_minutes_by_bus": {},
            "return_delay_minutes_by_bus": {},
            "energy_multiplier_by_bus": {},
            "charger_power_kw": {},
            "unavailable_chargers": [],
            **updates,
        },
        "evidence": ["advance_warning_benchmark_v1"],
    }


def _notice(
    *,
    notice_id: str,
    scenario_id: str,
    event_id: str,
    report: int,
    source_type: str,
    text: str,
    canonical: dict[str, Any],
) -> dict[str, Any]:
    return {
        "notice_id": notice_id,
        "scenario_id": scenario_id,
        "event_id": event_id,
        "source_type": source_type,
        "wording_variant": "uncertain_chat",
        "report_timestep": report,
        "text": text,
        "benchmark_split": "closed_loop_advance_warning",
        "uncertainty_case": "range_fragmentation_and_coreference",
        "canonical": canonical,
    }


def build() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    notices: list[dict[str, Any]] = []

    route_estimate_pending = [
        _estimate("return_delay_minutes", 6, 120, 150, None, "minutes")
    ]
    route_estimate = [
        _estimate("return_delay_minutes", 6, 120, 150, 150, "minutes")
    ]
    notices.extend(
        [
            _notice(
                notice_id="AW-ROUTE6-WARN",
                scenario_id="aw_route6_late_return",
                event_id="AW-ROUTE6",
                report=5,
                source_type="driver_chat",
                text=(
                    "Conditional warning — Driver 6: the evening roadworks note looks worse than the board. "
                    "Bus 6 return may be 120-150 minutes late; dispatch has not confirmed "
                    "the 21:30-to-end-of-day control window. Request confirmation; do not replan yet."
                ),
                canonical=_canonical(
                    event_id="AW-ROUTE6",
                    source_type="driver_chat",
                    event_type="service_delay",
                    phase="warning",
                    buses=[6],
                    chargers=[],
                    effective=44,
                    end=48,
                    recommendation="request_confirmation",
                    updates={},
                    estimates=route_estimate_pending,
                    confidence=0.58,
                    provisional=True,
                ),
            ),
            _notice(
                notice_id="AW-ROUTE6-CONFIRM",
                scenario_id="aw_route6_late_return",
                event_id="AW-ROUTE6",
                report=6,
                source_type="driver_chat",
                text=(
                    "Dispatcher: confirmed for Bus 6. Its return is expected 120 to 150 minutes "
                    "late, with the operational control window 21:30 to 24:00. Use the conservative upper bound "
                    "and optimize now; departure remains at its scheduled time."
                ),
                canonical=_canonical(
                    event_id="AW-ROUTE6",
                    source_type="driver_chat",
                    event_type="service_delay",
                    phase="onset",
                    buses=[6],
                    chargers=[],
                    effective=44,
                    end=48,
                    recommendation="optimize",
                    updates={"return_delay_minutes_by_bus": {6: 150}},
                    estimates=route_estimate,
                    confidence=0.92,
                    provisional=False,
                ),
            ),
            _notice(
                notice_id="AW-ROUTE6-HOLD",
                scenario_id="aw_route6_late_return",
                event_id="AW-ROUTE6",
                report=7,
                source_type="driver_chat",
                text="Dispatch: same Bus 6 late-return event remains active and unchanged. Keep the current plan; no replan.",
                canonical=_canonical(
                    event_id="AW-ROUTE6",
                    source_type="driver_chat",
                    event_type="service_delay",
                    phase="persistence",
                    buses=[6],
                    chargers=[],
                    effective=44,
                    end=48,
                    recommendation="wait",
                    updates={"return_delay_minutes_by_bus": {6: 150}},
                    estimates=route_estimate,
                    confidence=0.92,
                ),
            ),
        ]
    )

    charger_ids = [5, 6, 7, 8]
    notices.extend(
        [
            _notice(
                notice_id="AW-BANK-WARN",
                scenario_id="aw_charger_bank_shutdown",
                event_id="AW-BANK",
                report=4,
                source_type="combined",
                text=(
                    "Conditional warning — Maintenance chat — Lee: south bank may need isolating for the 03:30-05:00 "
                    "busbar job. Ops: which assets? Lee: the map labels that row EVSE 5, EVSE 6, EVSE 7 and EVSE 8, "
                    "not the north bank. Lockout is pending; request confirmation and do not replan yet."
                ),
                canonical=_canonical(
                    event_id="AW-BANK",
                    source_type="combined",
                    event_type="charger_fault",
                    phase="warning",
                    buses=[],
                    chargers=charger_ids,
                    effective=8,
                    end=10,
                    recommendation="request_confirmation",
                    updates={},
                    confidence=0.62,
                    provisional=True,
                ),
            ),
            _notice(
                notice_id="AW-BANK-CONFIRM",
                scenario_id="aw_charger_bank_shutdown",
                event_id="AW-BANK",
                report=5,
                source_type="combined",
                text=(
                    "Supervisor: confirmed—go ahead with that same south-row isolation for the "
                    "stated 03:30 to 05:00 window. Optimize now so charging can be moved ahead of it."
                ),
                canonical=_canonical(
                    event_id="AW-BANK",
                    source_type="combined",
                    event_type="charger_fault",
                    phase="onset",
                    buses=[],
                    chargers=charger_ids,
                    effective=8,
                    end=10,
                    recommendation="optimize",
                    updates={"unavailable_chargers": charger_ids},
                    confidence=0.98,
                ),
            ),
            _notice(
                notice_id="AW-BANK-HOLD",
                scenario_id="aw_charger_bank_shutdown",
                event_id="AW-BANK",
                report=6,
                source_type="combined",
                text="Maintenance: the same lockout remains active and unchanged. Keep the current plan; no replan.",
                canonical=_canonical(
                    event_id="AW-BANK",
                    source_type="combined",
                    event_type="charger_fault",
                    phase="persistence",
                    buses=[],
                    chargers=charger_ids,
                    effective=8,
                    end=10,
                    recommendation="wait",
                    updates={"unavailable_chargers": charger_ids},
                    confidence=0.98,
                ),
            ),
            _notice(
                notice_id="AW-BANK-RECOVER",
                scenario_id="aw_charger_bank_shutdown",
                event_id="AW-BANK",
                report=11,
                source_type="ocpp",
                text="Recovery confirmed: the south-bank lockout is cleared and EVSE 5, EVSE 6, EVSE 7 and EVSE 8 are restored to normal service.",
                canonical=_canonical(
                    event_id="AW-BANK",
                    source_type="ocpp",
                    event_type="charger_fault",
                    phase="recovery",
                    buses=[],
                    chargers=charger_ids,
                    effective=11,
                    end=11,
                    recommendation="optimize",
                    updates={},
                    confidence=1.0,
                ),
            ),
        ]
    )

    combined_chargers_initial = [3, 4, 5, 6, 7, 8]
    combined_chargers_corrected = [3, 4, 5]
    combined_estimate_pending = [
        _estimate("return_delay_minutes", 8, 120, 150, None, "minutes")
    ]
    combined_estimate = [
        _estimate("return_delay_minutes", 8, 120, 150, 150, "minutes")
    ]
    notices.extend(
        [
            _notice(
                notice_id="AW-COMBINED-WARN",
                scenario_id="aw_combined_evening",
                event_id="AW-COMBINED",
                report=29,
                source_type="combined",
                text=(
                    "Conditional warning — 14:00 thread — Driver of Bus 8: closure desk says my return could be 120-150 minutes late. "
                    "Maintenance: evening switching may also isolate the six-connector service row, "
                    "EVSE 3, EVSE 4, EVSE 5, EVSE 6, EVSE 7 and EVSE 8, from 20:00-24:00. Dispatcher: both are "
                    "still conditional; request confirmation and do not replan yet."
                ),
                canonical=_canonical(
                    event_id="AW-COMBINED",
                    source_type="combined",
                    event_type="combined",
                    phase="warning",
                    buses=[8],
                    chargers=combined_chargers_initial,
                    effective=41,
                    end=48,
                    recommendation="request_confirmation",
                    updates={},
                    estimates=combined_estimate_pending,
                    confidence=0.55,
                    provisional=True,
                ),
            ),
            _notice(
                notice_id="AW-COMBINED-CONFIRM",
                scenario_id="aw_combined_evening",
                event_id="AW-COMBINED",
                report=30,
                source_type="combined",
                text=(
                    "Dispatcher: combined event confirmed. Bus 8 return is expected 120 to 150 minutes "
                    "late, and that same six-connector row—EVSE 3, EVSE 4, EVSE 5, EVSE 6, EVSE 7 and EVSE 8—will be unavailable 20:00 to 24:00. "
                    "Use the upper return-delay bound and optimize now while lower-price preparation "
                    "and reassignment are still possible."
                ),
                canonical=_canonical(
                    event_id="AW-COMBINED",
                    source_type="combined",
                    event_type="combined",
                    phase="onset",
                    buses=[8],
                    chargers=combined_chargers_initial,
                    effective=41,
                    end=48,
                    recommendation="optimize",
                    updates={
                        "return_delay_minutes_by_bus": {8: 150},
                        "unavailable_chargers": combined_chargers_initial,
                    },
                    estimates=combined_estimate,
                    confidence=0.94,
                ),
            ),
            _notice(
                notice_id="AW-COMBINED-CORRECT",
                scenario_id="aw_combined_evening",
                event_id="AW-COMBINED",
                report=31,
                source_type="combined",
                text=(
                    "Dispatch correction—earlier values are superseded: EVSE 6, EVSE 7 and EVSE 8 remain available. "
                    "Only EVSE 3, EVSE 4 and EVSE 5 are locked out 20:00 to 24:00; "
                    "the Bus 8 return range is unchanged. Optimize for the corrected capacity."
                ),
                canonical=_canonical(
                    event_id="AW-COMBINED",
                    source_type="combined",
                    event_type="combined",
                    phase="severity_change",
                    buses=[8],
                    chargers=combined_chargers_corrected,
                    effective=41,
                    end=48,
                    recommendation="optimize",
                    updates={
                        "return_delay_minutes_by_bus": {8: 150},
                        "unavailable_chargers": combined_chargers_corrected,
                    },
                    estimates=combined_estimate,
                    confidence=0.94,
                ),
            ),
            _notice(
                notice_id="AW-COMBINED-HOLD",
                scenario_id="aw_combined_evening",
                event_id="AW-COMBINED",
                report=32,
                source_type="combined",
                text="Ops: the corrected combined evening event remains active and unchanged. No new dispatch; keep the current plan.",
                canonical=_canonical(
                    event_id="AW-COMBINED",
                    source_type="combined",
                    event_type="combined",
                    phase="persistence",
                    buses=[8],
                    chargers=combined_chargers_corrected,
                    effective=41,
                    end=48,
                    recommendation="wait",
                    updates={
                        "return_delay_minutes_by_bus": {8: 150},
                        "unavailable_chargers": combined_chargers_corrected,
                    },
                    estimates=combined_estimate,
                    confidence=0.97,
                ),
            ),
            _notice(
                notice_id="AW-COMBINED-RECOVER",
                scenario_id="aw_combined_evening",
                event_id="AW-COMBINED",
                report=48,
                source_type="combined",
                text="Recovery confirmed: the evening switching window has cleared and the late-return restriction is closed for the next planning horizon.",
                canonical=_canonical(
                    event_id="AW-COMBINED",
                    source_type="combined",
                    event_type="combined",
                    phase="recovery",
                    buses=[8],
                    chargers=combined_chargers_corrected,
                    effective=48,
                    end=48,
                    recommendation="optimize",
                    updates={},
                    confidence=1.0,
                ),
            ),
        ]
    )

    physical = [
        {
            "scenario_id": "aw_route6_late_return",
            "event_id": "AW-ROUTE6",
            "effective_timestep": 44,
            "end_timestep": 48,
            "sensor_detection_timestep": 44,
            "updates": {"return_delay_minutes_by_bus": {6: 150}},
        },
        {
            "scenario_id": "aw_charger_bank_shutdown",
            "event_id": "AW-BANK",
            "effective_timestep": 8,
            "end_timestep": 10,
            "sensor_detection_timestep": 8,
            "updates": {"unavailable_chargers": charger_ids},
        },
        {
            "scenario_id": "aw_combined_evening",
            "event_id": "AW-COMBINED",
            "effective_timestep": 41,
            "end_timestep": 48,
            "sensor_detection_timestep": 41,
            "updates": {
                "return_delay_minutes_by_bus": {8: 150},
                "unavailable_chargers": combined_chargers_corrected,
            },
        },
    ]
    return notices, physical


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    notices, physical = build()
    OUTPUT.write_text(json.dumps(notices, indent=2) + "\n", encoding="utf-8")
    PHYSICAL_OUTPUT.write_text(
        json.dumps({"events": physical}, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "version": "advance_warning_benchmark_v1",
        "notice_file": str(OUTPUT.relative_to(ROOT)).replace("\\", "/"),
        "physical_event_file": str(PHYSICAL_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
        "notice_sha256": sha256(OUTPUT),
        "physical_event_sha256": sha256(PHYSICAL_OUTPUT),
        "cases": [
            {
                "scenario_id": "aw_route6_late_return",
                "advance_report_timestep": 6,
                "physical_onset_timestep": 44,
                "sensor_detection_timestep": 44,
                "economic_opportunity": "pre-charge Bus 6 before departure instead of discovering the late return after cheap charging has passed",
            },
            {
                "scenario_id": "aw_charger_bank_shutdown",
                "advance_report_timestep": 5,
                "physical_onset_timestep": 8,
                "sensor_detection_timestep": 8,
                "economic_opportunity": "move charging into available pre-isolation intervals and avoid curtailed energy",
            },
            {
                "scenario_id": "aw_combined_evening",
                "advance_report_timestep": 30,
                "physical_onset_timestep": 41,
                "sensor_detection_timestep": 41,
                "economic_opportunity": "prepare at the afternoon price trough and reassign evening V2G before both constraints become measurable",
            },
        ],
        "comparison": ["agent", "rule_text", "numerical", "oracle"],
        "information_protocol": {
            "agent_and_rule_text": "identical public chat text",
            "numerical": "no chat; causal telemetry only from sensor_detection_timestep",
            "oracle": "canonical structured interpretation at the public report time",
            "all_methods": "identical hidden physical-event file and causal ex-post settlement",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(notices)} notices and {len(physical)} hidden physical events")


if __name__ == "__main__":
    main()

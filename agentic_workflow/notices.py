from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .io import dataframe_records
from .models import (
    NoticeInterpretation,
    NoticeParameterUpdates,
    NoticeUncertaintyAssessment,
    UncertainParameterEstimate,
)
from .uncertainty import select_operational_value


_BUS_RE = re.compile(r"\bbus(?:es)?\s+((?:\d+[\s,]*(?:and\s+)?)*)", re.IGNORECASE)
_ROUTE_RE = re.compile(r"\broute(?:\s+block)?\s+(\d+)\b", re.IGNORECASE)
_BLOCK_RE = re.compile(r"\bblock\s+(\d+)\b", re.IGNORECASE)
_UNIT_RE = re.compile(r"\bunit\s+(\d+)\b", re.IGNORECASE)
_CHARGER_RE = re.compile(
    r"\b(?:charger(?:s)?|connector(?:s)?|EVSE)\s*[A-Za-z-]*(\d+)\b",
    re.IGNORECASE,
)
_DELAY_RE = re.compile(
    r"(?:increase|delay|late|cycle time|allowance|carry)[^\d+]{0,24}\+?(\d+)"
    r"\s*(?:-|to\s*)?(\d+)?\s*(?:minutes?|mins?)",
    re.IGNORECASE,
)
_DELAY_RANGE_RE = re.compile(
    r"(?:delay|late|allowance|carry)[^\d]{0,30}(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*"
    r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|min)\b",
    re.IGNORECASE,
)
_RETURN_DELAY_RE = re.compile(
    r"(?:return|back|pull[ -]?in|arrival)[^\d]{0,40}(\d+(?:\.\d+)?)\s*"
    r"(?:-|â€“|–|to)\s*(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|min)\s*(?:late|later)?",
    re.IGNORECASE,
)
_TIME_WINDOW_RE = re.compile(
    r"\b(?:window\s+|from\s+)?([01]?\d|2[0-3]):([0-5]\d)\s*"
    r"(?:-|â€“|–|to|through|until)\s*((?:[01]?\d|2[0-3]):[0-5]\d|24:00)\b",
    re.IGNORECASE,
)
_POWER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*kW\b", re.IGNORECASE)
_POWER_RANGE_RE = re.compile(
    r"(?:power|ceiling|output)[^\d]{0,32}(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*"
    r"(\d+(?:\.\d+)?)\s*kW\b",
    re.IGNORECASE,
)
_ENERGY_RE = re.compile(r"(?:energy|consumption).{0,48}?(\d+(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
_ENERGY_PERCENT_RANGE_RE = re.compile(
    r"(?:energy|consumption).{0,48}?(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*"
    r"(\d+(?:\.\d+)?)\s*(?:%|percent)",
    re.IGNORECASE,
)
_ENERGY_FACTOR_RE = re.compile(
    r"(?:(1(?:\.\d+)?)\s*x.{0,32}?(?:energy|traction)|"
    r"(?:energy|traction).{0,32}?(1(?:\.\d+)?)\s*x)",
    re.IGNORECASE,
)
_ENERGY_FACTOR_RANGE_RE = re.compile(
    r"(?:energy|traction).{0,48}?(1(?:\.\d+)?)\s*(?:-|–|to)\s*"
    r"(1(?:\.\d+)?)\s*x",
    re.IGNORECASE,
)
_UNAVAILABILITY_RANGE_RE = re.compile(
    r"unavailability probability[^\d]{0,20}(0(?:\.\d+)?|1(?:\.0+)?)\s*"
    r"(?:-|–|to)\s*(0(?:\.\d+)?|1(?:\.0+)?)",
    re.IGNORECASE,
)
_CONFIDENCE_RE = re.compile(r"\b(?:confidence|conf)\s*(?:is|as|=|:)?\s*(0(?:\.\d+)?|1(?:\.0+)?)", re.IGNORECASE)


@dataclass(slots=True)
class NoticeRecord:
    notice_id: str
    scenario_id: str
    event_id: str
    source_type: str
    wording_variant: str
    report_timestep: int
    text: str
    benchmark_split: str | None = None
    uncertainty_case: str | None = None
    canonical: NoticeInterpretation | None = None

    def public_dict(self) -> dict[str, Any]:
        """Return only fields available to an operational decision maker.

        Scenario, wording, split, and uncertainty-case fields are experimental
        labels. Keeping them out of the agent/parser payload prevents benchmark
        metadata from leaking the event class or test condition.
        """
        return {
            "notice_id": self.notice_id,
            "event_id": self.event_id,
            "source_type": self.source_type,
            "report_timestep": self.report_timestep,
            "text": self.text,
        }


class NoticeSeries:
    """Timestep-indexed operational notices with optional canonical truth."""

    def __init__(self, path: Path | None):
        self.path = path
        self._records: list[NoticeRecord] = []
        if path is None:
            return
        if not path.exists():
            raise FileNotFoundError(f"Notice input not found: {path}")
        if path.suffix.lower() == ".json":
            raw = json.loads(path.read_text(encoding="utf-8"))
            rows = raw if isinstance(raw, list) else raw.get("notices", [])
        else:
            rows = dataframe_records(pd.read_csv(path))
        for row in rows:
            canonical_raw = row.get("canonical")
            if isinstance(canonical_raw, str) and canonical_raw.strip():
                canonical_raw = json.loads(canonical_raw)
            canonical = NoticeInterpretation.model_validate(canonical_raw) if canonical_raw else None
            self._records.append(
                NoticeRecord(
                    notice_id=str(row["notice_id"]),
                    scenario_id=str(row.get("scenario_id") or row["notice_id"]),
                    event_id=str(row.get("event_id") or row["notice_id"]),
                    source_type=str(row.get("source_type") or "informational"),
                    wording_variant=str(row.get("wording_variant") or "explicit"),
                    report_timestep=int(row["report_timestep"]),
                    text=str(row["text"]),
                    benchmark_split=(
                        str(row["benchmark_split"])
                        if row.get("benchmark_split")
                        else None
                    ),
                    uncertainty_case=(
                        str(row["uncertainty_case"])
                        if row.get("uncertainty_case")
                        else None
                    ),
                    canonical=canonical,
                )
            )

    def at(
        self,
        timestep: int,
        *,
        scenario_ids: tuple[str, ...] = (),
        wording_variant: str | None = None,
    ) -> list[NoticeRecord]:
        selected = set(scenario_ids)
        return [
            record
            for record in self._records
            if record.report_timestep == timestep
            and (not selected or record.scenario_id in selected)
            and (wording_variant is None or record.wording_variant == wording_variant)
        ]

    @property
    def records(self) -> tuple[NoticeRecord, ...]:
        """All records in source order, exposed read-only for evaluation scripts."""

        return tuple(self._records)


def _ints(match: re.Match[str] | None) -> list[int]:
    if not match:
        return []
    return sorted({int(item) for item in re.findall(r"\d+", match.group(1))})


def _timestep_window(text: str) -> tuple[int, int] | None:
    """Parse a half-hour operational window into inclusive day timesteps."""

    match = _TIME_WINDOW_RE.search(text)
    if not match:
        return None
    start_minutes = int(match.group(1)) * 60 + int(match.group(2))
    end_hour, end_minute = (int(part) for part in match.group(3).split(":", 1))
    end_minutes = end_hour * 60 + end_minute
    if end_minutes <= start_minutes:
        return None
    start = max(1, min(48, start_minutes // 30 + 1))
    end = max(start, min(48, end_minutes // 30))
    return start, end


def _phase(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("conditional warning", "warning was raised")):
        return "warning"
    if any(word in lowered for word in ("restored", "return to normal", "back to normal", "normal service", "cleared", "reopened", "recovery confirmed")):
        return "recovery"
    if any(
        word in lowered
        for word in (
            "worsened",
            "increased further",
            "now fully unavailable",
            "severity",
            "dispatch correction",
            "earlier values are superseded",
            "verified correction",
            "supersedes the previous",
        )
    ):
        return "severity_change"
    if any(
        word in lowered
        for word in ("continues", "still active", "remains active", "unchanged", "no change", "remain in force")
    ):
        return "persistence"
    if any(
        word in lowered
        for word in (
            "informational",
            "no operational impact",
            "no action required",
            "monitoring note",
            "no new dispatch",
            "no new operational restriction",
            "post-recovery monitoring",
        )
    ):
        return "stable"
    return "onset"


def _range(match: re.Match[str] | None) -> tuple[float, float] | None:
    if match is None:
        return None
    lower, upper = (float(value) for value in match.groups())
    return (min(lower, upper), max(lower, upper))


def _recommendation(phase: str, text: str) -> str:
    lowered = text.lower()
    if phase == "warning" or "request confirmation" in lowered:
        return "request_confirmation"
    if phase in {"persistence", "stable"} or any(
        phrase in lowered
        for phrase in ("do not replan", "keep the current plan", "wait", "no replan")
    ):
        return "wait"
    return "optimize"


def _uncertainty_details(
    record: NoticeRecord,
    *,
    phase: str,
    bus_ids: list[int],
    charger_ids: list[int],
) -> NoticeUncertaintyAssessment:
    text = record.text
    lowered = text.lower()
    recommendation = _recommendation(phase, text)
    estimates: list[UncertainParameterEstimate] = []

    def add_estimates(
        parameter: str,
        assets: list[int],
        bounds: tuple[float, float] | None,
        unit: str,
    ) -> None:
        if bounds is None:
            return
        lower, upper = bounds
        selection_recommendation = (
            "optimize" if phase == "persistence" else recommendation
        )
        selected, policy = select_operational_value(
            parameter, lower, upper, selection_recommendation
        )
        for asset_id in assets:
            estimates.append(
                UncertainParameterEstimate(
                    parameter=parameter,
                    asset_id=asset_id,
                    lower_bound=lower,
                    upper_bound=upper,
                    selected_value=selected,
                    unit=unit,
                    selection_policy=policy,
                )
            )

    delay_bounds = _range(_DELAY_RANGE_RE.search(text))
    return_delay_bounds = _range(_RETURN_DELAY_RE.search(text))
    energy_percent_bounds = _range(_ENERGY_PERCENT_RANGE_RE.search(text))
    energy_factor_bounds = _range(_ENERGY_FACTOR_RANGE_RE.search(text))
    power_bounds = _range(_POWER_RANGE_RE.search(text))
    unavailable_bounds = _range(_UNAVAILABILITY_RANGE_RE.search(text))
    add_estimates("delay_minutes", bus_ids, delay_bounds, "minutes")
    add_estimates(
        "return_delay_minutes", bus_ids, return_delay_bounds, "minutes"
    )
    if energy_percent_bounds is not None:
        energy_percent_bounds = (
            1 + energy_percent_bounds[0] / 100,
            1 + energy_percent_bounds[1] / 100,
        )
    add_estimates(
        "energy_multiplier",
        bus_ids,
        energy_factor_bounds or energy_percent_bounds,
        "multiplier",
    )
    add_estimates("charger_power_kw", charger_ids, power_bounds, "kw")
    add_estimates(
        "charger_unavailability_probability",
        charger_ids,
        unavailable_bounds,
        "probability",
    )
    confidence_match = _CONFIDENCE_RE.search(text)
    confidence = float(confidence_match.group(1)) if confidence_match else (
        0.6 if any(word in lowered for word in ("maybe", "roughly", "pending", "uncertain")) else 1.0
    )
    conflict_signal = any(
        phrase in lowered
        for phrase in ("conflict", "disagree", "does not fully match", "doesn't match", "flaky", "feed may be stale")
    )
    conflicts: list[str] = []
    if conflict_signal:
        conflicts = {
            "warning": ["field_report_vs_initial_telemetry"],
            "onset": ["field_estimate_vs_fluctuating_dashboard"],
            "persistence": ["driver_or_maintenance_report_vs_delayed_telemetry"],
        }.get(phase, [])
    provisional = any(
        word in lowered for word in ("provisional", "provisionally", "conditional", "flaky")
    ) or phase in {"warning", "onset", "persistence"}
    return NoticeUncertaintyAssessment(
        confidence_level=confidence,
        provisional=provisional,
        conflicting_evidence=conflicts,
        estimates=estimates,
        recommended_action=recommendation,
        rationale={
            "warning": "Conditional warning requires confirmation before optimizer inputs change.",
            "onset": "Confirmed event uses the frozen conservative parameter policy.",
            "persistence": "No selected value changed; avoid duplicate optimization.",
            "severity_change": "A correction supersedes earlier operational values.",
            "recovery": "Confirmed recovery restores nominal assumptions.",
            "stable": "No new operational restriction is present.",
        }[phase],
    )


def frozen_rule_parse(record: NoticeRecord, bus_route_map: dict[int, int] | None = None) -> NoticeInterpretation:
    """Transparent, deliberately lightweight comparator frozen before evaluation."""

    text = record.text
    lowered = text.lower()
    bus_ids = _ints(_BUS_RE.search(text))
    route_match = _ROUTE_RE.search(text) or _BLOCK_RE.search(text)
    charger_ids = sorted({int(item) for item in _CHARGER_RE.findall(text)})
    if not bus_ids and record.source_type in {"service_alert", "driver_chat", "combined"}:
        bus_ids = sorted({int(item) for item in _UNIT_RE.findall(text)})
    if not bus_ids and route_match and bus_route_map:
        route_id = int(route_match.group(1))
        bus_ids = sorted(bus for bus, route in bus_route_map.items() if route == route_id)
    phase = _phase(text)
    uncertainty_details = _uncertainty_details(
        record, phase=phase, bus_ids=bus_ids, charger_ids=charger_ids
    )
    estimates = {
        (item.parameter, item.asset_id): item.selected_value
        for item in uncertainty_details.estimates
    }
    delay_range = _range(_DELAY_RANGE_RE.search(text))
    return_delay_range = _range(_RETURN_DELAY_RE.search(text))
    delay_match = _DELAY_RE.search(text)
    delay_minutes = 0
    if delay_range is not None:
        delay_minutes = round(delay_range[1])
    elif delay_match:
        bounds = [int(value) for value in delay_match.groups() if value]
        delay_minutes = round(sum(bounds) / len(bounds))
    energy_percent_range = _range(_ENERGY_PERCENT_RANGE_RE.search(text))
    energy_factor_range = _range(_ENERGY_FACTOR_RANGE_RE.search(text))
    energy_match = _ENERGY_RE.search(text)
    energy_factor_match = _ENERGY_FACTOR_RE.search(text)
    if energy_factor_range is not None:
        energy_multiplier = energy_factor_range[1]
    elif energy_percent_range is not None:
        energy_multiplier = 1 + energy_percent_range[1] / 100
    elif energy_match:
        energy_multiplier = 1 + float(energy_match.group(1)) / 100
    elif energy_factor_match:
        energy_multiplier = float(next(value for value in energy_factor_match.groups() if value))
    else:
        energy_multiplier = 1.0
    power_range = _range(_POWER_RANGE_RE.search(text))
    power_match = _POWER_RE.search(text)
    power = power_range[0] if power_range is not None else (
        float(power_match.group(1)) if power_match else None
    )
    faulted = bool(
        re.search(
            r"\b(faulted|unavailable|unavailability|out of service|offline|locked out|lockout)\b",
            lowered,
        )
    )
    recovered = phase == "recovery"
    informational = phase == "stable"
    if charger_ids and bus_ids:
        event_type = "combined"
        source_type = record.source_type if record.source_type == "driver_chat" else "combined"
    elif charger_ids:
        event_type = "charger_fault" if faulted else "charger_derating"
        source_type = record.source_type if record.source_type == "driver_chat" else "ocpp"
    elif delay_match or delay_range or return_delay_range:
        event_type = "service_delay"
        source_type = record.source_type if record.source_type == "driver_chat" else "service_alert"
    elif energy_match or energy_factor_match or energy_percent_range or energy_factor_range:
        event_type = "route_energy_change"
        source_type = record.source_type if record.source_type == "driver_chat" else "service_alert"
    elif bus_ids or route_match:
        event_type = "service_delay"
        source_type = "service_alert"
    else:
        event_type = "informational"
        source_type = "informational"
        informational = True
    actionable = uncertainty_details.recommended_action == "optimize"
    updates = NoticeParameterUpdates(
        delay_minutes_by_bus={
            bus: (0 if recovered else int(round(estimates.get(("delay_minutes", bus), delay_minutes))))
            for bus in bus_ids
            if recovered or (actionable and (delay_match or delay_range))
        },
        return_delay_minutes_by_bus={
            bus: (
                0
                if recovered
                else int(
                    round(
                        estimates.get(
                            ("return_delay_minutes", bus),
                            return_delay_range[1] if return_delay_range else 0,
                        )
                    )
                )
            )
            for bus in bus_ids
            if recovered or (actionable and return_delay_range is not None)
        },
        energy_multiplier_by_bus={
            bus: (1.0 if recovered else float(estimates.get(("energy_multiplier", bus), energy_multiplier)))
            for bus in bus_ids
            if actionable
            and (
                energy_match
                or energy_factor_match
                or energy_percent_range
                or energy_factor_range
            )
        },
        charger_power_kw={
            charger: (
                200.0
                if recovered
                else float(estimates.get(("charger_power_kw", charger), power))
            )
            for charger in charger_ids
            if recovered or (actionable and power is not None)
        },
        unavailable_chargers=[] if recovered else (
            charger_ids if actionable and faulted and power is None else []
        ),
    )
    time_window = _timestep_window(text)
    return NoticeInterpretation(
        event_id=record.event_id,
        source_type=source_type,
        event_type=event_type,
        phase=phase,
        affected_buses=bus_ids,
        affected_chargers=charger_ids,
        effective_timestep=(time_window[0] if time_window else record.report_timestep),
        expected_end_timestep=(time_window[1] if time_window else None),
        uncertainty=bool(uncertainty_details.estimates or uncertainty_details.conflicting_evidence) or bool(
            re.search(
                r"\b(may|approximately|expected|uncertain|not confirmed|pending|eta)\b",
                lowered,
            )
        ),
        uncertainty_details=uncertainty_details,
        material=not informational,
        updates=updates,
        evidence=["frozen_regex_v1", record.notice_id],
    )


def resolve_notice_coreferences(
    record: NoticeRecord,
    parsed: NoticeInterpretation,
    active_events: dict[str, dict[str, Any]],
) -> NoticeInterpretation:
    """Resolve explicit same-event references for the stateful rule baseline.

    This deliberately narrow memory layer does not perform general language
    understanding. It only carries assets and current settings forward when a
    notice reuses the exact event identifier already accepted by the workflow.
    """

    previous_raw = active_events.get(record.event_id)
    if previous_raw is None:
        return parsed
    previous = NoticeInterpretation.model_validate(previous_raw)
    buses = parsed.affected_buses or previous.affected_buses
    chargers = parsed.affected_chargers or previous.affected_chargers
    delay = dict(previous.updates.delay_minutes_by_bus)
    return_delay = dict(previous.updates.return_delay_minutes_by_bus)
    energy = dict(previous.updates.energy_multiplier_by_bus)
    power = dict(previous.updates.charger_power_kw)
    unavailable = list(previous.updates.unavailable_chargers)
    previous_details = previous.uncertainty_details
    parsed_details = parsed.uncertainty_details

    if parsed.phase not in {"recovery", "stable"}:
        delay.update(parsed.updates.delay_minutes_by_bus)
        return_delay.update(parsed.updates.return_delay_minutes_by_bus)
        energy.update(parsed.updates.energy_multiplier_by_bus)
        power.update(parsed.updates.charger_power_kw)
        if parsed.updates.unavailable_chargers:
            unavailable = sorted(
                set(unavailable) | set(parsed.updates.unavailable_chargers)
            )
        # A coreferential update may omit the asset identifier while still
        # providing a new scalar value ("same unit ... 28 min"). Apply that
        # value only to assets recovered from the exact same event ID.
        if buses and not parsed.updates.delay_minutes_by_bus:
            bounds = _range(_DELAY_RANGE_RE.search(record.text))
            match = _DELAY_RE.search(record.text)
            if bounds is not None:
                delay.update({bus: int(round(bounds[1])) for bus in buses})
            elif match is not None:
                values = [int(value) for value in match.groups() if value]
                delay.update({bus: round(sum(values) / len(values)) for bus in buses})
        if buses and not parsed.updates.energy_multiplier_by_bus:
            factor_bounds = _range(_ENERGY_FACTOR_RANGE_RE.search(record.text))
            percent_bounds = _range(_ENERGY_PERCENT_RANGE_RE.search(record.text))
            factor_match = _ENERGY_FACTOR_RE.search(record.text)
            percent_match = _ENERGY_RE.search(record.text)
            if factor_bounds is not None:
                energy.update({bus: factor_bounds[1] for bus in buses})
            elif percent_bounds is not None:
                energy.update({bus: 1 + percent_bounds[1] / 100 for bus in buses})
            elif factor_match is not None:
                value = float(next(item for item in factor_match.groups() if item))
                energy.update({bus: value for bus in buses})
            elif percent_match is not None:
                value = 1 + float(percent_match.group(1)) / 100
                energy.update({bus: value for bus in buses})
        if chargers and not parsed.updates.charger_power_kw:
            bounds = _range(_POWER_RANGE_RE.search(record.text))
            match = _POWER_RE.search(record.text)
            if bounds is not None:
                power.update({charger: bounds[0] for charger in chargers})
            elif match is not None:
                power.update(
                    {charger: float(match.group(1)) for charger in chargers}
                )
        if chargers and re.search(
            r"\b(faulted|unavailable|unavailability|out of service|offline|locked out|lockout)\b",
            record.text,
            re.IGNORECASE,
        ):
            unavailable = sorted(set(unavailable) | set(chargers))

    if parsed.phase == "recovery":
        delay = {bus: 0 for bus in buses if bus in delay}
        return_delay = {bus: 0 for bus in buses if bus in return_delay}
        energy = {bus: 1.0 for bus in buses if bus in energy}
        power = {charger: 200.0 for charger in chargers}
        unavailable = []

    if parsed.phase == "recovery":
        restored_estimates = []
        for item in previous_details.estimates:
            nominal = {
                "delay_minutes": 0.0,
                "return_delay_minutes": 0.0,
                "energy_multiplier": 1.0,
                "charger_power_kw": 200.0,
                "charger_unavailability_probability": 0.0,
            }[item.parameter]
            restored_estimates.append(
                item.model_copy(
                    update={
                        "lower_bound": nominal,
                        "upper_bound": nominal,
                        "selected_value": nominal,
                        "selection_policy": "restored_nominal",
                    }
                )
            )
        uncertainty_details = parsed_details.model_copy(
            update={"estimates": restored_estimates}
        )
    elif parsed.phase == "stable":
        uncertainty_details = parsed_details.model_copy(update={"estimates": []})
    elif parsed_details.estimates:
        uncertainty_details = parsed_details
    else:
        uncertainty_details = parsed_details.model_copy(
            update={"estimates": previous_details.estimates}
        )

    event_type = parsed.event_type
    source_type = parsed.source_type
    if event_type == "informational" and parsed.phase != "stable":
        event_type = previous.event_type
        source_type = previous.source_type
    return parsed.model_copy(
        update={
            "source_type": source_type,
            "event_type": event_type,
            "affected_buses": buses,
            "affected_chargers": chargers,
            "expected_end_timestep": (
                None
                if parsed.phase in {"recovery", "stable"}
                else parsed.expected_end_timestep or previous.expected_end_timestep
            ),
            "uncertainty": (
                False
                if parsed.phase in {"recovery", "stable"}
                else parsed.uncertainty or previous.uncertainty
            ),
            "uncertainty_details": uncertainty_details,
            "material": parsed.phase != "stable",
            "updates": NoticeParameterUpdates(
                delay_minutes_by_bus=delay,
                return_delay_minutes_by_bus=return_delay,
                energy_multiplier_by_bus=energy,
                charger_power_kw=power,
                unavailable_chargers=sorted(unavailable),
            ),
            "evidence": [*parsed.evidence, "same_event_memory_v1"],
        }
    )


def merge_interpretations(items: Iterable[NoticeInterpretation]) -> NoticeInterpretation | None:
    values = list(items)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    phases = {item.phase for item in values}
    phase = next(iter(phases)) if len(phases) == 1 else "severity_change"
    delay: dict[int, int] = {}
    return_delay: dict[int, int] = {}
    energy: dict[int, float] = {}
    power: dict[int, float] = {}
    unavailable: set[int] = set()
    estimates = []
    conflicts: list[str] = []
    for item in values:
        delay.update(item.updates.delay_minutes_by_bus)
        return_delay.update(item.updates.return_delay_minutes_by_bus)
        energy.update(item.updates.energy_multiplier_by_bus)
        power.update(item.updates.charger_power_kw)
        unavailable.update(item.updates.unavailable_chargers)
        estimates.extend(item.uncertainty_details.estimates)
        conflicts.extend(item.uncertainty_details.conflicting_evidence)
    recommendations = {
        item.uncertainty_details.recommended_action for item in values
    }
    recommendation = (
        "optimize"
        if "optimize" in recommendations
        else "request_confirmation"
        if "request_confirmation" in recommendations
        else "wait"
    )
    return NoticeInterpretation(
        event_id="+".join(item.event_id for item in values),
        source_type="combined",
        event_type="combined",
        phase=phase,
        affected_buses=sorted({bus for item in values for bus in item.affected_buses}),
        affected_chargers=sorted({charger for item in values for charger in item.affected_chargers}),
        effective_timestep=min(item.effective_timestep for item in values),
        expected_end_timestep=max(
            (
                item.expected_end_timestep
                for item in values
                if item.expected_end_timestep is not None
            ),
            default=None,
        ),
        uncertainty=any(item.uncertainty for item in values),
        uncertainty_details=NoticeUncertaintyAssessment(
            confidence_level=min(
                item.uncertainty_details.confidence_level for item in values
            ),
            provisional=any(
                item.uncertainty_details.provisional for item in values
            ),
            conflicting_evidence=sorted(set(conflicts)),
            estimates=estimates,
            recommended_action=recommendation,
            rationale="Merged operational notices use the most actionable recommendation.",
        ),
        material=any(item.material for item in values),
        updates=NoticeParameterUpdates(
            delay_minutes_by_bus=delay,
            return_delay_minutes_by_bus=return_delay,
            energy_multiplier_by_bus=energy,
            charger_power_kw=power,
            unavailable_chargers=sorted(unavailable),
        ),
        evidence=[evidence for item in values for evidence in item.evidence],
    )


def apply_notice_updates(
    interpretation: NoticeInterpretation | None,
    *,
    chargers: pd.DataFrame,
    trips: pd.DataFrame,
    energy_consumption: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Apply validated notice updates to copies of the common optimizer inputs."""

    revised_chargers = chargers.copy()
    revised_trips = trips.copy()
    revised_energy = energy_consumption.copy()
    if interpretation is None:
        return revised_chargers, revised_trips, revised_energy
    updates = interpretation.updates
    unavailable = set(updates.unavailable_chargers)
    temporal_window = interpretation.expected_end_timestep is not None
    if unavailable and not temporal_window:
        revised_chargers = revised_chargers.loc[
            ~pd.to_numeric(revised_chargers["charger_id"], errors="coerce").isin(unavailable)
        ].copy()
    if temporal_window and (unavailable or updates.charger_power_kw):
        if "power_schedule_kw" not in revised_chargers:
            revised_chargers["power_schedule_kw"] = pd.Series(
                [None] * len(revised_chargers), dtype="object"
            )
        start = interpretation.effective_timestep
        end = int(interpretation.expected_end_timestep or 48)
        for index, row in revised_chargers.iterrows():
            charger_id = int(row["charger_id"])
            column = "max_power_kw" if "max_power_kw" in revised_chargers else "charger_kw"
            nominal = float(row[column])
            schedule = [nominal] * 48
            window_power = (
                0.0
                if charger_id in unavailable
                else updates.charger_power_kw.get(charger_id)
            )
            if window_power is None:
                continue
            for timestep in range(start, end + 1):
                schedule[timestep - 1] = max(0.0, float(window_power))
            revised_chargers.at[index, "power_schedule_kw"] = schedule
    for charger_id, power in updates.charger_power_kw.items():
        if temporal_window:
            continue
        mask = pd.to_numeric(revised_chargers["charger_id"], errors="coerce") == charger_id
        column = "max_power_kw" if "max_power_kw" in revised_chargers else "charger_kw"
        revised_chargers.loc[mask, column] = float(power)
    for bus_id, multiplier in updates.energy_multiplier_by_bus.items():
        mask = pd.to_numeric(revised_energy["bus_id"], errors="coerce") == bus_id
        revised_energy.loc[mask, "energy_kwhkm"] = (
            pd.to_numeric(revised_energy.loc[mask, "energy_kwhkm"], errors="coerce") * float(multiplier)
        )
    for bus_id, minutes in updates.delay_minutes_by_bus.items():
        mask = pd.to_numeric(revised_trips["bus_id"], errors="coerce") == bus_id
        for column in ("time_begin", "time_end"):
            revised_trips.loc[mask, column] = revised_trips.loc[mask, column].map(
                lambda value: _shift_time(value, minutes)
            )
    for bus_id, minutes in updates.return_delay_minutes_by_bus.items():
        mask = pd.to_numeric(revised_trips["bus_id"], errors="coerce") == bus_id
        revised_trips.loc[mask, "time_end"] = revised_trips.loc[mask, "time_end"].map(
            lambda value: _shift_return_time(value, minutes)
        )
    return revised_chargers, revised_trips, revised_energy


def _shift_time(value: Any, minutes: int) -> str:
    hour, minute = (int(part) for part in str(value).split(":", 1))
    total = (hour * 60 + minute + int(minutes)) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def _shift_return_time(value: Any, minutes: int) -> str:
    """Extend a return without wrapping a late trip into the start of the day."""

    hour, minute = (int(part) for part in str(value).split(":", 1))
    total = min(1440, max(0, hour * 60 + minute + int(minutes)))
    return "24:00" if total == 1440 else f"{total // 60:02d}:{total % 60:02d}"

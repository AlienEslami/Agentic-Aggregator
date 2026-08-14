from __future__ import annotations

import re
from typing import Any, Iterable

from .models import (
    EvaluationFeedback,
    MultiplierAdjustment,
    OperationalPriority,
    PricingDecision,
    PriorityAssessment,
)


DEFAULT_EXTRA_RESERVE_SOC_FRACTION = 0.30
DEFAULT_FRONTLOAD_CHARGE_KWH = 100.0
DEFAULT_V2G_EXPORT_KWH = 50.0

_BUS_RE = re.compile(r"\bbus\s*#?\s*(\d+)\b", re.IGNORECASE)
_PERCENT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:%|percent(?:age points?)?)\b", re.IGNORECASE)
_KWH_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*kwh\b", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b([01]?\d|2[0-4]):([0-5]\d)\b")


def clock_to_timestep(hour: int, minute: int) -> int:
    """Map a clock boundary to the containing one-based 30-minute interval."""

    if hour == 24:
        return 48
    total_minutes = max(0, min(1439, hour * 60 + minute))
    return min(48, total_minutes // 30 + 1)


def _public_texts(messages: Iterable[dict[str, Any]]) -> list[str]:
    return [str(message.get("text") or "").strip() for message in messages if message.get("text")]


def frozen_priority_parse(
    messages: Iterable[dict[str, Any]],
    *,
    planning_start_timestep: int,
) -> OperationalPriority | None:
    """Frozen, deliberately limited non-LLM parser for evaluator comparison.

    It consumes exactly the same public text as the LLM evaluator.  Defaults are
    declared here rather than selected after observing experimental outcomes.
    """

    messages = list(messages)
    texts = _public_texts(messages)
    if not texts:
        return None
    text = " ".join(texts)
    lowered = text.lower()
    buses = sorted({int(value) for value in _BUS_RE.findall(text)})
    clocks = [(int(hour), int(minute)) for hour, minute in _CLOCK_RE.findall(text)]
    start = max(1, int(planning_start_timestep))
    end = 48
    policy_window = "tonight" in lowered or "end of day" in lowered
    if policy_window:
        start = max(start, 37)
    elif len(clocks) >= 2:
        start = max(start, clock_to_timestep(*clocks[-2]))
        end = clock_to_timestep(*clocks[-1])
    elif clocks and "before" in lowered:
        end = clock_to_timestep(*clocks[-1])

    percentages = [float(value) / 100.0 for value in _PERCENT_RE.findall(text)]
    energy_targets = [float(value) for value in _KWH_RE.findall(text)]
    priority_id = str(messages[0].get("notice_id") or "TEXT-PRIORITY")

    reserve_language = any(
        phrase in lowered
        for phrase in (
            "extra charge",
            "extra reserve",
            "additional reserve",
            "keep more charge",
            "preserve reserve",
        )
    )
    if reserve_language and buses:
        target = percentages[-1] if percentages else DEFAULT_EXTRA_RESERVE_SOC_FRACTION
        return OperationalPriority(
            priority_id=priority_id,
            objective="preserve_bus_reserve",
            affected_buses=buses,
            timestep_start=min(start, end),
            timestep_end=max(start, end),
            target_value=target,
            target_unit="soc_fraction",
            priority_level="soft",
            default_policy_applied=not bool(percentages),
            evidence=["frozen_priority_regex_v1"],
        )

    frontload_language = any(
        phrase in lowered
        for phrase in (
            "front-load charging",
            "frontload charging",
            "move charging ahead",
            "charge before the outage",
            "charging before the outage",
        )
    )
    if frontload_language:
        target = energy_targets[-1] if energy_targets else DEFAULT_FRONTLOAD_CHARGE_KWH
        return OperationalPriority(
            priority_id=priority_id,
            objective="frontload_site_charging",
            timestep_start=min(start, end),
            timestep_end=max(start, end),
            target_value=target,
            target_unit="kwh",
            priority_level="soft",
            default_policy_applied=not bool(energy_targets),
            evidence=["frozen_priority_regex_v1"],
        )

    export_language = any(
        phrase in lowered
        for phrase in ("prioritize v2g", "prioritize export", "support the grid by exporting")
    )
    if export_language:
        target = energy_targets[-1] if energy_targets else DEFAULT_V2G_EXPORT_KWH
        return OperationalPriority(
            priority_id=priority_id,
            objective="prioritize_v2g_export",
            timestep_start=min(start, end),
            timestep_end=max(start, end),
            target_value=target,
            target_unit="kwh",
            priority_level="soft",
            default_policy_applied=not bool(energy_targets),
            evidence=["frozen_priority_regex_v1"],
        )
    return None


def schedule_priority_metrics(
    result: dict[str, Any],
    priority: OperationalPriority,
    *,
    battery_capacity_kwh_by_bus: dict[int, float],
) -> tuple[float | None, str]:
    horizon_start = int(result.get("remaining_horizon_start") or 1)
    horizon_end = int(result.get("remaining_horizon_end") or horizon_start - 1)
    overlap_start = max(priority.timestep_start, horizon_start)
    overlap_end = min(priority.timestep_end, horizon_end)
    if overlap_start > overlap_end:
        return None, "The requested window does not overlap the candidate horizon."

    first_index = overlap_start - horizon_start
    last_index = overlap_end - horizon_start + 1
    if priority.objective == "preserve_bus_reserve":
        energy = list(result.get("energy") or [])
        measured: list[float] = []
        for bus_id in priority.affected_buses:
            if bus_id < 1 or bus_id > len(energy):
                continue
            capacity = float(battery_capacity_kwh_by_bus.get(bus_id) or 0.0)
            values = list(energy[bus_id - 1] or [])[first_index:last_index]
            if capacity > 0 and values:
                measured.append(min(float(value) / capacity for value in values))
        return (
            (min(measured) if measured else None),
            "Minimum SOC fraction across the requested buses and window.",
        )
    if priority.objective == "frontload_site_charging":
        values = list(result.get("w_buy") or [])[first_index:last_index]
        return (sum(float(value) for value in values), "Total site charging in the requested window.")
    if priority.objective == "prioritize_v2g_export":
        values = list(result.get("w_sell") or [])[first_index:last_index]
        return (sum(float(value) for value in values), "Total V2G export in the requested window.")
    return None, "The candidate result does not expose bus-specific V2G needed by this priority."


def assess_priority(
    result: dict[str, Any],
    priority: OperationalPriority | None,
    *,
    battery_capacity_kwh_by_bus: dict[int, float],
) -> PriorityAssessment | None:
    if priority is None:
        return None
    measured, description = schedule_priority_metrics(
        result,
        priority,
        battery_capacity_kwh_by_bus=battery_capacity_kwh_by_bus,
    )
    if measured is None:
        return PriorityAssessment(
            applicable=False,
            satisfied=None,
            measured_value=None,
            target_value=priority.target_value,
            compliance_gap=None,
            rationale=description,
        )
    gap = float(measured) - float(priority.target_value)
    return PriorityAssessment(
        applicable=True,
        satisfied=gap >= -1e-9,
        measured_value=round(float(measured), 6),
        target_value=float(priority.target_value),
        compliance_gap=round(gap, 6),
        rationale=description,
    )


def priority_feedback(
    priority: OperationalPriority,
    pricing: PricingDecision,
    *,
    planning_start_timestep: int,
) -> EvaluationFeedback:
    start = max(priority.timestep_start, int(planning_start_timestep))
    end = max(start, priority.timestep_end)
    first = max(0, start - int(planning_start_timestep))
    last = min(len(pricing.buy_multipliers), end - int(planning_start_timestep) + 1)
    buy_values = pricing.buy_multipliers[first:last] or pricing.buy_multipliers
    sell_values = pricing.sell_multipliers[first:last] or pricing.sell_multipliers
    current_buy = sum(buy_values) / len(buy_values)
    current_sell = sum(sell_values) / len(sell_values)
    buy_adjustment = None
    sell_adjustment = None
    if priority.objective in {"preserve_bus_reserve", "frontload_site_charging"}:
        buy_adjustment = MultiplierAdjustment(
            timestep_start=start,
            timestep_end=end,
            direction="lower",
            amount=0.05,
            current_value=round(current_buy, 4),
            target_value=round(max(1.01, current_buy - 0.05), 4),
            instruction="Lower the buying tariff in the requested window to encourage the least-cost additional charging needed for the operator priority.",
        )
    if priority.objective == "preserve_bus_reserve":
        sell_adjustment = MultiplierAdjustment(
            timestep_start=start,
            timestep_end=end,
            direction="lower",
            amount=0.05,
            current_value=round(current_sell, 4),
            target_value=round(max(0.40, current_sell - 0.05), 4),
            instruction="Lower V2G compensation in the requested window so the selected bus reserve is less likely to be discharged.",
        )
    elif priority.objective == "prioritize_v2g_export":
        sell_adjustment = MultiplierAdjustment(
            timestep_start=start,
            timestep_end=end,
            direction="raise",
            amount=0.05,
            current_value=round(current_sell, 4),
            target_value=round(min(0.99, current_sell + 0.05), 4),
            instruction="Raise V2G compensation in the requested window to encourage the requested grid export.",
        )
    return EvaluationFeedback(
        reason="operational_priority",
        buy_multiplier_adjustment=buy_adjustment,
        sell_multiplier_adjustment=sell_adjustment,
        period_adjustment=(
            f"Address operator priority {priority.priority_id} over timesteps {start}-{end}."
        ),
        priority="operational_compliance",
        operational_priority=priority,
    )

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .agents import AgentBackend
from .models import NoticeInterpretation, TriggerDecision
from .notices import NoticeRecord
from .telemetry import ResourceMeter, summarize_agent_calls


INTERPRETATION_FIELDS = (
    "event_id",
    "source_type",
    "event_type",
    "phase",
    "affected_buses",
    "affected_chargers",
    "effective_timestep",
    "expected_end_timestep",
    "uncertainty",
    "uncertainty_details",
    "material",
    "updates",
)


def reference_action(canonical: NoticeInterpretation) -> str:
    """Map canonical event truth to the prespecified Trigger action."""

    return (
        "optimize"
        if canonical.material
        and canonical.phase in {"onset", "severity_change", "recovery"}
        and canonical.uncertainty_details.recommended_action == "optimize"
        else "skip"
    )


def _normalized(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, list):
        items = [item.model_dump() if hasattr(item, "model_dump") else item for item in value]
        return json.dumps(
            sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str)),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return value


def score_trigger_decision(
    decision: TriggerDecision,
    canonical: NoticeInterpretation,
) -> dict[str, bool]:
    """Score one raw or guarded Trigger decision against hidden truth."""

    interpretation = decision.notice_interpretation
    scores = {"action_correct": decision.action == reference_action(canonical)}
    for field in INTERPRETATION_FIELDS:
        observed = getattr(interpretation, field) if interpretation is not None else None
        scores[f"{field}_correct"] = _normalized(observed) == _normalized(
            getattr(canonical, field)
        )
    observed_uncertainty = (
        interpretation.uncertainty_details if interpretation is not None else None
    )
    canonical_uncertainty = canonical.uncertainty_details
    for field in (
        "confidence_level",
        "provisional",
        "conflicting_evidence",
        "estimates",
        "recommended_action",
    ):
        observed = (
            getattr(observed_uncertainty, field)
            if observed_uncertainty is not None
            else None
        )
        scores[f"uncertainty_{field}_correct"] = _normalized(observed) == _normalized(
            getattr(canonical_uncertainty, field)
        )
    return scores


def build_notice_only_context(
    record: NoticeRecord,
    *,
    active_events: dict[str, dict[str, Any]] | None = None,
    notice_memory: dict[str, dict[str, Any]] | None = None,
    last_reoptimization: dict[str, Any] | None = None,
    mode: str = "selfish",
) -> dict[str, Any]:
    """Build a neutral numerical context that isolates notice interpretation.

    Canonical truth and experimental labels never enter this payload. Numerical
    trigger flags are deliberately neutral so the measured decision is caused by
    the operational notice and its event-scoped memory.
    """

    active_events = active_events or {}
    notice_memory = notice_memory or {}
    last_reoptimization = last_reoptimization or {}
    timestep = int(record.report_timestep)
    remaining = 49 - timestep
    trigger_flags = {
        "energy_event_onset_pending": False,
        "price_event_onset_pending": False,
        "delay_event_onset_pending": False,
        "energy_event_active_accounted": False,
        "price_event_active_accounted": False,
        "delay_event_active_accounted": False,
        "energy_recovery_active": False,
        "price_recovery_active": False,
        "delay_recovery_active": False,
        "same_event_already_accounted": False,
        "energy_event_buses": [],
        "has_severe_delay": False,
        "severe_delay_buses": [],
        "has_high_energy_deviation": False,
        "high_energy_deviation_buses": [],
        "multi_bus_moderate_deviation": False,
        "moderate_deviation_buses": [],
        "price_high": False,
        "price_low": False,
        "price_deviation_significant": False,
        "unexpected_discharging_buses": [],
        "delay_sign_reversed": False,
        "delay_sign_reversed_buses": [],
        "delay_removal_active": False,
    }
    prices = [
        {"timestep": step, "spot_market": 0.1, "price_zone": "transition"}
        for step in range(timestep, 49)
    ]
    return {
        "timestep": timestep,
        "total_timesteps": 48,
        "remaining_timesteps": remaining,
        "remaining_hours": f"{remaining * 0.5:.1f}",
        "mode": mode,
        "trigger_flags": trigger_flags,
        "realtime_state": [],
        "day_ahead_state": [],
        "day_ahead_summary": {
            "pto_daily_cost": None,
            "aggregator_revenue": None,
            "buy_multipliers": [],
            "sell_multipliers": [],
            "avg_grid_price": 0.1,
        },
        "reoptimization_history": {
            "last_reopt_timestep": last_reoptimization.get("timestep"),
            "last_reopt_trigger_type": last_reoptimization.get("trigger_type"),
            "last_reopt_buy_multipliers": None,
            "last_reopt_sell_multipliers": None,
            "last_reopt_prices": None,
            "using_reopt_plan": bool(last_reoptimization),
        },
        "intraday_prices": {
            "remaining_count": len(prices),
            "avg_price": 0.1,
            "min_price": 0.1,
            "max_price": 0.1,
            "current_price": 0.1,
            "forecasted_price": 0.1,
            "undisturbed_price": 0.1,
            "price_deviation_pct": 0.0,
            "prices": prices,
        },
        "deviations": [],
        "deviation_summary": {
            "max_energy_deviation_pct": 0.0,
            "has_severe_deviation": False,
            "max_energy_delta_deviation_pct": 0.0,
            "has_energy_disturbance": False,
            "disturbed_energy_buses": [],
            "disturbed_energy_bus_count": 0,
            "delayed_buses": [],
            "delayed_bus_count": 0,
            "in_trip_bus_count": 0,
            "has_delays": False,
        },
        "history": [],
        "active_scenarios": [],
        "event_status": {},
        "operational_notices": [record.public_dict()],
        "active_operational_events": [
            value for _, value in sorted(active_events.items())
        ],
        "notice_event_memory": [
            {
                "event_id": event_id,
                "previous_phase": value.get("phase"),
                "previous_timestep": value.get("timestep"),
                "incorporated": bool(value.get("incorporated", False)),
            }
            for event_id, value in sorted(notice_memory.items())
        ],
    }


def _observe_interpretation(
    interpretation: NoticeInterpretation,
    active_events: dict[str, dict[str, Any]],
    notice_memory: dict[str, dict[str, Any]],
) -> None:
    previous = notice_memory.get(interpretation.event_id, {})
    notice_memory[interpretation.event_id] = {
        "phase": interpretation.phase,
        "timestep": interpretation.effective_timestep,
        "incorporated": bool(previous.get("incorporated", False)),
    }
    if interpretation.phase in {"recovery", "stable"}:
        active_events.pop(interpretation.event_id, None)
    else:
        active_events[interpretation.event_id] = interpretation.model_dump()


def _mark_incorporated(
    interpretation: NoticeInterpretation,
    notice_memory: dict[str, dict[str, Any]],
) -> None:
    notice_memory.setdefault(
        interpretation.event_id,
        {"phase": interpretation.phase, "timestep": interpretation.effective_timestep},
    )["incorporated"] = True


def evaluate_notice_sequences(
    records: Iterable[NoticeRecord],
    backend: AgentBackend,
    *,
    mode: str = "selfish",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate lifecycle sequences and return decision rows plus API-call rows."""

    grouped: dict[tuple[str, str], list[NoticeRecord]] = {}
    for record in records:
        if record.canonical is not None:
            grouped.setdefault((record.scenario_id, record.wording_variant), []).append(record)

    decision_rows: list[dict[str, Any]] = []
    call_rows: list[dict[str, Any]] = []
    for (scenario_id, wording_variant), sequence in sorted(grouped.items()):
        active_events: dict[str, dict[str, Any]] = {}
        notice_memory: dict[str, dict[str, Any]] = {}
        last_reoptimization: dict[str, Any] = {}
        for record in sorted(sequence, key=lambda item: (item.report_timestep, item.notice_id)):
            canonical = record.canonical
            if canonical is None:
                continue
            context = build_notice_only_context(
                record,
                active_events=active_events,
                notice_memory=notice_memory,
                last_reoptimization=last_reoptimization,
                mode=mode,
            )
            before = len(getattr(backend, "call_records", []))
            meter = ResourceMeter().start()
            effective = backend.trigger(context)
            local_resources = meter.stop()
            raw = getattr(backend, "last_raw_trigger", None) or effective
            new_calls = list(getattr(backend, "call_records", [])[before:])
            for call in new_calls:
                call_rows.append(
                    {
                        "scenario_id": scenario_id,
                        "wording_variant": wording_variant,
                        "notice_id": record.notice_id,
                        **call,
                    }
                )
            usage = summarize_agent_calls(new_calls)
            raw_scores = score_trigger_decision(raw, canonical)
            effective_scores = score_trigger_decision(effective, canonical)
            decision_rows.append(
                {
                    **record.public_dict(),
                    "scenario_id": scenario_id,
                    "wording_variant": wording_variant,
                    "benchmark_split": record.benchmark_split,
                    "uncertainty_case": record.uncertainty_case,
                    "reference_action": reference_action(canonical),
                    "raw_action": raw.action,
                    "effective_action": effective.action,
                    "guard_applied": raw.model_dump() != effective.model_dump(),
                    "raw_decision": raw.model_dump(),
                    "effective_decision": effective.model_dump(),
                    **{f"raw_{key}": value for key, value in raw_scores.items()},
                    **{
                        f"effective_{key}": value
                        for key, value in effective_scores.items()
                    },
                    **usage,
                    **{f"local_{key}": value for key, value in local_resources.items()},
                }
            )
            if effective.notice_interpretation is not None:
                _observe_interpretation(
                    effective.notice_interpretation, active_events, notice_memory
                )
            if effective.action == "optimize" and effective.notice_interpretation is not None:
                _mark_incorporated(effective.notice_interpretation, notice_memory)
                last_reoptimization = {
                    "timestep": record.report_timestep,
                    "trigger_type": effective.trigger_type,
                }
    return decision_rows, call_rows

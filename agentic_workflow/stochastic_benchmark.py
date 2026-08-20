"""Causal full-day integration for the no-LLM stochastic comparator."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import app_rt

from .agents import HardCheckAgentBackend
from .models import (
    NoticeInterpretation,
    NoticeParameterUpdates,
    NoticeUncertaintyAssessment,
    TriggerDecision,
)
from .optimizer import DirectOptimizerBackend, OptimizerBackend
from .stochastic_programming import (
    scenarios_from_definitions,
    solve_two_stage_stochastic,
)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_stochastic_protocol(protocol_path: Path) -> dict[str, Any]:
    """Resolve a versioned protocol and its explicitly declared base file."""

    raw = json.loads(protocol_path.read_text(encoding="utf-8"))
    base_name = raw.get("base_protocol_file")
    if not base_name:
        return raw
    base_path = protocol_path.parent / str(base_name)
    base = load_stochastic_protocol(base_path)
    metadata = {
        key: value
        for key, value in raw.items()
        if key != "base_protocol_file"
    }
    return _deep_merge(base, metadata)


def load_stochastic_case(protocol_path: Path, case_id: str) -> dict[str, Any]:
    protocol = load_stochastic_protocol(protocol_path)
    cases = {str(case["case_id"]): case for case in protocol["cases"]}
    if case_id not in cases:
        raise ValueError(f"Unknown stochastic case: {case_id}")
    return copy.deepcopy(cases[case_id])


def _localize_updates(
    updates: dict[str, Any], current_timestep: int
) -> dict[str, Any]:
    localized = copy.deepcopy(updates)
    if "price_multiplier_end_absolute_timestep" in localized:
        localized["price_multiplier_end_timestep"] = (
            int(localized.pop("price_multiplier_end_absolute_timestep"))
            - current_timestep
            + 1
        )
    for window in localized.get("charger_power_windows") or []:
        if "absolute_timestep_start" in window:
            window["timestep_start"] = (
                int(window.pop("absolute_timestep_start"))
                - current_timestep
                + 1
            )
        if "absolute_timestep_end" in window:
            window["timestep_end"] = (
                int(window.pop("absolute_timestep_end"))
                - current_timestep
                + 1
            )
    return localized


def _localize_definitions(
    definitions: list[dict[str, Any]], current_timestep: int
) -> list[dict[str, Any]]:
    localized = copy.deepcopy(definitions)
    for definition in localized:
        definition["future_updates"] = _localize_updates(
            definition.get("future_updates") or {}, current_timestep
        )
    return localized


class EventRecedingStochasticOptimizerBackend(OptimizerBackend):
    """Use a frozen two-stage scenario set at each public information update."""

    def __init__(
        self,
        case: dict[str, Any],
        *,
        solver_name: str = "gurobi",
        time_limit_seconds: float = 300.0,
        mip_gap: float = 0.02,
    ) -> None:
        self.case = copy.deepcopy(case)
        self.solver_name = solver_name
        self.time_limit_seconds = float(time_limit_seconds)
        self.mip_gap = float(mip_gap)
        self.direct = DirectOptimizerBackend()
        self.stages_by_first_executable = {
            int(stage["first_executable_timestep"]): stage
            for stage in self.case["decision_stages"]
        }

    def optimize(self, payload: dict[str, Any]) -> dict[str, Any]:
        current_timestep = int(payload.get("current_timestep", 1))
        stage = self.stages_by_first_executable.get(current_timestep)
        if stage is None:
            result = self.direct.optimize(payload)
            result["optimization_strategy"] = "observed_outcome_deterministic_recourse"
            result["stochastic_stage_id"] = "observed_outcome_recourse"
            result["external_llm_used"] = False
            result["llm_tokens"] = 0
            result["llm_cost_usd"] = 0.0
            return result

        data = app_rt.build_dataframes(copy.deepcopy(payload["input"]))
        base_context = app_rt.build_rt_context(
            data,
            copy.deepcopy(payload.get("price_guidance", {})),
            current_timestep,
            copy.deepcopy(payload.get("disturbances", [])),
        )

        common_updates = stage.get("common_future_updates") or {}
        if common_updates:
            common_reveal_absolute = int(
                stage["common_future_update_reveal_absolute_timestep"]
            )
            common_reveal_local = common_reveal_absolute - current_timestep + 1
            common_definition = {
                "scenario_id": "common_public_update",
                "probability": 1.0,
                "future_updates": _localize_updates(
                    common_updates, current_timestep
                ),
            }
            base_context = scenarios_from_definitions(
                base_context,
                [common_definition],
                reveal_timestep=common_reveal_local,
            )[0].context

        first_recourse = int(stage["first_recourse_timestep"])
        reveal_local = first_recourse - current_timestep + 1
        scenarios = scenarios_from_definitions(
            base_context,
            _localize_definitions(stage["scenarios"], current_timestep),
            reveal_timestep=reveal_local,
        )
        result = solve_two_stage_stochastic(
            scenarios,
            reveal_timestep=reveal_local,
            solver_name=self.solver_name,
            time_limit_seconds=self.time_limit_seconds,
            mip_gap=self.mip_gap,
        )
        if result.get("status") != "complete":
            return result
        if stage.get("commitment_policy") == "full_horizon_deterministic_collapse":
            commitment_steps = len(result.get("w_buy") or [])
        else:
            commitment_steps = max(1, first_recourse - current_timestep)
        result.update(
            {
                "commitment_steps": commitment_steps,
                "stochastic_stage_id": stage["stage_id"],
                "decision_observation_timestep": int(
                    stage["decision_observation_timestep"]
                ),
                "first_executable_timestep": current_timestep,
                "first_recourse_timestep": first_recourse,
                "structured_public_basis": stage["structured_public_basis"],
            }
        )
        return result


class EventRecedingStochasticAgentBackend(HardCheckAgentBackend):
    """Deterministic controller that invokes the stochastic program on schedule."""

    def __init__(self, case: dict[str, Any]) -> None:
        super().__init__()
        self.case = copy.deepcopy(case)
        self.decision_observations = {
            int(stage["decision_observation_timestep"]): stage
            for stage in self.case["decision_stages"]
        }
        self.physical_observation_timestep = int(
            self.case["physical_observation_timestep"]
        )
        self.physical_end_timestep = int(self.case["physical_end_timestep"])
        self.has_stochastic_uncertainty = any(
            len(stage["scenarios"]) > 1 for stage in self.case["decision_stages"]
        )
        self.physical_event_active = False

    def _observable_interpretation(
        self, context: dict[str, Any], *, recovery: bool
    ) -> NoticeInterpretation:
        telemetry = context.get("numerical_event_telemetry") or {}
        return_delay = {
            int(key): int(value)
            for key, value in (
                telemetry.get("return_delay_minutes_by_bus") or {}
            ).items()
        }
        unavailable = [
            int(value) for value in telemetry.get("unavailable_chargers") or []
        ]
        power = {
            int(key): float(value)
            for key, value in (telemetry.get("charger_power_kw") or {}).items()
        }
        has_bus = bool(return_delay)
        has_charger = bool(unavailable or power)
        event_type = (
            "combined"
            if has_bus and has_charger
            else "charger_fault"
            if has_charger
            else "service_delay"
        )
        if recovery and not (has_bus or has_charger):
            event_type = "combined"
        return NoticeInterpretation(
            event_id=f"STOCHASTIC-{self.case['case_id']}",
            source_type=(
                "combined"
                if event_type == "combined"
                else "ocpp"
                if event_type == "charger_fault"
                else "service_alert"
            ),
            event_type=event_type,
            phase="recovery" if recovery else "onset",
            affected_buses=sorted(return_delay),
            affected_chargers=sorted(set(unavailable) | set(power)),
            effective_timestep=int(context["timestep"]),
            expected_end_timestep=(
                int(context["timestep"])
                if recovery
                else self.physical_end_timestep
            ),
            uncertainty=False,
            uncertainty_details=NoticeUncertaintyAssessment(
                confidence_level=1.0,
                provisional=False,
                recommended_action="optimize",
                rationale="The physical outcome is now causally observable.",
            ),
            updates=(
                NoticeParameterUpdates()
                if recovery
                else NoticeParameterUpdates(
                    return_delay_minutes_by_bus=return_delay,
                    charger_power_kw=power,
                    unavailable_chargers=unavailable,
                )
            ),
            evidence=["causal_observed_outcome_for_stochastic_recourse"],
        )

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        timestep = int(context["timestep"])
        if timestep in self.decision_observations:
            stage = self.decision_observations[timestep]
            return TriggerDecision(
                action="optimize",
                reasoning=(
                    "The frozen structured public uncertainty set changed; "
                    f"solve stochastic stage {stage['stage_id']}."
                ),
                confidence=1.0,
                trigger_type="combined_notice",
                flagged_buses=[],
            )
        if (
            self.has_stochastic_uncertainty
            and timestep == self.physical_observation_timestep
        ):
            self.physical_event_active = True
            interpretation = self._observable_interpretation(
                context, recovery=False
            )
            return TriggerDecision(
                action="optimize",
                reasoning=(
                    "The uncertain physical outcome is now causally observable; "
                    "apply deterministic recourse from the next interval."
                ),
                confidence=1.0,
                trigger_type="combined_notice",
                flagged_buses=interpretation.affected_buses,
                notice_interpretation=interpretation,
            )
        if self.physical_event_active and timestep == self.physical_end_timestep + 1:
            self.physical_event_active = False
            interpretation = self._observable_interpretation(
                context, recovery=True
            )
            return TriggerDecision(
                action="optimize",
                reasoning="The observed physical event ended; restore nominal inputs.",
                confidence=1.0,
                trigger_type="delay_recovery",
                flagged_buses=[],
                notice_interpretation=interpretation,
            )
        return TriggerDecision(
            action="skip",
            reasoning="No new public uncertainty set or observable outcome is available.",
            confidence=1.0,
            trigger_type="none",
            flagged_buses=[],
        )

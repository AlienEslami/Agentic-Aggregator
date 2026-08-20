from __future__ import annotations

import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_CACHED_INPUT_USD_PER_MILLION,
    DEFAULT_CACHE_WRITE_MULTIPLIER,
    DEFAULT_INPUT_USD_PER_MILLION,
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_USD_PER_MILLION,
    DEFAULT_REASONING_EFFORT,
)
from .models import (
    EvaluationDecision,
    EvaluationFeedback,
    NULL_FEEDBACK,
    NoticeInterpretation,
    NoticeParameterUpdates,
    OperationalPriority,
    PricingDecision,
    StructuredPricingDecision,
    StructuredTriggerDecision,
    TriggerDecision,
)
from .evaluation import assess_priority, frozen_priority_parse, priority_feedback
from .experiment_controls import (
    spread_reference,
    validate_pricing_guidance_variant,
    validate_trigger_confidence_threshold,
    validate_trigger_prompt_variant,
)
from .notices import normalize_notice_clock_timesteps


PROMPT_DIR = Path(__file__).with_name("prompts")
PUBLIC_TRIGGER_CONTEXT_FIELDS = (
    "timestep",
    "total_timesteps",
    "planning_start_timestep",
    "remaining_timesteps",
    "remaining_hours",
    "mode",
    "trigger_flags",
    "n_periods",
    "period_size",
    "realtime_state",
    "day_ahead_state",
    "day_ahead_summary",
    "reoptimization_history",
    "da_benchmark",
    "intraday_prices",
    "deviations",
    "deviation_summary",
    "history",
    "operational_notices",
    "numerical_event_telemetry",
    "active_operational_events",
    "notice_event_memory",
)


def _prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


PRICING_ZONE_REFERENCES: dict[str, dict[str, dict[str, float]]] = {
    "selfish": {
        "buy": {"cheap": 1.10, "transition": 1.14, "expensive": 1.18},
        "sell": {"cheap": 0.58, "transition": 0.66, "expensive": 0.72},
    },
    "altruistic": {
        "buy": {"cheap": 1.01, "transition": 1.03, "expensive": 1.05},
        "sell": {"cheap": 0.82, "transition": 0.89, "expensive": 0.96},
    },
}


def build_pricing_reference(
    context: dict[str, Any], guidance_variant: str | None = None
) -> dict[str, Any]:
    """Build optional, horizon-matched context from the deterministic policy.

    The reference is disclosed to the Pricing Agent and used for reporting.  It
    is deliberately not used by ``normalize_pricing_decision``: the Agent keeps
    the freedom to choose any multipliers inside the existing economic bounds.
    """

    mode = str(context["mode"])
    if mode not in PRICING_ZONE_REFERENCES:
        raise ValueError(f"Unsupported pricing mode: {mode}")
    guidance_variant = validate_pricing_guidance_variant(
        guidance_variant or str(context.get("pricing_guidance_variant") or "base")
    )
    base_zone_values = PRICING_ZONE_REFERENCES[mode]
    nominal_zone_values = {
        side: spread_reference(values, guidance_variant)
        for side, values in base_zone_values.items()
    }
    rows = list((context.get("intraday_prices") or {}).get("prices") or [])
    remaining = int(context.get("remaining_timesteps") or len(rows))
    rows = rows[:remaining]
    buy: list[float] = []
    sell: list[float] = []
    zones: list[str] = []
    for index, row in enumerate(rows):
        zone = str(row.get("price_zone") or "transition")
        if zone not in base_zone_values["buy"]:
            zone = "transition"
        buy_value = base_zone_values["buy"][zone]
        if mode == "selfish" and index < 6:
            buy_value = min(buy_value, 1.10)
        buy.append(float(buy_value))
        sell.append(float(base_zone_values["sell"][zone]))
        zones.append(zone)

    spread_factor = {
        "narrow": 0.5,
        "base": 1.0,
        "wide": 1.5,
    }[guidance_variant]

    def spread_horizon(values: list[float]) -> list[float]:
        if not values:
            return []
        center = sum(values) / len(values)
        return [round(center + spread_factor * (value - center), 6) for value in values]

    # Center on the actual remaining horizon, not on three equally weighted
    # abstract zones. This keeps the disclosed arithmetic mean identical when
    # the horizon contains unequal counts of cheap/transition/expensive periods.
    buy = spread_horizon(buy)
    sell = spread_horizon(sell)

    def summary(values: list[float]) -> dict[str, float | None]:
        return {
            "minimum": min(values) if values else None,
            "arithmetic_mean": sum(values) / len(values) if values else None,
            "maximum": max(values) if values else None,
        }

    return {
        "status": "optional_context_not_constraint",
        "guidance_variant": guidance_variant,
        "temporal_spread_factor": spread_factor,
        "current_horizon_average_preserved_across_variants": True,
        "hard_economic_bounds_changed": False,
        "guidance": (
            "This deterministic reference is supplied for context. One reasonable "
            "option is to keep a similar average while redistributing markups over "
            "time. Different levels are allowed when the operational context supports "
            "them; explain any substantial difference."
        ),
        "mode": mode,
        "nominal_zone_reference": nominal_zone_values,
        "current_horizon": {
            "price_zones": zones,
            "buy_multipliers": buy,
            "sell_multipliers": sell,
            "buy_summary": summary(buy),
            "sell_summary": summary(sell),
        },
    }


def pricing_comparison_metrics(
    context: dict[str, Any],
    pricing: PricingDecision,
    result: dict[str, Any] | None = None,
) -> dict[str, float | bool | str | None]:
    """Separate overall markup level from temporal redistribution for reporting."""

    reference = build_pricing_reference(context)
    reference_horizon = reference["current_horizon"]
    reference_buy = list(reference_horizon["buy_multipliers"])
    reference_sell = list(reference_horizon["sell_multipliers"])
    chosen_buy = list(pricing.buy_multipliers)
    chosen_sell = list(pricing.sell_multipliers)

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    def centered_mae(chosen: list[float], baseline: list[float]) -> float | None:
        count = min(len(chosen), len(baseline))
        if count == 0:
            return None
        chosen = chosen[:count]
        baseline = baseline[:count]
        chosen_mean = float(mean(chosen))
        baseline_mean = float(mean(baseline))
        return sum(
            abs((chosen[index] - chosen_mean) - (baseline[index] - baseline_mean))
            for index in range(count)
        ) / count

    def dispatch_weighted_mean(
        values: list[float], volumes: list[Any]
    ) -> float | None:
        count = min(len(values), len(volumes))
        if count == 0:
            return None
        weights = [max(0.0, float(value or 0.0)) for value in volumes[:count]]
        total = sum(weights)
        if total <= 0:
            return None
        return sum(values[index] * weights[index] for index in range(count)) / total

    reference_buy_mean = mean(reference_buy)
    reference_sell_mean = mean(reference_sell)
    chosen_buy_mean = mean(chosen_buy)
    chosen_sell_mean = mean(chosen_sell)
    result = result or {}
    return {
        "reference_is_guidance_only": True,
        "reference_policy": "deterministic_zone_policy_same_remaining_horizon",
        "reference_buy_arithmetic_mean": reference_buy_mean,
        "chosen_buy_arithmetic_mean": chosen_buy_mean,
        "buy_arithmetic_mean_gap": (
            chosen_buy_mean - reference_buy_mean
            if chosen_buy_mean is not None and reference_buy_mean is not None
            else None
        ),
        "reference_sell_arithmetic_mean": reference_sell_mean,
        "chosen_sell_arithmetic_mean": chosen_sell_mean,
        "sell_arithmetic_mean_gap": (
            chosen_sell_mean - reference_sell_mean
            if chosen_sell_mean is not None and reference_sell_mean is not None
            else None
        ),
        "buy_centered_temporal_mae": centered_mae(chosen_buy, reference_buy),
        "sell_centered_temporal_mae": centered_mae(chosen_sell, reference_sell),
        "chosen_buy_dispatch_weighted_mean": dispatch_weighted_mean(
            chosen_buy, list(result.get("w_buy") or [])
        ),
        "chosen_sell_dispatch_weighted_mean": dispatch_weighted_mean(
            chosen_sell, list(result.get("w_sell") or [])
        ),
    }


def build_openai_trigger_payload(context: dict[str, Any]) -> dict[str, Any]:
    """Project runtime state onto the public operational Trigger interface."""

    payload = {
        key: context[key]
        for key in PUBLIC_TRIGGER_CONTEXT_FIELDS
        if key in context
    }
    if "history" in payload:
        payload["history"] = list(payload["history"][-5:])
    # A Trigger Agent must interpret raw public evidence itself.  Never expose a
    # manual/rule/canonical interpretation if a caller accidentally places one
    # in the shared runtime context.
    payload.pop("notice_interpretation", None)
    payload.pop("notice_flags", None)
    payload.pop("benchmark_canonical_priorities", None)
    return payload


class AgentBackend(ABC):
    @abstractmethod
    def trigger(self, context: dict[str, Any]) -> TriggerDecision: ...

    @abstractmethod
    def price(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        *,
        rerun_count: int,
        previous: PricingDecision | None,
        feedback: EvaluationFeedback | None,
    ) -> PricingDecision: ...

    @abstractmethod
    def evaluate(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        pricing: PricingDecision,
        result: dict[str, Any],
        *,
        rerun_count: int,
    ) -> EvaluationDecision: ...


class OpenAIAgentBackend(AgentBackend):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        allow_deterministic_trigger_fallback: bool = True,
        trigger_prompt_variant: str = "baseline",
        trigger_confidence_threshold: float = 0.0,
        pricing_guidance_variant: str = "base",
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the 'openai' package to use the OpenAI backend") from exc
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for --agent-backend openai")
        self.client = OpenAI()
        self.model = model
        self.last_raw_trigger: TriggerDecision | None = None
        self.last_trigger_guard_applied = False
        self.call_records: list[dict[str, Any]] = []
        self.allow_deterministic_trigger_fallback = (
            allow_deterministic_trigger_fallback
        )
        self.trigger_prompt_variant = validate_trigger_prompt_variant(
            trigger_prompt_variant
        )
        self.trigger_confidence_threshold = validate_trigger_confidence_threshold(
            trigger_confidence_threshold
        )
        self.pricing_guidance_variant = validate_pricing_guidance_variant(
            pricing_guidance_variant
        )

    def _parse(
        self,
        system: str,
        user_data: dict[str, Any],
        schema: type[Any],
        *,
        role: str = "unknown",
    ) -> Any:
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_data, default=str, separators=(",", ":"))},
            ],
            "response_format": schema,
        }
        if self.model.startswith("gpt-5.6"):
            request["reasoning_effort"] = DEFAULT_REASONING_EFFORT
        last_error: Exception | None = None
        for attempt in range(1, 4):
            started = time.perf_counter()
            record = {
                "role": role,
                "model": self.model,
                "schema": schema.__name__,
                "attempt": attempt,
                "request": user_data,
                "system_prompt_sha256": hashlib.sha256(
                    system.encode("utf-8")
                ).hexdigest(),
                "experiment_controls": {
                    "trigger_prompt_variant": getattr(
                        self, "trigger_prompt_variant", "baseline"
                    ),
                    "trigger_confidence_threshold": getattr(
                        self, "trigger_confidence_threshold", 0.0
                    ),
                    "pricing_guidance_variant": getattr(
                        self, "pricing_guidance_variant", "base"
                    ),
                },
            }
            try:
                completion = self.client.chat.completions.parse(**request)
                message = completion.choices[0].message
                record["raw_output"] = getattr(message, "content", None)
                record["refusal"] = getattr(message, "refusal", None)
                if record["refusal"]:
                    raise RuntimeError(f"Model refused the workflow request: {record['refusal']}")
                if message.parsed is None:
                    raise RuntimeError("Model returned no parsed structured output")
                record["schema_valid"] = True
                record["parsed_output"] = message.parsed.model_dump()
                usage = getattr(completion, "usage", None)
                if usage is not None:
                    usage_data = usage.model_dump() if hasattr(usage, "model_dump") else {}
                    record["usage"] = usage_data
                    input_tokens = int(usage_data.get("prompt_tokens") or usage_data.get("input_tokens") or 0)
                    output_tokens = int(usage_data.get("completion_tokens") or usage_data.get("output_tokens") or 0)
                    total_tokens = int(usage_data.get("total_tokens") or input_tokens + output_tokens)
                    input_details = (
                        usage_data.get("prompt_tokens_details")
                        or usage_data.get("input_tokens_details")
                        or {}
                    )
                    output_details = (
                        usage_data.get("completion_tokens_details")
                        or usage_data.get("output_tokens_details")
                        or {}
                    )
                    cached_input_tokens = int(input_details.get("cached_tokens") or 0)
                    cache_write_tokens = int(input_details.get("cache_write_tokens") or 0)
                    uncached_input_tokens = max(
                        0, input_tokens - cached_input_tokens - cache_write_tokens
                    )
                    reasoning_tokens = int(output_details.get("reasoning_tokens") or 0)
                    record.update(
                        {
                            "input_tokens": input_tokens,
                            "cached_input_tokens": cached_input_tokens,
                            "cache_write_tokens": cache_write_tokens,
                            "uncached_input_tokens": uncached_input_tokens,
                            "output_tokens": output_tokens,
                            "reasoning_tokens": reasoning_tokens,
                            "total_tokens": total_tokens,
                        }
                    )
                    default_input_rate = (
                        DEFAULT_INPUT_USD_PER_MILLION if self.model == DEFAULT_MODEL else 0.0
                    )
                    default_cached_rate = (
                        DEFAULT_CACHED_INPUT_USD_PER_MILLION
                        if self.model == DEFAULT_MODEL
                        else 0.0
                    )
                    default_output_rate = (
                        DEFAULT_OUTPUT_USD_PER_MILLION if self.model == DEFAULT_MODEL else 0.0
                    )
                    input_rate = float(
                        os.environ.get(
                            "OPENAI_INPUT_USD_PER_MILLION", str(default_input_rate)
                        )
                    )
                    output_rate = float(
                        os.environ.get(
                            "OPENAI_OUTPUT_USD_PER_MILLION", str(default_output_rate)
                        )
                    )
                    cached_input_rate = float(
                        os.environ.get(
                            "OPENAI_CACHED_INPUT_USD_PER_MILLION",
                            str(default_cached_rate),
                        )
                    )
                    cache_write_rate = float(
                        os.environ.get(
                            "OPENAI_CACHE_WRITE_USD_PER_MILLION",
                            str(default_input_rate * DEFAULT_CACHE_WRITE_MULTIPLIER),
                        )
                    )
                    record["cost_rates_usd_per_million"] = {
                        "input": input_rate,
                        "cached_input": cached_input_rate,
                        "cache_write": cache_write_rate,
                        "output": output_rate,
                    }
                    record["approximate_cost_usd"] = round(
                        (
                            uncached_input_tokens * input_rate
                            + cached_input_tokens * cached_input_rate
                            + cache_write_tokens * cache_write_rate
                            + output_tokens * output_rate
                        )
                        / 1_000_000,
                        8,
                    )
                return message.parsed
            except Exception as exc:
                last_error = exc
                record["schema_valid"] = False
                record["error"] = f"{type(exc).__name__}: {exc}"
                if attempt == 3:
                    raise
            finally:
                record["latency_seconds"] = round(time.perf_counter() - started, 6)
                getattr(self, "call_records", []).append(record)
        raise RuntimeError("Structured-output retries exhausted") from last_error

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        system_prompt = _prompt("trigger_system.txt")
        trigger_prompt_variant = getattr(
            self, "trigger_prompt_variant", "baseline"
        )
        if trigger_prompt_variant != "baseline":
            system_prompt += "\n\n" + _prompt(
                f"trigger_variant_{trigger_prompt_variant}.txt"
            )
        structured = self._parse(
            system_prompt,
            build_openai_trigger_payload(context),
            StructuredTriggerDecision,
            role="trigger",
        )
        decision = structured.to_domain()
        normalized_notice = (
            normalize_notice_clock_timesteps(decision.notice_interpretation, context)
            if decision.notice_interpretation is not None
            else None
        )
        normalized_decision = decision.model_copy(
            update={"notice_interpretation": normalized_notice}
        )
        effective = normalize_trigger_decision(
            normalized_decision,
            context,
            allow_numerical_fallback=self.allow_deterministic_trigger_fallback,
        )
        effective = apply_trigger_confidence_threshold(
            effective, getattr(self, "trigger_confidence_threshold", 0.0)
        )
        self.last_raw_trigger = decision
        self.last_trigger_guard_applied = decision.model_dump() != effective.model_dump()
        return effective

    def price(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        *,
        rerun_count: int,
        previous: PricingDecision | None,
        feedback: EvaluationFeedback | None,
    ) -> PricingDecision:
        prompt_name = (
            "pricing_selfish_system.txt"
            if context["mode"] == "selfish"
            else "pricing_altruistic_system.txt"
        )
        system_prompt = _prompt(prompt_name) + (
            "\n\nPYTHON MIGRATION COMPATIBILITY NOTE:\n"
            "The trigger schema emits event onset and recovery types. Treat "
            "energy_recovery, price_recovery, delay_recovery, and delay_removal as "
            "the restoration of the corresponding pre-disturbance assumptions."
        )
        user_data = {
            "mode": context["mode"],
            "timestep": context["timestep"],
            "planning_start_timestep": context.get("planning_start_timestep"),
            "remaining_timesteps": context["remaining_timesteps"],
            "remaining_hours": context["remaining_hours"],
            "trigger": trigger.model_dump(),
            "realtime_state": context["realtime_state"],
            "deviations": context["deviations"],
            "deviation_summary": context["deviation_summary"],
            "trigger_flags": context["trigger_flags"],
            "remaining_prices": context["intraday_prices"],
            "day_ahead_summary": context["day_ahead_summary"],
            "reoptimization_history": context["reoptimization_history"],
            "full_day_accounting": context.get("full_day_accounting", {}),
            "baseline_revenue_retention": context.get("revenue_neutrality", {}),
            "rerun_count": rerun_count,
            "previous_multipliers": previous.model_dump() if previous else None,
            "evaluator_feedback": feedback.model_dump() if feedback else None,
            "pricing_reference_guidance": build_pricing_reference(
                context, getattr(self, "pricing_guidance_variant", "base")
            ),
        }
        decision = self._parse(
            system_prompt,
            user_data,
            StructuredPricingDecision,
            role="pricing",
        )
        expected_length = int(context["remaining_timesteps"])
        actual_lengths = {
            "buy": len(decision.buy_multipliers),
            "sell": len(decision.sell_multipliers),
        }
        if actual_lengths != {"buy": expected_length, "sell": expected_length}:
            self.call_records[-1]["post_parse_normalization"] = {
                "kind": "pricing_array_length",
                "expected_length": expected_length,
                "actual_lengths": actual_lengths,
                "method": "truncate_or_extend_last_value",
            }
        normalized = normalize_pricing_decision(
            decision,
            context["mode"],
            expected_length,
        )
        effective, feedback_guard = enforce_evaluator_pricing_feedback(
            normalized,
            feedback=feedback,
            mode=context["mode"],
            planning_start_timestep=int(
                context.get("planning_start_timestep") or context["timestep"]
            ),
        )
        if feedback_guard is not None:
            self.call_records[-1]["post_parse_feedback_enforcement"] = feedback_guard
        return effective

    def evaluate(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        pricing: PricingDecision,
        result: dict[str, Any],
        *,
        rerun_count: int,
    ) -> EvaluationDecision:
        energy = result.get("energy") or []
        end_energy = [series[-1] if series else None for series in energy]
        battery_capacity = {
            int(key): float(value)
            for key, value in (
                context.get("fleet_constraints", {}).get(
                    "battery_capacity_kwh_by_bus", {}
                )
                or {}
            ).items()
        }
        user_data = {
            "mode": context["mode"],
            "timestep": context["timestep"],
            "planning_start_timestep": context.get("planning_start_timestep"),
            "remaining_timesteps": context["remaining_timesteps"],
            "rerun_count": rerun_count,
            "maximum_reruns": int(context["maximum_reruns"]),
            "trigger": trigger.model_dump(),
            "pricing": pricing.model_dump(),
            "optimization_result": result,
            "end_of_day_energy_kwh": {
                str(bus_id): value for bus_id, value in enumerate(end_energy, start=1)
            },
            "deviations": context["deviations"],
            "day_ahead_summary": context["day_ahead_summary"],
            "da_benchmark": context["da_benchmark"],
            "intraday_prices": context["intraday_prices"],
            "operational_messages": context.get("operational_notices", []),
            "operational_priority_policy": context.get(
                "operational_priority_policy", {}
            ),
            "fleet_constraints": context.get("fleet_constraints", {}),
            "full_day_accounting": context.get("full_day_accounting", {}),
            "baseline_revenue_retention": context.get("revenue_neutrality", {}),
        }
        decision = self._parse(
            _prompt("evaluator_system.txt"), user_data, EvaluationDecision, role="evaluator"
        )
        priority = decision.interpreted_priority
        assessment = assess_priority(
            result,
            priority,
            battery_capacity_kwh_by_bus=battery_capacity,
        )
        decision = decision.model_copy(update={"priority_assessment": assessment})
        if assessment is not None and assessment.applicable and not assessment.satisfied:
            assert priority is not None
            if getattr(self, "call_records", None):
                self.call_records[-1]["post_parse_normalization"] = {
                    "kind": "deterministic_operational_priority_assessment",
                    "interpreted_priority": priority.model_dump(),
                    "assessment": assessment.model_dump(),
                    "original_accept": decision.accept,
                }
            return decision.model_copy(
                update={
                    "accept": False,
                    "reasoning": (
                        f"The candidate is optimizer-usable but does not satisfy operator "
                        f"priority {priority.priority_id}: measured "
                        f"{assessment.measured_value} versus target "
                        f"{assessment.target_value}."
                    ),
                    "feedback": priority_feedback(
                        priority,
                        pricing,
                        planning_start_timestep=int(
                            context.get("planning_start_timestep") or 1
                        ),
                    ),
                }
            )
        if assessment is not None and assessment.applicable and assessment.satisfied:
            solver_status = str(result.get("solver_status", "")).lower()
            if not result.get("is_mock") and solver_status not in {
                "infeasible",
                "error",
                "unknown",
                "mock",
            }:
                return decision.model_copy(
                    update={
                        "accept": True,
                        "reasoning": (
                            f"The optimizer-usable candidate satisfies operator priority "
                            f"{priority.priority_id}; its projected full-day economic "
                            "premium is retained for reporting rather than used to erase "
                            "the explicit operator request."
                        ),
                        "feedback": NULL_FEEDBACK,
                    }
                )
        return decision


class EvidenceGatedAgentBackend(AgentBackend):
    """Call an LLM Trigger only when new causal evidence is available.

    The gate is deterministic: new public notice text or a changed observable
    physical-event telemetry state opens it. Pricing and evaluation calls are
    delegated unchanged after an optimization is triggered.
    """

    def __init__(self, backend: AgentBackend):
        self.backend = backend
        self._last_evidence_signature: str | None = None
        self._last_raw_trigger: TriggerDecision | None = None
        self._last_trigger_guard_applied = False
        self._last_trigger_was_gated = False

    @property
    def last_raw_trigger(self) -> TriggerDecision | None:
        if self._last_trigger_was_gated:
            return self._last_raw_trigger
        delegated = getattr(self.backend, "last_raw_trigger", None)
        return delegated if delegated is not None else self._last_raw_trigger

    @property
    def last_trigger_guard_applied(self) -> bool:
        if self._last_trigger_was_gated:
            return self._last_trigger_guard_applied
        return bool(
            getattr(
                self.backend,
                "last_trigger_guard_applied",
                self._last_trigger_guard_applied,
            )
        )

    @property
    def call_records(self) -> list[dict[str, Any]]:
        return getattr(self.backend, "call_records", [])

    @classmethod
    def _evidence_payload(cls, context: dict[str, Any]) -> dict[str, Any]:
        telemetry = context.get("numerical_event_telemetry") or {}
        return {
            "telemetry": {
                "return_delay_minutes_by_bus": telemetry.get(
                    "return_delay_minutes_by_bus", {}
                ),
                "charger_power_kw": telemetry.get("charger_power_kw", {}),
                "unavailable_chargers": telemetry.get("unavailable_chargers", []),
                "effective_timestep": telemetry.get("effective_timestep"),
                "expected_end_timestep": telemetry.get("expected_end_timestep"),
            },
        }

    @staticmethod
    def _has_material_evidence(payload: dict[str, Any]) -> bool:
        telemetry = payload["telemetry"]
        telemetry_active = bool(
            telemetry["return_delay_minutes_by_bus"]
            or telemetry["charger_power_kw"]
            or telemetry["unavailable_chargers"]
            or telemetry["effective_timestep"] is not None
            or telemetry["expected_end_timestep"] is not None
        )
        return telemetry_active

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        public_notices = context.get("operational_notices") or []
        evidence = self._evidence_payload(context)
        signature = json.dumps(evidence, sort_keys=True, default=str, separators=(",", ":"))
        changed = self._last_evidence_signature is not None and (
            signature != self._last_evidence_signature
        )
        first_material_evidence = (
            self._last_evidence_signature is None
            and self._has_material_evidence(evidence)
        )
        should_call = bool(public_notices) or changed or first_material_evidence
        self._last_evidence_signature = signature
        if should_call:
            self._last_trigger_was_gated = False
            self._last_raw_trigger = None
            self._last_trigger_guard_applied = False
            return self.backend.trigger(context)

        decision = TriggerDecision(
            action="skip",
            reasoning=(
                "Evidence gate: no new public notice or changed causal numerical "
                "event evidence is available."
            ),
            confidence=1.0,
            trigger_type="none",
            flagged_buses=[],
        )
        self._last_raw_trigger = decision
        self._last_trigger_guard_applied = False
        self._last_trigger_was_gated = True
        return decision

    def price(self, context, trigger, *, rerun_count, previous, feedback):
        return self.backend.price(
            context,
            trigger,
            rerun_count=rerun_count,
            previous=previous,
            feedback=feedback,
        )

    def evaluate(self, context, trigger, pricing, result, *, rerun_count):
        return self.backend.evaluate(
            context,
            trigger,
            pricing,
            result,
            rerun_count=rerun_count,
        )


class RuleBasedAgentBackend(AgentBackend):
    """Deterministic substitute used for tests and API-key-free local runs."""

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        timestep = int(context["timestep"])
        remaining = int(context["remaining_timesteps"])
        flags = context["trigger_flags"]
        history = context["reoptimization_history"]
        last_type = history.get("last_reopt_trigger_type")
        last_timestep = history.get("last_reopt_timestep")

        if timestep == 1 or remaining < 4 or last_timestep == timestep:
            return TriggerDecision(
                action="skip",
                reasoning="No actionable real-time deviation is available at this timestep.",
                confidence=1.0,
                trigger_type="none",
                flagged_buses=[],
            )
        notice = context.get("notice_interpretation")
        notice_flags = context.get("notice_flags", {})
        if notice is not None:
            recommendation = (
                notice.get("uncertainty_details") or {}
            ).get("recommended_action", "optimize")
            if (
                notice_flags.get("same_event_already_accounted")
                or not notice.get("material", True)
                or recommendation in {"wait", "request_confirmation"}
            ):
                return TriggerDecision(
                    action="skip",
                    reasoning=(
                        "The operational notice is informational, awaits confirmation, "
                        "or its unchanged state is already incorporated in the active plan."
                    ),
                    confidence=1.0,
                    trigger_type="none",
                    flagged_buses=[],
                    notice_interpretation=notice,
                )
            event_type = str(notice.get("event_type"))
            trigger_type = (
                "combined_notice"
                if event_type == "combined"
                else "charger_event"
                if event_type.startswith("charger_")
                else "service_notice"
            )
            return TriggerDecision(
                action="optimize",
                reasoning=f"A material {event_type} notice in phase {notice.get('phase')} changes optimizer inputs.",
                confidence=1.0,
                trigger_type=trigger_type,
                flagged_buses=[int(bus) for bus in notice.get("affected_buses", [])],
                notice_interpretation=notice,
            )
        if flags.get("energy_recovery_active"):
            return TriggerDecision(
                action="optimize",
                reasoning="The persistent energy-consumption disturbance has ended, so the active plan's route-energy assumptions must be restored.",
                confidence=0.98,
                trigger_type="energy_recovery",
                flagged_buses=[int(bus) for bus in flags.get("energy_event_buses", [])],
            )
        if flags.get("price_recovery_active"):
            return TriggerDecision(
                action="optimize",
                reasoning="The persistent price disturbance has ended, so the active plan's price assumptions must be restored.",
                confidence=0.98,
                trigger_type="price_recovery",
                flagged_buses=[],
            )
        if flags.get("delay_removal_active"):
            buses = [int(item["bus_id"]) for item in flags.get("severe_delay_buses", [])]
            return TriggerDecision(
                action="optimize",
                reasoning="The active delay assumption is being removed, so the current plan is outdated.",
                confidence=0.95,
                trigger_type="delay_removal",
                flagged_buses=buses,
            )
        if flags.get("delay_sign_reversed"):
            buses = [int(item["bus_id"]) for item in flags.get("delay_sign_reversed_buses", [])]
            return TriggerDecision(
                action="optimize",
                reasoning="A severe delay reversed direction after the prior delay-related re-optimization.",
                confidence=0.95,
                trigger_type="delay",
                flagged_buses=buses,
            )
        if flags.get("has_severe_delay") and last_type not in {"delay", "delay_removal", "delay_recovery"}:
            buses = [int(item["bus_id"]) for item in flags.get("severe_delay_buses", [])]
            return TriggerDecision(
                action="optimize",
                reasoning="At least one bus has a severe unaccounted delay.",
                confidence=0.95,
                trigger_type="delay",
                flagged_buses=buses,
            )
        summary = context["deviation_summary"]
        if summary.get("has_energy_disturbance"):
            return TriggerDecision(
                action="optimize",
                reasoning="Observed interval energy consumption differs materially from the forecast.",
                confidence=0.95,
                trigger_type="energy_disturbance",
                flagged_buses=[int(bus) for bus in summary.get("disturbed_energy_buses", [])],
            )
        if flags.get("price_deviation_significant") and remaining >= 6:
            return TriggerDecision(
                action="optimize",
                reasoning="Observed price differs from the forecast by more than the configured threshold.",
                confidence=0.95,
                trigger_type="price",
                flagged_buses=[],
            )
        if flags.get("unexpected_discharging_buses"):
            return TriggerDecision(
                action="optimize",
                reasoning="A bus is discharging while its observed energy no longer matches the active plan.",
                confidence=0.9,
                trigger_type="deviation",
                flagged_buses=[int(bus) for bus in flags["unexpected_discharging_buses"]],
            )
        if flags.get("has_high_energy_deviation") or flags.get("multi_bus_moderate_deviation"):
            items = flags.get("high_energy_deviation_buses") or flags.get("moderate_deviation_buses") or []
            return TriggerDecision(
                action="optimize",
                reasoning="Fleet battery energy has drifted materially from the active plan.",
                confidence=0.9,
                trigger_type="deviation",
                flagged_buses=[int(item["bus_id"]) for item in items],
            )
        return TriggerDecision(
            action="skip",
            reasoning="All monitored deviations remain within the workflow thresholds.",
            confidence=0.95,
            trigger_type="none",
            flagged_buses=[],
        )

    def price(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        *,
        rerun_count: int,
        previous: PricingDecision | None,
        feedback: EvaluationFeedback | None,
    ) -> PricingDecision:
        mode = context["mode"]
        rows = context["intraday_prices"]["prices"]
        reference = build_pricing_reference(context)["current_horizon"]
        buy = list(reference["buy_multipliers"])
        sell = list(reference["sell_multipliers"])
        if previous and rerun_count > 0:
            buy = list(previous.buy_multipliers)
            sell = list(previous.sell_multipliers)
            if feedback and feedback.operational_priority is not None:
                priority = feedback.operational_priority
                planning_start = int(context.get("planning_start_timestep") or 1)
                first = max(0, priority.timestep_start - planning_start)
                last = min(
                    len(buy), priority.timestep_end - planning_start + 1
                )
                for index in range(first, max(first, last)):
                    if priority.objective in {
                        "preserve_bus_reserve",
                        "frontload_site_charging",
                    }:
                        buy[index] = max(1.01, buy[index] - 0.05)
                    if priority.objective == "preserve_bus_reserve":
                        sell[index] = max(0.40, sell[index] - 0.05)
                    elif priority.objective == "prioritize_v2g_export":
                        sell[index] = min(0.99, sell[index] + 0.05)
            elif feedback and feedback.reason == "infeasible":
                buy = [1.03] * len(buy)
                sell = [0.65] * len(sell)
            elif feedback and feedback.reason == "cost_too_high":
                buy = [max(1.01, value - 0.05) for value in buy]
            elif (
                feedback
                and feedback.reason == "revenue_too_low"
                and mode == "altruistic"
            ):
                # The structured revenue-neutrality adjustment is applied below.
                # Do not run the legacy cost-reduction fallback, which would move
                # margins in the wrong direction for a revenue shortfall.
                pass
            elif feedback and feedback.reason == "v2g_unused":
                sell = [min(0.97, value + 0.05) for value in sell]
            else:
                buy = [max(1.01, value - 0.01) for value in buy]
                sell = [min(0.97, value + 0.02) for value in sell]
        decision = PricingDecision(
            buy_multipliers=buy,
            sell_multipliers=sell,
            reasoning=f"Deterministic {mode} pricing policy for {len(rows)} remaining timesteps.",
            confidence=1.0,
        )
        normalized = normalize_pricing_decision(
            decision, mode, int(context["remaining_timesteps"])
        )
        effective, _ = enforce_evaluator_pricing_feedback(
            normalized,
            feedback=feedback,
            mode=mode,
            planning_start_timestep=int(
                context.get("planning_start_timestep") or context["timestep"]
            ),
        )
        return effective

    def evaluate(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        pricing: PricingDecision,
        result: dict[str, Any],
        *,
        rerun_count: int,
    ) -> EvaluationDecision:
        if result.get("is_mock"):
            feedback = EvaluationFeedback(
                reason="infeasible" if result.get("solver_status") in {None, "unknown", "infeasible", "mock"} else "solver_error",
                buy_multiplier_adjustment=None,
                sell_multiplier_adjustment=None,
                period_adjustment="Set all buy multipliers to 1.03 and sell multipliers to 0.65.",
                priority="mock_recovery",
            )
            return EvaluationDecision(
                accept=False,
                reasoning="The optimizer returned a mock result, so another attempt is required.",
                confidence=1.0,
                feedback=feedback,
            )
        mode = context["mode"]
        benchmark = context["da_benchmark"]
        accounting = context.get("full_day_accounting") or {}
        if accounting.get("incumbent_remaining_valid"):
            if mode == "selfish":
                result_value = float(
                    result.get("projected_full_day_aggregator_revenue") or 0
                )
                target = float(
                    accounting.get(
                        "projected_full_day_incumbent_aggregator_revenue"
                    )
                    or 0
                )
                accept = result_value >= target - 1e-3
                reason = "The projected full-day revenue is no worse than the incumbent schedule."
                feedback_reason = "revenue_too_low"
            else:
                result_value = float(result.get("projected_full_day_pto_cost") or 0)
                target = float(
                    accounting.get("projected_full_day_incumbent_pto_cost") or 0
                )
                accept = result_value <= target + 1e-3
                reason = "The projected full-day PTO cost is no worse than the incumbent schedule."
                feedback_reason = "cost_too_high"
        elif benchmark.get("da_benchmark_valid"):
            if mode == "selfish":
                result_value = float(
                    result.get("projected_full_day_aggregator_revenue") or 0
                )
                target = float(
                    benchmark.get("projected_full_day_da_aggregator_revenue") or 0
                )
                accept = result_value >= 0.90 * target
                reason = "The projected full-day revenue is acceptable relative to the full-day day-ahead benchmark."
                feedback_reason = "revenue_too_low"
            else:
                result_value = float(result.get("projected_full_day_pto_cost") or 0)
                target = float(
                    benchmark.get("projected_full_day_da_pto_cost") or 0
                )
                accept = (
                    result_value <= 1.10 * target
                    if target > 0
                    else result_value <= target + 1e-3
                )
                reason = "The projected full-day PTO cost is acceptable relative to the full-day day-ahead benchmark."
                feedback_reason = "cost_too_high"
        else:
            accept = True
            reason = "The optimization is feasible and no valid like-for-like benchmark is available."
            feedback_reason = "cost_too_high" if mode == "altruistic" else "revenue_too_low"
        feedback = NULL_FEEDBACK if accept else EvaluationFeedback(
            reason=feedback_reason,
            buy_multiplier_adjustment=None,
            sell_multiplier_adjustment=None,
            period_adjustment="Move the multipliers toward the day-ahead baseline.",
            priority="cost_reduction",
        )
        return EvaluationDecision(
            accept=accept,
            reasoning=reason,
            confidence=1.0,
            feedback=feedback,
        )


class FixedPlanAgentBackend(RuleBasedAgentBackend):
    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        return TriggerDecision(
            action="skip",
            reasoning="Fixed day-ahead configuration: adaptive optimization is disabled.",
            confidence=1.0,
            trigger_type="none",
            flagged_buses=[],
        )


class NoticeOnlyAgentBackend(RuleBasedAgentBackend):
    """Trigger exclusively from the interpretation supplied by its text path."""

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        if context.get("notice_interpretation") is None:
            return TriggerDecision(
                action="skip",
                reasoning="No new interpreted operational notice is available.",
                confidence=1.0,
                trigger_type="none",
                flagged_buses=[],
            )
        return super().trigger(context)


class NumericalOnlyAgentBackend(RuleBasedAgentBackend):
    """Trigger from numerical deviations without access to notice semantics."""

    def __init__(self) -> None:
        self._active_telemetry_signature: str | None = None

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        numerical_context = dict(context)
        numerical_context.pop("notice_interpretation", None)
        numerical_context.pop("notice_flags", None)
        numerical_context["operational_notices"] = []
        numerical_context["active_operational_events"] = []
        numerical_context["notice_event_memory"] = []
        timestep = int(context["timestep"])
        telemetry = context.get("numerical_event_telemetry") or {}
        return_delay = {
            int(key): int(value)
            for key, value in (telemetry.get("return_delay_minutes_by_bus") or {}).items()
        }
        power = {
            int(key): float(value)
            for key, value in (telemetry.get("charger_power_kw") or {}).items()
        }
        unavailable = sorted(
            int(value) for value in (telemetry.get("unavailable_chargers") or [])
        )
        signature = json.dumps(
            {
                "return_delay": return_delay,
                "power": power,
                "unavailable": unavailable,
            },
            sort_keys=True,
        )
        nominal_signature = json.dumps(
            {"return_delay": {}, "power": {}, "unavailable": []}, sort_keys=True
        )
        if signature != nominal_signature and signature != self._active_telemetry_signature:
            self._active_telemetry_signature = signature
            has_bus = bool(return_delay)
            has_charger = bool(power or unavailable)
            event_type = (
                "combined"
                if has_bus and has_charger
                else "charger_fault"
                if unavailable
                else "charger_derating"
                if has_charger
                else "service_delay"
            )
            interpretation = NoticeInterpretation(
                event_id="NUMERICAL-SENSOR-EVENT",
                source_type=("combined" if has_bus and has_charger else "ocpp" if has_charger else "service_alert"),
                event_type=event_type,
                phase="onset",
                affected_buses=sorted(return_delay),
                affected_chargers=sorted(set(power) | set(unavailable)),
                effective_timestep=int(
                    telemetry.get("effective_timestep") or timestep
                ),
                expected_end_timestep=(
                    int(telemetry["expected_end_timestep"])
                    if telemetry.get("expected_end_timestep") is not None
                    else None
                ),
                updates=NoticeParameterUpdates(
                    return_delay_minutes_by_bus=return_delay,
                    charger_power_kw=power,
                    unavailable_chargers=unavailable,
                ),
                evidence=["causal_numerical_telemetry_v1"],
            )
            return TriggerDecision(
                action="optimize",
                reasoning="A new stateful numerical estimator event was detected from causal charger or return telemetry.",
                confidence=1.0,
                trigger_type=(
                    "combined_notice" if has_bus and has_charger else "charger_event" if has_charger else "delay"
                ),
                flagged_buses=sorted(return_delay),
                notice_interpretation=interpretation,
            )
        if signature == nominal_signature and self._active_telemetry_signature is not None:
            self._active_telemetry_signature = None
            interpretation = NoticeInterpretation(
                event_id="NUMERICAL-SENSOR-EVENT",
                source_type="combined",
                event_type="combined",
                phase="recovery",
                effective_timestep=timestep,
                updates=NoticeParameterUpdates(),
                evidence=["causal_numerical_telemetry_recovery_v1"],
            )
            return TriggerDecision(
                action="optimize",
                reasoning="The stateful numerical estimator detected recovery to nominal telemetry.",
                confidence=1.0,
                trigger_type="combined_notice",
                flagged_buses=[],
                notice_interpretation=interpretation,
            )
        return TriggerDecision(
            action="skip",
            reasoning=(
                "The stateful numerical estimator has no new causal charger-capacity "
                "or return-time event; unchanged telemetry does not retrigger."
            ),
            confidence=1.0,
            trigger_type="none",
            flagged_buses=[],
        )


class HardCheckAgentBackend(RuleBasedAgentBackend):
    """Evaluator-removal comparator: only solver and feasibility guards remain."""

    def evaluate(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        pricing: PricingDecision,
        result: dict[str, Any],
        *,
        rerun_count: int,
    ) -> EvaluationDecision:
        accepted = not bool(result.get("is_mock")) and str(result.get("solver_status", "")).lower() not in {
            "infeasible",
            "error",
            "unknown",
        }
        return EvaluationDecision(
            accept=accepted,
            reasoning="Accepted by deterministic solver/feasibility checks only." if accepted else "Rejected by deterministic solver/feasibility checks.",
            confidence=1.0,
            feedback=NULL_FEEDBACK if accepted else EvaluationFeedback(
                reason="infeasible",
                buy_multiplier_adjustment=None,
                sell_multiplier_adjustment=None,
                period_adjustment=None,
                priority="mock_recovery",
            ),
        )


def _deterministic_priority_evaluation(
    context: dict[str, Any],
    trigger: TriggerDecision,
    pricing: PricingDecision,
    result: dict[str, Any],
    *,
    rerun_count: int,
    priority: OperationalPriority | None,
) -> EvaluationDecision:
    """Apply one common numerical assessment after any interpretation path."""

    battery_capacity = {
        int(key): float(value)
        for key, value in (
            context.get("fleet_constraints", {}).get(
                "battery_capacity_kwh_by_bus", {}
            )
            or {}
        ).items()
    }
    assessment = assess_priority(
        result,
        priority,
        battery_capacity_kwh_by_bus=battery_capacity,
    )
    if assessment is not None and assessment.applicable and not assessment.satisfied:
        assert priority is not None
        return EvaluationDecision(
            accept=False,
            reasoning=(
                f"Operator priority {priority.priority_id} is not satisfied: "
                f"measured {assessment.measured_value} versus target "
                f"{assessment.target_value}."
            ),
            confidence=1.0,
            feedback=priority_feedback(
                priority,
                pricing,
                planning_start_timestep=int(
                    context.get("planning_start_timestep") or 1
                ),
            ),
            interpreted_priority=priority,
            priority_assessment=assessment,
        )
    if assessment is not None and assessment.applicable and assessment.satisfied:
        return EvaluationDecision(
            accept=True,
            reasoning=(
                f"Operator priority {priority.priority_id} is satisfied. The "
                "projected full-day economic premium is reported separately."
            ),
            confidence=1.0,
            feedback=NULL_FEEDBACK,
            interpreted_priority=priority,
            priority_assessment=assessment,
        )
    economic = RuleBasedAgentBackend().evaluate(
        context,
        trigger,
        pricing,
        result,
        rerun_count=rerun_count,
    )
    return economic.model_copy(
        update={
            "interpreted_priority": priority,
            "priority_assessment": assessment,
        }
    )


class RuleTextPriorityEvaluatorBackend(RuleBasedAgentBackend):
    """Non-LLM evaluator that consumes the same raw public text as the LLM."""

    def evaluate(self, context, trigger, pricing, result, *, rerun_count):
        priority = frozen_priority_parse(
            context.get("operational_notices", []),
            planning_start_timestep=int(
                context.get("planning_start_timestep") or 1
            ),
        )
        return _deterministic_priority_evaluation(
            context,
            trigger,
            pricing,
            result,
            rerun_count=rerun_count,
            priority=priority,
        )


class StructuredPriorityEvaluatorBackend(RuleBasedAgentBackend):
    """Labelled oracle: receives the canonical structured operator priority."""

    def evaluate(self, context, trigger, pricing, result, *, rerun_count):
        priorities = context.get("benchmark_canonical_priorities") or []
        priority = (
            OperationalPriority.model_validate(priorities[0]) if priorities else None
        )
        return _deterministic_priority_evaluation(
            context,
            trigger,
            pricing,
            result,
            rerun_count=rerun_count,
            priority=priority,
        )


class LLMPriorityEvaluatorBackend(AgentBackend):
    """Use an LLM only to interpret text, then share the deterministic scorer."""

    def __init__(self, backend: OpenAIAgentBackend):
        self.backend = backend

    @property
    def call_records(self) -> list[dict[str, Any]]:
        return self.backend.call_records

    def trigger(self, context):
        return self.backend.trigger(context)

    def price(self, context, trigger, *, rerun_count, previous, feedback):
        return self.backend.price(
            context,
            trigger,
            rerun_count=rerun_count,
            previous=previous,
            feedback=feedback,
        )

    def evaluate(self, context, trigger, pricing, result, *, rerun_count):
        interpreted = self.backend.evaluate(
            context,
            trigger,
            pricing,
            result,
            rerun_count=rerun_count,
        )
        return _deterministic_priority_evaluation(
            context,
            trigger,
            pricing,
            result,
            rerun_count=rerun_count,
            priority=interpreted.interpreted_priority,
        )


class CompositeAgentBackend(AgentBackend):
    """Role-level composition used by the prespecified ablation configurations."""

    def __init__(
        self,
        trigger_backend: AgentBackend,
        pricing_backend: AgentBackend,
        evaluator_backend: AgentBackend,
    ):
        self.trigger_backend = trigger_backend
        self.pricing_backend = pricing_backend
        self.evaluator_backend = evaluator_backend

    @property
    def last_raw_trigger(self) -> TriggerDecision | None:
        return getattr(self.trigger_backend, "last_raw_trigger", None)

    @property
    def last_trigger_guard_applied(self) -> bool:
        return bool(getattr(self.trigger_backend, "last_trigger_guard_applied", False))

    @property
    def call_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[int] = set()
        for backend in (self.trigger_backend, self.pricing_backend, self.evaluator_backend):
            if id(backend) in seen:
                continue
            seen.add(id(backend))
            records.extend(getattr(backend, "call_records", []))
        return records

    def trigger(self, context: dict[str, Any]) -> TriggerDecision:
        return self.trigger_backend.trigger(context)

    def price(self, context, trigger, *, rerun_count, previous, feedback):
        return self.pricing_backend.price(
            context, trigger, rerun_count=rerun_count, previous=previous, feedback=feedback
        )

    def evaluate(self, context, trigger, pricing, result, *, rerun_count):
        return self.evaluator_backend.evaluate(
            context, trigger, pricing, result, rerun_count=rerun_count
        )


def describe_agent_roles(backend: AgentBackend) -> dict[str, dict[str, Any]]:
    """Return non-secret, machine-readable provenance for each agent role."""

    if isinstance(backend, CompositeAgentBackend):
        role_backends = {
            "trigger": backend.trigger_backend,
            "pricing": backend.pricing_backend,
            "evaluator": backend.evaluator_backend,
        }
    else:
        role_backends = {role: backend for role in ("trigger", "pricing", "evaluator")}

    descriptions: dict[str, dict[str, Any]] = {}
    for role, role_backend in role_backends.items():
        wrapped_by_evidence_gate = isinstance(
            role_backend, EvidenceGatedAgentBackend
        )
        evidence_gate = role == "trigger" and wrapped_by_evidence_gate
        resolved = (
            role_backend.backend if wrapped_by_evidence_gate else role_backend
        )
        description: dict[str, Any] = {
            "backend": type(resolved).__name__,
            "evidence_gate": evidence_gate,
        }
        if isinstance(resolved, OpenAIAgentBackend):
            description.update(
                {
                    "model": resolved.model,
                    "trigger_prompt_variant": getattr(
                        resolved, "trigger_prompt_variant", "baseline"
                    ),
                    "trigger_confidence_threshold": (
                        getattr(resolved, "trigger_confidence_threshold", 0.0)
                    ),
                    "pricing_guidance_variant": getattr(
                        resolved, "pricing_guidance_variant", "base"
                    ),
                    "deterministic_numerical_trigger_fallback": (
                        resolved.allow_deterministic_trigger_fallback
                    ),
                }
            )
        elif isinstance(resolved, LLMPriorityEvaluatorBackend):
            description.update(
                {
                    "model": resolved.backend.model,
                    "deterministic_numerical_trigger_fallback": False,
                    "llm_scope": "raw_priority_text_interpretation_only",
                    "schedule_scoring": "deterministic",
                }
            )
        descriptions[role] = description
    return descriptions


def normalize_trigger_decision(
    decision: TriggerDecision,
    context: dict[str, Any],
    *,
    allow_numerical_fallback: bool = True,
) -> TriggerDecision:
    """Enforce trigger invariants and recover clear rule-detected events."""
    deterministic = RuleBasedAgentBackend().trigger(context)
    timestep = int(context["timestep"])
    remaining = int(context["remaining_timesteps"])
    last_timestep = context["reoptimization_history"].get("last_reopt_timestep")

    if timestep == 1 or remaining < 4 or last_timestep == timestep:
        return deterministic
    notice = decision.notice_interpretation
    if notice is not None:
        recommendation = notice.uncertainty_details.recommended_action
        if decision.action == "optimize" and (
            recommendation in {"wait", "request_confirmation"}
            or notice.phase in {"warning", "stable"}
            or not notice.material
        ):
            return decision.model_copy(
                update={
                    "action": "skip",
                    "trigger_type": "none",
                    "flagged_buses": [],
                    "reasoning": (
                        "Structured-decision guard skipped optimization because the "
                        f"notice is phase={notice.phase}, material={notice.material}, "
                        f"and recommends {recommendation}."
                    ),
                }
            )
        if (
            decision.action == "skip"
            and recommendation == "optimize"
            and notice.material
            and notice.phase in {"onset", "severity_change", "recovery"}
        ):
            trigger_type = (
                "combined_notice"
                if notice.event_type == "combined"
                else "charger_event"
                if notice.event_type.startswith("charger_")
                else "service_notice"
            )
            return decision.model_copy(
                update={
                    "action": "optimize",
                    "trigger_type": trigger_type,
                    "flagged_buses": notice.affected_buses,
                    "reasoning": (
                        "Structured-decision guard triggered optimization because "
                        "the model's normalized material notice recommends optimize."
                    ),
                }
            )
    if (
        allow_numerical_fallback
        and decision.action == "skip"
        and deterministic.action == "optimize"
    ):
        return deterministic.model_copy(
            update={
                "reasoning": (
                    "Deterministic safety guard overrode an LLM skip because the supplied "
                    f"context contains an actionable {deterministic.trigger_type} event."
                )
            }
        )
    notice_already_accounted = bool(
        context["trigger_flags"].get("same_event_already_accounted")
        or context.get("notice_flags", {}).get("same_event_already_accounted")
    )
    if notice is not None and notice.phase in {"persistence", "stable"}:
        remembered_event_ids = {
            str(item.get("event_id"))
            for item in context.get("notice_event_memory", [])
            if item.get("event_id") is not None and item.get("incorporated", True)
        }
        current_event_ids = set(notice.event_id.split("+"))
        notice_already_accounted = notice_already_accounted or bool(
            current_event_ids and current_event_ids.issubset(remembered_event_ids)
        )
    if (
        decision.action == "optimize"
        and deterministic.action == "skip"
        and notice_already_accounted
    ):
        return deterministic.model_copy(
            update={
                "reasoning": (
                    "Deterministic event-memory guard overrode a duplicate LLM optimization: "
                    "the continuing disturbance is already represented in the active plan."
                )
            }
        )
    if decision.action == "skip":
        return decision.model_copy(update={"trigger_type": "none", "flagged_buses": []})
    if decision.trigger_type == "none":
        fallback_type = (
            deterministic.trigger_type if deterministic.action == "optimize" else "trend"
        )
        return decision.model_copy(update={"trigger_type": fallback_type})
    return decision


def apply_trigger_confidence_threshold(
    decision: TriggerDecision, threshold: float
) -> TriggerDecision:
    """Apply a logged deployment threshold without changing interpretation fields."""

    threshold = validate_trigger_confidence_threshold(threshold)
    if decision.action != "optimize" or decision.confidence >= threshold:
        return decision
    return decision.model_copy(
        update={
            "action": "skip",
            "trigger_type": "none",
            "flagged_buses": [],
            "reasoning": (
                f"Confidence-threshold policy held the proposed optimization: "
                f"reported confidence {decision.confidence:.3f} is below the "
                f"prespecified threshold {threshold:.3f}. The notice interpretation "
                "is retained for audit and confirmation."
            ),
        }
    )


def normalize_pricing_decision(
    decision: PricingDecision | StructuredPricingDecision,
    mode: str,
    remaining_timesteps: int,
) -> PricingDecision:
    def exact_length(values: list[float], fallback: float) -> list[float]:
        if not values:
            return [fallback] * remaining_timesteps
        result = list(values[:remaining_timesteps])
        result.extend([result[-1]] * (remaining_timesteps - len(result)))
        return result

    buy = exact_length(decision.buy_multipliers, 1.05)
    sell = exact_length(decision.sell_multipliers, 0.80)
    buy_upper = 1.50 if mode == "selfish" else 1.20
    sell_lower = 0.40 if mode == "selfish" else 0.55
    for index in range(remaining_timesteps):
        buy[index] = min(buy_upper, max(1.01, float(buy[index])))
        sell[index] = min(0.99, max(sell_lower, float(sell[index])))
        if mode == "selfish" and index < 6:
            buy[index] = min(buy[index], 1.10)
        if remaining_timesteps <= 10:
            buy[index] = min(buy[index], 1.15)
        sell[index] = min(sell[index], buy[index] - 0.01)
    return PricingDecision(
        buy_multipliers=buy,
        sell_multipliers=sell,
        reasoning=decision.reasoning,
        confidence=decision.confidence,
    )


def enforce_evaluator_pricing_feedback(
    decision: PricingDecision,
    *,
    feedback: EvaluationFeedback | None,
    mode: str,
    planning_start_timestep: int,
) -> tuple[PricingDecision, dict[str, Any] | None]:
    """Make a rerun's numeric arrays honor its explicit evaluator adjustment.

    The Evaluator chooses the side, executable window, direction, and target. This
    guard only repairs a schema-valid Pricing response whose numbers contradict that
    structured instruction (for example, reasoning says 0.72 -> 0.75 but the array
    remains unchanged). More aggressive Agent changes in the requested direction are
    preserved.
    """

    if feedback is None:
        return decision, None

    buy = list(decision.buy_multipliers)
    sell = list(decision.sell_multipliers)
    applied: list[dict[str, Any]] = []
    for side, adjustment in (
        ("buy", feedback.buy_multiplier_adjustment),
        ("sell", feedback.sell_multiplier_adjustment),
    ):
        if adjustment is None:
            continue
        values = buy if side == "buy" else sell
        first = max(0, adjustment.timestep_start - planning_start_timestep)
        last = min(
            len(values), adjustment.timestep_end - planning_start_timestep + 1
        )
        changed_timesteps: list[int] = []
        for index in range(first, max(first, last)):
            before = values[index]
            if adjustment.direction == "raise" and before < adjustment.target_value:
                values[index] = adjustment.target_value
            elif adjustment.direction == "lower" and before > adjustment.target_value:
                values[index] = adjustment.target_value
            if values[index] != before:
                changed_timesteps.append(planning_start_timestep + index)
        if changed_timesteps:
            applied.append(
                {
                    "side": side,
                    "direction": adjustment.direction,
                    "target_value": adjustment.target_value,
                    "changed_timesteps": changed_timesteps,
                }
            )

    if not applied:
        return decision, None
    repaired = PricingDecision(
        buy_multipliers=buy,
        sell_multipliers=sell,
        reasoning=(
            decision.reasoning
            + " Deterministic feedback-compliance guard applied the Evaluator's "
            "explicit structured multiplier target where the numeric array did not."
        ),
        confidence=decision.confidence,
    )
    effective = normalize_pricing_decision(repaired, mode, len(buy))
    return effective, {
        "kind": "evaluator_feedback_compliance",
        "method": "enforce_explicit_direction_and_target_only",
        "adjustments": applied,
    }


def create_agent_backend(
    name: str,
    model: str,
    *,
    trigger_prompt_variant: str = "baseline",
    trigger_confidence_threshold: float = 0.0,
    pricing_guidance_variant: str = "base",
) -> AgentBackend:
    resolved = name
    if name == "auto":
        resolved = "openai" if os.environ.get("OPENAI_API_KEY") else "rule"
    if resolved == "openai":
        return OpenAIAgentBackend(
            model=model,
            trigger_prompt_variant=trigger_prompt_variant,
            trigger_confidence_threshold=trigger_confidence_threshold,
            pricing_guidance_variant=pricing_guidance_variant,
        )
    if resolved == "rule":
        return RuleBasedAgentBackend()
    raise ValueError(f"Unsupported agent backend: {name}")


def create_experiment_backend(
    configuration: str,
    legacy_backend: str,
    model: str,
    *,
    trigger_prompt_variant: str = "baseline",
    trigger_confidence_threshold: float = 0.0,
    pricing_guidance_variant: str = "base",
) -> AgentBackend:
    def llm_backend() -> OpenAIAgentBackend:
        backend = OpenAIAgentBackend(
            model=model,
            allow_deterministic_trigger_fallback=False,
        )
        backend.trigger_prompt_variant = validate_trigger_prompt_variant(
            trigger_prompt_variant
        )
        backend.trigger_confidence_threshold = validate_trigger_confidence_threshold(
            trigger_confidence_threshold
        )
        backend.pricing_guidance_variant = validate_pricing_guidance_variant(
            pricing_guidance_variant
        )
        return backend

    if configuration == "legacy":
        return create_agent_backend(
            legacy_backend,
            model,
            trigger_prompt_variant=trigger_prompt_variant,
            trigger_confidence_threshold=trigger_confidence_threshold,
            pricing_guidance_variant=pricing_guidance_variant,
        )
    rule = RuleBasedAgentBackend()
    if configuration == "fixed_da_plan":
        return FixedPlanAgentBackend()
    if configuration in {
        "structured_reference",
        "oracle_event_trigger",
        "rule_text_event_trigger",
    }:
        return NoticeOnlyAgentBackend()
    if configuration == "numerical_event_trigger":
        return NumericalOnlyAgentBackend()
    if configuration == "full_deterministic":
        # The frozen comparison protocol requires the same evidence-change gate
        # for Agent and non-Agent triggers.  Without it, a persistent deviation
        # is treated as a new event every interval, creating trigger chattering
        # that is unrelated to the presence or absence of an LLM.
        return EvidenceGatedAgentBackend(rule)
    if configuration == "agent_trigger_only":
        trigger_llm = EvidenceGatedAgentBackend(llm_backend())
        return CompositeAgentBackend(trigger_llm, rule, rule)
    if configuration == "pricing_agent_only":
        return CompositeAgentBackend(
            NoticeOnlyAgentBackend(), llm_backend(), HardCheckAgentBackend()
        )
    if configuration in {
        "agent_evaluator_raw_text",
        "rule_text_evaluator",
        "structured_evaluator_oracle",
        "evaluator_removal_control",
    }:
        trigger = NoticeOnlyAgentBackend()
        pricing = RuleBasedAgentBackend()
        if configuration == "agent_evaluator_raw_text":
            evaluator: AgentBackend = LLMPriorityEvaluatorBackend(
                llm_backend()
            )
        elif configuration == "rule_text_evaluator":
            evaluator = RuleTextPriorityEvaluatorBackend()
        elif configuration == "structured_evaluator_oracle":
            evaluator = StructuredPriorityEvaluatorBackend()
        else:
            evaluator = HardCheckAgentBackend()
        return CompositeAgentBackend(trigger, pricing, evaluator)
    llm = EvidenceGatedAgentBackend(llm_backend())
    if configuration == "full_agentic":
        return llm
    if configuration == "rule_parser_trigger_substitution":
        return CompositeAgentBackend(EvidenceGatedAgentBackend(rule), llm, llm)
    if configuration in {
        "mathematical_pricing_substitution",
        "deterministic_pricing_substitution",
    }:
        return CompositeAgentBackend(llm, rule, llm)
    if configuration == "evaluator_removal":
        return CompositeAgentBackend(llm, llm, HardCheckAgentBackend())
    raise ValueError(f"Unsupported experiment configuration: {configuration}")

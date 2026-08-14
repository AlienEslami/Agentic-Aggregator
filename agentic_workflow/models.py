from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoticeParameterUpdates(StrictModel):
    """Optimizer-facing updates extracted from one operational notice."""

    delay_minutes_by_bus: dict[int, int] = Field(default_factory=dict)
    return_delay_minutes_by_bus: dict[int, int] = Field(default_factory=dict)
    energy_multiplier_by_bus: dict[int, float] = Field(default_factory=dict)
    charger_power_kw: dict[int, float] = Field(default_factory=dict)
    unavailable_chargers: list[int] = Field(default_factory=list)


class UncertainParameterEstimate(StrictModel):
    """One uncertain operational parameter and its deterministic selected value."""

    parameter: Literal[
        "delay_minutes",
        "return_delay_minutes",
        "energy_multiplier",
        "charger_power_kw",
        "charger_unavailability_probability",
    ]
    asset_id: int = Field(ge=1)
    lower_bound: float
    upper_bound: float
    selected_value: float | None = None
    unit: Literal["minutes", "multiplier", "kw", "probability"]
    selection_policy: Literal[
        "conservative_upper",
        "conservative_lower",
        "confirmed_unavailable",
        "no_update_pending_confirmation",
        "restored_nominal",
    ]

    @model_validator(mode="after")
    def valid_bounds_and_selection(self) -> "UncertainParameterEstimate":
        if self.lower_bound > self.upper_bound:
            raise ValueError("lower_bound must not exceed upper_bound")
        if self.selected_value is not None and not (
            self.lower_bound <= self.selected_value <= self.upper_bound
        ):
            raise ValueError("selected_value must lie within the stated range")
        return self


class NoticeUncertaintyAssessment(StrictModel):
    """Decision-relevant uncertainty retained beside deterministic optimizer inputs."""

    confidence_level: float = Field(default=1.0, ge=0.0, le=1.0)
    provisional: bool = False
    conflicting_evidence: list[str] = Field(default_factory=list)
    estimates: list[UncertainParameterEstimate] = Field(default_factory=list)
    recommended_action: Literal[
        "optimize", "wait", "request_confirmation"
    ] = "optimize"
    rationale: str = "No material uncertainty reported."


class NoticeInterpretation(StrictModel):
    """Common schema emitted by manual, rule-parser, and LLM information paths."""

    event_id: str
    source_type: Literal[
        "service_alert", "ocpp", "driver_chat", "combined", "informational"
    ]
    event_type: Literal[
        "service_delay",
        "route_energy_change",
        "charger_fault",
        "charger_derating",
        "combined",
        "informational",
    ]
    phase: Literal[
        "warning", "onset", "persistence", "severity_change", "recovery", "stable"
    ]
    affected_buses: list[int] = Field(default_factory=list)
    affected_chargers: list[int] = Field(default_factory=list)
    effective_timestep: int = Field(ge=1, le=48)
    expected_end_timestep: int | None = Field(default=None, ge=1, le=48)
    uncertainty: bool = False
    uncertainty_details: NoticeUncertaintyAssessment = Field(
        default_factory=NoticeUncertaintyAssessment
    )
    material: bool = True
    updates: NoticeParameterUpdates = Field(default_factory=NoticeParameterUpdates)
    evidence: list[str] = Field(default_factory=list)


class TriggerDecision(StrictModel):
    action: Literal["optimize", "skip"]
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    trigger_type: Literal[
        "deviation",
        "price",
        "price_recovery",
        "energy_disturbance",
        "energy_recovery",
        "delay",
        "delay_recovery",
        "delay_removal",
        "service_notice",
        "charger_event",
        "combined_notice",
        "trend",
        "none",
    ]
    flagged_buses: list[int]
    notice_interpretation: NoticeInterpretation | None = None


class IntegerAssetUpdate(StrictModel):
    """One integer-valued update in the API-compatible wire schema."""

    asset_id: int = Field(ge=1)
    value: int


class NumericAssetUpdate(StrictModel):
    """One numeric update in the API-compatible wire schema."""

    asset_id: int = Field(ge=1)
    value: float


class StructuredNoticeParameterUpdates(StrictModel):
    """Strict-output representation that avoids arbitrary JSON object keys."""

    delay_minutes_by_bus: list[IntegerAssetUpdate] = Field(default_factory=list)
    return_delay_minutes_by_bus: list[IntegerAssetUpdate] = Field(default_factory=list)
    energy_multiplier_by_bus: list[NumericAssetUpdate] = Field(default_factory=list)
    charger_power_kw: list[NumericAssetUpdate] = Field(default_factory=list)
    unavailable_chargers: list[int] = Field(default_factory=list)

    def to_domain(self) -> NoticeParameterUpdates:
        return NoticeParameterUpdates(
            delay_minutes_by_bus={item.asset_id: item.value for item in self.delay_minutes_by_bus},
            return_delay_minutes_by_bus={
                item.asset_id: item.value
                for item in self.return_delay_minutes_by_bus
            },
            energy_multiplier_by_bus={
                item.asset_id: item.value for item in self.energy_multiplier_by_bus
            },
            charger_power_kw={item.asset_id: item.value for item in self.charger_power_kw},
            unavailable_chargers=self.unavailable_chargers,
        )


class StructuredNoticeInterpretation(StrictModel):
    """Notice schema accepted by OpenAI Structured Outputs."""

    event_id: str
    source_type: Literal[
        "service_alert", "ocpp", "driver_chat", "combined", "informational"
    ]
    event_type: Literal[
        "service_delay",
        "route_energy_change",
        "charger_fault",
        "charger_derating",
        "combined",
        "informational",
    ]
    phase: Literal[
        "warning", "onset", "persistence", "severity_change", "recovery", "stable"
    ]
    affected_buses: list[int] = Field(default_factory=list)
    affected_chargers: list[int] = Field(default_factory=list)
    effective_timestep: int = Field(ge=1, le=48)
    expected_end_timestep: int | None = Field(default=None, ge=1, le=48)
    uncertainty: bool = False
    uncertainty_details: NoticeUncertaintyAssessment = Field(
        default_factory=NoticeUncertaintyAssessment
    )
    material: bool = True
    updates: StructuredNoticeParameterUpdates = Field(
        default_factory=StructuredNoticeParameterUpdates
    )
    evidence: list[str] = Field(default_factory=list)

    def to_domain(self) -> NoticeInterpretation:
        return NoticeInterpretation(
            event_id=self.event_id,
            source_type=self.source_type,
            event_type=self.event_type,
            phase=self.phase,
            affected_buses=self.affected_buses,
            affected_chargers=self.affected_chargers,
            effective_timestep=self.effective_timestep,
            expected_end_timestep=self.expected_end_timestep,
            uncertainty=self.uncertainty,
            uncertainty_details=self.uncertainty_details,
            material=self.material,
            updates=self.updates.to_domain(),
            evidence=self.evidence,
        )


class StructuredTriggerDecision(StrictModel):
    """OpenAI wire response converted to the optimizer-facing domain model."""

    action: Literal["optimize", "skip"]
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    trigger_type: Literal[
        "deviation",
        "price",
        "price_recovery",
        "energy_disturbance",
        "energy_recovery",
        "delay",
        "delay_recovery",
        "delay_removal",
        "service_notice",
        "charger_event",
        "combined_notice",
        "trend",
        "none",
    ]
    flagged_buses: list[int]
    notice_interpretation: StructuredNoticeInterpretation | None = None

    def to_domain(self) -> TriggerDecision:
        return TriggerDecision(
            action=self.action,
            reasoning=self.reasoning,
            confidence=self.confidence,
            trigger_type=self.trigger_type,
            flagged_buses=self.flagged_buses,
            notice_interpretation=(
                self.notice_interpretation.to_domain()
                if self.notice_interpretation is not None
                else None
            ),
        )


class StructuredPricingDecision(StrictModel):
    """Transport schema for LLM pricing output before deterministic normalization.

    JSON Schema cannot express that two arrays must have equal lengths. Keeping
    that cross-field rule out of the transport schema lets the workflow repair
    length-only defects deterministically instead of spending three identical
    retries and aborting the episode.
    """

    buy_multipliers: list[float] = Field(min_length=1)
    sell_multipliers: list[float] = Field(min_length=1)
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class PricingDecision(StructuredPricingDecision):

    @model_validator(mode="after")
    def matching_lengths(self) -> "PricingDecision":
        if len(self.buy_multipliers) != len(self.sell_multipliers):
            raise ValueError("buy_multipliers and sell_multipliers must have matching lengths")
        return self


class MultiplierAdjustment(StrictModel):
    timestep_start: int = Field(ge=1, le=48)
    timestep_end: int = Field(ge=1, le=48)
    direction: Literal["lower", "raise"]
    amount: float = Field(ge=0.03)
    current_value: float
    target_value: float
    instruction: str


class EvaluationFeedback(StrictModel):
    reason: Literal[
        "infeasible",
        "solver_error",
        "v2g_unused",
        "cost_too_high",
        "revenue_too_low",
        "deviation_correction",
    ] | None
    buy_multiplier_adjustment: MultiplierAdjustment | None
    sell_multiplier_adjustment: MultiplierAdjustment | None
    period_adjustment: str | None
    priority: Literal[
        "cost_reduction",
        "v2g_increase",
        "deviation_correction",
        "mock_recovery",
    ] | None


class EvaluationDecision(StrictModel):
    accept: bool
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    feedback: EvaluationFeedback


NULL_FEEDBACK = EvaluationFeedback(
    reason=None,
    buy_multiplier_adjustment=None,
    sell_multiplier_adjustment=None,
    period_adjustment=None,
    priority=None,
)

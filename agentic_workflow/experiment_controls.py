from __future__ import annotations

from typing import Literal


TriggerPromptVariant = Literal["baseline", "action_first", "evidence_first"]
PricingGuidanceVariant = Literal["narrow", "base", "wide"]

TRIGGER_PROMPT_VARIANTS: tuple[str, ...] = (
    "baseline",
    "action_first",
    "evidence_first",
)
PRICING_GUIDANCE_VARIANTS: tuple[str, ...] = ("narrow", "base", "wide")

# These are deployment-policy thresholds applied after schema validation and
# structural notice normalization.  They are sensitivity levels, not claims
# that model-reported confidence is calibrated probability.
TRIGGER_CONFIDENCE_LEVELS: dict[str, float] = {
    "low": 0.50,
    "base": 0.70,
    "high": 0.90,
}

# Vary only the temporal spread of the optional deterministic reference.  The
# hard economic guards in normalize_pricing_decision remain fixed in every arm.
PRICING_GUIDANCE_SPREAD_FACTORS: dict[str, float] = {
    "narrow": 0.50,
    "base": 1.00,
    "wide": 1.50,
}


def validate_trigger_prompt_variant(value: str) -> str:
    if value not in TRIGGER_PROMPT_VARIANTS:
        raise ValueError(
            f"Unsupported Trigger prompt variant {value!r}; "
            f"expected one of {TRIGGER_PROMPT_VARIANTS}"
        )
    return value


def validate_pricing_guidance_variant(value: str) -> str:
    if value not in PRICING_GUIDANCE_VARIANTS:
        raise ValueError(
            f"Unsupported pricing guidance variant {value!r}; "
            f"expected one of {PRICING_GUIDANCE_VARIANTS}"
        )
    return value


def validate_trigger_confidence_threshold(value: float) -> float:
    threshold = float(value)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("trigger_confidence_threshold must be in [0, 1]")
    return threshold


def spread_reference(values: dict[str, float], variant: str) -> dict[str, float]:
    """Scale a three-zone reference around its mean without changing the mean."""

    validate_pricing_guidance_variant(variant)
    factor = PRICING_GUIDANCE_SPREAD_FACTORS[variant]
    mean = sum(float(value) for value in values.values()) / len(values)
    return {
        zone: round(mean + factor * (float(value) - mean), 6)
        for zone, value in values.items()
    }

from __future__ import annotations

from typing import Literal


UncertainParameterName = Literal[
    "delay_minutes",
    "energy_multiplier",
    "charger_power_kw",
    "charger_unavailability_probability",
]
UncertaintyRecommendation = Literal[
    "optimize", "wait", "request_confirmation"
]


def select_operational_value(
    parameter: UncertainParameterName,
    lower_bound: float,
    upper_bound: float,
    recommendation: UncertaintyRecommendation,
) -> tuple[float | None, str]:
    """Apply the frozen risk-aware mapping from uncertainty to optimizer input.

    Delay and energy demand use the upper bound; charger capacity uses the lower
    bound. A confirmed charger-fault event is represented as unavailable. No
    optimizer value is selected while the recommendation is to wait or request
    confirmation.
    """

    if lower_bound > upper_bound:
        raise ValueError("lower_bound must not exceed upper_bound")
    if recommendation != "optimize":
        return None, "no_update_pending_confirmation"
    if parameter in {"delay_minutes", "energy_multiplier"}:
        return upper_bound, "conservative_upper"
    if parameter == "charger_power_kw":
        return lower_bound, "conservative_lower"
    if parameter == "charger_unavailability_probability":
        return 1.0, "confirmed_unavailable"
    raise ValueError(f"Unsupported uncertain parameter: {parameter}")

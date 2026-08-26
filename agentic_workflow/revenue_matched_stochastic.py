"""Posture-shifted pricing for the revenue-matched stochastic benchmark.

The v4 stochastic benchmark prices through the deterministic zone table, so in
profit-based (selfish) mode it never attempts to earn aggregator revenue while
the agentic workflow it is compared against does. The v5 benchmark closes that
objective mismatch on the leader--follower structure the workflow actually
has: the tariff posture is the leader decision, chosen from a frozen candidate
grid to maximize the projected full-day aggregator revenue of the two-stage
stochastic program, while the scheduling follower continues to minimize
expected PTO cost given the tariffs. The posture is selected ex ante, at the
first public information update, and held for the day, matching the
single-table structure of the deterministic pricing policy.
"""

from __future__ import annotations

import copy
from typing import Any

from .agents import (
    build_pricing_reference,
    normalize_pricing_decision,
)
from .models import (
    EvaluationFeedback,
    PricingDecision,
    TriggerDecision,
)
from .stochastic_benchmark import EventRecedingStochasticAgentBackend


class PostureShiftAgentBackend(EventRecedingStochasticAgentBackend):
    """Deterministic stochastic-benchmark controller with a shifted posture.

    The pricing decision is the mode's deterministic zone reference with a
    uniform buy shift and a uniform sell shift applied, then normalized by the
    same bounds that constrain the Pricing Agent, so every candidate posture
    lies inside the action space available to the agentic workflow.
    """

    def __init__(
        self, case: dict[str, Any], *, buy_shift: float, sell_shift: float
    ) -> None:
        super().__init__(case)
        self.buy_shift = float(buy_shift)
        self.sell_shift = float(sell_shift)

    def price(
        self,
        context: dict[str, Any],
        trigger: TriggerDecision,
        *,
        rerun_count: int,
        previous: PricingDecision | None,
        feedback: EvaluationFeedback | None,
    ) -> PricingDecision:
        mode = str(context["mode"])
        reference = build_pricing_reference(context)["current_horizon"]
        buy = [value + self.buy_shift for value in reference["buy_multipliers"]]
        sell = [value + self.sell_shift for value in reference["sell_multipliers"]]
        decision = PricingDecision(
            buy_multipliers=buy,
            sell_multipliers=sell,
            reasoning=(
                "Revenue-matched stochastic posture: deterministic "
                f"{mode} zone reference with buy shift {self.buy_shift:+.2f} "
                f"and sell shift {self.sell_shift:+.2f}, selected ex ante from "
                "the frozen candidate grid by projected full-day aggregator "
                "revenue of the two-stage program."
            ),
            confidence=1.0,
        )
        return normalize_pricing_decision(
            decision, mode, int(context["remaining_timesteps"])
        )


def posture_grid(protocol: dict[str, Any]) -> list[dict[str, float]]:
    """Return the frozen candidate grid declared by the protocol."""

    grid = protocol["method"]["revenue_matched_posture_grid"]
    candidates = [
        {"buy_shift": float(buy), "sell_shift": float(sell)}
        for buy in grid["buy_shifts"]
        for sell in grid["sell_shifts"]
    ]
    if not candidates:
        raise ValueError("The frozen posture grid is empty")
    return candidates


def selection_record(
    *,
    case_id: str,
    candidates: list[dict[str, Any]],
    winner: dict[str, Any],
) -> dict[str, Any]:
    """Auditable record of one ex-ante posture selection."""

    return {
        "case_id": case_id,
        "selection_criterion": (
            "maximum projected_full_day_aggregator_revenue of the first "
            "accepted optimization decision (expected value over the frozen "
            "scenario set plus the settled prefix; no realized information)"
        ),
        "candidates": copy.deepcopy(candidates),
        "selected": copy.deepcopy(winner),
    }

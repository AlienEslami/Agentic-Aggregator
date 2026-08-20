from __future__ import annotations

import pandas as pd

from scripts.compare_agent_nonagentic import CASES, MODES, build_comparison


def _row(
    case: str,
    mode: str,
    configuration: str,
    repetition: int,
    *,
    revenue: float,
    cost: float,
) -> dict[str, object]:
    return {
        "case": case,
        "mode": mode,
        "configuration": configuration,
        "repetition": repetition,
        "status": "complete",
        "timesteps_completed": 48,
        "maximum_reserve_shortfall_kwh": 0.0,
        "reserve_violation_timesteps": 0,
        "minimum_observed_soc_fraction": 0.2,
        "terminal_minimum_soc_fraction": 0.2,
        "baseline_revenue_retention_compliant": True,
        "realized_aggregator_revenue": revenue,
        "realized_pto_cost": cost,
    }


def test_comparison_uses_mode_aligned_economics():
    agents = []
    baselines = []
    for case in CASES:
        for mode in MODES:
            agents.extend(
                _row(
                    case,
                    mode,
                    "full_agentic",
                    repetition,
                    revenue=12.0,
                    cost=8.0,
                )
                for repetition in range(1, 6)
            )
            baselines.append(
                _row(
                    case,
                    mode,
                    "full_deterministic",
                    1,
                    revenue=10.0,
                    cost=10.0,
                )
            )

    result = build_comparison(pd.DataFrame(agents), pd.DataFrame(baselines))

    assert len(result) == 6
    assert set(result["outcome"]) == {"agent_win"}
    assert set(result["mode_aligned_gain"]) == {2.0}
    assert set(result["agent_wins"]) == {5}


def test_operational_failure_precedes_economics():
    agents = []
    baselines = []
    for case in CASES:
        for mode in MODES:
            agents.extend(
                _row(
                    case,
                    mode,
                    "full_agentic",
                    repetition,
                    revenue=12.0,
                    cost=8.0,
                )
                for repetition in range(1, 6)
            )
            baseline = _row(
                case,
                mode,
                "full_deterministic",
                1,
                revenue=20.0,
                cost=5.0,
            )
            if case == CASES[0] and mode == "selfish":
                baseline["maximum_reserve_shortfall_kwh"] = 1.0
            baselines.append(baseline)

    result = build_comparison(pd.DataFrame(agents), pd.DataFrame(baselines))
    row = result.loc[
        (result["case"] == CASES[0]) & (result["mode"] == "selfish")
    ].iloc[0]
    assert row["outcome"] == "agent_win_on_feasibility"
    assert not row["economic_comparison_eligible"]
    assert pd.isna(row["mode_aligned_gain"])

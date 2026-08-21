from __future__ import annotations

import json

import pandas as pd

from scripts.build_multiday_charger_derating import (
    MULTIDAY_NOMINAL_DAY_CASES,
    build,
)
from scripts.run_multiday_charger_derating import (
    _day_operationally_feasible,
    _load_complete_day,
    _maximum_energy_error,
    build_specs,
)


def test_multiday_protocol_includes_two_nominal_daily_handovers() -> None:
    notices, physical = build()
    nominal = [
        row for row in notices if row["scenario_id"] in MULTIDAY_NOMINAL_DAY_CASES
    ]

    assert [row["scenario_id"] for row in nominal] == [
        MULTIDAY_NOMINAL_DAY_CASES[1],
        MULTIDAY_NOMINAL_DAY_CASES[2],
    ]
    assert all(row["canonical"]["event_type"] == "informational" for row in nominal)
    assert all(
        row["canonical"]["uncertainty_details"]["recommended_action"]
        == "optimize"
        for row in nominal
    )
    assert not any(
        row["scenario_id"] in MULTIDAY_NOMINAL_DAY_CASES for row in physical
    )


def test_multiday_factorial_adds_one_nominal_control_per_mode() -> None:
    specs = build_specs(agent_repetitions=5)

    assert len(specs) == 18
    assert sum(spec.condition == "derating" for spec in specs) == 16
    assert sum(spec.condition == "nominal" for spec in specs) == 2
    assert sum(spec.stochastic for spec in specs) == 10
    assert {
        spec.method for spec in specs if spec.condition == "nominal"
    } == {"scheduled_daily_replan"}


def test_operational_feasibility_is_physical_not_revenue_policy() -> None:
    summary = {
        "status": "complete",
        "timesteps_completed": 48,
        "maximum_reserve_shortfall_kwh": 0.0,
        "reserve_violation_timesteps": 0,
        "minimum_observed_soc_fraction": 0.20,
        "terminal_minimum_soc_fraction": 0.21,
        "baseline_revenue_retention_compliant": False,
    }

    assert _day_operationally_feasible(summary) is True
    summary["terminal_minimum_soc_fraction"] = 0.19
    assert _day_operationally_feasible(summary) is False


def test_complete_day_reuse_requires_matching_signature(tmp_path) -> None:
    workbook = tmp_path / "day.xlsx"
    carryover = tmp_path / "carryover.json"
    pd.DataFrame(
        [{"status": "complete", "timesteps_completed": 48}]
    ).to_excel(workbook, sheet_name="run_summary", index=False)
    carryover.write_text(
        json.dumps(
            {
                "run_signature_sha256": "frozen-signature",
                "terminal_realized_energy_kwh_by_bus": {"1": 100.0},
                "solver_names": ["gurobi"],
            }
        ),
        encoding="utf-8",
    )

    assert (
        _load_complete_day(
            workbook,
            carryover,
            expected_signature_sha256="different-signature",
        )
        is None
    )
    loaded = _load_complete_day(
        workbook,
        carryover,
        expected_signature_sha256="frozen-signature",
    )
    assert loaded is not None
    assert loaded[2] == ["gurobi"]


def test_carryover_error_compares_bus_level_physical_state() -> None:
    assert _maximum_energy_error({1: 100.0, 2: 90.0}, {1: 100.0, 2: 90.0}) == 0
    assert _maximum_energy_error(
        {1: 100.0, 2: 90.0}, {1: 99.75, 2: 90.0}
    ) == 0.25

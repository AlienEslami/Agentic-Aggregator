from __future__ import annotations

import pyomo.environ as pyo
import pytest

from app import configure_milp_solver
from run_dumb_charging import solve_dumb_charging_model


def available_solvers() -> list[str]:
    names = []
    for candidate in ("appsi_highs", "highs", "gurobi", "cbc", "glpk"):
        try:
            configure_milp_solver(time_limit=5, mip_gap=0.0, solver_candidates=(candidate,))
        except RuntimeError:
            continue
        names.append(candidate)
    return names


def degenerate_model():
    """Two schedules score identically but cost differently.

    Exactly two kWh must be bought across two timesteps.  The benchmark score
    counts kWh only, so both allocations are optimal for it; only the second
    stage can separate them, and it must pick the cheap timestep.
    """

    model = pyo.ConcreteModel()
    model.T = pyo.RangeSet(2)
    model.w_buy = pyo.Var(model.T, domain=pyo.NonNegativeReals, bounds=(0, 2))
    model.constraints = pyo.ConstraintList()
    model.constraints.add(model.w_buy[1] + model.w_buy[2] == 2)
    prices = [1.0, 5.0]
    model.benchmark_score = pyo.Expression(
        expr=sum(model.w_buy[t] for t in model.T)
    )
    model.charging_cost = pyo.Expression(
        expr=sum(prices[t - 1] * model.w_buy[t] for t in model.T)
    )
    model.obj = pyo.Objective(expr=model.benchmark_score, sense=pyo.maximize)
    return model


@pytest.mark.parametrize("solver", available_solvers() or [pytest.param("none", marks=pytest.mark.skip(reason="no MILP solver installed"))])
def test_cost_tie_break_selects_the_cheapest_optimal_schedule(monkeypatch, solver):
    monkeypatch.setenv("DA_SOLVER_ORDER", solver)
    monkeypatch.setenv("DA_DUMB_CHARGING_MIP_GAP", "0.0")

    model = solve_dumb_charging_model(degenerate_model())

    assert model is not None
    assert pyo.value(model.benchmark_score) == pytest.approx(2.0)
    # Without the tie-break both (2, 0) and (0, 2) are optimal; only the
    # cheap-timestep allocation survives the second stage.
    assert pyo.value(model.charging_cost) == pytest.approx(2.0)
    assert pyo.value(model.w_buy[1]) == pytest.approx(2.0)
    assert pyo.value(model.w_buy[2]) == pytest.approx(0.0)


def test_solver_order_environment_variable_is_honoured(monkeypatch):
    monkeypatch.setenv("DA_SOLVER_ORDER", "definitely_not_a_solver")

    assert solve_dumb_charging_model(degenerate_model()) is None

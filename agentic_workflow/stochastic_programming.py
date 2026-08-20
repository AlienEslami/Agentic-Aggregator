"""Two-stage stochastic MILP benchmark for real-time e-bus rescheduling.

This module deliberately reuses the physical RT model in :mod:`app_rt`.  It
only adds a finite scenario set, probabilities, and non-anticipativity.  The
existing deterministic/agentic optimizer is therefore not replaced or
modified by a different plant model.
"""

from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass
from typing import Any, Iterable

import pyomo.environ as pyo

import app_rt
from agentic_workflow.telemetry import ResourceMeter


@dataclass(frozen=True, slots=True)
class StochasticScenario:
    """One finite-support realization available to the stochastic program."""

    scenario_id: str
    probability: float
    context: dict[str, Any]


def _ids(records: Iterable[dict[str, Any]], key: str) -> tuple[int, ...]:
    return tuple(sorted(int(record[key]) for record in records))


def _trip_active(trip: dict[str, Any], timestep: int) -> bool:
    return int(trip["start_rt"]) <= timestep < int(trip["end_rt"])


def bind_physical_trips(
    context: dict[str, Any], assignments: dict[int, int]
) -> dict[str, Any]:
    """Attach public asset-identity bindings to a copied optimizer context."""

    bound = copy.deepcopy(context)
    bound["stochastic_physical_trip_bus_bindings"] = {
        str(int(trip_id)): int(bus_id)
        for trip_id, bus_id in assignments.items()
    }
    _validated_physical_trip_bindings(bound)
    return bound


def _validated_physical_trip_bindings(context: dict[str, Any]) -> dict[int, int]:
    raw = context.get("stochastic_physical_trip_bus_bindings") or {}
    bindings = {int(trip_id): int(bus_id) for trip_id, bus_id in raw.items()}
    trips = {int(trip["trip_id"]): trip for trip in context["trips"]}
    buses = {int(bus["bus_id"]) for bus in context["buses"]}
    for trip_id, bus_id in bindings.items():
        if trip_id not in trips:
            raise ValueError(f"Physical binding names unknown trip {trip_id}")
        if bus_id not in buses:
            raise ValueError(f"Physical binding names unknown bus {bus_id}")
        planned_bus_id = int(trips[trip_id]["planned_bus_id"])
        if planned_bus_id != bus_id:
            raise ValueError(
                f"Physical binding for trip {trip_id} names bus {bus_id}, "
                f"but the public schedule names bus {planned_bus_id}"
            )
    return bindings


def validate_scenarios(
    scenarios: Iterable[StochasticScenario],
    *,
    reveal_timestep: int,
    tolerance: float = 1e-9,
) -> tuple[StochasticScenario, ...]:
    """Validate probabilities, compatible model dimensions, and causality.

    ``reveal_timestep`` is local to the optimization horizon.  Decisions for
    timesteps strictly before it are here-and-now decisions and must see
    identical exogenous data in every scenario.
    """

    items = tuple(scenarios)
    if not items:
        raise ValueError("The stochastic benchmark requires at least one scenario")
    identifiers = [item.scenario_id for item in items]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("scenario_id values must be unique")
    if any(
        not math.isfinite(item.probability) or item.probability <= 0
        for item in items
    ):
        raise ValueError("Every scenario probability must be finite and positive")
    probability_sum = sum(item.probability for item in items)
    if abs(probability_sum - 1.0) > tolerance:
        raise ValueError(
            f"Scenario probabilities must sum to 1; received {probability_sum:.12f}"
        )

    reference = items[0].context
    horizon = len(reference["prices"]["spot"])
    if not 2 <= reveal_timestep <= horizon + 1:
        raise ValueError("reveal_timestep must be in [2, horizon + 1]")

    reference_bus_ids = _ids(reference["buses"], "bus_id")
    reference_charger_ids = _ids(reference["chargers"], "charger_id")
    reference_trip_ids = _ids(reference["trips"], "trip_id")
    reference_trips = {
        int(trip["trip_id"]): trip for trip in reference["trips"]
    }
    reference_buses = {
        int(bus["bus_id"]): bus for bus in reference["buses"]
    }
    reference_chargers = {
        int(charger["charger_id"]): charger
        for charger in reference["chargers"]
    }
    reference_physical_bindings = _validated_physical_trip_bindings(reference)

    for item in items[1:]:
        context = item.context
        if len(context["prices"]["spot"]) != horizon:
            raise ValueError("All scenarios must use the same remaining horizon")
        if int(context["current_timestep"]) != int(reference["current_timestep"]):
            raise ValueError("All scenarios must use the same current_timestep")
        if bool(context["v2g_enabled"]) != bool(reference["v2g_enabled"]):
            raise ValueError("All scenarios must use the same V2G setting")
        if _ids(context["buses"], "bus_id") != reference_bus_ids:
            raise ValueError("All scenarios must contain the same buses")
        if _ids(context["chargers"], "charger_id") != reference_charger_ids:
            raise ValueError("All scenarios must contain the same chargers")
        if _ids(context["trips"], "trip_id") != reference_trip_ids:
            raise ValueError("All scenarios must contain the same remaining trips")
        if _validated_physical_trip_bindings(context) != reference_physical_bindings:
            raise ValueError(
                "All scenarios must use the same public physical trip-to-bus bindings"
            )

        buses = {int(bus["bus_id"]): bus for bus in context["buses"]}
        chargers = {
            int(charger["charger_id"]): charger
            for charger in context["chargers"]
        }
        trips = {int(trip["trip_id"]): trip for trip in context["trips"]}
        for bus_id in reference_bus_ids:
            for key in ("bus_kwh", "initial_soc_rt", "availability_status"):
                if buses[bus_id][key] != reference_buses[bus_id][key]:
                    raise ValueError(
                        f"Scenario {item.scenario_id!r} changes bus {bus_id} {key} "
                        "before uncertainty is revealed"
                    )

        for timestep in range(1, reveal_timestep):
            index = timestep - 1
            for price_name in ("spot", "buy", "sell"):
                if abs(
                    float(context["prices"][price_name][index])
                    - float(reference["prices"][price_name][index])
                ) > tolerance:
                    raise ValueError(
                        f"Scenario {item.scenario_id!r} changes {price_name} at "
                        f"pre-reveal timestep {timestep}"
                    )
            for charger_id in reference_charger_ids:
                if abs(
                    float(chargers[charger_id]["alpha_by_step"][index])
                    - float(reference_chargers[charger_id]["alpha_by_step"][index])
                ) > tolerance:
                    raise ValueError(
                        f"Scenario {item.scenario_id!r} changes charger "
                        f"{charger_id} before uncertainty is revealed"
                    )
            for trip_id in reference_trip_ids:
                trip = trips[trip_id]
                reference_trip = reference_trips[trip_id]
                if _trip_active(trip, timestep) != _trip_active(
                    reference_trip, timestep
                ):
                    raise ValueError(
                        f"Scenario {item.scenario_id!r} changes trip {trip_id} "
                        "activity before uncertainty is revealed"
                    )
                if _trip_active(reference_trip, timestep) and abs(
                    float(trip["energy_per_step"])
                    - float(reference_trip["energy_per_step"])
                ) > tolerance:
                    raise ValueError(
                        f"Scenario {item.scenario_id!r} changes trip {trip_id} "
                        "energy use before uncertainty is revealed"
                    )
    return items


def apply_future_updates(
    context: dict[str, Any],
    *,
    reveal_timestep: int,
    price_multiplier: float = 1.0,
    price_multiplier_end_timestep: int | None = None,
    charger_power_multipliers: dict[int, float] | None = None,
    charger_power_windows: Iterable[dict[str, Any]] | None = None,
    trip_delay_minutes: dict[int, int] | None = None,
    trip_return_delay_minutes: dict[int, int] | None = None,
    trip_energy_multipliers: dict[int, float] | None = None,
) -> dict[str, Any]:
    """Return a scenario context changed only at/after the reveal timestep.

    Delays and route-energy changes are accepted only for trips that have not
    started at revelation.  This prevents a scenario definition from silently
    rewriting already-settled physical history.
    """

    scenario = copy.deepcopy(context)
    horizon = len(scenario["prices"]["spot"])
    if not 2 <= reveal_timestep <= horizon + 1:
        raise ValueError("reveal_timestep must be in [2, horizon + 1]")
    if not math.isfinite(price_multiplier) or price_multiplier <= 0:
        raise ValueError("price_multiplier must be finite and positive")
    start_index = reveal_timestep - 1
    price_end = (
        horizon
        if price_multiplier_end_timestep is None
        else int(price_multiplier_end_timestep)
    )
    if not reveal_timestep <= price_end <= horizon:
        raise ValueError(
            "price_multiplier_end_timestep must be between reveal_timestep and horizon"
        )
    for price_name in ("spot", "buy", "sell"):
        values = list(scenario["prices"][price_name])
        values[start_index:price_end] = [
            float(value) * price_multiplier
            for value in values[start_index:price_end]
        ]
        scenario["prices"][price_name] = values

    for charger in scenario["chargers"]:
        charger_id = int(charger["charger_id"])
        multiplier = float((charger_power_multipliers or {}).get(charger_id, 1.0))
        if not math.isfinite(multiplier) or multiplier < 0:
            raise ValueError("Charger power multipliers must be finite and nonnegative")
        schedule = list(charger["alpha_by_step"])
        schedule[start_index:] = [float(value) * multiplier for value in schedule[start_index:]]
        charger["alpha_by_step"] = schedule

    charger_by_id = {
        int(charger["charger_id"]): charger for charger in scenario["chargers"]
    }
    for window in charger_power_windows or ():
        window_start = int(window.get("timestep_start", reveal_timestep))
        window_end = int(window.get("timestep_end", horizon))
        if not reveal_timestep <= window_start <= window_end <= horizon:
            raise ValueError(
                "Charger power windows must lie at/after reveal_timestep and within horizon"
            )
        multiplier = float(window.get("multiplier", 1.0))
        if not math.isfinite(multiplier) or multiplier < 0:
            raise ValueError("Charger window multipliers must be finite and nonnegative")
        for charger_id in window.get("charger_ids", ()):
            charger_id = int(charger_id)
            if charger_id not in charger_by_id:
                raise ValueError(f"Unknown charger_id in power window: {charger_id}")
            schedule = charger_by_id[charger_id]["alpha_by_step"]
            schedule[window_start - 1 : window_end] = [
                float(value) * multiplier
                for value in schedule[window_start - 1 : window_end]
            ]

    timestep_minutes = int(scenario["timestep_minutes"])
    delays = trip_delay_minutes or {}
    return_delays = trip_return_delay_minutes or {}
    energy_multipliers = trip_energy_multipliers or {}
    for trip in scenario["trips"]:
        trip_id = int(trip["trip_id"])
        if (
            trip_id not in delays
            and trip_id not in return_delays
            and trip_id not in energy_multipliers
        ):
            continue
        if (
            int(trip["start_rt"]) < reveal_timestep
            and (trip_id in delays or trip_id in energy_multipliers)
        ):
            raise ValueError(
                f"Future trip update for trip {trip_id} would rewrite a trip "
                "that starts before revelation"
            )
        delay_minutes = int(delays.get(trip_id, 0))
        return_delay_minutes = int(return_delays.get(trip_id, 0))
        if delay_minutes < 0 or return_delay_minutes < 0:
            raise ValueError("Trip delays must be nonnegative")
        if (
            delay_minutes % timestep_minutes != 0
            or return_delay_minutes % timestep_minutes != 0
        ):
            raise ValueError(
                f"Trip delays must align to the {timestep_minutes}-minute model grid"
            )
        delay_steps = delay_minutes // timestep_minutes
        return_delay_steps = return_delay_minutes // timestep_minutes
        trip["start_rt"] = min(horizon, int(trip["start_rt"]) + delay_steps)
        trip["end_rt"] = min(
            horizon + 1,
            max(
                int(trip["start_rt"]) + 1,
                int(trip["end_rt"]) + delay_steps + return_delay_steps,
            ),
        )
        multiplier = float(energy_multipliers.get(trip_id, 1.0))
        if not math.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("Trip energy multipliers must be finite and positive")
        trip["energy_per_step"] = float(trip["energy_per_step"]) * multiplier
        trip["remaining_active_steps"] = max(
            0, int(trip["end_rt"]) - int(trip["start_rt"])
        )
        trip["remaining_energy_need"] = (
            float(trip["energy_per_step"]) * int(trip["remaining_active_steps"])
        )
    return scenario


def scenarios_from_definitions(
    base_context: dict[str, Any],
    definitions: Iterable[dict[str, Any]],
    *,
    reveal_timestep: int,
) -> tuple[StochasticScenario, ...]:
    """Build validated scenarios from a JSON-serializable frozen protocol."""

    scenarios = []
    for definition in definitions:
        updates = dict(definition.get("future_updates") or {})
        context = apply_future_updates(
            base_context,
            reveal_timestep=reveal_timestep,
            price_multiplier=float(updates.get("price_multiplier", 1.0)),
            price_multiplier_end_timestep=updates.get(
                "price_multiplier_end_timestep"
            ),
            charger_power_multipliers={
                int(key): float(value)
                for key, value in (
                    updates.get("charger_power_multipliers") or {}
                ).items()
            },
            charger_power_windows=updates.get("charger_power_windows") or (),
            trip_delay_minutes={
                int(key): int(value)
                for key, value in (updates.get("trip_delay_minutes") or {}).items()
            },
            trip_return_delay_minutes={
                int(key): int(value)
                for key, value in (
                    updates.get("trip_return_delay_minutes") or {}
                ).items()
            },
            trip_energy_multipliers={
                int(key): float(value)
                for key, value in (
                    updates.get("trip_energy_multipliers") or {}
                ).items()
            },
        )
        scenarios.append(
            StochasticScenario(
                scenario_id=str(definition["scenario_id"]),
                probability=float(definition["probability"]),
                context=context,
            )
        )
    return validate_scenarios(scenarios, reveal_timestep=reveal_timestep)


def build_extensive_form(
    scenarios: Iterable[StochasticScenario],
    *,
    reveal_timestep: int,
) -> tuple[pyo.ConcreteModel, tuple[StochasticScenario, ...]]:
    """Build the finite-scenario extensive form using the production RT MILP."""

    items = validate_scenarios(scenarios, reveal_timestep=reveal_timestep)
    model = pyo.ConcreteModel()
    scenario_ids = [item.scenario_id for item in items]
    model.Omega = pyo.Set(initialize=scenario_ids, ordered=True)
    model.scenario = pyo.Block(model.Omega)
    probability = {item.scenario_id: item.probability for item in items}
    model.probability = pyo.Param(model.Omega, initialize=probability)

    for item in items:
        scenario_model, _ = app_rt.solve_rt_rescheduling(
            item.context, build_only=True
        )
        scenario_model.obj.deactivate()
        block = model.scenario[item.scenario_id]
        block.transfer_attributes_from(scenario_model)
        block.physical_trip_bus_binding_constraints = pyo.ConstraintList()
        for trip_id, bus_id in _validated_physical_trip_bindings(
            item.context
        ).items():
            for timestep in block.T:
                if int(pyo.value(block.start_rt[trip_id])) <= int(timestep) < int(
                    pyo.value(block.end_rt[trip_id])
                ):
                    block.physical_trip_bus_binding_constraints.add(
                        block.s[bus_id, trip_id, timestep] == 1
                    )

    reference_id = scenario_ids[0]
    reference = model.scenario[reference_id]
    physical_bindings = _validated_physical_trip_bindings(items[0].context)
    model.nonanticipativity = pyo.ConstraintList()
    for scenario_id in scenario_ids[1:]:
        block = model.scenario[scenario_id]
        for timestep in range(1, reveal_timestep):
            model.nonanticipativity.add(block.w_buy[timestep] == reference.w_buy[timestep])
            model.nonanticipativity.add(block.w_sell[timestep] == reference.w_sell[timestep])
            for bus_id in reference.K:
                model.nonanticipativity.add(block.c[bus_id, timestep] == reference.c[bus_id, timestep])
                model.nonanticipativity.add(block.e[bus_id, timestep] == reference.e[bus_id, timestep])
                model.nonanticipativity.add(
                    block.soc_shortfall[bus_id, timestep]
                    == reference.soc_shortfall[bus_id, timestep]
                )
                for trip_id in reference.I:
                    model.nonanticipativity.add(
                        block.s[bus_id, trip_id, timestep]
                        == reference.s[bus_id, trip_id, timestep]
                    )
                    model.nonanticipativity.add(
                        block.switch[bus_id, trip_id, timestep]
                        == reference.switch[bus_id, trip_id, timestep]
                    )
                for charger_id in reference.N:
                    model.nonanticipativity.add(
                        block.x[bus_id, charger_id, timestep]
                        == reference.x[bus_id, charger_id, timestep]
                    )
                    model.nonanticipativity.add(
                        block.y[bus_id, charger_id, timestep]
                        == reference.y[bus_id, charger_id, timestep]
                    )
            for trip_id in reference.I:
                model.nonanticipativity.add(
                    block.u[trip_id, timestep] == reference.u[trip_id, timestep]
                )

    model.expected_service_feasibility_score = pyo.Expression(
        expr=sum(
            model.probability[scenario_id]
            * model.scenario[scenario_id].service_feasibility_score
            for scenario_id in model.Omega
        )
    )
    model.expected_operational_priority_violation = pyo.Expression(
        expr=sum(
            model.probability[scenario_id]
            * model.scenario[scenario_id].operational_priority_violation
            for scenario_id in model.Omega
        )
    )
    model.expected_economic_dispatch_score = pyo.Expression(
        expr=sum(
            model.probability[scenario_id]
            * model.scenario[scenario_id].economic_dispatch_score
            for scenario_id in model.Omega
        )
    )
    model.expected_baseline_weighted_score = pyo.Expression(
        expr=model.expected_service_feasibility_score
        + model.expected_economic_dispatch_score
    )
    model.obj = pyo.Objective(
        expr=model.expected_baseline_weighted_score, sense=pyo.minimize
    )
    model._stochastic_reveal_timestep = reveal_timestep
    model._stochastic_reference_scenario = reference_id
    model._stochastic_physical_trip_bus_bindings = physical_bindings
    return model, items


def _scenario_outcome(block: pyo.Block, context: dict[str, Any]) -> dict[str, float]:
    pto_cost = sum(
        float(context["prices"]["buy"][t - 1]) * pyo.value(block.w_buy[t])
        - float(context["prices"]["sell"][t - 1]) * pyo.value(block.w_sell[t])
        for t in block.T
    )
    aggregator_revenue = sum(
        (
            float(context["prices"]["buy"][t - 1])
            - float(context["prices"]["spot"][t - 1])
        )
        * pyo.value(block.w_buy[t])
        + (
            float(context["prices"]["spot"][t - 1])
            - float(context["prices"]["sell"][t - 1])
        )
        * pyo.value(block.w_sell[t])
        for t in block.T
    )
    unmet_by_trip = {
        int(trip_id): sum(
            float(pyo.value(block.u[trip_id, timestep]))
            for timestep in block.T
        )
        for trip_id in block.I
    }
    soc_violation_count = sum(
        1
        for bus_id in block.K
        for timestep in block.T
        if float(pyo.value(block.soc_shortfall[bus_id, timestep])) > 1e-6
    )
    return {
        "service_feasibility_score": float(pyo.value(block.service_feasibility_score)),
        "operational_priority_violation": float(pyo.value(block.operational_priority_violation)),
        "economic_dispatch_score": float(pyo.value(block.economic_dispatch_score)),
        "pto_cost": float(pto_cost),
        "aggregator_revenue": float(aggregator_revenue),
        "energy_bought_kwh": float(sum(pyo.value(block.w_buy[t]) for t in block.T)),
        "energy_sold_kwh": float(sum(pyo.value(block.w_sell[t]) for t in block.T)),
        "service_unmet_count": float(
            sum(duration > 0.5 for duration in unmet_by_trip.values())
        ),
        "service_unmet_duration": float(sum(unmet_by_trip.values())),
        "soc_violation_count": float(soc_violation_count),
    }


def solve_two_stage_stochastic(
    scenarios: Iterable[StochasticScenario],
    *,
    reveal_timestep: int,
    solver_name: str = "gurobi",
    time_limit_seconds: float = 300.0,
    mip_gap: float = 0.02,
    tee: bool = False,
) -> dict[str, Any]:
    """Solve one rolling two-stage decision point and return auditable metrics."""

    if time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be positive")
    if not 0 <= mip_gap < 1:
        raise ValueError("mip_gap must be in [0, 1)")
    model, items = build_extensive_form(
        scenarios, reveal_timestep=reveal_timestep
    )
    solver = pyo.SolverFactory(solver_name)
    if solver is None or not solver.available(False):
        raise RuntimeError(f"Required stochastic-program solver is unavailable: {solver_name}")
    if solver_name != "gurobi":
        raise ValueError("Confirmatory stochastic benchmark currently requires Gurobi")

    use_operator_priority = any(
        item.context.get("operational_requirements") for item in items
    )
    if use_operator_priority:
        stages = (
            ("service_feasibility", model.expected_service_feasibility_score, True),
            (
                "operator_priority_violation",
                model.expected_operational_priority_violation,
                True,
            ),
            ("economic_dispatch", model.expected_economic_dispatch_score, False),
        )
    else:
        stages = (
            ("service_feasibility", model.expected_service_feasibility_score, True),
            ("economic_dispatch", model.expected_economic_dispatch_score, False),
        )

    reference = model.scenario[model._stochastic_reference_scenario]
    stage_records = []
    stage_telemetry = []
    status = "unknown"
    termination = "unknown"
    for stage_index, (stage_name, expression, lock_result) in enumerate(
        stages, start=1
    ):
        model.obj.set_value(expression)
        solver.options["TimeLimit"] = float(time_limit_seconds)
        solver.options["MIPGap"] = float(
            mip_gap if stage_index == len(stages) else 0.0
        )
        with ResourceMeter() as meter:
            solved = solver.solve(model, tee=tee)
        telemetry = app_rt._extract_solver_telemetry(
            solved, model, meter.metrics or {}
        )
        stage_telemetry.append(telemetry)
        termination = str(solved.solver.termination_condition).lower()
        status = str(solved.solver.status).lower()
        has_incumbent = any(
            reference.e[bus_id, 1].value is not None for bus_id in reference.K
        )
        usable = "optimal" in termination or "feasible" in termination or has_incumbent
        if not usable:
            return {
                "status": "infeasible_or_no_incumbent",
                "solver_name": solver_name,
                "solver_status": f"{status}/{termination}",
                "failed_stage": stage_name,
                "solver_telemetry": telemetry,
                "lexicographic_stages": stage_records,
            }
        objective_value = float(pyo.value(expression))
        proven_optimal = "optimal" in termination
        tolerance = None
        if lock_result:
            tolerance = max(1e-6, 1e-8 * abs(objective_value))
            model.add_component(
                f"lexicographic_{stage_name}_lock",
                pyo.Constraint(expr=expression <= objective_value + tolerance),
            )
        stage_records.append(
            {
                "stage": stage_name,
                "objective_value": objective_value,
                "lock_tolerance": tolerance,
                "mip_gap_target": (
                    mip_gap if stage_index == len(stages) else 0.0
                ),
                "proven_optimal": proven_optimal,
                "solver_status": f"{status}/{termination}",
            }
        )

    telemetry = dict(stage_telemetry[-1])
    for key in (
        "wall_seconds",
        "process_cpu_seconds",
        "reported_time_seconds",
        "reported_user_time_seconds",
        "reported_system_time_seconds",
        "branch_and_bound_nodes",
        "iterations",
    ):
        values = [
            float(item[key]) for item in stage_telemetry if item.get(key) is not None
        ]
        if values:
            telemetry[key] = sum(values)
    for key in ("peak_rss_mb", "peak_rss_delta_mb"):
        values = [
            float(item[key]) for item in stage_telemetry if item.get(key) is not None
        ]
        if values:
            telemetry[key] = max(values)
    telemetry["solve_stage_count"] = len(stage_records)

    by_id = {item.scenario_id: item for item in items}
    outcomes = {
        scenario_id: {
            "probability": by_id[scenario_id].probability,
            **_scenario_outcome(
                model.scenario[scenario_id], by_id[scenario_id].context
            ),
        }
        for scenario_id in model.Omega
    }
    expected = {
        key: sum(
            row["probability"] * row[key] for row in outcomes.values()
        )
        for key in (
            "service_feasibility_score",
            "operational_priority_violation",
            "economic_dispatch_score",
            "pto_cost",
            "aggregator_revenue",
            "energy_bought_kwh",
            "energy_sold_kwh",
            "service_unmet_count",
            "service_unmet_duration",
            "soc_violation_count",
        )
    }
    reference_context = by_id[model._stochastic_reference_scenario].context
    reference_series = app_rt.extract_time_series_results(
        reference, reference_context
    )
    first_stage = {
        "timesteps": list(range(1, reveal_timestep)),
        "w_buy": [
            float(pyo.value(reference.w_buy[t]))
            for t in range(1, reveal_timestep)
        ],
        "w_sell": [
            float(pyo.value(reference.w_sell[t]))
            for t in range(1, reveal_timestep)
        ],
        "bus_energy_kwh": {
            str(bus_id): [
                float(pyo.value(reference.e[bus_id, t]))
                for t in range(1, reveal_timestep)
            ]
            for bus_id in reference.K
        },
    }
    return {
        "status": "complete",
        "is_mock": False,
        "method": "rolling_two_stage_stochastic_milp",
        "solver_name": solver_name,
        "solver_status": f"{status}/{termination}",
        "proven_optimal": "optimal" in termination,
        "optimization_strategy": (
            "lexicographic_expected_service_priority_economics"
            if use_operator_priority
            else "lexicographic_expected_service_then_economics"
        ),
        "lexicographic_stages": stage_records,
        "scenario_count": len(items),
        "probability_sum": sum(item.probability for item in items),
        "reveal_timestep": reveal_timestep,
        "nonanticipativity_timesteps": list(range(1, reveal_timestep)),
        "physical_trip_bus_bindings": dict(
            model._stochastic_physical_trip_bus_bindings
        ),
        "expected": expected,
        "scenario_outcomes": outcomes,
        "first_stage": first_stage,
        # Compatibility with the existing workflow/evaluator result contract.
        # The reference-scenario tail is available for inspection, but a rolling
        # controller must apply only ``commitment_steps`` before solving again.
        "commitment_steps": 1,
        "w_buy": reference_series["w_buy"],
        "w_sell": reference_series["w_sell"],
        "energy": reference_series["energy"],
        "soc_shortfall": reference_series["soc_shortfall"],
        "trip_assignment_by_timestep": reference_series[
            "trip_assignment_by_timestep"
        ],
        "trip_coverage_by_timestep": reference_series[
            "trip_coverage_by_timestep"
        ],
        "temporarily_unserved_trip_ids": reference_series[
            "temporarily_unserved_trip_ids"
        ],
        "service_interruption_events": reference_series[
            "service_interruption_events"
        ],
        "service_restoration_events": reference_series[
            "service_restoration_events"
        ],
        "reassignment_mapping": reference_series["reassignment_mapping"],
        "pto_daily_cost": expected["pto_cost"],
        "aggregator_revenue": expected["aggregator_revenue"],
        "total_kwh_bought": expected["energy_bought_kwh"],
        "total_kwh_sold": expected["energy_sold_kwh"],
        "service_unmet_count": int(
            max(row["service_unmet_count"] for row in outcomes.values())
        ),
        "service_unmet_duration": max(
            row["service_unmet_duration"] for row in outcomes.values()
        ),
        "soc_violation_count": int(
            max(row["soc_violation_count"] for row in outcomes.values())
        ),
        "solver_telemetry": telemetry,
        "model_variables": telemetry.get("model_variables"),
        "model_constraints": telemetry.get("model_constraints"),
        "random_seed": None,
        "external_llm_used": False,
        "llm_tokens": 0,
        "llm_cost_usd": 0.0,
        "process_id": os.getpid(),
    }

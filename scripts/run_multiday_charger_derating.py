"""Run the frozen three-day charger-derating case with physical SOC carryover.

This is a chained rolling-horizon experiment, not a single 144-step perfect-
foresight solve.  Each daily episode has the same 48-step planning horizon and
the terminal realized energy of every bus is copied exactly into the next
day's initial physical state.  The disturbance begins on day 1, persists
through day 2, and clears during day 3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_workflow.config import DEFAULT_MODEL, WorkflowConfig  # noqa: E402
from agentic_workflow.io import planned_row_for_observation  # noqa: E402
from agentic_workflow.runner import WorkflowRunner  # noqa: E402
from scripts.build_multiday_charger_derating import (  # noqa: E402
    DERATED_POWER_KW,
    MANIFEST_OUTPUT as CASE_MANIFEST,
    MULTIDAY_CHARGERS,
    MULTIDAY_DAY_CASES,
    MULTIDAY_NOMINAL_DAY_CASES,
    NOTICE_OUTPUT,
    NOMINAL_POWER_KW,
    PHYSICAL_OUTPUT,
)


DEFAULT_OUTPUT_ROOT = (
    ROOT / "results" / "revision" / "multiday_charger_derating_v1"
)
DEFAULT_CONFIGURATIONS = (
    "oracle_event_trigger",
    "numerical_event_trigger",
    "rule_text_event_trigger",
    "agent_trigger_only",
)
METHOD_LABELS = {
    "oracle_event_trigger": "oracle",
    "numerical_event_trigger": "numerical",
    "rule_text_event_trigger": "rule_text",
    "agent_trigger_only": "agent",
}
LLM_CONFIGURATIONS = frozenset({"agent_trigger_only"})
PROMPT_PATHS = tuple(sorted((ROOT / "agentic_workflow" / "prompts").glob("*.txt")))
MODES = ("selfish", "altruistic")
CONDITIONS = ("derating", "nominal")
RESERVE_FRACTION = 0.20
FEASIBILITY_TOLERANCE_KWH = 1e-6
OBSERVATION_ROUNDING_TOLERANCE_KWH = 5.1e-5
SIGNATURE_CODE_PATHS = (
    Path(__file__).resolve(),
    ROOT / "agentic_workflow" / "agents.py",
    ROOT / "agentic_workflow" / "context.py",
    ROOT / "agentic_workflow" / "physical_events.py",
    ROOT / "agentic_workflow" / "runner.py",
)


@dataclass(frozen=True, slots=True)
class EpisodeSpec:
    mode: str
    configuration: str
    repetition: int
    condition: str = "derating"

    @property
    def stochastic(self) -> bool:
        return self.configuration in LLM_CONFIGURATIONS

    @property
    def method(self) -> str:
        if self.condition == "nominal":
            return "scheduled_daily_replan"
        return METHOD_LABELS[self.configuration]

    @property
    def episode_id(self) -> str:
        if self.condition == "nominal":
            return f"md_nominal__{self.mode}__scheduled_daily_replan__r001"
        return (
            f"md_charger_derating__{self.mode}__{self.configuration}__"
            f"r{self.repetition:03d}"
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def build_specs(
    *,
    configurations: Iterable[str] = DEFAULT_CONFIGURATIONS,
    modes: Iterable[str] = MODES,
    agent_repetitions: int = 5,
    include_nominal_control: bool = True,
) -> list[EpisodeSpec]:
    if agent_repetitions < 1:
        raise ValueError("agent_repetitions must be positive")
    selected = unique(configurations)
    unsupported = set(selected) - set(DEFAULT_CONFIGURATIONS)
    if unsupported:
        raise ValueError(f"Unsupported configurations: {sorted(unsupported)!r}")
    specs: list[EpisodeSpec] = []
    for mode in unique(modes):
        if mode not in MODES:
            raise ValueError(f"Unsupported mode: {mode}")
        for configuration in selected:
            repetitions = (
                agent_repetitions if configuration in LLM_CONFIGURATIONS else 1
            )
            specs.extend(
                EpisodeSpec(mode, configuration, repetition)
                for repetition in range(1, repetitions + 1)
            )
        if include_nominal_control:
            specs.append(
                EpisodeSpec(
                    mode=mode,
                    configuration="oracle_event_trigger",
                    repetition=1,
                    condition="nominal",
                )
            )
    return specs


def _case_ids(spec: EpisodeSpec) -> tuple[str, str, str]:
    return (
        MULTIDAY_NOMINAL_DAY_CASES
        if spec.condition == "nominal"
        else MULTIDAY_DAY_CASES
    )


def _initial_plan_energy(runner: WorkflowRunner) -> dict[int, float]:
    row = planned_row_for_observation(runner.state.realtime_plan, 1) or {}
    return {
        int(key.removeprefix("bus_").removesuffix("_kwh")): float(value)
        for key, value in row.items()
        if key.startswith("bus_") and key.endswith("_kwh")
    }


def _json_energy(value: dict[int, float]) -> str:
    return json.dumps(
        {str(key): round(float(item), 6) for key, item in sorted(value.items())},
        sort_keys=True,
    )


def _run_signature(
    *,
    spec: EpisodeSpec,
    day: int,
    model: str,
) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "signature_version": 1,
        "episode_id": spec.episode_id,
        "condition": spec.condition,
        "mode": spec.mode,
        "configuration": spec.configuration,
        "repetition": spec.repetition,
        "day": day,
        "case_id": _case_ids(spec)[day - 1],
        "model": model if spec.stochastic else "not_used",
        "solver_order": "gurobi",
        "solver_time_limit_seconds": float(
            os.environ.get("RT_SOLVER_TIME_LIMIT", "300")
        ),
        "solver_mip_gap": float(os.environ.get("RT_SOLVER_MIP_GAP", "0.02")),
        "altruistic_revenue_retention_fraction": 0.50,
        "trigger_prompt_variant": "daily_handover",
        "input_sha256": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
            for path in (
                ROOT / "inputs" / "State.xlsx",
                ROOT / "inputs" / "Forecasted.xlsx",
                ROOT / "inputs" / "SpotPrices.xlsx",
                ROOT / "inputs" / "rt_disturbance_scenarios_multiple.xlsx",
                CASE_MANIFEST,
                NOTICE_OUTPUT,
                PHYSICAL_OUTPUT,
            )
        },
        "prompt_sha256": {path.name: sha256(path) for path in PROMPT_PATHS},
        "code_sha256": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
            for path in SIGNATURE_CODE_PATHS
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), payload


def _day_operationally_feasible(summary: dict[str, Any]) -> bool:
    status_ok = summary.get("status") == "complete"
    timesteps_ok = int(summary.get("timesteps_completed") or 0) == 48
    shortfall = float(summary.get("maximum_reserve_shortfall_kwh") or 0.0)
    violations = int(summary.get("reserve_violation_timesteps") or 0)
    minimum_soc = summary.get("minimum_observed_soc_fraction")
    terminal_soc = summary.get("terminal_minimum_soc_fraction")
    minimum_soc_ok = (
        minimum_soc is not None
        and not pd.isna(minimum_soc)
        and float(minimum_soc) + 1e-12 >= RESERVE_FRACTION
    )
    terminal_soc_ok = (
        terminal_soc is not None
        and not pd.isna(terminal_soc)
        and float(terminal_soc) + 1e-12 >= RESERVE_FRACTION
    )
    return bool(
        status_ok
        and timesteps_ok
        and shortfall <= FEASIBILITY_TOLERANCE_KWH
        and violations == 0
        and minimum_soc_ok
        and terminal_soc_ok
    )


def _retention_compliant(summary: dict[str, Any], *, mode: str) -> bool:
    if mode != "altruistic":
        return True
    value = summary.get("baseline_revenue_retention_compliant")
    return bool(not pd.isna(value) and value in {True, 1})


def _solver_names(state: Any) -> list[str]:
    names = {
        str(row.get("solver_name"))
        for row in state.attempts
        if row.get("solver_name") not in {None, "", "nan"}
    }
    return sorted(names)


def _workbook_for(output_root: Path, spec: EpisodeSpec, day: int) -> Path:
    return output_root / "runs" / spec.episode_id / f"day_{day}.xlsx"


def _carryover_for(output_root: Path, spec: EpisodeSpec, day: int) -> Path:
    return output_root / "runs" / spec.episode_id / f"day_{day}_carryover.json"


def _load_complete_day(
    workbook: Path,
    carryover_path: Path,
    *,
    expected_signature_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]] | None:
    if not workbook.exists() or not carryover_path.exists():
        return None
    try:
        frame = pd.read_excel(workbook, sheet_name="run_summary")
        if frame.empty:
            return None
        summary = frame.iloc[0].to_dict()
        payload = json.loads(carryover_path.read_text(encoding="utf-8"))
        solver_names = [str(value) for value in payload.get("solver_names", [])]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if summary.get("status") != "complete" or int(
        summary.get("timesteps_completed") or 0
    ) != 48:
        return None
    if payload.get("run_signature_sha256") != expected_signature_sha256:
        return None
    return summary, payload, solver_names


def _config(
    *,
    spec: EpisodeSpec,
    day: int,
    output_workbook: Path,
    model: str,
) -> WorkflowConfig:
    return WorkflowConfig(
        state_workbook=ROOT / "inputs" / "State.xlsx",
        forecast_workbook=ROOT / "inputs" / "Forecasted.xlsx",
        spot_prices_workbook=ROOT / "inputs" / "SpotPrices.xlsx",
        realtime_states=ROOT / "inputs" / "realtime_states",
        intraday_prices=ROOT / "inputs" / "intraday_prices",
        disturbance_workbook=(
            ROOT / "inputs" / "rt_disturbance_scenarios_multiple.xlsx"
        ),
        output_workbook=output_workbook,
        notices_file=NOTICE_OUTPUT,
        physical_events_file=PHYSICAL_OUTPUT,
        notice_scenario_ids=(_case_ids(spec)[day - 1],),
        notice_variant="uncertain_chat",
        mode=spec.mode,  # type: ignore[arg-type]
        altruistic_revenue_retention_fraction=0.50,
        scenario_ids=("rt_none",),
        start_timestep=1,
        end_timestep=48,
        agent_backend="openai" if spec.stochastic else "rule",
        experiment_configuration=spec.configuration,  # type: ignore[arg-type]
        realize_notice_truth=True,
        optimizer_backend="direct",
        model=model,
        trigger_prompt_variant="daily_handover",
        max_reruns=1,
        checkpoint_every=48,
        metadata={
            "experiment": "three_day_chained_charger_derating_v1",
            "episode_id": spec.episode_id,
            "condition": spec.condition,
            "day": str(day),
            "physical_soc_carryover": "true",
        },
    )


def _sum(summary_rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(row.get(key) or 0.0) for row in summary_rows)


def _energy_map(payload: dict[str, Any], key: str) -> dict[int, float]:
    return {
        int(bus): float(value)
        for bus, value in (payload.get(key) or {}).items()
    }


def _maximum_energy_error(
    expected: dict[int, float], observed: dict[int, float]
) -> float:
    keys = set(expected) | set(observed)
    return max(
        (
            abs(float(expected.get(key, 0.0)) - float(observed.get(key, 0.0)))
            for key in keys
        ),
        default=0.0,
    )


def run_episode(
    *,
    output_root: Path,
    spec: EpisodeSpec,
    model: str,
    force: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    carry: dict[int, float] | None = None
    prior_terminal: dict[int, float] | None = None
    day_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    all_solver_names: set[str] = set()
    maximum_carry_error = 0.0
    maximum_observed_carry_error = 0.0
    run_signatures: list[str] = []

    for day, case_id in enumerate(_case_ids(spec), start=1):
        workbook = _workbook_for(output_root, spec, day)
        carryover_path = _carryover_for(output_root, spec, day)
        signature_sha256, signature_payload = _run_signature(
            spec=spec,
            day=day,
            model=model,
        )
        run_signatures.append(signature_sha256)
        loaded = (
            None
            if force
            else _load_complete_day(
                workbook,
                carryover_path,
                expected_signature_sha256=signature_sha256,
            )
        )
        reused = loaded is not None
        expected_initial = dict(carry or {})

        if loaded is not None:
            summary, carryover_payload, solver_names = loaded
            initial = _energy_map(
                carryover_payload, "initial_realized_energy_kwh_by_bus"
            )
            first_settlement_energy = _energy_map(
                carryover_payload,
                "first_settlement_realized_energy_kwh_by_bus",
            )
            terminal = _energy_map(
                carryover_payload, "terminal_realized_energy_kwh_by_bus"
            )
        else:
            workbook.parent.mkdir(parents=True, exist_ok=True)
            runner = WorkflowRunner(
                _config(
                    spec=spec,
                    day=day,
                    output_workbook=workbook,
                    model=model,
                )
            )
            initial = dict(carry) if carry is not None else _initial_plan_energy(runner)
            if carry is not None:
                runner.state.realized_energy_by_bus.update(carry)
            state = runner.run()
            summary = dict(state.run_summary)
            first_settlement_energy = {
                int(key): float(value)
                for key, value in (
                    (state.settlement[0].get("realized_energy_by_bus") or {})
                    if state.settlement
                    else {}
                ).items()
            }
            terminal = {
                int(key): float(value)
                for key, value in state.realized_energy_by_bus.items()
            }
            solver_names = _solver_names(state)
            carryover_path.write_text(
                json.dumps(
                    {
                        "episode_id": spec.episode_id,
                        "condition": spec.condition,
                        "day": day,
                        "case_id": case_id,
                        "run_signature_sha256": signature_sha256,
                        "run_signature": signature_payload,
                        "initial_realized_energy_kwh_by_bus": {
                            str(key): value for key, value in sorted(initial.items())
                        },
                        "first_settlement_realized_energy_kwh_by_bus": {
                            str(key): value
                            for key, value in sorted(first_settlement_energy.items())
                        },
                        "terminal_realized_energy_kwh_by_bus": {
                            str(key): value for key, value in sorted(terminal.items())
                        },
                        "solver_names": solver_names,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        carry_error = (
            _maximum_energy_error(expected_initial, initial)
            if expected_initial
            else 0.0
        )
        if prior_terminal is not None:
            carry_error = max(
                carry_error,
                _maximum_energy_error(prior_terminal, initial),
            )
        observed_carry_error = (
            _maximum_energy_error(expected_initial, first_settlement_energy)
            if expected_initial
            else 0.0
        )
        if carry_error > 1e-12:
            raise RuntimeError(
                f"Internal carryover mismatch for {spec.episode_id} day {day}: "
                f"{carry_error:.12g} kWh"
            )
        if observed_carry_error > OBSERVATION_ROUNDING_TOLERANCE_KWH:
            raise RuntimeError(
                f"First-settlement carryover mismatch for {spec.episode_id} "
                f"day {day}: {observed_carry_error:.12g} kWh"
            )
        maximum_carry_error = max(maximum_carry_error, carry_error)
        maximum_observed_carry_error = max(
            maximum_observed_carry_error, observed_carry_error
        )
        all_solver_names.update(solver_names)
        summaries.append(summary)
        day_feasible = _day_operationally_feasible(summary)
        retention_compliant = _retention_compliant(summary, mode=spec.mode)
        day_rows.append(
            {
                "episode_id": spec.episode_id,
                "condition": spec.condition,
                "mode": spec.mode,
                "configuration": spec.configuration,
                "method": spec.method,
                "repetition": spec.repetition,
                "stochastic": spec.stochastic,
                "day": day,
                "case_id": case_id,
                "reused_complete_workbook": reused,
                "workbook": str(workbook.relative_to(ROOT)).replace("\\", "/"),
                "status": summary.get("status"),
                "timesteps_completed": summary.get("timesteps_completed"),
                "operationally_feasible": day_feasible,
                "economic_comparison_eligible": (
                    day_feasible and retention_compliant
                ),
                "realized_pto_cost": summary.get("realized_pto_cost"),
                "realized_aggregator_revenue": summary.get(
                    "realized_aggregator_revenue"
                ),
                "realized_grid_net_cost": summary.get("realized_grid_net_cost"),
                "realized_buy_kwh": summary.get("realized_buy_kwh"),
                "realized_sell_kwh": summary.get("realized_sell_kwh"),
                "minimum_observed_soc_fraction": summary.get(
                    "minimum_observed_soc_fraction"
                ),
                "terminal_minimum_soc_fraction": summary.get(
                    "terminal_minimum_soc_fraction"
                ),
                "maximum_reserve_shortfall_kwh": summary.get(
                    "maximum_reserve_shortfall_kwh"
                ),
                "reserve_violation_timesteps": summary.get(
                    "reserve_violation_timesteps"
                ),
                "baseline_revenue_retention_floor": summary.get(
                    "baseline_revenue_retention_floor"
                ),
                "baseline_revenue_retention_compliant": summary.get(
                    "baseline_revenue_retention_compliant"
                ),
                "optimizer_calls": summary.get("optimizer_calls"),
                "optimize_decisions": summary.get("optimize_decisions"),
                "llm_successful_requests": summary.get("llm_successful_requests"),
                "llm_total_tokens": summary.get("llm_total_tokens"),
                "llm_approximate_cost_usd": summary.get(
                    "llm_approximate_cost_usd"
                ),
                "run_wall_seconds": summary.get("run_wall_seconds"),
                "solver_names": json.dumps(solver_names),
                "initial_realized_energy_kwh_by_bus": _json_energy(initial),
                "terminal_realized_energy_kwh_by_bus": _json_energy(terminal),
                "carryover_max_abs_error_kwh": carry_error,
                "first_settlement_carryover_max_abs_error_kwh": (
                    observed_carry_error
                ),
                "run_signature_sha256": signature_sha256,
            }
        )
        carry = terminal
        prior_terminal = terminal

    per_day_feasible = [bool(row["operationally_feasible"]) for row in day_rows]
    total_revenue = _sum(summaries, "realized_aggregator_revenue")
    total_floor = _sum(summaries, "baseline_revenue_retention_floor")
    episode = {
        "episode_id": spec.episode_id,
        "condition": spec.condition,
        "mode": spec.mode,
        "configuration": spec.configuration,
        "method": spec.method,
        "repetition": spec.repetition,
        "stochastic": spec.stochastic,
        "days_completed": len(summaries),
        "operationally_feasible": all(per_day_feasible),
        "all_daily_retention_floors_compliant": all(
            _retention_compliant(summary, mode=spec.mode)
            for summary in summaries
        ),
        "economic_comparison_eligible": bool(
            all(per_day_feasible)
            and all(
                _retention_compliant(summary, mode=spec.mode)
                for summary in summaries
            )
        ),
        "total_realized_pto_cost": _sum(summaries, "realized_pto_cost"),
        "total_realized_aggregator_revenue": total_revenue,
        "total_realized_grid_net_cost": _sum(summaries, "realized_grid_net_cost"),
        "total_realized_buy_kwh": _sum(summaries, "realized_buy_kwh"),
        "total_realized_sell_kwh": _sum(summaries, "realized_sell_kwh"),
        "minimum_observed_soc_fraction": min(
            float(row["minimum_observed_soc_fraction"])
            for row in day_rows
            if row["minimum_observed_soc_fraction"] is not None
        ),
        "maximum_reserve_shortfall_kwh": max(
            float(row["maximum_reserve_shortfall_kwh"] or 0.0)
            for row in day_rows
        ),
        "total_baseline_revenue_retention_floor": total_floor,
        "aggregate_revenue_above_floor": (
            total_revenue - total_floor if spec.mode == "altruistic" else None
        ),
        "total_optimizer_calls": _sum(summaries, "optimizer_calls"),
        "total_optimize_decisions": _sum(summaries, "optimize_decisions"),
        "llm_successful_requests": _sum(summaries, "llm_successful_requests"),
        "llm_total_tokens": _sum(summaries, "llm_total_tokens"),
        "llm_approximate_cost_usd": _sum(
            summaries, "llm_approximate_cost_usd"
        ),
        "total_run_wall_seconds": _sum(summaries, "run_wall_seconds"),
        "solver_names": json.dumps(sorted(all_solver_names)),
        "day_to_day_carryover_max_abs_error_kwh": maximum_carry_error,
        "first_settlement_carryover_max_abs_error_kwh": (
            maximum_observed_carry_error
        ),
        "run_signature_sha256_by_day": json.dumps(run_signatures),
        "initial_day1_energy_kwh_by_bus": day_rows[0][
            "initial_realized_energy_kwh_by_bus"
        ],
        "terminal_day3_energy_kwh_by_bus": day_rows[-1][
            "terminal_realized_energy_kwh_by_bus"
        ],
    }
    return episode, day_rows


def write_outputs(
    output_root: Path,
    episode_rows: list[dict[str, Any]],
    day_rows: list[dict[str, Any]],
    *,
    specs: list[EpisodeSpec],
    args: argparse.Namespace,
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    episode_frame = pd.DataFrame(episode_rows)
    day_frame = pd.DataFrame(day_rows)
    episode_frame.to_csv(
        output_root / "multiday_episodes.csv", index=False, lineterminator="\n"
    )
    day_frame.to_csv(
        output_root / "multiday_days.csv", index=False, lineterminator="\n"
    )
    (output_root / "multiday_episodes.json").write_text(
        json.dumps(episode_rows, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (output_root / "multiday_days.json").write_text(
        json.dumps(day_rows, indent=2, default=str) + "\n", encoding="utf-8"
    )
    method_summary = (
        episode_frame.groupby(
            ["condition", "mode", "configuration", "method"], as_index=False
        )
        .agg(
            n_episodes=("episode_id", "size"),
            n_operationally_feasible=("operationally_feasible", "sum"),
            operational_feasibility_rate=("operationally_feasible", "mean"),
            total_realized_pto_cost_mean=("total_realized_pto_cost", "mean"),
            total_realized_pto_cost_std=("total_realized_pto_cost", "std"),
            total_realized_aggregator_revenue_mean=(
                "total_realized_aggregator_revenue",
                "mean",
            ),
            total_realized_aggregator_revenue_std=(
                "total_realized_aggregator_revenue",
                "std",
            ),
            total_realized_buy_kwh_mean=("total_realized_buy_kwh", "mean"),
            total_realized_sell_kwh_mean=("total_realized_sell_kwh", "mean"),
            minimum_observed_soc_fraction_min=(
                "minimum_observed_soc_fraction",
                "min",
            ),
            maximum_reserve_shortfall_kwh_max=(
                "maximum_reserve_shortfall_kwh",
                "max",
            ),
            maximum_carryover_error_kwh=(
                "day_to_day_carryover_max_abs_error_kwh",
                "max",
            ),
            maximum_first_settlement_carryover_error_kwh=(
                "first_settlement_carryover_max_abs_error_kwh",
                "max",
            ),
            economic_comparison_eligibility_rate=(
                "economic_comparison_eligible",
                "mean",
            ),
            llm_successful_requests_total=("llm_successful_requests", "sum"),
            llm_total_tokens=("llm_total_tokens", "sum"),
            llm_approximate_cost_usd=("llm_approximate_cost_usd", "sum"),
        )
        .sort_values(["condition", "mode", "method"])
    )
    method_summary.to_csv(
        output_root / "multiday_method_summary.csv",
        index=False,
        lineterminator="\n",
    )
    (output_root / "multiday_method_summary.json").write_text(
        method_summary.to_json(orient="records", indent=2) + "\n",
        encoding="utf-8",
    )
    episode_effect_columns = [
        "mode",
        "method",
        "n_derating_episodes",
        "n_nominal_episodes",
        "derating_pto_cost_mean",
        "nominal_pto_cost_mean",
        "pto_cost_delta_derating_minus_nominal",
        "derating_aggregator_revenue_mean",
        "nominal_aggregator_revenue_mean",
        "aggregator_revenue_delta_derating_minus_nominal",
        "derating_grid_net_cost_mean",
        "nominal_grid_net_cost_mean",
        "grid_net_cost_delta_derating_minus_nominal",
        "derating_buy_kwh_mean",
        "nominal_buy_kwh_mean",
        "buy_kwh_delta_derating_minus_nominal",
        "derating_sell_kwh_mean",
        "nominal_sell_kwh_mean",
        "sell_kwh_delta_derating_minus_nominal",
    ]
    episode_effect_rows: list[dict[str, Any]] = []
    for mode in sorted(episode_frame["mode"].unique()):
        nominal = episode_frame.loc[
            (episode_frame["condition"] == "nominal")
            & (episode_frame["mode"] == mode)
        ]
        if nominal.empty:
            continue
        for method, derating in episode_frame.loc[
            (episode_frame["condition"] == "derating")
            & (episode_frame["mode"] == mode)
        ].groupby("method"):
            row: dict[str, Any] = {
                "mode": mode,
                "method": method,
                "n_derating_episodes": len(derating),
                "n_nominal_episodes": len(nominal),
            }
            for source, label in (
                ("total_realized_pto_cost", "pto_cost"),
                ("total_realized_aggregator_revenue", "aggregator_revenue"),
                ("total_realized_grid_net_cost", "grid_net_cost"),
                ("total_realized_buy_kwh", "buy_kwh"),
                ("total_realized_sell_kwh", "sell_kwh"),
            ):
                derating_mean = float(derating[source].mean())
                nominal_mean = float(nominal[source].mean())
                row[f"derating_{label}_mean"] = derating_mean
                row[f"nominal_{label}_mean"] = nominal_mean
                row[f"{label}_delta_derating_minus_nominal"] = (
                    derating_mean - nominal_mean
                )
            episode_effect_rows.append(row)
    episode_effects = pd.DataFrame(
        episode_effect_rows, columns=episode_effect_columns
    )
    episode_effects.to_csv(
        output_root / "multiday_episode_effects.csv",
        index=False,
        lineterminator="\n",
    )
    (output_root / "multiday_episode_effects.json").write_text(
        episode_effects.to_json(orient="records", indent=2) + "\n",
        encoding="utf-8",
    )

    daily_effect_columns = [
        "mode",
        "method",
        "day",
        "n_derating_runs",
        "n_nominal_runs",
        "pto_cost_delta_derating_minus_nominal",
        "aggregator_revenue_delta_derating_minus_nominal",
        "grid_net_cost_delta_derating_minus_nominal",
        "buy_kwh_delta_derating_minus_nominal",
        "sell_kwh_delta_derating_minus_nominal",
        "terminal_minimum_soc_fraction_delta_derating_minus_nominal",
    ]
    daily_effect_rows: list[dict[str, Any]] = []
    for (mode, day), nominal in day_frame.loc[
        day_frame["condition"] == "nominal"
    ].groupby(["mode", "day"]):
        derating_cell = day_frame.loc[
            (day_frame["condition"] == "derating")
            & (day_frame["mode"] == mode)
            & (day_frame["day"] == day)
        ]
        for method, derating in derating_cell.groupby("method"):
            row = {
                "mode": mode,
                "method": method,
                "day": int(day),
                "n_derating_runs": len(derating),
                "n_nominal_runs": len(nominal),
            }
            for source, label in (
                ("realized_pto_cost", "pto_cost"),
                ("realized_aggregator_revenue", "aggregator_revenue"),
                ("realized_grid_net_cost", "grid_net_cost"),
                ("realized_buy_kwh", "buy_kwh"),
                ("realized_sell_kwh", "sell_kwh"),
                (
                    "terminal_minimum_soc_fraction",
                    "terminal_minimum_soc_fraction",
                ),
            ):
                row[f"{label}_delta_derating_minus_nominal"] = float(
                    derating[source].mean() - nominal[source].mean()
                )
            daily_effect_rows.append(row)
    daily_effects = pd.DataFrame(daily_effect_rows, columns=daily_effect_columns)
    daily_effects.to_csv(
        output_root / "multiday_daily_effects.csv",
        index=False,
        lineterminator="\n",
    )
    (output_root / "multiday_daily_effects.json").write_text(
        daily_effects.to_json(orient="records", indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "experiment": "three_day_chained_charger_derating_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "design": {
            "fleet_size": 8,
            "days": 3,
            "timesteps_per_day": 48,
            "planning_design": "daily rolling horizon with exact realized terminal battery energy carried to the next day",
            "single_144_step_perfect_foresight_solve": False,
            "disturbance": "temporary charger cooling derating",
            "affected_chargers": MULTIDAY_CHARGERS,
            "nominal_power_kw": NOMINAL_POWER_KW,
            "derated_power_kw": DERATED_POWER_KW,
            "day_windows": {"day_1": [31, 48], "day_2": [1, 48], "day_3": [1, 30]},
            "daily_case_ids": list(MULTIDAY_DAY_CASES),
            "nominal_control_daily_case_ids": list(MULTIDAY_NOMINAL_DAY_CASES),
            "conditions": list(unique(spec.condition for spec in specs)),
            "configurations": list(unique(spec.configuration for spec in specs)),
            "modes": list(unique(spec.mode for spec in specs)),
            "agent_repetitions": args.agent_repetitions,
            "planned_three_day_episodes": len(specs),
            "planned_daily_runs": len(specs) * 3,
            "nominal_control": (
                "scheduled daily replanning with no physical derating"
                if any(spec.condition == "nominal" for spec in specs)
                else "not included"
            ),
        },
        "controls": {
            "common_hidden_physical_truth": True,
            "hidden_truth_sent_to_llm": False,
            "causal_settlement": True,
            "physical_soc_carryover": True,
            "altruistic_baseline_revenue_retention_fraction_per_day": 0.50,
            "solver_order": "gurobi",
            "solver_time_limit_seconds": args.solver_time_limit,
            "solver_mip_gap": args.solver_mip_gap,
            "model": args.model,
            "trigger_prompt_variant": "daily_handover",
            "maximum_approximate_api_cost_usd": args.max_approximate_api_cost_usd,
        },
        "inputs": {
            "case_manifest": str(CASE_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
            "case_manifest_sha256": sha256(CASE_MANIFEST),
            "notices": str(NOTICE_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
            "notices_sha256": sha256(NOTICE_OUTPUT),
            "physical_events": str(PHYSICAL_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
            "physical_events_sha256": sha256(PHYSICAL_OUTPUT),
            "prompt_sha256": {
                path.name: sha256(path) for path in PROMPT_PATHS
            },
        },
        "execution": {
            "episodes_indexed": len(episode_rows),
            "daily_runs_indexed": len(day_rows),
            "operationally_feasible_episodes": sum(
                bool(row["operationally_feasible"]) for row in episode_rows
            ),
            "total_llm_successful_requests": sum(
                float(row.get("llm_successful_requests") or 0.0)
                for row in episode_rows
            ),
            "total_llm_tokens": sum(
                float(row.get("llm_total_tokens") or 0.0)
                for row in episode_rows
            ),
            "total_approximate_api_cost_usd": sum(
                float(row.get("llm_approximate_cost_usd") or 0.0)
                for row in episode_rows
            ),
            "maximum_carryover_error_kwh": max(
                (
                    float(row["day_to_day_carryover_max_abs_error_kwh"])
                    for row in episode_rows
                ),
                default=0.0,
            ),
            "maximum_first_settlement_carryover_error_kwh": max(
                (
                    float(row["first_settlement_carryover_max_abs_error_kwh"])
                    for row in episode_rows
                ),
                default=0.0,
            ),
        },
        "reuse_validation": {
            "signature_version": 1,
            "requires_matching_inputs_prompts_code_model_and_solver_controls": True,
            "unsigned_legacy_workbooks_are_recomputed": True,
        },
    }
    (output_root / "multiday_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the chained three-day persistent charger-derating experiment."
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--configuration",
        action="append",
        choices=DEFAULT_CONFIGURATIONS,
        default=[],
    )
    parser.add_argument("--mode", action="append", choices=MODES, default=[])
    parser.add_argument("--agent-repetitions", type=int, default=5)
    parser.add_argument(
        "--no-nominal-control",
        action="store_true",
        help="Omit the scheduled no-derating three-day control.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--max-approximate-api-cost-usd", type=float, default=None)
    parser.add_argument("--solver-time-limit", type=float, default=300.0)
    parser.add_argument("--solver-mip-gap", type=float, default=0.02)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--force-agent",
        action="store_true",
        help="Rerun only stochastic Agent episodes while reusing complete deterministic episodes.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    configurations = args.configuration or list(DEFAULT_CONFIGURATIONS)
    modes = args.mode or list(MODES)
    specs = build_specs(
        configurations=configurations,
        modes=modes,
        agent_repetitions=args.agent_repetitions,
        include_nominal_control=not args.no_nominal_control,
    )
    uses_llm = any(spec.stochastic for spec in specs)
    if uses_llm and not args.dry_run:
        if not args.allow_external_llm:
            raise SystemExit(
                "Agent episodes require --allow-external-llm to record explicit authorization."
            )
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is required for Agent episodes")
    if args.max_approximate_api_cost_usd is not None and (
        args.max_approximate_api_cost_usd <= 0
    ):
        raise SystemExit("--max-approximate-api-cost-usd must be positive")
    if args.solver_time_limit <= 0:
        raise SystemExit("--solver-time-limit must be positive")
    if not 0 <= args.solver_mip_gap < 1:
        raise SystemExit("--solver-mip-gap must be in [0,1)")

    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    if args.dry_run:
        print(json.dumps([asdict(spec) | {"episode_id": spec.episode_id} for spec in specs], indent=2))
        return

    os.environ["RT_SOLVER_ORDER"] = "gurobi"
    os.environ["RT_SOLVER_TIME_LIMIT"] = str(args.solver_time_limit)
    os.environ["RT_SOLVER_MIP_GAP"] = str(args.solver_mip_gap)

    episode_rows: list[dict[str, Any]] = []
    day_rows: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, start=1):
        spent = sum(
            float(row.get("llm_approximate_cost_usd") or 0.0)
            for row in episode_rows
        )
        if (
            args.max_approximate_api_cost_usd is not None
            and spent >= args.max_approximate_api_cost_usd
        ):
            write_outputs(
                output_root,
                episode_rows,
                day_rows,
                specs=specs,
                args=args,
            )
            raise SystemExit(
                "Stopped before the next episode because recorded API cost "
                f"{spent:.6f} reached the ceiling."
            )
        print(
            f"[{index}/{len(specs)}] {spec.episode_id}: three chained days",
            flush=True,
        )
        episode, days = run_episode(
            output_root=output_root,
            spec=spec,
            model=args.model,
            force=args.force or (args.force_agent and spec.stochastic),
        )
        episode_rows.append(episode)
        day_rows.extend(days)
        write_outputs(
            output_root,
            episode_rows,
            day_rows,
            specs=specs,
            args=args,
        )
    print(
        f"Indexed {len(episode_rows)} three-day episodes and {len(day_rows)} "
        f"daily runs in {output_root}",
        flush=True,
    )


if __name__ == "__main__":
    main()

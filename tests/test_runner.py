from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pandas as pd

from agentic_workflow.config import WorkflowConfig
from agentic_workflow.agents import AgentBackend
from agentic_workflow.models import (
    EvaluationDecision,
    NoticeInterpretation,
    NoticeUncertaintyAssessment,
    NULL_FEEDBACK,
    PricingDecision,
    TriggerDecision,
)
from agentic_workflow.optimizer import OptimizerBackend
from agentic_workflow.runner import OptimizationCandidate, WorkflowRunner


class FakeOptimizer(OptimizerBackend):
    def optimize(self, payload):
        count = 49 - int(payload["current_timestep"])
        return {
            "status": "complete",
            "is_mock": False,
            "solver_status": "ok/optimal",
            "buy_multipliers": payload["price_guidance"]["buy_multipliers"],
            "sell_multipliers": payload["price_guidance"]["sell_multipliers"],
            "period_boundaries": list(range(1, count + 1)),
            "avg_grid_price": 0.15,
            "avg_buy_price": 0.16,
            "avg_sell_price": 0.1,
            "pto_daily_cost": 1.0,
            "aggregator_revenue": 2.0,
            "total_kwh_bought": 10.0,
            "total_kwh_sold": 5.0,
            "w_buy": [1.0] * count,
            "w_sell": [0.5] * count,
            "energy": [[100.0] * count for _ in range(8)],
        }


class MockOptimizer(OptimizerBackend):
    def optimize(self, payload):
        return {
            "status": "complete",
            "is_mock": True,
            "solver_status": "mock",
            "mock_reason": "test failure",
        }


class DecliningRevenueOptimizer(OptimizerBackend):
    def optimize(self, payload):
        attempt = int(payload["rerun_count"])
        return {
            "status": "complete",
            "is_mock": False,
            "solver_status": "ok/optimal",
            "solver_name": "test",
            "aggregator_revenue": 10.0 if attempt == 0 else 5.0,
            "pto_daily_cost": 1.0,
        }


class AcceptSecondAgent(AgentBackend):
    def trigger(self, context):
        raise NotImplementedError

    def price(self, context, trigger, *, rerun_count, previous, feedback):
        count = int(context["remaining_timesteps"])
        return PricingDecision(
            buy_multipliers=[1.1] * count,
            sell_multipliers=[0.7] * count,
            reasoning=f"attempt {rerun_count}",
            confidence=1,
        )

    def evaluate(self, context, trigger, pricing, result, *, rerun_count):
        return EvaluationDecision(
            accept=rerun_count == 1,
            reasoning="accept second" if rerun_count == 1 else "retry",
            confidence=1,
            feedback=NULL_FEEDBACK,
        )


class RejectEveryAttemptAgent(AcceptSecondAgent):
    def evaluate(self, context, trigger, pricing, result, *, rerun_count):
        return EvaluationDecision(
            accept=False,
            reasoning=f"reject attempt {rerun_count}",
            confidence=1,
            feedback=NULL_FEEDBACK,
        )


def test_isolated_trigger_configurations_resolve_expected_information_paths():
    expected = {
        "oracle_event_trigger": "manual",
        "numerical_event_trigger": "none",
        "rule_text_event_trigger": "rule",
        "agent_trigger_only": "llm",
    }
    runner = WorkflowRunner.__new__(WorkflowRunner)
    for configuration, notice_path in expected.items():
        runner.config = SimpleNamespace(
            notice_path="none", experiment_configuration=configuration
        )
        assert runner._resolved_notice_path() == notice_path


def test_full_day_projection_adds_settled_prefix_and_compares_incumbent():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner.state = SimpleNamespace(
        settlement=[
            {
                "realized_pto_cost": 5.0,
                "realized_aggregator_revenue": 2.0,
            }
        ],
        realtime_plan=pd.DataFrame(
            [
                {"timestep": timestep, "w_buy": 1.0, "w_sell": 0.0}
                for timestep in range(48)
            ]
        ),
        buy_multiplier_schedule={timestep: 1.1 for timestep in range(1, 49)},
        sell_multiplier_schedule={timestep: 0.7 for timestep in range(1, 49)},
    )
    context = {
        "intraday_prices": {
            "prices": [
                {"timestep": timestep, "spot_market": 1.0}
                for timestep in range(2, 49)
            ]
        },
        "da_benchmark": {
            "da_cost_remaining": 40.0,
            "da_revenue_remaining": 4.0,
        },
        "revenue_neutrality": {
            "active": True,
            "full_day_revenue_floor": 4.0,
        },
    }

    runner._update_full_day_accounting(context, timestep=1)
    result = {"pto_daily_cost": -3.0, "aggregator_revenue": 1.0}
    runner._attach_full_day_projection(context, result)

    assert result["remaining_horizon_pto_cost"] == -3.0
    assert result["projected_full_day_pto_cost"] == 2.0
    assert result["projected_full_day_aggregator_revenue"] == 3.0
    assert result["revenue_neutrality_floor"] == 4.0
    assert result["revenue_neutrality_shortfall"] == 1.0
    assert result["revenue_neutrality_compliant"] is False
    assert context["revenue_neutrality"]["remaining_revenue_required"] == 2.0
    assert context["da_benchmark"]["projected_full_day_da_pto_cost"] == 45.0
    assert result["projected_full_day_pto_cost_delta_vs_incumbent"] == -54.7


def test_altruistic_revenue_neutrality_guard_rejects_shortfall_with_actionable_feedback():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner.config = SimpleNamespace(mode="altruistic")
    pricing = PricingDecision(
        buy_multipliers=[1.01, 1.01],
        sell_multipliers=[0.99, 0.99],
        reasoning="PTO-favourable proposal",
        confidence=1.0,
    )
    result = {
        "is_mock": False,
        "solver_status": "ok/optimal",
        "projected_full_day_aggregator_revenue": 4.0,
        "revenue_neutrality_floor": 5.0,
        "revenue_neutrality_shortfall": 1.0,
        "revenue_neutrality_compliant": False,
        "w_buy": [10.0, 10.0],
        "w_sell": [0.0, 0.0],
    }
    context = {
        "planning_start_timestep": 7,
        "intraday_prices": {
            "prices": [
                {"timestep": 7, "spot_market": 1.0},
                {"timestep": 8, "spot_market": 1.0},
            ]
        },
    }
    evaluation = EvaluationDecision(
        accept=True,
        reasoning="PTO cost improved.",
        confidence=1.0,
        feedback=NULL_FEEDBACK,
    )

    guarded = runner._apply_revenue_neutrality_guard(
        context, pricing, result, evaluation
    )

    assert guarded.accept is False
    assert guarded.feedback.reason == "revenue_too_low"
    assert guarded.feedback.priority == "revenue_neutrality"
    assert guarded.feedback.buy_multiplier_adjustment is not None
    assert guarded.feedback.buy_multiplier_adjustment.direction == "raise"


def test_altruistic_candidate_selection_requires_revenue_neutrality_before_cost():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner.config = SimpleNamespace(mode="altruistic")
    pricing = PricingDecision(
        buy_multipliers=[1.01],
        sell_multipliers=[0.99],
        reasoning="test",
        confidence=1.0,
    )
    accepted = EvaluationDecision(
        accept=True,
        reasoning="accepted",
        confidence=1.0,
        feedback=NULL_FEEDBACK,
    )
    compliant = OptimizationCandidate(
        pricing=pricing,
        result={
            "solver_status": "ok/optimal",
            "projected_full_day_pto_cost": 110.0,
            "revenue_neutrality_compliant": True,
            "revenue_neutrality_shortfall": 0.0,
        },
        evaluation=accepted,
        attempt=1,
    )
    cheaper_but_noncompliant = OptimizationCandidate(
        pricing=pricing,
        result={
            "solver_status": "ok/optimal",
            "projected_full_day_pto_cost": 90.0,
            "revenue_neutrality_compliant": False,
            "revenue_neutrality_shortfall": 1.0,
        },
        evaluation=accepted.model_copy(update={"accept": False}),
        attempt=2,
    )

    assert not runner._is_better(cheaper_but_noncompliant, compliant)
    assert runner._is_better(compliant, cheaper_but_noncompliant)


def test_observed_warning_memory_is_separate_from_accepted_optimizer_state():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner.state = SimpleNamespace(
        observed_notice_memory={},
        active_observed_notice_interpretations={},
        notice_memory={},
        active_notice_interpretations={},
    )
    warning = NoticeInterpretation(
        event_id="OPS-104",
        source_type="driver_chat",
        event_type="charger_fault",
        phase="warning",
        affected_chargers=[2],
        effective_timestep=9,
        uncertainty=True,
        uncertainty_details=NoticeUncertaintyAssessment(
            confidence_level=0.45,
            provisional=True,
            recommended_action="request_confirmation",
            rationale="Conditional alarm requires confirmation.",
        ),
    )

    runner._observe_notice(warning)

    assert runner.state.observed_notice_memory["OPS-104"]["phase"] == "warning"
    assert "OPS-104" in runner.state.active_observed_notice_interpretations
    assert runner.state.notice_memory == {}
    assert runner.state.active_notice_interpretations == {}


def _write_excel_bytes(sheets):
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return buffer.getvalue()


def _build_inputs(tmp_path):
    state = tmp_path / "State.xlsx"
    summary = pd.DataFrame(
        [
            {
                "run_timestamp": "2026-01-01T00:00:00Z",
                "mode": "selfish",
                "pto_daily_cost": 10,
                "aggregator_revenue": 1,
                "buy_multipliers": json.dumps([1.1] * 48),
                "sell_multipliers": json.dumps([0.7] * 48),
                "avg_grid_price": 0.1,
            }
        ]
    )
    plan_rows = []
    for timestep in range(48):
        row = {
            "run_timestamp": "2026-01-01T00:00:00Z",
            "mode": "selfish",
            "timestep": timestep,
            "w_buy": 0,
            "w_sell": 0,
        }
        row.update({f"bus_{bus}_kwh": 100.0 for bus in range(1, 9)})
        plan_rows.append(row)
    with pd.ExcelWriter(state) as writer:
        summary.to_excel(writer, sheet_name="day_ahead_summary", index=False)
        pd.DataFrame(plan_rows).to_excel(writer, sheet_name="day_ahead_plan", index=False)

    forecast = tmp_path / "Forecasted.xlsx"
    forecast_energy = pd.DataFrame(
        [
            {"timestep": timestep, **{f"bus_{bus}_kwh": 123.0 for bus in range(1, 9)}}
            for timestep in range(48)
        ]
    )
    forecast_prices = pd.DataFrame({"timestep": range(1, 49), "spot_market": [0.1] * 48})
    with pd.ExcelWriter(forecast) as writer:
        forecast_energy.to_excel(writer, sheet_name="Forecasted Energy", index=False)
        forecast_prices.to_excel(writer, sheet_name="Forecasted", index=False)

    spot = tmp_path / "SpotPrices.xlsx"
    with pd.ExcelWriter(spot) as writer:
        forecast_prices.to_excel(writer, sheet_name="Spot Prices", index=False)

    buses = pd.DataFrame({"bus_id": range(1, 9), "bus_kwh": [365] * 8, "initial_soc": [30] * 8})
    chargers = pd.DataFrame({"charger_id": range(1, 9), "charger_kw": [200] * 8})
    trips = pd.DataFrame(
        {
            "trip_id": range(1, 9),
            "bus_id": range(1, 9),
            "time_begin": ["07:00"] * 8,
            "time_end": ["20:00"] * 8,
            "energy_kwhkm": [1.0] * 8,
            "average_velocity_kmh": [12.0] * 8,
        }
    )
    rt_state = pd.DataFrame(
        {
            "current_timestep": [1] * 8,
            "bus_id": range(1, 9),
            "current_energy_kwh": [100.0] * 8,
            "operation_status": ["idle"] * 8,
        }
    )
    states_zip = tmp_path / "states.zip"
    with ZipFile(states_zip, "w") as archive:
        for timestep in (1, 2):
            current = rt_state.assign(current_timestep=timestep)
            archive.writestr(
                f"states/benchmark_timestep_{timestep:02d}.xlsx",
                _write_excel_bytes(
                    {"Buses": buses, "Chargers": chargers, "Trips": trips, "Realtime state": current}
                ),
            )

    prices_zip = tmp_path / "prices.zip"
    with ZipFile(prices_zip, "w") as archive:
        for timestep in (1, 2):
            curve = forecast_prices.loc[forecast_prices["timestep"] >= timestep]
            archive.writestr(
                f"prices/intraday_prices_t{timestep:02d}.xlsx",
                _write_excel_bytes({"Prices": curve}),
            )

    disturbances = tmp_path / "disturbances.xlsx"
    with pd.ExcelWriter(disturbances) as writer:
        pd.DataFrame(
            [
                {
                    "scenario_id": "price_plus_50",
                    "scenario_family": "price_pct",
                    "scenario_level": 50,
                    "disturbance_sign": 1,
                    "target_scope": "global",
                    "target_bus_id": None,
                    "start_timestep": 2,
                    "end_timestep": 48,
                }
            ]
        ).to_excel(writer, sheet_name="scenarios", index=False)
    return state, forecast, spot, states_zip, prices_zip, disturbances


def test_runner_replaces_branching_merging_and_persistence(tmp_path):
    state, forecast, spot, states_zip, prices_zip, disturbances = _build_inputs(tmp_path)
    output = tmp_path / "result.xlsx"
    config = WorkflowConfig(
        state_workbook=state,
        forecast_workbook=forecast,
        spot_prices_workbook=spot,
        realtime_states=states_zip,
        intraday_prices=prices_zip,
        disturbance_workbook=disturbances,
        output_workbook=output,
        scenario_ids=("price_plus_50",),
        start_timestep=1,
        end_timestep=2,
        agent_backend="rule",
        max_reruns=1,
    )
    runner = WorkflowRunner(config, optimizer=FakeOptimizer())
    assert runner.state.forecast_energy.iloc[0]["bus_1_kwh"] == 123.0
    result = runner.run()
    assert [row["action"] for row in result.logs] == ["skip", "optimize"]
    assert len(result.attempts) == 1
    marker = result.realtime_plan.loc[result.realtime_plan["reoptimized"] == True].iloc[0]
    assert marker["timestep"] == 2
    assert marker["decision_timestep"] == 2
    assert marker["trigger_type"] == "price"
    assert result.logs[-1]["rerun_count"] == 0
    assert output.exists()
    assert output.with_suffix(".agent_calls.jsonl").exists()
    assert output.with_suffix(".run_summary.json").exists()
    sheet_names = pd.ExcelFile(output).sheet_names
    assert "optimization_attempts" in sheet_names
    assert "agent_calls" in sheet_names
    assert "resource_usage" in sheet_names
    assert "run_summary" in sheet_names
    assert "ex_post_settlement" in sheet_names
    agent_calls = pd.read_excel(output, sheet_name="agent_calls")
    assert "input_tokens" in agent_calls.columns
    assert "approximate_cost_usd" in agent_calls.columns
    resources = pd.read_excel(output, sheet_name="resource_usage")
    assert resources["scope"].tolist() == ["timestep", "timestep", "run"]
    summary = pd.read_excel(output, sheet_name="run_summary").iloc[0]
    assert summary["status"] == "complete"
    assert summary["timesteps_completed"] == 2
    assert summary["optimizer_calls"] == 1
    assert summary["evaluator_accepted_optimizer_calls"] == 1
    assert summary["forced_optimizer_selections"] == 0
    assert summary["llm_total_tokens"] == 0
    # The optimization at timestep 2 changes only future intervals.  It must
    # not retroactively change the just-observed timestep-1 settlement.
    assert summary["realized_pto_cost"] == 0
    settlement = pd.read_excel(output, sheet_name="ex_post_settlement")
    assert settlement["planned_buy_kwh"].tolist() == [0, 0]


def test_excel_agent_call_log_points_overlong_cells_to_full_jsonl_sidecar():
    from agentic_workflow.state import _excel_safe_agent_calls

    long_request = "x" * 40_000
    frame = pd.DataFrame(
        [{"request": long_request, "parsed_output": "short"}]
    )
    safe = _excel_safe_agent_calls(frame, "result.agent_calls.jsonl")

    assert safe.loc[0, "parsed_output"] == "short"
    assert "result.agent_calls.jsonl" in safe.loc[0, "request"]
    assert "JSONL row 1" in safe.loc[0, "request"]
    assert "characters=40000" in safe.loc[0, "request"]
    assert "sha256=" in safe.loc[0, "request"]
    assert frame.loc[0, "request"] == long_request


def test_optimizer_never_accepts_an_all_mock_rerun_sequence(tmp_path):
    state, forecast, spot, states_zip, prices_zip, disturbances = _build_inputs(tmp_path)
    config = WorkflowConfig(
        state_workbook=state,
        forecast_workbook=forecast,
        spot_prices_workbook=spot,
        realtime_states=states_zip,
        intraday_prices=prices_zip,
        disturbance_workbook=disturbances,
        output_workbook=tmp_path / "result.xlsx",
        scenario_ids=("price_plus_50",),
        start_timestep=1,
        end_timestep=2,
        agent_backend="rule",
        max_reruns=2,
    )
    runner = WorkflowRunner(config, optimizer=MockOptimizer())
    workbook = runner._workbook_inputs(2)
    price_rows = runner.price_series.read_sheet(2, "Prices")
    context = {
        "mode": "selfish",
        "timestep": 2,
        "remaining_timesteps": 47,
        "intraday_prices": {
            "prices": [
                {"timestep": int(row.timestep), "spot_market": float(row.spot_market), "price_zone": "transition"}
                for row in price_rows.itertuples()
            ]
        },
        "da_benchmark": {"da_benchmark_valid": False},
    }
    disturbance = SimpleNamespace(
        trips=workbook["Trips"],
        energy_consumption=workbook["Trips"],
        prices=price_rows,
        scenarios=[],
        optimizer_disturbances=[],
    )
    trigger = TriggerDecision(
        action="optimize",
        reasoning="test",
        confidence=1,
        trigger_type="price",
        flagged_buses=[],
    )
    selected = runner._optimize(
        timestep=2,
        context=context,
        trigger=trigger,
        workbook=workbook,
        observation=[],
        disturbance=disturbance,
    )
    assert selected.result["is_mock"] is True
    assert selected.evaluation.accept is False
    assert len(runner.state.attempts) == 3
    assert not any(row["accepted"] for row in runner.state.attempts)


def test_better_earlier_schedule_outranks_worse_evaluator_accepted_rerun(tmp_path):
    state, forecast, spot, states_zip, prices_zip, disturbances = _build_inputs(tmp_path)
    config = WorkflowConfig(
        state_workbook=state,
        forecast_workbook=forecast,
        spot_prices_workbook=spot,
        realtime_states=states_zip,
        intraday_prices=prices_zip,
        disturbance_workbook=disturbances,
        output_workbook=tmp_path / "result.xlsx",
        scenario_ids=("price_plus_50",),
        start_timestep=1,
        end_timestep=2,
        agent_backend="rule",
        max_reruns=2,
    )
    runner = WorkflowRunner(
        config,
        agents=AcceptSecondAgent(),
        optimizer=DecliningRevenueOptimizer(),
    )
    workbook = runner._workbook_inputs(2)
    price_rows = runner.price_series.read_sheet(2, "Prices")
    context = {
        "mode": "selfish",
        "timestep": 2,
        "remaining_timesteps": 47,
        "intraday_prices": {
            "prices": [
                {"timestep": int(row.timestep), "spot_market": float(row.spot_market)}
                for row in price_rows.itertuples()
            ]
        },
    }
    disturbance = SimpleNamespace(
        trips=workbook["Trips"],
        energy_consumption=workbook["Trips"],
        prices=price_rows,
        scenarios=[],
        optimizer_disturbances=[],
    )
    trigger = TriggerDecision(
        action="optimize",
        reasoning="test",
        confidence=1,
        trigger_type="price",
        flagged_buses=[],
    )
    selected = runner._optimize(
        timestep=2,
        context=context,
        trigger=trigger,
        workbook=workbook,
        observation=[],
        disturbance=disturbance,
    )
    assert selected.attempt == 1
    assert selected.result["aggregator_revenue"] == 10.0
    accepted = [row for row in runner.state.attempts if row["accepted"]]
    assert len(accepted) == 1
    assert accepted[0]["attempt"] == 1
    assert accepted[0]["selected_for_execution"] is True
    assert accepted[0]["evaluator_accepted"] is False
    assert accepted[0]["forced_at_rerun_cap"] is False
    assert accepted[0]["retained_better_candidate"] is True
    assert "better mode-aligned economic objective" in accepted[0]["selection_reasoning"]
    assert all("proposed_w_buy_kwh" in row for row in runner.state.attempts)
    assert all(row["reference_is_guidance_only"] for row in runner.state.attempts)
    assert all("buy_arithmetic_mean_gap" in row for row in runner.state.attempts)
    assert all("buy_centered_temporal_mae" in row for row in runner.state.attempts)


def test_forced_cap_selection_preserves_original_evaluator_rejections(tmp_path):
    state, forecast, spot, states_zip, prices_zip, disturbances = _build_inputs(tmp_path)
    config = WorkflowConfig(
        state_workbook=state,
        forecast_workbook=forecast,
        spot_prices_workbook=spot,
        realtime_states=states_zip,
        intraday_prices=prices_zip,
        disturbance_workbook=disturbances,
        output_workbook=tmp_path / "result.xlsx",
        scenario_ids=("price_plus_50",),
        start_timestep=1,
        end_timestep=2,
        agent_backend="rule",
        max_reruns=1,
    )
    runner = WorkflowRunner(
        config,
        agents=RejectEveryAttemptAgent(),
        optimizer=DecliningRevenueOptimizer(),
    )
    workbook = runner._workbook_inputs(2)
    price_rows = runner.price_series.read_sheet(2, "Prices")
    context = {
        "mode": "selfish",
        "timestep": 2,
        "remaining_timesteps": 47,
        "intraday_prices": {
            "prices": [
                {"timestep": int(row.timestep), "spot_market": float(row.spot_market)}
                for row in price_rows.itertuples()
            ]
        },
    }
    disturbance = SimpleNamespace(
        trips=workbook["Trips"],
        energy_consumption=workbook["Trips"],
        prices=price_rows,
        scenarios=[],
        optimizer_disturbances=[],
    )
    trigger = TriggerDecision(
        action="optimize",
        reasoning="test",
        confidence=1,
        trigger_type="price",
        flagged_buses=[],
    )

    selected = runner._optimize(
        timestep=2,
        context=context,
        trigger=trigger,
        workbook=workbook,
        observation=[],
        disturbance=disturbance,
    )

    assert selected.attempt == 1
    assert selected.result["aggregator_revenue"] == 10.0
    assert selected.evaluation.accept is True
    selected_row = next(row for row in runner.state.attempts if row["accepted"])
    assert selected_row["attempt"] == 1
    assert selected_row["selected_for_execution"] is True
    assert selected_row["evaluator_accepted"] is False
    assert selected_row["forced_at_rerun_cap"] is True
    assert selected_row["retained_better_candidate"] is False
    assert selected_row["evaluation_reasoning"] == "reject attempt 0"
    assert "Rerun cap of 1 reached" in selected_row["selection_reasoning"]
    assert not any(row["evaluator_accepted"] for row in runner.state.attempts)

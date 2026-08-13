from __future__ import annotations

import time
from types import SimpleNamespace

import pyomo.environ as pyo

from agentic_workflow.telemetry import ResourceMeter, summarize_agent_calls, system_profile
from app_rt import _extract_solver_telemetry


def test_resource_meter_reports_reproducible_local_proxies():
    with ResourceMeter(sample_interval_seconds=0.005) as meter:
        sum(index * index for index in range(20_000))
        time.sleep(0.01)

    metrics = meter.metrics
    assert metrics is not None
    assert metrics["wall_seconds"] >= 0.01
    assert metrics["process_cpu_seconds"] >= 0
    assert metrics["average_cpu_cores"] >= 0
    assert metrics["logical_cpu_count"] >= 1
    if metrics["memory_sampler_available"]:
        assert metrics["peak_rss_mb"] > 0
        assert metrics["peak_rss_delta_mb"] >= 0


def test_agent_call_summary_aggregates_token_and_cost_fields():
    summary = summarize_agent_calls(
        [
            {
                "schema_valid": True,
                "input_tokens": 100,
                "cached_input_tokens": 20,
                "cache_write_tokens": 30,
                "uncached_input_tokens": 50,
                "output_tokens": 10,
                "reasoning_tokens": 4,
                "total_tokens": 110,
                "latency_seconds": 1.25,
                "approximate_cost_usd": 0.001,
            },
            {
                "schema_valid": False,
                "latency_seconds": 0.5,
            },
        ]
    )
    assert summary["llm_request_attempts"] == 2
    assert summary["llm_successful_requests"] == 1
    assert summary["llm_failed_attempts"] == 1
    assert summary["llm_total_tokens"] == 110
    assert summary["llm_latency_seconds"] == 1.75
    assert summary["llm_approximate_cost_usd"] == 0.001


def test_system_profile_declares_local_and_provider_measurement_boundaries():
    profile = system_profile()
    assert profile["logical_cpu_count"] >= 1
    assert "current Python process" in profile["local_measurement_scope"]
    assert "not exposed" in profile["provider_compute_scope"]


def test_solver_telemetry_extracts_standard_results_and_model_size():
    model = pyo.ConcreteModel()
    model.x = pyo.Var(domain=pyo.Binary)
    model.limit = pyo.Constraint(expr=model.x <= 1)
    solved = SimpleNamespace(
        solver=SimpleNamespace(
            time=0.25,
            user_time=0.2,
            system_time=0.05,
            statistics=SimpleNamespace(
                branch_and_bound=SimpleNamespace(number_of_bounded_subproblems=3),
                black_box=SimpleNamespace(number_of_iterations=7),
            ),
        ),
        problem=SimpleNamespace(lower_bound=9.0, upper_bound=10.0),
    )
    telemetry = _extract_solver_telemetry(
        solved, model, {"wall_seconds": 0.3, "process_cpu_seconds": 0.2}
    )
    assert telemetry["reported_time_seconds"] == 0.25
    assert telemetry["branch_and_bound_nodes"] == 3
    assert telemetry["iterations"] == 7
    assert telemetry["model_variables"] == 1
    assert telemetry["model_constraints"] == 1
    assert telemetry["model_binary_variables"] == 1
    assert telemetry["relative_gap"] == 0.1

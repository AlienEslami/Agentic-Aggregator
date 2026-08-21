from __future__ import annotations

import pandas as pd
import pytest

from scripts.analyze_advance_warning_matrix import (
    annotate_runs,
    build_ablation_contrasts,
    build_evaluator_attempt_audit,
    build_primary_pairs,
    settlement_secondary_outcomes,
    summarize_pairs,
)
from scripts.run_advance_warning_matrix import (
    ABLATION_PROTOCOL_PATH,
    NONAGENTIC_BASELINE_CONFIGURATIONS,
    ROLE_ABLATION_CONFIGURATIONS,
    build_run_specs,
    read_solver_provenance,
    require_gurobi_only,
    should_reuse_workbook,
    validate_ablation_protocol,
    validate_external_llm_gate,
    validate_execution_budget,
    validate_resume_fingerprints,
    validate_solver_time_limit,
    workbook_path,
)


HISTORICAL_ABLATION_PROTOCOL_PATH = (
    ABLATION_PROTOCOL_PATH.parent / "advance_warning_ablation_protocol_v6.json"
)
NONAGENTIC_PROTOCOL_PATH = (
    ABLATION_PROTOCOL_PATH.parent / "advance_warning_ablation_protocol_v7.json"
)


def test_final_matrix_rejects_solver_fallback(tmp_path):
    workbook = tmp_path / "run.xlsx"
    require_gurobi_only(
        {"solver_names": ["gurobi"], "solver_fallback_errors": []}, workbook
    )
    with pytest.raises(ValueError, match="requires Gurobi with no fallback"):
        require_gurobi_only(
            {"solver_names": ["appsi_highs", "gurobi"], "solver_fallback_errors": []},
            workbook,
        )


def test_final_matrix_accepts_explicit_zero_optimizer_call_episode(tmp_path):
    workbook = tmp_path / "no-trigger.xlsx"

    require_gurobi_only(
        {"solver_names": [], "solver_fallback_errors": []},
        workbook,
        optimizer_calls=0,
    )
    with pytest.raises(ValueError, match="requires Gurobi with no fallback"):
        require_gurobi_only(
            {"solver_names": [], "solver_fallback_errors": []},
            workbook,
            optimizer_calls=1,
        )


def test_matrix_design_separates_primary_and_secondary_repetitions(tmp_path):
    specs = build_run_specs(
        cases=["aw_route6_late_return"],
        modes=["selfish"],
        include_agent=True,
        agent_repetitions=3,
        include_role_ablations=True,
        ablation_repetitions=2,
    )
    assert len(specs) == 3 + 3 + (4 * 2)
    assert sum(spec.configuration == "agent_trigger_only" for spec in specs) == 3
    assert sum(spec.run_family == "secondary_role_ablation" for spec in specs) == 8
    deterministic = next(
        spec for spec in specs if spec.configuration == "oracle_event_trigger"
    )
    stochastic = next(
        spec for spec in specs if spec.configuration == "agent_trigger_only"
    )
    assert workbook_path(tmp_path, deterministic).name == "oracle_event_trigger.xlsx"
    assert workbook_path(tmp_path, stochastic).name == "agent_trigger_only_rep_001.xlsx"


def test_isolated_ablation_pilot_has_no_primary_workbooks():
    specs = build_run_specs(
        include_role_ablations=True,
        ablation_repetitions=1,
        include_primary_deterministic=False,
    )
    assert len(specs) == 24
    assert all(spec.run_family == "secondary_role_ablation" for spec in specs)
    assert {
        spec.configuration for spec in specs
    } == set(ROLE_ABLATION_CONFIGURATIONS)


def test_isolated_ablation_pilot_can_select_only_full_agentic():
    specs = build_run_specs(
        cases=["aw_route6_late_return", "aw_combined_evening"],
        modes=["selfish"],
        include_role_ablations=True,
        ablation_repetitions=5,
        role_ablation_configurations=["full_agentic"],
        include_primary_deterministic=False,
    )
    assert len(specs) == 10
    assert {spec.configuration for spec in specs} == {"full_agentic"}
    assert {spec.case for spec in specs} == {
        "aw_route6_late_return",
        "aw_combined_evening",
    }
    assert {spec.repetition for spec in specs} == set(range(1, 6))


def test_stochastic_only_force_never_reuses_agent_but_keeps_fixed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "scripts.run_advance_warning_matrix.workbook_is_complete",
        lambda workbook, expected_timesteps: True,
    )
    specs = build_run_specs(
        cases=["aw_route6_late_return"],
        modes=["selfish"],
        include_agent=True,
        agent_repetitions=1,
    )
    deterministic = next(spec for spec in specs if not spec.stochastic)
    stochastic = next(spec for spec in specs if spec.stochastic)
    workbook = tmp_path / "complete.xlsx"
    assert should_reuse_workbook(
        workbook, 48, deterministic, force=False, force_stochastic=True
    )
    assert not should_reuse_workbook(
        workbook, 48, stochastic, force=False, force_stochastic=True
    )


def test_ablation_protocol_is_frozen_and_matches_runner():
    protocol = validate_ablation_protocol()
    assert ABLATION_PROTOCOL_PATH.exists()
    assert protocol["status"] == (
        "implemented_and_frozen_pending_300_second_execution"
    )
    assert tuple(protocol["design"]["configurations"]) == ROLE_ABLATION_CONFIGURATIONS
    assert protocol["design"]["planned_runs"] == 120
    assert protocol["design"]["modes"] == ["selfish", "altruistic"]
    assert protocol["controls"]["shared_trigger_evidence_change_gate"] is True
    assert protocol["controls"]["maximum_pricing_reruns"] == 1
    assert protocol["controls"]["maximum_optimizer_attempts_per_trigger"] == 2
    assert protocol["controls"]["solver_time_limit_seconds"] == 300
    retention = protocol["controls"]["altruistic_baseline_revenue_retention"]
    assert retention["active"] is True
    assert retention["retention_fraction"] == 0.5
    assert retention["expected_retention_floor_for_current_input"] == 9.214257
    assert protocol["outcomes"][
        "battery_throughput_in_primary_or_secondary_contrasts"
    ] is False


def test_solver_time_limit_must_match_selected_frozen_protocol():
    current = validate_ablation_protocol()
    historical = validate_ablation_protocol(HISTORICAL_ABLATION_PROTOCOL_PATH)

    validate_solver_time_limit(current, 300.0)
    validate_solver_time_limit(historical, 60.0)
    with pytest.raises(ValueError, match="must match the frozen protocol value"):
        validate_solver_time_limit(current, 60.0)
    with pytest.raises(ValueError, match="must match the frozen protocol value"):
        validate_solver_time_limit(historical, 300.0)


def test_resume_rejects_changed_frozen_inputs(monkeypatch, tmp_path):
    (tmp_path / "matrix_manifest.json").write_text(
        '{"inputs":{"notice_sha256":"old","physical_event_sha256":"old",'
        '"ablation_protocol_sha256":"old"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.run_advance_warning_matrix.current_input_fingerprints",
        lambda protocol_path=None, notice_path=None, physical_path=None: {
            "notice_sha256": "new",
            "physical_event_sha256": "new",
            "ablation_protocol_sha256": "new",
        },
    )
    with pytest.raises(ValueError, match="different frozen inputs"):
        validate_resume_fingerprints(tmp_path, force=False)
    assert not validate_resume_fingerprints(tmp_path, force=True)


def test_external_llm_gate_requires_explicit_authorization_and_key():
    specs = build_run_specs(
        cases=["aw_route6_late_return"],
        modes=["selfish"],
        include_agent=True,
        agent_repetitions=1,
    )
    with pytest.raises(ValueError, match="allow-external-llm"):
        validate_external_llm_gate(
            specs, allow_external_llm=False, dry_run=False, environ={}
        )
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validate_external_llm_gate(
            specs, allow_external_llm=True, dry_run=False, environ={}
        )
    validate_external_llm_gate(
        specs,
        allow_external_llm=True,
        dry_run=False,
        environ={"OPENAI_API_KEY": "test-only-placeholder"},
    )


def test_execution_budget_stops_before_next_episode_at_ceiling():
    rows = [{"llm_approximate_cost_usd": 0.04}, {"llm_approximate_cost_usd": 0.06}]
    validate_execution_budget(rows, 0.11)
    with pytest.raises(RuntimeError, match="cost ceiling reached"):
        validate_execution_budget(rows, 0.10)
    with pytest.raises(ValueError, match="must be positive"):
        validate_execution_budget(rows, 0)


def test_solver_provenance_records_solver_and_fallback(monkeypatch, tmp_path):
    def fake_read_excel(path, sheet_name):
        assert sheet_name == "optimization_attempts"
        return pd.DataFrame(
            {
                "solver_name": ["appsi_highs", "appsi_highs"],
                "solver_fallback_errors": [
                    '["gurobi: HostID mismatch"]',
                    '["gurobi: HostID mismatch"]',
                ],
            }
        )

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)
    provenance = read_solver_provenance(tmp_path / "result.xlsx")
    assert provenance["solver_names"] == ["appsi_highs"]
    assert provenance["solver_fallback_errors"] == [
        '["gurobi: HostID mismatch"]'
    ]


def _run(
    configuration: str,
    repetition: int,
    *,
    revenue: float,
    pto_cost: float,
    shortfall: float = 0.0,
) -> dict:
    return {
        "configuration": configuration,
        "method": configuration,
        "run_family": "primary_trigger_comparison",
        "case": "aw_route6_late_return",
        "variant": "uncertain_chat",
        "mode": "selfish",
        "repetition": repetition,
        "status": "complete",
        "realized_aggregator_revenue": revenue,
        "realized_pto_cost": pto_cost,
        "maximum_reserve_shortfall_kwh": shortfall,
        "reserve_violation_timesteps": int(shortfall > 0),
        "minimum_observed_soc_fraction": 0.2 if shortfall == 0 else 0.18,
        "terminal_minimum_soc_fraction": 0.21 if shortfall == 0 else 0.18,
        "llm_total_tokens": 100 if configuration == "agent_trigger_only" else 0,
        "llm_approximate_cost_usd": 0.001 if configuration == "agent_trigger_only" else 0,
        "optimize_decisions": 2,
        "evaluator_accepted_optimizer_calls": 1,
        "forced_optimizer_selections": 1,
    }


def test_paired_analysis_reports_economics_only_when_both_runs_are_feasible():
    raw = pd.DataFrame(
        [
            _run("rule_text_event_trigger", 1, revenue=10, pto_cost=20),
            _run(
                "numerical_event_trigger",
                1,
                revenue=30,
                pto_cost=10,
                shortfall=5,
            ),
            _run("oracle_event_trigger", 1, revenue=12, pto_cost=18),
            _run("agent_trigger_only", 1, revenue=15, pto_cost=19),
            _run(
                "agent_trigger_only",
                2,
                revenue=40,
                pto_cost=8,
                shortfall=4,
            ),
        ]
    )
    runs = annotate_runs(raw)
    pairs = build_primary_pairs(runs)

    first_rule = pairs[
        pairs["contrast"].eq("agent_vs_rule_text") & pairs["repetition"].eq(1)
    ].iloc[0]
    assert first_rule["valid_economic_comparison"]
    assert first_rule["mode_aligned_economic_gain"] == 5

    first_numerical = pairs[
        pairs["contrast"].eq("agent_vs_numerical") & pairs["repetition"].eq(1)
    ].iloc[0]
    assert first_numerical["feasibility_outcome"] == "candidate_only_feasible"
    assert not first_numerical["valid_economic_comparison"]
    assert pd.isna(first_numerical["mode_aligned_economic_gain"])

    summary = summarize_pairs(pairs, bootstrap_iterations=100)
    numerical = summary[summary["contrast"].eq("agent_vs_numerical")].iloc[0]
    assert numerical["comparable_feasible_pairs"] == 0
    assert numerical["candidate_feasible_rate"] == 0.5
    assert numerical["baseline_feasible_rate"] == 0.0


def test_altruistic_economics_require_revenue_neutrality_compliance():
    rows = [
        _run("rule_text_event_trigger", 1, revenue=10, pto_cost=20),
        _run("numerical_event_trigger", 1, revenue=10, pto_cost=20),
        _run("oracle_event_trigger", 1, revenue=10, pto_cost=20),
        _run("agent_trigger_only", 1, revenue=9, pto_cost=15),
    ]
    for row in rows:
        row["mode"] = "altruistic"
        row["revenue_neutrality_compliant"] = True
        row["revenue_neutrality_floor"] = 10.0
        row["revenue_neutrality_shortfall"] = 0.0
    rows[-1]["revenue_neutrality_compliant"] = False
    rows[-1]["revenue_neutrality_shortfall"] = 1.0

    pairs = build_primary_pairs(annotate_runs(pd.DataFrame(rows)))
    comparison = pairs[pairs["contrast"].eq("agent_vs_rule_text")].iloc[0]

    assert comparison["candidate_feasible"]
    assert not comparison["candidate_revenue_neutrality_compliant"]
    assert not comparison["valid_economic_comparison"]
    assert pd.isna(comparison["mode_aligned_economic_gain"])


def test_role_ablation_uses_independent_sample_difference():
    rows = []
    for repetition, revenue in enumerate((20, 30), start=1):
        row = _run(
            "full_agentic",
            repetition,
            revenue=revenue,
            pto_cost=20,
        )
        row["run_family"] = "secondary_role_ablation"
        rows.append(row)
    for repetition, revenue in enumerate((10, 14, 18), start=1):
        row = _run(
            "rule_parser_trigger_substitution",
            repetition,
            revenue=revenue,
            pto_cost=20,
        )
        row["run_family"] = "secondary_role_ablation"
        rows.append(row)

    contrasts = build_ablation_contrasts(
        annotate_runs(pd.DataFrame(rows)), bootstrap_iterations=100
    )
    result = contrasts[
        contrasts["contrast"].eq("llm_trigger_contribution")
    ].iloc[0]
    assert result["candidate_runs"] == 2
    assert result["baseline_runs"] == 3
    assert result["mode_aligned_economic_gain_mean"] == 11
    assert result["candidate_evaluator_acceptance_rate"] == 0.5
    assert result["baseline_evaluator_acceptance_rate"] == 0.5
    assert result["candidate_forced_selection_rate"] == 0.5


def test_paired_analysis_treats_sub_milliscale_solver_noise_as_a_tie():
    raw = pd.DataFrame(
        [
            _run("rule_text_event_trigger", 1, revenue=10.0, pto_cost=20),
            _run("numerical_event_trigger", 1, revenue=10.0, pto_cost=20),
            _run("oracle_event_trigger", 1, revenue=10.0, pto_cost=20),
            _run("agent_trigger_only", 1, revenue=10.0000018, pto_cost=20),
        ]
    )
    summary = summarize_pairs(
        build_primary_pairs(annotate_runs(raw)),
        bootstrap_iterations=100,
        economic_tie_tolerance=1e-3,
    )
    comparison = summary[summary["contrast"].eq("agent_vs_rule_text")].iloc[0]
    assert comparison["economic_wins"] == 0
    assert comparison["economic_ties"] == 1
    assert comparison["economic_losses"] == 0


def test_secondary_outcomes_measure_price_aligned_flexibility_and_throughput():
    outcomes = settlement_secondary_outcomes(
        pd.DataFrame(
            {
                "spot_price": [1.0, 2.0, 3.0, 4.0],
                "realized_buy_kwh": [10.0, 20.0, 0.0, 0.0],
                "realized_sell_kwh": [0.0, 0.0, 5.0, 15.0],
            }
        )
    )

    assert outcomes["downward_flexibility_cheap_buy_kwh"] == 30.0
    assert outcomes["upward_flexibility_expensive_sell_kwh"] == 20.0
    assert outcomes["price_aligned_flexibility_kwh"] == 50.0
    assert outcomes["cheap_period_buy_share"] == 1.0
    assert outcomes["expensive_period_sell_share"] == 1.0
    assert outcomes["energy_weighted_average_buy_grid_price"] == pytest.approx(5 / 3)
    assert outcomes["energy_weighted_average_sell_grid_price"] == pytest.approx(3.75)
    assert outcomes["peak_net_import_kwh_per_interval"] == 20.0
    assert outcomes["battery_throughput_proxy_kwh"] == 50.0


def test_evaluator_audit_uses_projected_full_day_economics(monkeypatch, tmp_path):
    attempts = pd.DataFrame(
        {
            "timestep": [11, 11],
            "attempt": [1, 2],
            "mode": ["altruistic", "altruistic"],
            "pto_daily_cost": [-40.0, -30.0],
            "projected_full_day_pto_cost": [120.0, 110.0],
            "aggregator_revenue": [1.0, 1.0],
            "projected_full_day_aggregator_revenue": [1.0, 1.0],
            "solver_status": ["ok/optimal", "ok/optimal"],
            "is_mock": [False, False],
            "accepted": [False, True],
            "evaluator_accepted": [False, False],
            "total_kwh_bought": [0.0, 0.0],
            "total_kwh_sold": [5.0, 5.0],
            "feedback": [
                '{"reason":"cost_too_high",'
                '"buy_multiplier_adjustment":{"direction":"lower"}}',
                '{"reason":"cost_too_high",'
                '"sell_multiplier_adjustment":{"direction":"raise"}}',
            ],
        }
    )
    monkeypatch.setattr(pd, "read_excel", lambda *args, **kwargs: attempts.copy())
    runs = pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "configuration": "full_agentic",
                "case": "case-1",
                "variant": "uncertain_chat",
                "mode": "altruistic",
                "repetition": 1,
                "workbook": str(tmp_path / "run.xlsx"),
            }
        ]
    )

    attempt_audit, decision_audit = build_evaluator_attempt_audit(
        runs, repository_root=tmp_path
    )

    decision = decision_audit.iloc[0]
    assert decision["objective_metric"] == "projected_full_day_pto_cost"
    assert decision["best_usable_attempt"] == 2
    assert decision["best_usable_economic_attempt"] == 2
    assert decision["selected_is_best_usable_candidate"]
    assert decision["selected_is_best_usable_objective"]
    assert decision["selected_objective_regret"] == 0
    assert decision["rejected_negative_pto_cost_count"] == 0
    assert attempt_audit["rejection_feedback_no_op_on_current_schedule"].tolist() == [
        True,
        False,
    ]


def test_evaluator_audit_ranks_revenue_neutral_candidate_before_lower_pto_cost(
    monkeypatch, tmp_path
):
    attempts = pd.DataFrame(
        {
            "timestep": [11, 11],
            "attempt": [1, 2],
            "mode": ["altruistic", "altruistic"],
            "projected_full_day_pto_cost": [100.0, 105.0],
            "projected_full_day_aggregator_revenue": [9.0, 10.0],
            "revenue_neutrality_compliant": [False, True],
            "revenue_neutrality_shortfall": [1.0, 0.0],
            "solver_status": ["ok/optimal", "ok/optimal"],
            "is_mock": [False, False],
            "accepted": [False, True],
            "evaluator_accepted": [False, True],
            "total_kwh_bought": [100.0, 100.0],
            "total_kwh_sold": [0.0, 0.0],
            "feedback": [
                '{"reason":"revenue_too_low",'
                '"buy_multiplier_adjustment":{"direction":"raise"}}',
                "{}",
            ],
        }
    )
    monkeypatch.setattr(pd, "read_excel", lambda *args, **kwargs: attempts.copy())
    runs = pd.DataFrame(
        [
            {
                "run_id": "run-revenue-neutral",
                "configuration": "full_agentic",
                "case": "case-1",
                "variant": "uncertain_chat",
                "mode": "altruistic",
                "repetition": 1,
                "workbook": str(tmp_path / "run.xlsx"),
            }
        ]
    )

    _, decisions = build_evaluator_attempt_audit(
        runs, repository_root=tmp_path
    )
    decision = decisions.iloc[0]

    assert decision["best_usable_attempt"] == 2
    assert decision["best_usable_economic_attempt"] == 1
    assert decision["selected_is_best_usable_candidate"]
    assert not decision["accepted_rerun_worse_than_earlier_usable_attempt"]
    assert decision[
        "accepted_rerun_economically_worse_than_earlier_usable_attempt"
    ]


def test_evaluator_audit_treats_satisfied_operator_priority_as_lexicographic(
    monkeypatch, tmp_path
):
    attempts = pd.DataFrame(
        {
            "timestep": [5, 5],
            "attempt": [1, 2],
            "mode": ["selfish", "selfish"],
            "projected_full_day_aggregator_revenue": [13.0, 7.0],
            "projected_full_day_pto_cost": [140.0, 141.0],
            "solver_status": ["ok/optimal", "ok/optimal"],
            "is_mock": [False, False],
            "accepted": [False, True],
            "evaluator_accepted": [False, True],
            "total_kwh_bought": [0.0, 100.0],
            "total_kwh_sold": [0.0, 0.0],
            "feedback": ["{}", "{}"],
            "priority_assessment": [
                '{"applicable":true,"satisfied":false}',
                '{"applicable":true,"satisfied":true}',
            ],
        }
    )
    monkeypatch.setattr(pd, "read_excel", lambda *args, **kwargs: attempts.copy())
    runs = pd.DataFrame(
        [
            {
                "run_id": "run-priority",
                "configuration": "full_agentic",
                "case": "case-priority",
                "variant": "uncertain_chat",
                "mode": "selfish",
                "repetition": 1,
                "workbook": str(tmp_path / "priority.xlsx"),
            }
        ]
    )

    _, decisions = build_evaluator_attempt_audit(runs, repository_root=tmp_path)
    decision = decisions.iloc[0]
    assert decision["best_usable_attempt"] == 2
    assert decision["best_usable_economic_attempt"] == 1
    assert decision["selected_is_best_usable_candidate"]
    assert not decision["selected_is_best_usable_objective"]
    assert not decision["accepted_rerun_worse_than_earlier_usable_attempt"]
    assert decision[
        "accepted_rerun_economically_worse_than_earlier_usable_attempt"
    ]


def test_nonagentic_stack_baseline_protocol_v7_is_frozen_and_matches_runner():
    protocol = validate_ablation_protocol(NONAGENTIC_PROTOCOL_PATH)

    assert NONAGENTIC_PROTOCOL_PATH.exists()
    assert protocol["protocol_version"] == "advance_warning_ablation_v7"
    assert protocol["design"]["modes"] == ["selfish", "altruistic"]
    baseline = protocol["design"]["nonagentic_stack_baseline"]
    assert tuple(baseline["configurations"]) == NONAGENTIC_BASELINE_CONFIGURATIONS
    assert baseline["uses_external_llm"] is False
    assert baseline["repetitions_per_configuration_case_mode"] == 1
    # Three cases times two modes, one deterministic run each.
    assert baseline["planned_runs"] == 6
    assert any(
        contrast["baseline"] == "full_deterministic"
        for contrast in protocol["contrasts"]
    )
    # The v6 protocol must stay free of the new arm so the historical matrices
    # keep validating against it unchanged.
    assert "nonagentic_stack_baseline" not in validate_ablation_protocol(
        HISTORICAL_ABLATION_PROTOCOL_PATH
    )["design"]


def test_nonagentic_baseline_specs_are_deterministic_and_llm_free():
    specs = build_run_specs(
        cases=["aw_route6_late_return", "aw_charger_bank_shutdown"],
        modes=["selfish", "altruistic"],
        include_role_ablations=False,
        include_nonagentic_baseline=True,
        include_primary_deterministic=False,
    )

    assert len(specs) == 4
    assert {spec.configuration for spec in specs} == {"full_deterministic"}
    assert {spec.run_family for spec in specs} == {"nonagentic_stack_baseline"}
    assert not any(spec.stochastic for spec in specs)
    assert not any(spec.uses_external_llm for spec in specs)
    assert all(spec.repetition == 1 for spec in specs)


def test_nonagentic_baseline_workbooks_have_no_repetition_suffix(tmp_path):
    spec = build_run_specs(
        cases=["aw_combined_evening"],
        modes=["selfish"],
        include_nonagentic_baseline=True,
        include_primary_deterministic=False,
    )[0]

    assert workbook_path(tmp_path, spec).name == "full_deterministic.xlsx"


def test_nonagentic_baseline_run_does_not_require_external_llm_authorization():
    specs = build_run_specs(
        cases=["aw_combined_evening"],
        modes=["selfish"],
        include_nonagentic_baseline=True,
        include_primary_deterministic=False,
    )

    # No exception: the gate only applies when a scheduled run calls the model.
    validate_external_llm_gate(specs, allow_external_llm=False, dry_run=False)


def test_agentic_stack_contrast_is_reported_against_the_full_stack():
    runs = pd.DataFrame(
        [
            _ablation_run("full_agentic", 1, pto_cost=100.0),
            _ablation_run("full_agentic", 2, pto_cost=101.0),
            _ablation_run("full_deterministic", 1, pto_cost=110.0),
        ]
    )

    contrasts = build_ablation_contrasts(annotate_runs(runs), bootstrap_iterations=25)

    row = contrasts.loc[contrasts["contrast"].eq("agentic_stack_contribution")]
    assert len(row) == 1
    assert row.iloc[0]["baseline_configuration"] == "full_deterministic"
    assert row.iloc[0]["candidate_runs"] == 2
    assert row.iloc[0]["baseline_runs"] == 1


def _ablation_run(configuration: str, repetition: int, *, pto_cost: float) -> dict:
    row = _run(configuration, repetition, revenue=20.0, pto_cost=pto_cost)
    row["run_family"] = (
        "nonagentic_stack_baseline"
        if configuration == "full_deterministic"
        else "secondary_role_ablation"
    )
    return row

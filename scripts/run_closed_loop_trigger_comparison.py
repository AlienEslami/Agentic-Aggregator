from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DETERMINISTIC_CONFIGURATIONS = (
    "fixed_da_plan",
    "oracle_event_trigger",
    "numerical_event_trigger",
    "rule_text_event_trigger",
)
SUMMARY_COLUMNS = (
    "status",
    "timesteps_completed",
    "optimizer_calls",
    "maximum_pricing_reruns",
    "maximum_optimizer_attempts_per_trigger",
    "agent_role_provenance",
    "accepted_optimizer_calls",
    "evaluator_accepted_optimizer_calls",
    "forced_optimizer_selections",
    "retained_better_candidate_selections",
    "optimize_decisions",
    "realized_pto_cost",
    "realized_aggregator_revenue",
    "realized_grid_net_cost",
    "realized_buy_kwh",
    "realized_sell_kwh",
    "curtailed_energy_kwh",
    "event_model_mismatch_timesteps",
    "delay_parameter_absolute_error_minutes",
    "return_delay_parameter_absolute_error_minutes",
    "charger_power_absolute_error_kw",
    "charger_availability_mismatch_count",
    "minimum_observed_soc_fraction",
    "terminal_minimum_soc_fraction",
    "maximum_reserve_shortfall_kwh",
    "reserve_violation_timesteps",
    "llm_request_attempts",
    "llm_successful_requests",
    "llm_failed_attempts",
    "llm_input_tokens",
    "llm_cached_input_tokens",
    "llm_cache_write_tokens",
    "llm_uncached_input_tokens",
    "llm_output_tokens",
    "llm_reasoning_tokens",
    "llm_total_tokens",
    "llm_latency_seconds",
    "llm_approximate_cost_usd",
    "run_wall_seconds",
    "run_process_cpu_seconds",
    "run_average_cpu_cores",
    "run_average_cpu_percent_total_capacity",
    "run_logical_cpu_count",
    "run_rss_start_mb",
    "run_rss_end_mb",
    "run_peak_rss_mb",
    "run_peak_rss_delta_mb",
    "run_memory_sampler_available",
    "local_measurement_scope",
    "provider_compute_scope",
)


def command_for(
    *,
    configuration: str,
    case: str,
    variant: str,
    mode: str,
    start: int,
    end: int,
    model: str,
    output: Path,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "agentic_workflow",
        "--state-workbook",
        "inputs/State.xlsx",
        "--forecast-workbook",
        "inputs/Forecasted.xlsx",
        "--spot-prices",
        "inputs/SpotPrices.xlsx",
        "--realtime-states",
        "inputs/realtime_states",
        "--intraday-prices",
        "inputs/intraday_prices",
        "--disturbances",
        "inputs/rt_disturbance_scenarios_multiple.xlsx",
        "--scenario",
        "rt_none",
        "--notices-file",
        "inputs/revision/advance_warning_notices_v1.json",
        "--physical-events-file",
        "inputs/revision/advance_warning_physical_events_v1.json",
        "--notice-scenario",
        case,
        "--notice-variant",
        variant,
        "--configuration",
        configuration,
        "--realize-notice-truth",
        "--mode",
        mode,
        "--start-timestep",
        str(start),
        "--end-timestep",
        str(end),
        "--max-reruns",
        "1",
        "--checkpoint-every",
        "48",
        "--optimizer-backend",
        "direct",
        "--model",
        model,
        "--output",
        str(output),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run paired Trigger methods against common physical truth."
    )
    parser.add_argument("--case", default="aw_route6_late_return")
    parser.add_argument("--variant", default="uncertain_chat")
    parser.add_argument("--mode", choices=("selfish", "altruistic"), default="selfish")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=48)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--include-agent", action="store_true")
    parser.add_argument("--configuration", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    configurations = list(args.configuration or DETERMINISTIC_CONFIGURATIONS)
    if args.include_agent and "agent_trigger_only" not in configurations:
        configurations.append("agent_trigger_only")
    if "agent_trigger_only" in configurations and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for agent_trigger_only")

    output_dir = (
        ROOT
        / "results"
        / "revision"
        / "closed_loop"
        / args.case
        / args.mode
        / args.variant
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for configuration in configurations:
        workbook = output_dir / f"{configuration}.xlsx"
        if not (args.resume and workbook.exists()):
            print(f"\n=== {configuration} ===", flush=True)
            subprocess.run(
                command_for(
                    configuration=configuration,
                    case=args.case,
                    variant=args.variant,
                    mode=args.mode,
                    start=args.start,
                    end=args.end,
                    model=args.model,
                    output=workbook,
                ),
                cwd=ROOT,
                check=True,
            )
        summary = pd.read_excel(workbook, sheet_name="run_summary").iloc[0].to_dict()
        rows.append(
            {
                "configuration": configuration,
                "case": args.case,
                "variant": args.variant,
                "mode": args.mode,
                **{column: summary.get(column) for column in SUMMARY_COLUMNS},
            }
        )

    comparison = pd.DataFrame(rows)
    comparison.to_csv(output_dir / "comparison.csv", index=False)
    (output_dir / "comparison.json").write_text(
        json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print("\n" + comparison.to_string(index=False))
    print(f"\nWrote comparison to {output_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

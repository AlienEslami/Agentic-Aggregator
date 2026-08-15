from __future__ import annotations

import argparse
from pathlib import Path

from .config import DEFAULT_MODEL, WorkflowConfig
from .experiment_controls import (
    PRICING_GUIDANCE_VARIANTS,
    TRIGGER_PROMPT_VARIANTS,
)
from .runner import WorkflowRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the real-time agentic aggregator workflow without n8n."
    )
    parser.add_argument("--state-workbook", required=True, type=Path)
    parser.add_argument("--forecast-workbook", required=True, type=Path)
    parser.add_argument("--realtime-states", required=True, type=Path)
    parser.add_argument("--intraday-prices", required=True, type=Path)
    parser.add_argument("--disturbances", required=True, type=Path, dest="disturbance_workbook")
    parser.add_argument("--spot-prices", type=Path, dest="spot_prices_workbook")
    parser.add_argument("--output", required=True, type=Path, dest="output_workbook")
    parser.add_argument("--notices-file", type=Path)
    parser.add_argument("--physical-events-file", type=Path)
    parser.add_argument(
        "--notice-scenario",
        action="append",
        dest="notice_scenario_ids",
        help="Operational notice scenario to run; repeat to compose notices.",
    )
    parser.add_argument("--notice-variant", default="explicit")
    parser.add_argument("--mode", choices=("selfish", "altruistic"), default="selfish")
    parser.add_argument(
        "--altruistic-revenue-retention-fraction",
        type=float,
        default=0.50,
        help=(
            "Fraction of the frozen day-ahead aggregator revenue retained as the "
            "full-day floor in altruistic mode (v6 default: 0.50)."
        ),
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenario_ids",
        help="Scenario ID to activate; repeat for combined disturbances. Defaults to rt_none.",
    )
    parser.add_argument("--start-timestep", type=int, default=1)
    parser.add_argument("--end-timestep", type=int, default=48)
    parser.add_argument("--agent-backend", choices=("auto", "openai", "rule"), default="auto")
    parser.add_argument(
        "--configuration",
        dest="experiment_configuration",
        choices=(
            "legacy",
            "fixed_da_plan",
            "structured_reference",
            "oracle_event_trigger",
            "numerical_event_trigger",
            "rule_text_event_trigger",
            "agent_trigger_only",
            "full_deterministic",
            "full_agentic",
            "rule_parser_trigger_substitution",
            "mathematical_pricing_substitution",
            "deterministic_pricing_substitution",
            "evaluator_removal",
            "pricing_agent_only",
            "agent_evaluator_raw_text",
            "rule_text_evaluator",
            "structured_evaluator_oracle",
            "evaluator_removal_control",
        ),
        default="legacy",
        help="Prespecified baseline or component-ablation configuration.",
    )
    parser.add_argument(
        "--notice-path",
        choices=("none", "manual", "rule", "llm"),
        default="none",
        help="Operational-notice formalization path; configurations set a default when omitted.",
    )
    parser.add_argument(
        "--realize-notice-truth",
        action="store_true",
        help=(
            "Experiment-only: apply hidden canonical notice truth to numerical "
            "consequences and ex-post settlement without exposing it to triggers."
        ),
    )
    parser.add_argument("--optimizer-backend", choices=("direct", "http"), default="direct")
    parser.add_argument("--optimizer-url", default="http://127.0.0.1:5002")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--trigger-prompt-variant",
        choices=TRIGGER_PROMPT_VARIANTS,
        default="baseline",
        help="Frozen Trigger system-prompt wording arm.",
    )
    parser.add_argument(
        "--trigger-confidence-threshold",
        type=float,
        default=0.0,
        help=(
            "Post-output deployment threshold in [0,1]. An otherwise actionable "
            "LLM Trigger decision below it is held for confirmation."
        ),
    )
    parser.add_argument(
        "--pricing-guidance-variant",
        choices=PRICING_GUIDANCE_VARIANTS,
        default="base",
        help=(
            "Narrow/base/wide optional deterministic Pricing reference; hard "
            "economic bounds do not change."
        ),
    )
    parser.add_argument(
        "--max-reruns",
        type=int,
        default=3,
        help="Additional pricing/optimization attempts allowed after the initial attempt.",
    )
    parser.add_argument("--state-source", choices=("plan", "workbook"), default="plan")
    parser.add_argument("--checkpoint-every", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = WorkflowConfig(
        state_workbook=args.state_workbook,
        forecast_workbook=args.forecast_workbook,
        realtime_states=args.realtime_states,
        intraday_prices=args.intraday_prices,
        disturbance_workbook=args.disturbance_workbook,
        spot_prices_workbook=args.spot_prices_workbook,
        output_workbook=args.output_workbook,
        notices_file=args.notices_file,
        physical_events_file=args.physical_events_file,
        notice_scenario_ids=tuple(args.notice_scenario_ids or []),
        notice_variant=args.notice_variant,
        mode=args.mode,
        altruistic_revenue_retention_fraction=(
            args.altruistic_revenue_retention_fraction
        ),
        scenario_ids=tuple(args.scenario_ids or ["rt_none"]),
        start_timestep=args.start_timestep,
        end_timestep=args.end_timestep,
        agent_backend=args.agent_backend,
        experiment_configuration=args.experiment_configuration,
        notice_path=args.notice_path,
        realize_notice_truth=args.realize_notice_truth,
        optimizer_backend=args.optimizer_backend,
        optimizer_url=args.optimizer_url,
        model=args.model,
        trigger_prompt_variant=args.trigger_prompt_variant,
        trigger_confidence_threshold=args.trigger_confidence_threshold,
        pricing_guidance_variant=args.pricing_guidance_variant,
        max_reruns=args.max_reruns,
        state_source=args.state_source,
        checkpoint_every=args.checkpoint_every,
    )
    state = WorkflowRunner(config).run()
    triggered = sum(log["action"] == "optimize" for log in state.logs)
    print(
        f"Completed {len(state.logs)} timesteps; triggered={triggered}; "
        f"attempts={len(state.attempts)}; output={config.output_workbook}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

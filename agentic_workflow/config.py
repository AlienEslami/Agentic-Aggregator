from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


Mode = Literal["selfish", "altruistic"]
AgentBackendName = Literal["auto", "openai", "rule"]
ExperimentConfiguration = Literal[
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
]
NoticePathName = Literal["none", "manual", "rule", "llm"]
OptimizerBackendName = Literal["direct", "http"]
StateSourceName = Literal["plan", "workbook"]
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_INPUT_USD_PER_MILLION = 0.20
DEFAULT_CACHED_INPUT_USD_PER_MILLION = 0.02
DEFAULT_CACHE_WRITE_MULTIPLIER = 1.25
DEFAULT_OUTPUT_USD_PER_MILLION = 1.20


@dataclass(slots=True)
class WorkflowConfig:
    state_workbook: Path
    forecast_workbook: Path
    realtime_states: Path
    intraday_prices: Path
    disturbance_workbook: Path
    output_workbook: Path
    notices_file: Path | None = None
    physical_events_file: Path | None = None
    notice_scenario_ids: tuple[str, ...] = ()
    notice_variant: str = "explicit"
    spot_prices_workbook: Path | None = None
    mode: Mode = "selfish"
    altruistic_revenue_retention_fraction: float = 0.50
    scenario_ids: tuple[str, ...] = ("rt_none",)
    start_timestep: int = 1
    end_timestep: int = 48
    agent_backend: AgentBackendName = "auto"
    experiment_configuration: ExperimentConfiguration = "legacy"
    notice_path: NoticePathName = "none"
    realize_notice_truth: bool = False
    optimizer_backend: OptimizerBackendName = "direct"
    optimizer_url: str = "http://127.0.0.1:5002"
    model: str = DEFAULT_MODEL
    trigger_prompt_variant: str = "baseline"
    trigger_confidence_threshold: float = 0.0
    pricing_guidance_variant: str = "base"
    max_reruns: int = 3
    state_source: StateSourceName = "plan"
    checkpoint_every: int = 1
    request_timeout_seconds: float = 600.0
    poll_interval_seconds: float = 2.0
    metadata: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        required = {
            "state_workbook": self.state_workbook,
            "forecast_workbook": self.forecast_workbook,
            "realtime_states": self.realtime_states,
            "intraday_prices": self.intraday_prices,
            "disturbance_workbook": self.disturbance_workbook,
        }
        missing = [f"{name}={path}" for name, path in required.items() if not path.exists()]
        if self.spot_prices_workbook is not None and not self.spot_prices_workbook.exists():
            missing.append(f"spot_prices_workbook={self.spot_prices_workbook}")
        if self.notices_file is not None and not self.notices_file.exists():
            missing.append(f"notices_file={self.notices_file}")
        if self.physical_events_file is not None and not self.physical_events_file.exists():
            missing.append(f"physical_events_file={self.physical_events_file}")
        if missing:
            raise FileNotFoundError("Missing workflow inputs: " + ", ".join(missing))
        if self.mode not in {"selfish", "altruistic"}:
            raise ValueError(f"Unsupported mode: {self.mode}")
        if (
            not math.isfinite(self.altruistic_revenue_retention_fraction)
            or not 0 <= self.altruistic_revenue_retention_fraction <= 1
        ):
            raise ValueError(
                "altruistic_revenue_retention_fraction must be finite and in [0, 1]"
            )
        if not 1 <= self.start_timestep <= 48:
            raise ValueError("start_timestep must be in [1, 48]")
        if not self.start_timestep <= self.end_timestep <= 48:
            raise ValueError("end_timestep must be in [start_timestep, 48]")
        if self.max_reruns < 0:
            raise ValueError("max_reruns must be nonnegative")
        from .experiment_controls import (
            validate_pricing_guidance_variant,
            validate_trigger_confidence_threshold,
            validate_trigger_prompt_variant,
        )

        validate_trigger_prompt_variant(self.trigger_prompt_variant)
        validate_trigger_confidence_threshold(self.trigger_confidence_threshold)
        validate_pricing_guidance_variant(self.pricing_guidance_variant)
        if self.checkpoint_every < 1:
            raise ValueError("checkpoint_every must be positive")

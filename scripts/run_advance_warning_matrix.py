from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_closed_loop_trigger_comparison import SUMMARY_COLUMNS, command_for


PRIMARY_CASES = (
    "aw_route6_late_return",
    "aw_charger_bank_shutdown",
    "aw_combined_evening",
)
PRIMARY_MODES = ("selfish", "altruistic")
NOTICE_INPUT = ROOT / "inputs" / "revision" / "advance_warning_notices_v1.json"
PHYSICAL_INPUT = (
    ROOT / "inputs" / "revision" / "advance_warning_physical_events_v1.json"
)
ABLATION_PROTOCOL_PATH = (
    ROOT / "inputs" / "revision" / "advance_warning_ablation_protocol_v6.json"
)
PROMPT_INPUTS = {
    name: ROOT / "agentic_workflow" / "prompts" / name
    for name in (
        "trigger_system.txt",
        "pricing_selfish_system.txt",
        "pricing_altruistic_system.txt",
        "evaluator_system.txt",
    )
}
PRIMARY_DETERMINISTIC_CONFIGURATIONS = (
    "oracle_event_trigger",
    "numerical_event_trigger",
    "rule_text_event_trigger",
)
PRIMARY_AGENT_CONFIGURATION = "agent_trigger_only"
ROLE_ABLATION_CONFIGURATIONS = (
    "full_agentic",
    "rule_parser_trigger_substitution",
    "deterministic_pricing_substitution",
    "evaluator_removal",
)
# Every role ablation replaces exactly one agent, so none of them answers the
# reviewers' question about the matched indicator-driven loop with no agentic
# layer at all.  This configuration runs the rule trigger, the deterministic
# price-zone pricing and the hard-check evaluator together against the same
# optimizer, inputs and disturbances.  It consumes no external model calls and
# is deterministic, so a single run per case and mode is sufficient.
NONAGENTIC_BASELINE_CONFIGURATIONS = ("full_deterministic",)
LLM_CONFIGURATIONS = frozenset(
    (PRIMARY_AGENT_CONFIGURATION, *ROLE_ABLATION_CONFIGURATIONS)
)
METHOD_LABELS = {
    "fixed_da_plan": "fixed_day_ahead",
    "oracle_event_trigger": "oracle",
    "numerical_event_trigger": "numerical",
    "rule_text_event_trigger": "rule_text",
    "agent_trigger_only": "agent",
    "full_agentic": "full_agentic",
    "rule_parser_trigger_substitution": "rule_trigger_ablation",
    "deterministic_pricing_substitution": "deterministic_price_zone_ablation",
    "evaluator_removal": "evaluator_ablation",
    "full_deterministic": "nonagentic_stack_baseline",
}


@dataclass(frozen=True, slots=True)
class RunSpec:
    case: str
    mode: str
    variant: str
    configuration: str
    repetition: int
    run_family: str
    stochastic: bool

    @property
    def run_id(self) -> str:
        return (
            f"{self.case}__{self.mode}__{self.variant}__"
            f"{self.configuration}__r{self.repetition:03d}"
        )

    @property
    def uses_external_llm(self) -> bool:
        return self.configuration in LLM_CONFIGURATIONS


def unique_in_order(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def build_run_specs(
    *,
    cases: Iterable[str] = PRIMARY_CASES,
    modes: Iterable[str] = PRIMARY_MODES,
    variant: str = "uncertain_chat",
    include_agent: bool = False,
    agent_repetitions: int = 5,
    include_role_ablations: bool = False,
    ablation_repetitions: int = 5,
    role_ablation_configurations: Iterable[str] = ROLE_ABLATION_CONFIGURATIONS,
    include_nonagentic_baseline: bool = False,
    include_fixed: bool = False,
    include_primary_deterministic: bool = True,
) -> list[RunSpec]:
    if agent_repetitions < 1:
        raise ValueError("agent_repetitions must be positive")
    if ablation_repetitions < 1:
        raise ValueError("ablation_repetitions must be positive")

    selected_role_ablations = unique_in_order(role_ablation_configurations)
    unsupported_role_ablations = set(selected_role_ablations) - set(
        ROLE_ABLATION_CONFIGURATIONS
    )
    if unsupported_role_ablations:
        raise ValueError(
            "Unsupported role-ablation configurations: "
            f"{sorted(unsupported_role_ablations)!r}"
        )
    if include_role_ablations and not selected_role_ablations:
        raise ValueError(
            "role_ablation_configurations cannot be empty when role ablations "
            "are enabled"
        )

    configurations = (
        list(PRIMARY_DETERMINISTIC_CONFIGURATIONS)
        if include_primary_deterministic
        else []
    )
    if include_fixed and include_primary_deterministic:
        configurations.insert(0, "fixed_da_plan")

    specs: list[RunSpec] = []
    for case in unique_in_order(cases):
        for mode in unique_in_order(modes):
            for configuration in configurations:
                specs.append(
                    RunSpec(
                        case=case,
                        mode=mode,
                        variant=variant,
                        configuration=configuration,
                        repetition=1,
                        run_family="primary_trigger_comparison",
                        stochastic=False,
                    )
                )
            if include_agent:
                for repetition in range(1, agent_repetitions + 1):
                    specs.append(
                        RunSpec(
                            case=case,
                            mode=mode,
                            variant=variant,
                            configuration=PRIMARY_AGENT_CONFIGURATION,
                            repetition=repetition,
                            run_family="primary_trigger_comparison",
                            stochastic=True,
                        )
                    )
            if include_role_ablations:
                for configuration in selected_role_ablations:
                    for repetition in range(1, ablation_repetitions + 1):
                        specs.append(
                            RunSpec(
                                case=case,
                                mode=mode,
                                variant=variant,
                                configuration=configuration,
                                repetition=repetition,
                                run_family="secondary_role_ablation",
                                stochastic=True,
                            )
                        )
            if include_nonagentic_baseline:
                for configuration in NONAGENTIC_BASELINE_CONFIGURATIONS:
                    specs.append(
                        RunSpec(
                            case=case,
                            mode=mode,
                            variant=variant,
                            configuration=configuration,
                            repetition=1,
                            run_family="nonagentic_stack_baseline",
                            stochastic=False,
                        )
                    )
    return specs


def workbook_path(output_root: Path, spec: RunSpec) -> Path:
    directory = output_root / spec.case / spec.mode / spec.variant
    if spec.stochastic:
        name = f"{spec.configuration}_rep_{spec.repetition:03d}.xlsx"
    else:
        # Preserve compatibility with run_closed_loop_trigger_comparison.py so
        # the validated deterministic workbooks are reused rather than rerun.
        name = f"{spec.configuration}.xlsx"
    return directory / name


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_run_summary(workbook: Path) -> dict[str, Any]:
    summary = pd.read_excel(workbook, sheet_name="run_summary")
    if summary.empty:
        raise ValueError(f"run_summary is empty: {workbook}")
    return summary.iloc[0].to_dict()


def read_solver_provenance(workbook: Path) -> dict[str, Any]:
    try:
        attempts = pd.read_excel(workbook, sheet_name="optimization_attempts")
    except (ValueError, FileNotFoundError):
        return {"solver_names": [], "solver_fallback_errors": []}
    solver_names = (
        sorted(attempts["solver_name"].dropna().astype(str).unique().tolist())
        if "solver_name" in attempts
        else []
    )
    fallback_errors = (
        sorted(
            value
            for value in attempts["solver_fallback_errors"].dropna().astype(str).unique()
            if value not in {"", "[]"}
        )
        if "solver_fallback_errors" in attempts
        else []
    )
    return {
        "solver_names": solver_names,
        "solver_fallback_errors": fallback_errors,
    }


def require_gurobi_only(provenance: dict[str, Any], workbook: Path) -> None:
    names = provenance.get("solver_names") or []
    errors = provenance.get("solver_fallback_errors") or []
    if names != ["gurobi"] or errors:
        raise ValueError(
            "Final evidence requires Gurobi with no fallback; refusing to index "
            f"{workbook} (solver_names={names!r}, fallback_errors={errors!r})"
        )


def workbook_is_complete(workbook: Path, expected_timesteps: int) -> bool:
    if not workbook.exists():
        return False
    try:
        summary = read_run_summary(workbook)
    except Exception:
        return False
    return bool(
        summary.get("status") == "complete"
        and int(summary.get("timesteps_completed") or 0) == expected_timesteps
    )


def should_reuse_workbook(
    workbook: Path,
    expected_timesteps: int,
    spec: RunSpec,
    *,
    force: bool,
    force_stochastic: bool,
) -> bool:
    """Apply explicit rerun policy without conflating fixed and LLM workbooks."""

    if force or (force_stochastic and spec.stochastic):
        return False
    return workbook_is_complete(workbook, expected_timesteps)


def validate_ablation_protocol(
    protocol_path: Path | None = None,
) -> dict[str, Any]:
    protocol_path = protocol_path or ABLATION_PROTOCOL_PATH
    if not protocol_path.exists():
        raise FileNotFoundError(
            f"Frozen ablation protocol not found: {protocol_path}"
        )
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    configured = tuple(protocol["design"]["configurations"])
    if configured != ROLE_ABLATION_CONFIGURATIONS:
        raise ValueError(
            "Frozen ablation configurations do not match the matrix runner: "
            f"{configured!r} != {ROLE_ABLATION_CONFIGURATIONS!r}"
        )
    design = protocol["design"]
    expected_runs = (
        len(configured)
        * len(design["cases"])
        * len(design["modes"])
        * int(design["repetitions_per_configuration_case_mode"])
    )
    if int(design["planned_runs"]) != expected_runs:
        raise ValueError(
            "Frozen ablation planned_runs does not match its factorial design: "
            f"{design['planned_runs']} != {expected_runs}"
        )
    baseline_design = design.get("nonagentic_stack_baseline")
    if baseline_design is not None:
        baseline_configurations = tuple(baseline_design["configurations"])
        if baseline_configurations != NONAGENTIC_BASELINE_CONFIGURATIONS:
            raise ValueError(
                "Frozen non-agentic baseline configurations do not match the "
                f"matrix runner: {baseline_configurations!r} != "
                f"{NONAGENTIC_BASELINE_CONFIGURATIONS!r}"
            )
        expected_baseline_runs = (
            len(baseline_configurations)
            * len(design["cases"])
            * len(design["modes"])
            * int(baseline_design["repetitions_per_configuration_case_mode"])
        )
        if int(baseline_design["planned_runs"]) != expected_baseline_runs:
            raise ValueError(
                "Frozen non-agentic baseline planned_runs does not match its "
                f"design: {baseline_design['planned_runs']} != "
                f"{expected_baseline_runs}"
            )
    controls = protocol["controls"]
    if int(controls["maximum_optimizer_attempts_per_trigger"]) != (
        int(controls["maximum_pricing_reruns"]) + 1
    ):
        raise ValueError(
            "Frozen rerun controls are inconsistent: maximum optimizer attempts "
            "must equal maximum pricing reruns plus the initial attempt"
        )
    recorded_prompt_hashes = controls.get("prompt_sha256") or {}
    current_prompt_hashes = {
        name: sha256(path) for name, path in PROMPT_INPUTS.items()
    }
    if recorded_prompt_hashes != current_prompt_hashes:
        raise ValueError(
            "Frozen prompt hashes do not match the current prompt files: "
            + json.dumps(
                {
                    "recorded": recorded_prompt_hashes,
                    "current": current_prompt_hashes,
                },
                sort_keys=True,
            )
        )
    return protocol


def current_input_fingerprints(
    protocol_path: Path | None = None,
    notice_path: Path | None = None,
    physical_path: Path | None = None,
) -> dict[str, str]:
    return {
        "notice_sha256": sha256(notice_path or NOTICE_INPUT),
        "physical_event_sha256": sha256(physical_path or PHYSICAL_INPUT),
        "ablation_protocol_sha256": sha256(protocol_path or ABLATION_PROTOCOL_PATH),
        **{
            f"prompt_{name}_sha256": sha256(path)
            for name, path in PROMPT_INPUTS.items()
        },
    }


def validate_resume_fingerprints(
    output_root: Path,
    *,
    force: bool,
    protocol_path: Path | None = None,
    notice_path: Path | None = None,
    physical_path: Path | None = None,
) -> bool:
    """Reject reuse when a fingerprinted matrix used different frozen inputs.

    Returns ``False`` for a legacy manifest without hashes.  That permits a
    one-time migration of already-audited workbooks; every newly written v2
    manifest carries hashes and is checked on subsequent resumes.
    """

    manifest_path = output_root / "matrix_manifest.json"
    if not manifest_path.exists():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded_inputs = manifest.get("inputs") or {}
    current = current_input_fingerprints(
        protocol_path, notice_path, physical_path
    )
    if not all(recorded_inputs.get(name) for name in current):
        return False
    mismatches = {
        name: {"recorded": recorded_inputs[name], "current": value}
        for name, value in current.items()
        if recorded_inputs[name] != value
    }
    if mismatches and not force:
        raise ValueError(
            "Refusing to reuse workbooks generated from different frozen inputs. "
            "Use --force to rerun the complete matrix. Mismatches: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return not mismatches


def validate_external_llm_gate(
    specs: Iterable[RunSpec],
    *,
    allow_external_llm: bool,
    dry_run: bool,
    environ: dict[str, str] | None = None,
) -> None:
    if dry_run or not any(spec.uses_external_llm for spec in specs):
        return
    if not allow_external_llm:
        raise ValueError(
            "LLM configurations require --allow-external-llm. This records explicit "
            "authorization to send the synthetic notice text and operational context "
            "to the configured model provider."
        )
    environment = os.environ if environ is None else environ
    if not environment.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required for LLM configurations")


def git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def git_worktree_is_clean() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return not result.stdout.strip()


def validate_execution_budget(
    rows: list[dict[str, Any]], maximum_cost_usd: float | None
) -> None:
    if maximum_cost_usd is None:
        return
    if maximum_cost_usd <= 0:
        raise ValueError("--max-approximate-api-cost-usd must be positive")
    spent = sum(float(row.get("llm_approximate_cost_usd") or 0) for row in rows)
    if spent >= maximum_cost_usd:
        raise RuntimeError(
            "Approved approximate API-cost ceiling reached before the next episode: "
            f"USD {spent:.4f} >= USD {maximum_cost_usd:.4f}"
        )


def run_row(
    spec: RunSpec,
    workbook: Path,
    summary: dict[str, Any],
    *,
    model: str,
    reused: bool,
) -> dict[str, Any]:
    try:
        relative_workbook = workbook.relative_to(ROOT).as_posix()
    except ValueError:
        relative_workbook = str(workbook)
    provenance = read_solver_provenance(workbook)
    require_gurobi_only(provenance, workbook)
    return {
        "run_id": spec.run_id,
        "run_family": spec.run_family,
        "configuration": spec.configuration,
        "method": METHOD_LABELS.get(spec.configuration, spec.configuration),
        "case": spec.case,
        "variant": spec.variant,
        "mode": spec.mode,
        "repetition": spec.repetition,
        "stochastic": spec.stochastic,
        "uses_external_llm": spec.uses_external_llm,
        "model": model if spec.uses_external_llm else "not_used",
        "reused_complete_workbook": reused,
        "workbook": relative_workbook,
        "workbook_sha256": sha256(workbook),
        **provenance,
        **{column: summary.get(column) for column in SUMMARY_COLUMNS},
    }


def write_run_index(output_root: Path, rows: list[dict[str, Any]]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(output_root / "matrix_runs.csv", index=False)
    (output_root / "matrix_runs.json").write_text(
        frame.to_json(orient="records", indent=2) + "\n", encoding="utf-8"
    )


def write_manifest(
    output_root: Path,
    specs: list[RunSpec],
    *,
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
) -> None:
    input_fingerprints = current_input_fingerprints(
        Path(args.ablation_protocol),
        Path(args.notices_file),
        Path(args.physical_events_file),
    )
    manifest = {
        "protocol_version": "advance_warning_matrix_v6",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_revision(),
        "git_worktree_clean": git_worktree_is_clean(),
        "design": {
            "cases": list(unique_in_order(spec.case for spec in specs)),
            "modes": list(unique_in_order(spec.mode for spec in specs)),
            "variant": args.variant,
            "agent_repetitions": args.agent_repetitions if args.include_agent else 0,
            "role_ablation_repetitions": (
                args.ablation_repetitions if args.include_role_ablations else 0
            ),
            "nonagentic_stack_baseline": (
                {
                    "configurations": list(NONAGENTIC_BASELINE_CONFIGURATIONS),
                    "repetitions": 1,
                    "deterministic": True,
                    "uses_external_llm": False,
                }
                if args.include_nonagentic_baseline
                else "not_scheduled"
            ),
            "primary_contrasts": [
                "agent_vs_rule_text",
                "agent_vs_numerical",
                "agent_vs_oracle",
            ],
            "secondary_role_ablations": list(
                unique_in_order(
                    spec.configuration
                    for spec in specs
                    if spec.run_family == "secondary_role_ablation"
                )
            ),
            "operational_feasibility_first": True,
        },
        "external_llm": {
            "scheduled": any(spec.uses_external_llm for spec in specs),
            "explicit_cli_authorization": bool(args.allow_external_llm),
            "payload_scope": (
                "synthetic public notice/chat text plus operational numerical context; "
                "canonical hidden truth is not included"
            ),
            "api_key_recorded": False,
            "model": args.model,
        },
        "solver_settings": {
            "order": args.solver_order,
            "time_limit_seconds": args.solver_time_limit,
            "mip_gap": args.solver_mip_gap,
            "fallback_permitted_in_final_results": False,
        },
        "altruistic_baseline_revenue_retention": {
            "fraction": args.altruistic_revenue_retention_fraction,
            "reference": "frozen day-ahead full-day aggregator revenue",
            "floor_formula": "fraction * reference",
        },
        "inputs": {
            "notice_file": Path(args.notices_file).as_posix(),
            "physical_event_file": Path(args.physical_events_file).as_posix(),
            "disturbance_workbook": Path(args.disturbances).as_posix(),
            "disturbance_scenarios": list(args.scenario_ids or ["rt_none"]),
            "ablation_protocol_file": Path(
                args.ablation_protocol
            ).as_posix(),
            **input_fingerprints,
            "prompt_sha256": {
                name: input_fingerprints[f"prompt_{name}_sha256"]
                for name in PROMPT_INPUTS
            },
        },
        "planned_runs": len(specs),
        "indexed_completed_runs": len(rows),
        "execution_budget": {
            "maximum_approximate_api_cost_usd": args.max_approximate_api_cost_usd,
            "indexed_approximate_api_cost_usd": sum(
                float(row.get("llm_approximate_cost_usd") or 0)
                for row in rows
            ),
        },
        "runs": [asdict(spec) | {"run_id": spec.run_id} for spec in specs],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "matrix_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the prespecified advance-warning matrix with "
            "resume-safe repeated Agent and role-ablation trials."
        )
    )
    parser.add_argument("--case", action="append", choices=PRIMARY_CASES, default=[])
    parser.add_argument("--mode", action="append", choices=PRIMARY_MODES, default=[])
    parser.add_argument("--variant", default="uncertain_chat")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=48)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument(
        "--altruistic-revenue-retention-fraction",
        type=float,
        default=0.50,
        help=(
            "Fraction of baseline full-day aggregator revenue retained in "
            "altruistic mode; must match the frozen protocol."
        ),
    )
    parser.add_argument("--solver-order", default="gurobi")
    parser.add_argument("--solver-time-limit", type=float, default=60.0)
    parser.add_argument("--solver-mip-gap", type=float, default=0.02)
    parser.add_argument("--include-agent", action="store_true")
    parser.add_argument("--agent-repetitions", type=int, default=5)
    parser.add_argument("--include-role-ablations", action="store_true")
    parser.add_argument(
        "--role-ablation-configuration",
        action="append",
        choices=ROLE_ABLATION_CONFIGURATIONS,
        default=[],
        help=(
            "Limit role-ablation execution to the selected configuration. Repeat "
            "the option to select more than one; defaults to all four."
        ),
    )
    parser.add_argument(
        "--only-role-ablations",
        action="store_true",
        help=(
            "Schedule only role-ablation configurations. Intended for an isolated "
            "pilot output root; requires --include-role-ablations."
        ),
    )
    parser.add_argument("--ablation-repetitions", type=int, default=5)
    parser.add_argument(
        "--include-nonagentic-baseline",
        action="store_true",
        help=(
            "Schedule the matched indicator-driven loop with no agentic layer "
            "(rule trigger, deterministic pricing, hard-check evaluator). It is "
            "deterministic and consumes no external model calls."
        ),
    )
    parser.add_argument(
        "--only-nonagentic-baseline",
        action="store_true",
        help=(
            "Schedule only the non-agentic baseline; requires "
            "--include-nonagentic-baseline."
        ),
    )
    parser.add_argument(
        "--notices-file",
        type=Path,
        default=NOTICE_INPUT,
        help=(
            "Advance-warning notice dataset. Defaults to v1; the broader "
            "disturbance cases live in the v2 dataset."
        ),
    )
    parser.add_argument(
        "--physical-events-file",
        type=Path,
        default=PHYSICAL_INPUT,
        help="Hidden physical-event file matching --notices-file.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenario_ids",
        help=(
            "Disturbance scenario from the disturbance workbook. Repeat to "
            "compose. Defaults to rt_none, where the advance-warning physical "
            "events are the only disturbance."
        ),
    )
    parser.add_argument(
        "--disturbances",
        type=Path,
        default=Path("inputs/rt_disturbance_scenarios_multiple.xlsx"),
        help="Disturbance workbook holding the scenarios sheet.",
    )
    parser.add_argument(
        "--ablation-protocol",
        type=Path,
        default=ABLATION_PROTOCOL_PATH,
        help=(
            "Frozen protocol to validate against. Defaults to the v6 protocol "
            "used by the published matrices; the non-agentic baseline requires "
            "a protocol that declares it, such as the v7 protocol."
        ),
    )
    parser.add_argument("--include-fixed", action="store_true")
    parser.add_argument(
        "--allow-external-llm",
        action="store_true",
        help=(
            "Explicitly authorize sending synthetic notice/chat text and operational "
            "context to the configured model provider."
        ),
    )
    parser.add_argument("--force", action="store_true", help="Rerun complete workbooks.")
    parser.add_argument(
        "--force-stochastic",
        action="store_true",
        help=(
            "Rerun only stochastic Agent/ablation workbooks while reusing complete "
            "deterministic comparators."
        ),
    )
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--require-clean-git",
        action="store_true",
        help="Refuse execution unless the Git worktree is clean.",
    )
    parser.add_argument(
        "--max-approximate-api-cost-usd",
        type=float,
        help=(
            "Stop before the next episode once indexed approximate API cost reaches "
            "this approved ceiling."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/revision/closed_loop"),
    )
    args = parser.parse_args()

    if args.only_role_ablations and not args.include_role_ablations:
        raise SystemExit("--only-role-ablations requires --include-role-ablations")
    if args.only_nonagentic_baseline and not args.include_nonagentic_baseline:
        raise SystemExit(
            "--only-nonagentic-baseline requires --include-nonagentic-baseline"
        )
    if args.role_ablation_configuration and not args.include_role_ablations:
        raise SystemExit(
            "--role-ablation-configuration requires --include-role-ablations"
        )
    if args.require_clean_git and not git_worktree_is_clean():
        raise SystemExit("Refusing execution because the Git worktree is not clean")
    if (
        args.max_approximate_api_cost_usd is not None
        and args.max_approximate_api_cost_usd <= 0
    ):
        raise SystemExit("--max-approximate-api-cost-usd must be positive")
    if args.solver_order.strip().lower() != "gurobi":
        raise SystemExit("Final v6 matrix requires --solver-order gurobi")
    if args.solver_time_limit <= 0:
        raise SystemExit("--solver-time-limit must be positive")
    if not 0 <= args.solver_mip_gap < 1:
        raise SystemExit("--solver-mip-gap must be in [0,1)")

    try:
        protocol = validate_ablation_protocol(args.ablation_protocol)
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    if args.include_nonagentic_baseline and not protocol["design"].get(
        "nonagentic_stack_baseline"
    ):
        raise SystemExit(
            "The selected protocol does not declare a non-agentic stack "
            "baseline; use inputs/revision/"
            "advance_warning_ablation_protocol_v7.json"
        )

    frozen_retention = protocol["controls"][
        "altruistic_baseline_revenue_retention"
    ]
    if abs(
        args.altruistic_revenue_retention_fraction
        - float(frozen_retention["retention_fraction"])
    ) > 1e-12:
        raise SystemExit(
            "--altruistic-revenue-retention-fraction must match the frozen "
            "protocol value"
        )

    cases = args.case or list(PRIMARY_CASES)
    modes = args.mode or list(protocol["design"]["modes"])
    specs = build_run_specs(
        cases=cases,
        modes=modes,
        variant=args.variant,
        include_agent=args.include_agent,
        agent_repetitions=args.agent_repetitions,
        include_role_ablations=args.include_role_ablations,
        ablation_repetitions=args.ablation_repetitions,
        role_ablation_configurations=(
            args.role_ablation_configuration or ROLE_ABLATION_CONFIGURATIONS
        ),
        include_nonagentic_baseline=args.include_nonagentic_baseline,
        include_fixed=args.include_fixed,
        include_primary_deterministic=not (
            args.only_role_ablations or args.only_nonagentic_baseline
        ),
    )
    try:
        validate_external_llm_gate(
            specs,
            allow_external_llm=args.allow_external_llm,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    try:
        validate_resume_fingerprints(
            output_root,
            force=args.force,
            protocol_path=Path(args.ablation_protocol),
            notice_path=Path(args.notices_file),
            physical_path=Path(args.physical_events_file),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    expected_timesteps = args.end - args.start + 1
    if expected_timesteps < 1:
        raise SystemExit("--end must be greater than or equal to --start")

    if args.dry_run:
        print(
            json.dumps(
                [
                    asdict(spec)
                    | {
                        "run_id": spec.run_id,
                        "workbook": str(workbook_path(output_root, spec)),
                    }
                    for spec in specs
                ],
                indent=2,
            )
        )
        return

    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for index, spec in enumerate(specs, start=1):
        try:
            validate_execution_budget(rows, args.max_approximate_api_cost_usd)
        except (RuntimeError, ValueError) as exc:
            write_run_index(output_root, rows)
            write_manifest(output_root, specs, args=args, rows=rows)
            raise SystemExit(str(exc)) from exc
        workbook = workbook_path(output_root, spec)
        reused = should_reuse_workbook(
            workbook,
            expected_timesteps,
            spec,
            force=args.force,
            force_stochastic=args.force_stochastic,
        )
        print(
            f"[{index}/{len(specs)}] {spec.run_id}: "
            f"{'reuse' if reused else 'run'}",
            flush=True,
        )
        if not reused:
            workbook.parent.mkdir(parents=True, exist_ok=True)
            try:
                environment = os.environ.copy()
                environment.update(
                    {
                        "RT_SOLVER_ORDER": args.solver_order,
                        "RT_SOLVER_TIME_LIMIT": str(args.solver_time_limit),
                        "RT_SOLVER_MIP_GAP": str(args.solver_mip_gap),
                    }
                )
                subprocess.run(
                    command_for(
                        configuration=spec.configuration,
                        case=spec.case,
                        variant=spec.variant,
                        mode=spec.mode,
                        start=args.start,
                        end=args.end,
                        model=args.model,
                        output=workbook,
                        altruistic_revenue_retention_fraction=(
                            args.altruistic_revenue_retention_fraction
                        ),
                        notices_file=args.notices_file,
                        physical_events_file=args.physical_events_file,
                        disturbances=args.disturbances,
                        scenarios=tuple(args.scenario_ids or ("rt_none",)),
                    ),
                    cwd=ROOT,
                    check=True,
                    env=environment,
                )
            except subprocess.CalledProcessError:
                failures.append(spec.run_id)
                if not args.keep_going:
                    raise
                continue
        try:
            summary = read_run_summary(workbook)
            rows.append(
                run_row(spec, workbook, summary, model=args.model, reused=reused)
            )
        except Exception:
            failures.append(spec.run_id)
            if not args.keep_going:
                raise
        finally:
            write_run_index(output_root, rows)
            write_manifest(output_root, specs, args=args, rows=rows)

    print(f"Indexed {len(rows)} of {len(specs)} planned runs in {output_root}")
    if failures:
        raise SystemExit("Failed runs: " + ", ".join(failures))


if __name__ == "__main__":
    main()

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
PRIMARY_DETERMINISTIC_CONFIGURATIONS = (
    "oracle_event_trigger",
    "numerical_event_trigger",
    "rule_text_event_trigger",
)
PRIMARY_AGENT_CONFIGURATION = "agent_trigger_only"
ROLE_ABLATION_CONFIGURATIONS = (
    "full_agentic",
    "rule_parser_trigger_substitution",
    "mathematical_pricing_substitution",
    "evaluator_removal",
)
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
    "mathematical_pricing_substitution": "mathematical_pricing_ablation",
    "evaluator_removal": "evaluator_ablation",
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
    include_fixed: bool = False,
) -> list[RunSpec]:
    if agent_repetitions < 1:
        raise ValueError("agent_repetitions must be positive")
    if ablation_repetitions < 1:
        raise ValueError("ablation_repetitions must be positive")

    configurations = list(PRIMARY_DETERMINISTIC_CONFIGURATIONS)
    if include_fixed:
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
                for configuration in ROLE_ABLATION_CONFIGURATIONS:
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
        **read_solver_provenance(workbook),
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
    completed_rows: int,
) -> None:
    manifest = {
        "protocol_version": "advance_warning_matrix_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_revision(),
        "design": {
            "cases": list(unique_in_order(spec.case for spec in specs)),
            "modes": list(unique_in_order(spec.mode for spec in specs)),
            "variant": args.variant,
            "agent_repetitions": args.agent_repetitions if args.include_agent else 0,
            "role_ablation_repetitions": (
                args.ablation_repetitions if args.include_role_ablations else 0
            ),
            "primary_contrasts": [
                "agent_vs_rule_text",
                "agent_vs_numerical",
                "agent_vs_oracle",
            ],
            "secondary_role_ablations": list(ROLE_ABLATION_CONFIGURATIONS),
            "safety_first": True,
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
        "inputs": {
            "notice_file": "inputs/revision/advance_warning_notices_v1.json",
            "physical_event_file": (
                "inputs/revision/advance_warning_physical_events_v1.json"
            ),
        },
        "planned_runs": len(specs),
        "indexed_completed_runs": completed_rows,
        "runs": [asdict(spec) | {"run_id": spec.run_id} for spec in specs],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "matrix_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the prespecified three-case, two-mode advance-warning matrix with "
            "resume-safe repeated Agent and role-ablation trials."
        )
    )
    parser.add_argument("--case", action="append", choices=PRIMARY_CASES, default=[])
    parser.add_argument("--mode", action="append", choices=PRIMARY_MODES, default=[])
    parser.add_argument("--variant", default="uncertain_chat")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=48)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--include-agent", action="store_true")
    parser.add_argument("--agent-repetitions", type=int, default=5)
    parser.add_argument("--include-role-ablations", action="store_true")
    parser.add_argument("--ablation-repetitions", type=int, default=5)
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
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/revision/closed_loop"),
    )
    args = parser.parse_args()

    cases = args.case or list(PRIMARY_CASES)
    modes = args.mode or list(PRIMARY_MODES)
    specs = build_run_specs(
        cases=cases,
        modes=modes,
        variant=args.variant,
        include_agent=args.include_agent,
        agent_repetitions=args.agent_repetitions,
        include_role_ablations=args.include_role_ablations,
        ablation_repetitions=args.ablation_repetitions,
        include_fixed=args.include_fixed,
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
        workbook = workbook_path(output_root, spec)
        complete = workbook_is_complete(workbook, expected_timesteps)
        reused = complete and not args.force
        print(
            f"[{index}/{len(specs)}] {spec.run_id}: "
            f"{'reuse' if reused else 'run'}",
            flush=True,
        )
        if not reused:
            workbook.parent.mkdir(parents=True, exist_ok=True)
            try:
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
                    ),
                    cwd=ROOT,
                    check=True,
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
            write_manifest(
                output_root, specs, args=args, completed_rows=len(rows)
            )

    print(f"Indexed {len(rows)} of {len(specs)} planned runs in {output_root}")
    if failures:
        raise SystemExit("Failed runs: " + ", ".join(failures))


if __name__ == "__main__":
    main()

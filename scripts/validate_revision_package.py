from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REVISION = ROOT / "inputs" / "revision"
REQUIRED_FILES = (
    ROOT / "requirements-lock.txt",
    ROOT / "requirements-dev-lock.txt",
    REVISION / "advance_warning_ablation_protocol_v6.json",
    REVISION / "information_and_evaluator_ablation_protocol_v2.json",
    REVISION / "revision_sensitivity_protocol_v1.json",
    REVISION / "scaling_and_second_depot_protocol_v1.json",
    REVISION / "independent_validation_checklist_v1.md",
    ROOT / "agentic_workflow" / "prompts" / "trigger_system.txt",
    ROOT / "agentic_workflow" / "prompts" / "pricing_selfish_system.txt",
    ROOT / "agentic_workflow" / "prompts" / "pricing_altruistic_system.txt",
    ROOT / "agentic_workflow" / "prompts" / "evaluator_system.txt",
    ROOT / "agentic_workflow" / "prompts" / "trigger_variant_action_first.txt",
    ROOT / "agentic_workflow" / "prompts" / "trigger_variant_evidence_first.txt",
)
PRIVATE_MARKERS = (
    "canonical",
    "physical_truth",
    "benchmark_split",
    "wording_variant",
    "uncertainty_case",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def validate_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def validate_notice_assets(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("notices", data) if isinstance(data, dict) else data
    errors: list[str] = []
    for record in records:
        canonical = record.get("canonical") or {}
        for bus in canonical.get("affected_buses") or []:
            if not 1 <= int(bus) <= 8:
                errors.append(f"{record.get('notice_id')}: unknown bus {bus}")
        for charger in canonical.get("affected_chargers") or []:
            if not 1 <= int(charger) <= 8:
                errors.append(f"{record.get('notice_id')}: unknown charger {charger}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate reviewer-facing protocols and reproducibility files."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/revision/package_validation_report.json"),
    )
    args = parser.parse_args(argv)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    checks: list[dict[str, Any]] = []

    for path in REQUIRED_FILES:
        checks.append(
            {
                "check": f"required_file:{path.relative_to(ROOT)}",
                "passed": path.exists(),
                "sha256": sha256(path) if path.exists() else None,
            }
        )
    for path in REQUIRED_FILES:
        if path.suffix == ".json" and path.exists():
            try:
                validate_json(path)
                checks.append(
                    {"check": f"valid_json:{path.relative_to(ROOT)}", "passed": True}
                )
            except (json.JSONDecodeError, ValueError) as exc:
                checks.append(
                    {
                        "check": f"valid_json:{path.relative_to(ROOT)}",
                        "passed": False,
                        "error": str(exc),
                    }
                )

    protocol_path = REVISION / "advance_warning_ablation_protocol_v6.json"
    prompt_paths = {
        name: ROOT / "agentic_workflow" / "prompts" / name
        for name in (
            "trigger_system.txt",
            "pricing_selfish_system.txt",
            "pricing_altruistic_system.txt",
            "evaluator_system.txt",
        )
    }
    if protocol_path.exists() and all(path.exists() for path in prompt_paths.values()):
        protocol = validate_json(protocol_path)
        recorded_prompt_hashes = (protocol.get("controls") or {}).get(
            "prompt_sha256"
        ) or {}
        current_prompt_hashes = {
            name: sha256(path) for name, path in prompt_paths.items()
        }
        checks.append(
            {
                "check": "final_v6_prompt_hashes_match_protocol",
                "passed": recorded_prompt_hashes == current_prompt_hashes,
                "recorded": recorded_prompt_hashes,
                "current": current_prompt_hashes,
            }
        )

    notice_errors: list[str] = []
    for name in ("trigger_notices_v3.json", "advance_warning_notices_v1.json"):
        notice_errors.extend(validate_notice_assets(REVISION / name))
    checks.append(
        {
            "check": "canonical_assets_exist_in_eight_bus_source_instance",
            "passed": not notice_errors,
            "errors": notice_errors,
        }
    )

    from agentic_workflow.agents import build_openai_trigger_payload

    sentinel = {marker: f"private-{marker}" for marker in PRIVATE_MARKERS}
    sentinel.update(
        {
            "timestep": 1,
            "operational_notices": [{"text": "public synthetic message"}],
            "history": [],
        }
    )
    payload = build_openai_trigger_payload(sentinel)
    leaked = [marker for marker in PRIVATE_MARKERS if marker in payload]
    checks.append(
        {
            "check": "trigger_payload_private_marker_exclusion",
            "passed": not leaked,
            "leaked_fields": leaked,
        }
    )

    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_revision(),
        "automated_checks_passed": all(check["passed"] for check in checks),
        "human_validation_required": True,
        "human_checklist": str(
            (REVISION / "independent_validation_checklist_v1.md").relative_to(ROOT)
        ),
        "checks": checks,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["automated_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

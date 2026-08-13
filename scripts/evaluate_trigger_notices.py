from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_workflow.notices import (
    NoticeSeries,
    frozen_rule_parse,
    resolve_notice_coreferences,
)


FIELDS = (
    "source_type",
    "event_type",
    "phase",
    "affected_buses",
    "affected_chargers",
    "uncertainty",
    "uncertainty_details",
    "material",
    "updates",
)


def normalize(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, list):
        items = [item.model_dump() if hasattr(item, "model_dump") else item for item in value]
        return json.dumps(
            sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str)),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return value


def reference_action(canonical) -> str:
    return (
        "optimize"
        if canonical.material
        and canonical.phase in {"onset", "severity_change", "recovery"}
        and canonical.uncertainty_details.recommended_action == "optimize"
        else "skip"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the frozen notice parser against canonical truth.")
    parser.add_argument(
        "--notices",
        type=Path,
        default=Path("inputs/revision/trigger_notices_v3.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results/revision"))
    parser.add_argument(
        "--split", choices=("development", "test", "all"), default="test"
    )
    parser.add_argument("--label", default="stateful_rule_v3")
    args = parser.parse_args()
    series = NoticeSeries(args.notices)
    rows = []
    grouped = {}
    for record in series.records:
        if record.canonical is not None and (
            args.split == "all" or record.benchmark_split == args.split
        ):
            grouped.setdefault((record.scenario_id, record.wording_variant), []).append(record)
    for sequence in grouped.values():
        active_events = {}
        for record in sorted(sequence, key=lambda item: (item.report_timestep, item.notice_id)):
            parsed = resolve_notice_coreferences(
                record,
                frozen_rule_parse(record, {bus: bus for bus in range(1, 9)}),
                active_events,
            )
            row = {
                **record.public_dict(),
                "scenario_id": record.scenario_id,
                "wording_variant": record.wording_variant,
                "benchmark_split": record.benchmark_split,
                "uncertainty_case": record.uncertainty_case,
                "reference_action": reference_action(record.canonical),
                "parser_action": reference_action(parsed),
            }
            for field in FIELDS:
                expected = normalize(getattr(record.canonical, field))
                observed = normalize(getattr(parsed, field))
                row[f"{field}_correct"] = expected == observed
            expected_details = record.canonical.uncertainty_details
            observed_details = parsed.uncertainty_details
            for field in (
                "confidence_level",
                "provisional",
                "conflicting_evidence",
                "estimates",
                "recommended_action",
            ):
                row[f"uncertainty_{field}_correct"] = normalize(
                    getattr(expected_details, field)
                ) == normalize(getattr(observed_details, field))
            row["action_correct"] = row["reference_action"] == row["parser_action"]
            rows.append(row)
            # Conversational memory is observational, not permission to update
            # optimizer inputs. Retain skipped warnings so later coreferences are
            # available to the rule baseline on the same terms as the Agent.
            if parsed.phase in {"recovery", "stable"}:
                active_events.pop(parsed.event_id, None)
            else:
                active_events[parsed.event_id] = parsed.model_dump()
    frame = pd.DataFrame(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output_dir / f"{args.label}_scores.csv", index=False)
    metric_columns = [column for column in frame if column.endswith("_correct")]
    summary = {
        "n_decisions": len(frame),
        "metrics": {column: float(frame[column].mean()) for column in metric_columns},
        "by_wording_variant": {
            variant: {
                column: float(group[column].mean()) for column in metric_columns
            }
            for variant, group in frame.groupby("wording_variant")
        },
        "by_uncertainty_case": {
            case: {
                column: float(group[column].mean()) for column in metric_columns
            }
            for case, group in frame.groupby("uncertainty_case")
        },
        "by_benchmark_split": {
            split: {
                column: float(group[column].mean()) for column in metric_columns
            }
            for split, group in frame.groupby("benchmark_split")
        },
    }
    (args.output_dir / f"{args.label}_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

"""Report which episodes of a study are already on disk and which are missing.

The study runners index any complete workbook they find and re-execute only
what is absent, so a partially executed output root can be finished without
repeating work. This script answers the question that has to be asked before
spending API budget: what exactly is left to run?

    python scripts/report_study_status.py scaling --output-root results/revision/scaling_v1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_scaling_study import (  # noqa: E402
    build_specs as build_scaling_specs,
    output_path as scaling_workbook_path,
)


def scaling_rows(output_root: Path, repetitions: int) -> list[dict]:
    rows = []
    for spec in build_scaling_specs(repetitions=repetitions):
        workbook = scaling_workbook_path(output_root, spec)
        rows.append(
            {
                "group": f"{spec.depot}_{spec.fleet_size}",
                "configuration": spec.configuration,
                "mode": spec.mode,
                "repetition": spec.repetition,
                "external_llm": spec.uses_external_llm,
                "present": workbook.exists(),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", choices=("scaling",))
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()

    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = ROOT / output_root

    rows = scaling_rows(output_root, args.repetitions)
    if not output_root.exists():
        print(f"Output root does not exist yet: {output_root}")

    width = max(len(row["configuration"]) for row in rows)
    print(f"{'configuration'.ljust(width)}  {'present':>7}  {'missing':>7}  external LLM")
    for configuration in sorted({row["configuration"] for row in rows}):
        subset = [row for row in rows if row["configuration"] == configuration]
        present = sum(row["present"] for row in subset)
        uses_llm = any(row["external_llm"] for row in subset)
        print(
            f"{configuration.ljust(width)}  {present:7d}  {len(subset) - present:7d}"
            f"  {'yes' if uses_llm else 'no'}"
        )

    missing = [row for row in rows if not row["present"]]
    billable = [row for row in missing if row["external_llm"]]
    print()
    print(f"total episodes: {len(rows)}, present: {len(rows) - len(missing)}, missing: {len(missing)}")
    print(f"of the missing, {len(billable)} call the external model and {len(missing) - len(billable)} do not")
    if missing:
        print("\nmissing episodes:")
        for row in missing:
            print(
                f"  {row['group']:12s} {row['mode']:10s} {row['configuration']:24s}"
                f" r{row['repetition']:03d}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

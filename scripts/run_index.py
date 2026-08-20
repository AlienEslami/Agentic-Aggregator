"""Merge run-index rows instead of replacing them.

The study runners write one CSV listing every episode they indexed. Writing
that file from the current invocation alone is destructive whenever the
invocation was filtered: running a single instance or a single configuration
into an output root that already holds a complete study silently rewrites the
index down to the filtered subset. The workbooks survive, so the loss is
invisible until someone reads the index and finds most of the evidence gone.

Rows are merged on their run identifier. A freshly indexed row supersedes the
recorded one, since it was produced from the workbook as it stands now, and
rows absent from this invocation are carried over unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

RUN_KEY = "run_id"


def merge_run_rows(
    existing: Iterable[dict[str, Any]],
    fresh: Iterable[dict[str, Any]],
    *,
    key: str = RUN_KEY,
) -> list[dict[str, Any]]:
    """Combine recorded rows with the rows indexed by this invocation."""

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in list(existing) + list(fresh):
        identifier = row.get(key)
        if identifier is None:
            # Without an identifier a row cannot be matched, so keep it as is
            # rather than dropping it or merging unrelated rows together.
            identifier = f"__unkeyed__{len(order)}"
        if identifier not in merged:
            order.append(identifier)
        merged[identifier] = row
    return [merged[identifier] for identifier in order]


def read_run_index(path: Path, *, key: str = RUN_KEY) -> list[dict[str, Any]]:
    """Read a previously written index, tolerating absence or corruption."""

    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, OSError):
        return []
    if key not in frame.columns:
        return []
    return frame.where(frame.notna(), None).to_dict(orient="records")


def write_run_index(
    output_root: Path,
    rows: list[dict[str, Any]],
    *,
    stem: str,
    key: str = RUN_KEY,
) -> list[dict[str, Any]]:
    """Write the union of the recorded and freshly indexed rows."""

    output_root.mkdir(parents=True, exist_ok=True)
    csv_path = output_root / f"{stem}.csv"
    merged = merge_run_rows(read_run_index(csv_path, key=key), rows, key=key)
    frame = pd.DataFrame(merged)
    frame.to_csv(csv_path, index=False)
    (output_root / f"{stem}.json").write_text(
        json.dumps(merged, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return merged

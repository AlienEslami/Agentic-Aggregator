"""Rebuild MANIFEST.sha256 for the reviewer-facing package.

Digests are taken over the raw file bytes.  `.gitattributes` pins every text
artifact to LF, so the recorded values verify on Linux, macOS and Windows from
a clean checkout; without that pin the same file would hash differently per
platform and the manifest would be unverifiable.

The package prefix is fixed rather than derived from the checkout directory
name, so re-running this script in a clone named differently does not rewrite
every path in the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "MANIFEST.sha256"
PACKAGE_NAME = "Agentic-Aggregator-Python-Workflow"
EXCLUDED_PARTS = {".git", ".pytest_cache", ".tmp", "__pycache__", "results"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".xls", ".xlsx", ".xlsb", ".xlsm"}


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def included(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return (
        path != OUTPUT
        and path.is_file()
        and not any(part in EXCLUDED_PARTS for part in relative.parts)
        and path.suffix.lower() not in EXCLUDED_SUFFIXES
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-name",
        default=PACKAGE_NAME,
        help="Prefix used for the recorded paths (default: %(default)s).",
    )
    args = parser.parse_args()

    rows = ["SHA256  BYTES  PATH"]
    for path in sorted((path for path in ROOT.rglob("*") if included(path))):
        relative = f"{args.package_name}/{path.relative_to(ROOT).as_posix()}"
        rows.append(f"{digest(path)}  {path.stat().st_size}  {relative}")
    OUTPUT.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    print(f"{OUTPUT} ({len(rows) - 1} files)")


if __name__ == "__main__":
    main()

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "MANIFEST.sha256"
EXCLUDED_PARTS = {".git", ".pytest_cache", "__pycache__", "results"}
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
    rows = ["SHA256  BYTES  PATH"]
    for path in sorted((path for path in ROOT.rglob("*") if included(path))):
        relative = f"{ROOT.name}/{path.relative_to(ROOT).as_posix()}"
        rows.append(f"{digest(path)}  {path.stat().st_size}  {relative}")
    OUTPUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"{OUTPUT} ({len(rows) - 1} files)")


if __name__ == "__main__":
    main()

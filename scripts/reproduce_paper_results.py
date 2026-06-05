from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_command(command: list[str], dry_run: bool) -> None:
    printable = " ".join(command)
    print(f"\n$ {printable}")
    if dry_run:
        return
    subprocess.run(command, cwd=ROOT, check=True)


def load_json_metrics(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    keys = [
        "scenario",
        "pto_daily_cost",
        "aggregator_revenue",
        "total_kwh_bought",
        "total_kwh_sold",
        "avg_grid_price",
        "avg_buy_price",
        "avg_sell_price",
    ]
    return {key: data.get(key) for key in keys}


def available_solver() -> str:
    try:
        from app import configure_milp_solver

        _, solver_name = configure_milp_solver(time_limit=1, mip_gap=0.1)
        return solver_name
    except Exception as exc:
        return f"unavailable: {exc}"


def write_manifest(output_dir: Path, args: argparse.Namespace) -> Path:
    dumb_result = output_dir / "dumb_charging_result.json"
    no_v2g_result = output_dir / "no_v2g_optimization_result.json"
    rel_output_dir = output_dir.relative_to(ROOT)
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "solver_detected": available_solver(),
        "inputs": {
            "input_workbook": str(args.input),
            "spot_prices_file": str(args.spot_prices_file),
            "tariffs_file": str(args.tariffs_file),
        },
        "outputs": {
            "summary_workbook": str(rel_output_dir / "day_ahead_local_comparison.xlsx"),
            "day_ahead_benchmark_dir": str(rel_output_dir / "day_ahead_benchmark"),
            "dumb_charging_result": str(rel_output_dir / "dumb_charging_result.json"),
            "no_v2g_optimization_result": str(rel_output_dir / "no_v2g_optimization_result.json"),
        },
        "metrics": {
            "dumb_charging_no_v2g": load_json_metrics(dumb_result),
            "optimization_no_v2g": load_json_metrics(no_v2g_result),
        },
        "agentic_workflows": {
            "day_ahead": "workflows/day_ahead_workflow_prompt_analysis_baseline.json",
            "real_time": "workflows/real_time_final.json",
            "note": "Import these n8n workflows to reproduce prompt-driven S3/S4 and RT disturbance experiments.",
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reproduce the repository's deterministic paper artifacts in a clean output folder."
    )
    parser.add_argument("--input", default="data/inputs/case_study_inputs.xlsx", help="Canonical input workbook.")
    parser.add_argument("--spot-prices-file", default="data/inputs/spot_prices.xlsx", help="External spot-price workbook.")
    parser.add_argument("--tariffs-file", default="data/inputs/aggregator_tariffs.xlsx", help="External aggregator tariff workbook.")
    parser.add_argument("--output-dir", default="results/reproduction", help="Folder for generated artifacts.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not delete an existing output folder before running.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    python = sys.executable
    output_dir = ROOT / args.output_dir
    summary_workbook = output_dir / "day_ahead_local_comparison.xlsx"
    benchmark_dir = output_dir / "day_ahead_benchmark"
    dumb_result = output_dir / "dumb_charging_result.json"
    no_v2g_result = output_dir / "no_v2g_optimization_result.json"

    if output_dir.exists() and not args.keep_existing and not args.dry_run:
        shutil.rmtree(output_dir)
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    commands = [
        [
            python,
            "run_dumb_charging.py",
            "--input",
            args.input,
            "--spot-prices-file",
            args.spot_prices_file,
            "--output",
            str(dumb_result.relative_to(ROOT)),
            "--summary-workbook",
            str(summary_workbook.relative_to(ROOT)),
            "--reasoning-source",
            "paper_reproduction_script",
        ],
        [
            python,
            "run_no_v2g_optimization.py",
            "--input",
            args.input,
            "--spot-prices-file",
            args.spot_prices_file,
            "--output",
            str(no_v2g_result.relative_to(ROOT)),
            "--summary-workbook",
            str(summary_workbook.relative_to(ROOT)),
            "--reasoning-source",
            "paper_reproduction_script",
        ],
        [
            python,
            "generate_benchmark_files.py",
            "--input",
            args.input,
            "--spot-prices-file",
            args.spot_prices_file,
            "--tariffs-file",
            args.tariffs_file,
            "--output-dir",
            str(benchmark_dir.relative_to(ROOT)),
            "--summary-workbook",
            str(summary_workbook.relative_to(ROOT)),
            "--reasoning-source",
            "paper_reproduction_script",
        ],
    ]

    for command in commands:
        run_command(command, args.dry_run)

    if args.dry_run:
        print("\nDry run complete.")
        return

    manifest_path = write_manifest(output_dir, args)
    print(f"\nReproduction artifacts written to {output_dir.relative_to(ROOT)}")
    print(f"Manifest: {manifest_path.relative_to(ROOT)}")
    print("\nNext step for agentic S3/S4 and RT scenarios: import the n8n workflows in workflows/.")


if __name__ == "__main__":
    main()

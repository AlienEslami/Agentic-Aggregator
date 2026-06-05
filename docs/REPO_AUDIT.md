# Repository Audit

This audit records the cleanup decisions made for the paper replication package.

## Kept

- `data/inputs/`: canonical case-study input, spot prices, and aggregator tariffs.
- `data/intraday_prices/`: real-time remaining-horizon price profiles used by the RT workflow.
- `paper_outputs/`: output workbooks and prompt artifacts used in the paper, renamed by paper table/mode/scenario.
- `workflows/`: sanitized n8n workflow exports for day-ahead and real-time agentic experiments.
- `scripts/reproduce_paper_results.py`: deterministic reproduction runner that writes generated artifacts to `results/`.
- Core optimization/API scripts: `app.py`, `app_rt.py`, `generate_benchmark_files.py`, `run_dumb_charging.py`, `run_no_v2g_optimization.py`, `scenario_summary.py`.

## Removed

- `Files/STM dataset/`: unrelated STM-scale exploratory data; not used by the 8-bus paper case study.
- `Files/day_ahead_benchmark/`: generated rolling benchmark workbooks; reproducible from `generate_benchmark_files.py`.
- `Files/day_ahead_local_comparison.xlsx`, `Files/dumb_charging_result.json`, `Files/no_v2g_optimization_result.json`, `Files/optimization_results_gurobi.xlsx`: generated or legacy result artifacts now represented by `paper_outputs/` or regenerated under `results/`.
- `benchmark_no_v2g_comparison.ipynb`: superseded by `scripts/reproduce_paper_results.py`.
- `Procfile` and `nixpack.toml`: deployment-specific files not needed for paper reproduction.
- Local cache files such as `.DS_Store` and `__pycache__/`.

## Generated Outputs

`results/` is ignored by git. Running the reproduction script creates:

- `results/reproduction/manifest.json`
- `results/reproduction/day_ahead_local_comparison.xlsx`
- `results/reproduction/dumb_charging_result.json`
- `results/reproduction/no_v2g_optimization_result.json`
- `results/reproduction/day_ahead_benchmark/`


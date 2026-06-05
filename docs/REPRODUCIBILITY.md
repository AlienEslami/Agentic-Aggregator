# Reproducibility Guide

This guide describes the recommended path for reproducing the paper artifacts from a clean clone.

Paper: *A Multi-Agentic Aggregator Design for Electric Bus Fleet Charging and Grid Flexibility Management*

## 1. Environment

Create and activate a Python environment, then install the project dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The optimization model is a MILP. The code now tries solvers in this order: Gurobi, HiGHS, CBC, and GLPK. Gurobi was used during development; HiGHS can be installed through `highspy` and is listed in `requirements.txt`.

## 2. Main Inputs

Use `Files/Inputs.xlsx` as the canonical workbook. It contains:

- `Settings`: timestep length, optimization mode, V2G flag.
- `Buses`: bus IDs, battery capacities, initial state of charge.
- `Chargers`: charger IDs and charging power.
- `Trips`: assigned trips and energy-use assumptions.
- `Prices` or `Spot Prices`: spot-market prices by timestep.
- `Tariffs`: optional buy/sell tariffs used by the aggregator.
- `Realtime state`: observed bus states used by real-time re-optimization.

Optional external price inputs are stored in `Files/SpotPrices.xlsx`, `Files/Tariffs.xlsx`, and `Files/IntradayPrices/`.

The paper output artifacts are stored in `paper_outputs/`, with cleaned filenames that follow the paper structure. See `paper_outputs/README.md` and `paper_outputs/manifest.json`.

## 3. Day-Ahead Baseline

For the deterministic Python-side artifacts, run:

```bash
python scripts/reproduce_paper_results.py
```

This writes fresh artifacts to `results/reproduction/` and leaves the checked-in `Files/` artifacts unchanged. The manifest at `results/reproduction/manifest.json` records the detected solver, input paths, output paths, and key baseline metrics.

To regenerate the checked-in benchmark folder directly, use the lower-level command below.

Generate the day-ahead optimization benchmark and rolling real-time input workbooks:

```bash
python generate_benchmark_files.py \
  --input Files/Inputs.xlsx \
  --spot-prices-file Files/SpotPrices.xlsx \
  --tariffs-file Files/Tariffs.xlsx \
  --output-dir Files/day_ahead_benchmark \
  --summary-workbook Files/day_ahead_local_comparison.xlsx
```

Outputs:

- `Files/day_ahead_benchmark/benchmark_summary.txt`
- `Files/day_ahead_benchmark/benchmark_timestep_01.xlsx` through `benchmark_timestep_48.xlsx`
- appended rows in `day_ahead_summary` and `agent_reasoning` sheets of the summary workbook

## 4. Baseline Comparisons

Run the no-V2G optimizer:

```bash
python run_no_v2g_optimization.py \
  --input Files/Inputs.xlsx \
  --spot-prices-file Files/SpotPrices.xlsx \
  --output Files/no_v2g_optimization_result.json \
  --summary-workbook Files/day_ahead_local_comparison.xlsx
```

Run the dumb-charging benchmark:

```bash
python run_dumb_charging.py \
  --input Files/Inputs.xlsx \
  --spot-prices-file Files/SpotPrices.xlsx \
  --output Files/dumb_charging_result.json \
  --summary-workbook Files/day_ahead_local_comparison.xlsx
```

## 5. API and Agentic Workflows

Start the optimization API:

```bash
python app.py
```

The API listens on `http://127.0.0.1:5002` unless `PORT` is set. The `/optimize` endpoint expects the workbook data already converted to JSON arrays; the n8n workflows in `workflows/` perform this extraction from Google Sheets/Drive and then call the API.

The real-time API implementation is in `app_rt.py`. It is designed for remaining-horizon re-optimization with observed bus states, disturbances, and intraday prices.

The paper's S3/S4 aggregator cases and all RT disturbance cases are prompt-driven. They require the n8n workflows because the accepted tariffs come from the Pricing Agent and Evaluator Agent loops. The deterministic Python scripts provide the optimization core and baseline artifacts; the workflows provide the agentic orchestration layer.

## 6. Workflow Imports

Import these files into n8n:

- `workflows/day_ahead_workflow_prompt_analysis_baseline.json`
- `workflows/real_time_final.json`

After import, update Google credentials, Google document IDs, and HTTP endpoint URLs for your deployment.

Use `docs/PAPER_RESULTS.md` as the target table when comparing reproduced values against the paper.

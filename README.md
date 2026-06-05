# Agentic Electric Bus Charging and V2G Optimization

This repository contains the code, data templates, benchmark outputs, and n8n workflow exports used for the paper's agentic day-ahead and real-time electric bus charging experiments.

## Abstract

This repository accompanies a study on agentic optimization for electric-bus fleet charging and vehicle-to-grid scheduling. The framework couples a mixed-integer optimization model for public transport operator operations with large-language-model pricing and re-optimization workflows. A day-ahead layer computes a baseline charging and V2G plan from bus, charger, trip, price, and tariff data. A real-time layer then updates the remaining horizon when observed fleet states, delays, service disruptions, or intraday prices diverge from the baseline. The optimization minimizes operator energy cost while tracking aggregator margins, grid purchases, V2G sales, state-of-charge trajectories, service feasibility, and re-optimization outcomes. The repository provides the Python optimization code, Excel input and benchmark files, scenario summary utilities, baseline comparison scripts, and n8n workflow exports used to orchestrate the agentic day-ahead and real-time experiments. Together, these artifacts are intended to make the computational workflow transparent, reproducible, and adaptable to other electric-bus fleet datasets.

## Repository Contents

```text
.
├── app.py                              # Day-ahead API and core PTO MILP model
├── app_rt.py                           # Real-time remaining-horizon optimizer API
├── generate_benchmark_files.py         # Builds day-ahead baseline and rolling RT workbooks
├── run_no_v2g_optimization.py          # Charging-only optimized baseline
├── run_dumb_charging.py                # Rule-like dumb-charging baseline
├── scenario_summary.py                 # Summary and reasoning-sheet metrics
├── benchmark_no_v2g_comparison.ipynb   # Notebook for baseline comparison
├── requirements.txt                    # Python dependencies
├── docs/
│   ├── PAPER_ABSTRACT.md
│   └── REPRODUCIBILITY.md
├── workflows/
│   ├── day_ahead_workflow_prompt_analysis_baseline.json
│   ├── real_time_final.json
│   └── README.md
└── Files/
    ├── Inputs.xlsx                     # Canonical input workbook
    ├── SpotPrices.xlsx                 # Optional external spot prices
    ├── Tariffs.xlsx                    # Optional external buy/sell tariffs
    ├── IntradayPrices/                 # Intraday price scenarios by timestep
    ├── day_ahead_benchmark/            # Rolling benchmark workbooks
    ├── day_ahead_local_comparison.xlsx # Summary workbook
    ├── dumb_charging_result.json
    └── no_v2g_optimization_result.json
```

## File Usage

`Files/Inputs.xlsx` is the main replication workbook. Its sheets define global settings, buses, chargers, trips, prices, tariffs, and real-time fleet state. Use this file when reproducing the paper scenarios or adapting the workflow to a new fleet.

`Files/SpotPrices.xlsx` and `Files/Tariffs.xlsx` decouple wholesale grid prices from aggregator buy/sell tariffs. Passing these files to the scripts makes the price inputs explicit and easier to audit.

`Files/day_ahead_benchmark/` contains one workbook per timestep. Each workbook stores the day-ahead state translated into a real-time input for that timestep.

`Files/IntradayPrices/` contains timestep-specific intraday price files used by the real-time workflow.

`workflows/` contains n8n exports for the agentic orchestration layer. These files preserve the workflow structure and prompts, but users must remap credentials, Google document IDs, and HTTP endpoint URLs before running them.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The model is a MILP. The code attempts to use the first available solver from Gurobi, HiGHS, CBC, or GLPK. Gurobi was used during development; `highspy` is included for HiGHS-based replication where supported.

## Reproducing the Day-Ahead Baseline

```bash
python generate_benchmark_files.py \
  --input Files/Inputs.xlsx \
  --spot-prices-file Files/SpotPrices.xlsx \
  --tariffs-file Files/Tariffs.xlsx \
  --output-dir Files/day_ahead_benchmark \
  --summary-workbook Files/day_ahead_local_comparison.xlsx
```

This appends summary rows to `Files/day_ahead_local_comparison.xlsx` and regenerates the rolling benchmark workbooks.

## Reproducing Baseline Comparisons

Optimized charging without V2G:

```bash
python run_no_v2g_optimization.py \
  --input Files/Inputs.xlsx \
  --spot-prices-file Files/SpotPrices.xlsx \
  --output Files/no_v2g_optimization_result.json \
  --summary-workbook Files/day_ahead_local_comparison.xlsx
```

Dumb-charging benchmark:

```bash
python run_dumb_charging.py \
  --input Files/Inputs.xlsx \
  --spot-prices-file Files/SpotPrices.xlsx \
  --output Files/dumb_charging_result.json \
  --summary-workbook Files/day_ahead_local_comparison.xlsx
```

## Running the APIs

Day-ahead/core optimization API:

```bash
python app.py
```

Real-time optimization API:

```bash
python app_rt.py
```

Both services default to port `5002` unless `PORT` is set. The `/optimize` endpoints expect structured JSON input arrays for buses, chargers, trips, prices, tariffs, and real-time state. The n8n workflows are the intended bridge from Google Sheets/Drive or workbook-derived data to those API payloads.

## Workflow Replication

Import these n8n workflows:

- `workflows/day_ahead_workflow_prompt_analysis_baseline.json`
- `workflows/real_time_final.json`

After import, update:

- Google Sheets and Google Drive credentials
- Google document and folder IDs
- OpenAI credentials
- HTTP request URLs for the local or deployed API
- Sheet names if your replicated data source differs from `Files/Inputs.xlsx`

See `workflows/README.md` and `docs/REPRODUCIBILITY.md` for details.

## Model Summary

The optimization assigns buses to required trips, charging sessions, and optional V2G discharge sessions over discrete timesteps. It tracks battery energy for each bus, charger exclusivity, site charging limits, state-of-charge bounds, trip service requirements, end-of-day reserve, and tariff-based costs/revenues.

The day-ahead objective minimizes:

```text
sum_t S_buy[t] * w_buy[t] - sum_t S_sell[t] * w_sell[t]
```

where `S_buy` is the tariff paid by the public transport operator, `S_sell` is the V2G revenue tariff, `w_buy` is grid energy purchased, and `w_sell` is energy sold through V2G. The real-time optimizer adds service continuity, interruption, switching, and SOC-shortfall penalties for remaining-horizon rescheduling.

## Citation and License

Add the final paper citation and license before public release. If you want others to reuse the code, choose an explicit license such as MIT, Apache-2.0, or BSD-3-Clause.


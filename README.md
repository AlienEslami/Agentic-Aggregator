# A Multi-Agentic Aggregator Design for Electric Bus Fleet Charging and Grid Flexibility Management

This repository contains the code, data templates, benchmark outputs, and n8n workflow exports used for the paper *A Multi-Agentic Aggregator Design for Electric Bus Fleet Charging and Grid Flexibility Management*.

## Abstract

Electric buses are becoming flexible energy assets, but their grid value depends on decisions that must adapt to service delays, battery states, electricity prices, and route-energy uncertainty. This paper proposes a multi-agentic aggregator framework that couples an optimization-based electric bus scheduling model with supervisory agents for pricing, disturbance mitigation, and schedule evaluation. The optimization core preserves bus-system operational feasibility across routes, chargers, batteries, and vehicle-to-grid exchanges, while the agentic layer determines when re-optimization is needed and how flexibility value is shared between the aggregator and the public transport operator.

## Repository Contents

```text
.
├── app.py                              # Day-ahead API and core PTO MILP model
├── app_rt.py                           # Real-time remaining-horizon optimizer API
├── generate_benchmark_files.py         # Builds day-ahead baseline and rolling RT workbooks
├── run_no_v2g_optimization.py          # Charging-only optimized baseline
├── run_dumb_charging.py                # Rule-like dumb-charging baseline
├── scripts/
│   └── reproduce_paper_results.py      # One-command deterministic reproduction runner
├── scenario_summary.py                 # Summary and reasoning-sheet metrics
├── requirements.txt                    # Python dependencies
├── data/
│   ├── inputs/                          # Canonical case-study inputs
│   └── intraday_prices/                 # RT price profiles by timestep
├── docs/
│   ├── PAPER_ABSTRACT.md
│   ├── PAPER_RESULTS.md
│   ├── REPO_AUDIT.md
│   └── REPRODUCIBILITY.md
├── paper_outputs/                     # Paper tables, RT outputs, prompt artifacts
├── workflows/
│   ├── day_ahead_workflow_prompt_analysis_baseline.json
│   ├── real_time_final.json
│   └── README.md
```

## File Usage

`data/inputs/case_study_inputs.xlsx` is the main replication workbook. Its sheets define global settings, buses, chargers, trips, prices, tariffs, and real-time fleet state. Use this file when reproducing the paper scenarios or adapting the workflow to a new fleet.

`data/inputs/spot_prices.xlsx` and `data/inputs/aggregator_tariffs.xlsx` decouple wholesale grid prices from aggregator buy/sell tariffs. Passing these files to the scripts makes the price inputs explicit and easier to audit.

`data/intraday_prices/` contains timestep-specific intraday price files used by the real-time workflow.

`results/` is ignored by git and is used for regenerated benchmark files, JSON results, and local reproduction summaries.

`workflows/` contains n8n exports for the agentic orchestration layer. These files preserve the workflow structure and prompts, but users must remap credentials, Google document IDs, and HTTP endpoint URLs before running them.

`paper_outputs/` contains the output workbooks and prompt artifacts used in the paper, renamed by paper section, strategy, mode, and scenario. The folder includes day-ahead Table 6 files, real-time disturbance files for Tables 7 and 9, combined-scenario files, prompt-sensitivity Table 14 files, and prompt templates.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The model is a MILP. The code attempts to use the first available solver from Gurobi, HiGHS, CBC, or GLPK. Gurobi was used during development; `highspy` is included for HiGHS-based replication where supported.

## One-Command Reproduction

Run the deterministic Python-side reproduction package without overwriting the checked-in artifacts:

```bash
python scripts/reproduce_paper_results.py
```

This writes fresh outputs to `results/reproduction/`, including a `manifest.json`, a reproduction summary workbook, baseline JSON files, and regenerated day-ahead benchmark workbooks. The `results/` folder is ignored by git.

The LLM-driven S3/S4 day-ahead cases, real-time disturbances, and prompt-sensitivity experiments are reproduced by importing the n8n workflows in `workflows/` and reconnecting credentials, document IDs, and API URLs. See `docs/REPRODUCIBILITY.md` and `docs/PAPER_RESULTS.md`.

## Reproducing the Day-Ahead Baseline

```bash
python generate_benchmark_files.py \
  --input data/inputs/case_study_inputs.xlsx \
  --spot-prices-file data/inputs/spot_prices.xlsx \
  --tariffs-file data/inputs/aggregator_tariffs.xlsx \
  --output-dir results/day_ahead_benchmark \
  --summary-workbook results/day_ahead_local_comparison.xlsx
```

This appends summary rows to `results/day_ahead_local_comparison.xlsx` and regenerates the rolling benchmark workbooks.

## Reproducing Baseline Comparisons

Optimized charging without V2G:

```bash
python run_no_v2g_optimization.py \
  --input data/inputs/case_study_inputs.xlsx \
  --spot-prices-file data/inputs/spot_prices.xlsx \
  --output results/no_v2g_optimization_result.json \
  --summary-workbook results/day_ahead_local_comparison.xlsx
```

Dumb-charging benchmark:

```bash
python run_dumb_charging.py \
  --input data/inputs/case_study_inputs.xlsx \
  --spot-prices-file data/inputs/spot_prices.xlsx \
  --output results/dumb_charging_result.json \
  --summary-workbook results/day_ahead_local_comparison.xlsx
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
- Sheet names if your replicated data source differs from `data/inputs/case_study_inputs.xlsx`

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

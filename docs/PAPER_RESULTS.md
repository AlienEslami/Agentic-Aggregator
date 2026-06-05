# Paper Result Targets

This file records the main result targets from the paper so that reproduced artifacts can be checked against the published tables.

## Paper

Title: *A Multi-Agentic Aggregator Design for Electric Bus Fleet Charging and Grid Flexibility Management*

Authors: Ali Eslami, Jonatas Augusto Manzolli, Luis Miranda-Moreno, Jiangbo Yu

## Case Study Configuration

- Fleet: 8 electric buses
- Battery capacity: 365 kWh per bus
- Initial SOC: 20%
- Chargers: 8 depot chargers
- Charger rating: 200 kW
- Routes/service blocks: 8
- Horizon: 24 hours
- Time discretization: 30 minutes, 48 intervals
- Spot price range: approximately 0.067 to 0.122 EUR/kWh
- Average spot price: approximately 0.090 EUR/kWh

## Day-Ahead Strategy Targets

| ID | Strategy | V2G | PTO cost (EUR/day) | Aggregator revenue (EUR/day) | Bought (kWh/day) | Sold (kWh/day) |
|---|---|---:|---:|---:|---:|---:|
| S1 | Dumb charging | Off | 218.10 | 0.00 | 2400.0 | 0.0 |
| S2 | Smart charging, no V2G | Off | 130.47 | 0.00 | 1600.0 | 0.0 |
| S3 | Profit-based aggregator | On | 140.59 | 20.30 | 1900.0 | 300.0 |
| S4 | Operational-based aggregator | On | 118.91 | 2.39 | 2000.0 | 400.0 |

The deterministic Python scripts reproduce the non-agentic baselines and generate the optimization-ready day-ahead benchmark files. The prompt-driven S3/S4 values are reproduced by importing and running the n8n workflows in `workflows/`, because those cases depend on the Pricing and Evaluator Agent prompts and accepted multiplier vectors.

The corresponding paper output workbooks are stored in `paper_outputs/day_ahead/table_06/`.

## Real-Time Disturbance Families

Each real-time scenario is evaluated under both profit-based and operational-based coordination:

- Delay: `D-30 beg.`, `D+30 beg.`, `D-30 end`, `D+30 end`
- Energy: `E+50`, `E-50`
- Price: `P+25`, `P+50`, `P-25`, `P-50`
- Combined: `C-Seq`, `C-All 5-48`, `C-All 5-25`, `C-All 20-48`

The key paper-level comparison is that operational-based coordination reduces PTO cost in all fourteen matched real-time scenarios relative to profit-based coordination, while reducing aggregator revenue.

The corresponding paper output workbooks are stored in `paper_outputs/real_time/profit_based/` and `paper_outputs/real_time/operational_based/`.

## Prompt-Sensitivity Targets

| Mode | Prompt paradigm | PTO cost (EUR/day) | Aggregator revenue (EUR/day) |
|---|---|---:|---:|
| Profit-based | Zero-shot | 137.13 | 6.66 |
| Profit-based | Few-shot | 137.31 | 10.61 |
| Profit-based | Chain-of-thought | 135.23 | 15.96 |
| Profit-based | Few-shot + chain-of-thought | 140.59 | 20.30 |
| Operational-based | Zero-shot | 118.83 | 2.75 |
| Operational-based | Few-shot | 118.51 | 2.43 |
| Operational-based | Chain-of-thought | 119.22 | 3.15 |
| Operational-based | Few-shot + chain-of-thought | 118.91 | 2.39 |

The corresponding workbooks are stored in `paper_outputs/prompt_sensitivity/table_14/`, and the prompt documents are stored in `paper_outputs/prompt_sensitivity/prompts/`.

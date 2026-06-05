# Data Inputs

This folder contains only input data needed to reproduce the paper workflows.

## Structure

- `inputs/case_study_inputs.xlsx`: canonical 8-bus case-study workbook used by the optimization scripts.
- `inputs/spot_prices.xlsx`: spot-market price profile used as the grid-price input.
- `inputs/aggregator_tariffs.xlsx`: buy/sell tariff profile used for aggregator-guided day-ahead optimization runs.
- `intraday_prices/`: remaining-horizon price workbooks used by the real-time workflow.

Generated files are intentionally excluded from this folder. Use `scripts/reproduce_paper_results.py` to write regenerated artifacts under `results/reproduction/`.


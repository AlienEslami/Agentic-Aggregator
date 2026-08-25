# Selfish-mode five-repetition ablation results

This folder contains the selfish-mode subset of the final corrected 120-episode matrix. It was derived without rerunning or altering the 60 selfish episodes from protocol `advance_warning_matrix_v5`, generated from commit `9ed28b6ae0d6782d8e16fd324f8ce7b145f9f316`. The subset manifest records the SHA-256 hashes of the original complete matrix and manifest.

## Experimental design

- Objective mode: selfish; higher realized full-day aggregator revenue is better.
- Cases: late return, charger-bank shutdown, and combined evening disturbance.
- Configurations: full agentic workflow, rule-parser trigger substitution, deterministic pricing substitution, and evaluator removal.
- Repetitions: five per case/configuration cell.
- Total: 60 completed episodes.
- LLM: `gpt-5.6-luna`.
- Optimizer: Gurobi only; no fallback solver was used.
- Approximate OpenAI API cost: USD 1.058006.
- Total recorded LLM tokens: 5,334,568.

Raw per-run Excel workbooks, API transcripts, and canonical hidden truth are deliberately not published in this repository.

## Main findings

The full agentic workflow was realized-operationally feasible in all 15 runs. The deterministic-pricing and evaluator-removal configurations were also feasible in all 15 runs. The rule-parser configuration was feasible in 14 of 15 runs; its one failure was in the charger-bank shutdown case.

The feasibility-first paired outcomes were:

| Full workflow compared with | Feasibility wins | Economic wins | Ties | Economic losses |
|---|---:|---:|---:|---:|
| Rule-parser trigger substitution | 1 | 9 | 3 | 2 |
| Deterministic pricing substitution | 0 | 10 | 3 | 2 |
| Evaluator removal | 0 | 11 | 2 | 2 |

The economic columns include only pairs where both runs were operationally feasible. A difference smaller than EUR 0.001 is counted as a tie.

Mean full-workflow revenue gains by case were:

| Ablated role | Late return | Charger shutdown | Combined disturbance |
|---|---:|---:|---:|
| Trigger Agent versus rule parser | +1.189 | +7.020 | -0.490 |
| Pricing Agent versus deterministic pricing | +5.532 | +0.674 | +3.624 |
| Evaluator versus evaluator removal | +11.880 | +1.453 | +3.701 |

All values are EUR of realized full-day aggregator revenue, with positive values favoring the full workflow. The charger Trigger contrast includes four economically comparable rule-parser repetitions because the fifth rule-parser run was infeasible.

The 95% bootstrap interval was entirely above zero for the charger Trigger comparison, late-return and combined Pricing comparisons, and late-return and combined Evaluator comparisons. The other case-level intervals included zero, so those mean differences should not be presented as conclusive on their own.

The evaluator audit covered 102 optimization decisions. No accepted rerun was operationally worse than an earlier usable attempt, and no selected schedule was worse than the best usable candidate under the workflow's selection ordering.

## Files

- `matrix_runs.csv`: the 60 selfish episodes with feasibility, settlement, solver, LLM-token, API-cost, time, and memory measurements.
- `matrix_manifest.json`: the filtered run inventory, source hashes, frozen protocol, input hashes, execution budget, and solver/LLM provenance.
- `analysis_summary.json`: complete machine-readable analysis and bootstrap summaries.
- `method_summary.csv`: case/configuration aggregates and confidence intervals.
- `ablation_contrasts.csv`: Trigger, Pricing, and Evaluator ablation contrasts.
- `secondary_outcome_contrasts.csv`: charging, V2G, grid-price alignment, and peak-import comparisons.
- `evaluator_decision_audit.csv`: decision-level audit of reruns and saved-candidate selection.
- `pricing_multiplier_summary.csv`: chosen/reference multiplier statistics.

Confidence intervals are nonparametric bootstrap intervals over the five repetitions within each case/configuration cell. The run-level workbook paths and hashes in the CSV identify the local raw artifacts used to construct the summaries; the workbooks themselves remain excluded from version control.

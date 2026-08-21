# Altruistic 50% baseline-revenue retention experiment

This folder contains the compact, non-workbook results from the corrected altruistic ablation matrix. The experiment was run under protocol `advance_warning_matrix_v6` from commit `6c5090eb56efc3054c4d4b2a451323dabfad49cb`.

## Experimental design

- Objective mode: altruistic.
- Revenue protection: retain at least 50% of the frozen day-ahead full-day aggregator revenue.
- Baseline aggregator revenue: USD 18.4285133072.
- Full-day retention floor: USD 9.2142566536.
- Cases: late return, charger-bank shutdown, and combined evening disturbance.
- Configurations: full agentic workflow, rule-parser trigger substitution, deterministic pricing substitution, and evaluator removal.
- Repetitions: five per case/configuration cell.
- Total: 60 completed episodes.
- LLM: `gpt-5.6-luna`.
- Optimizer: Gurobi only; no fallback solver was used.
- Approximate OpenAI API cost: USD 1.167019.
- Total recorded LLM tokens: 5,736,186.

Raw per-run Excel workbooks, API transcripts, and canonical hidden truth are deliberately not published in this repository.

## Main findings

The full agentic workflow was realized-operationally feasible and retention-floor compliant in all 15 of its runs. The deterministic-pricing and evaluator-removal configurations were also feasible and compliant in all 15 runs. The rule-parser configuration was feasible in 10 of 15 runs and retention compliant in 12 of 15 runs.

Across the 15 matched comparisons with deterministic pricing, the full workflow produced lower full-day PTO cost in five and tied in ten; it was never worse. Against evaluator removal, it was better in seven, tied in seven, and worse in one. The one loss was USD 0.415962 in a late-return repetition; the five-repetition mean difference for that case was USD -0.083192.

The clearest Trigger Agent result occurred in the charger-bank shutdown case. The full workflow interpreted the unstructured warning and remained feasible in all five repetitions. The frozen rule parser failed to represent the charger outage and was infeasible in all five repetitions. Across all trigger comparisons, this gives five feasibility wins; among the ten pairs where both methods were feasible and retention compliant, the full workflow had one economic win, eight ties, and one economic loss.

For the feasible pricing and evaluator comparisons, most realized physical flexibility outcomes were identical. The observed PTO-cost improvements therefore primarily represent a different division of the same grid-side value between the aggregator and PTO, constrained by the 50% retention floor. The charger case provides the stronger evidence of an operational benefit from interpreting unstructured advance information.

The evaluator audit covered 100 optimization decisions. In 27 of them the evaluator accepted a rerun that was worse in raw projected full-day PTO cost than an earlier usable attempt; under the workflow's full ordering, which places operational priority and retention compliance before cost, 1 of those acceptances was worse. In 49 decisions the selected schedule was not the cheapest usable candidate, with a maximum cost regret of USD 6.09 in a single decision; this is the explicit price of ranking retention compliance above cost. In all 100 decisions the selected schedule was the best usable candidate under the workflow's priority and retention ordering, and the saved-candidate mechanism never discarded a better usable schedule. These figures match `analysis_summary.json` in this folder.

## Files

- `matrix_runs.csv`: one row per episode, including feasibility, settlement, solver, LLM-token, API-cost, time, and memory measurements.
- `matrix_manifest.json`: frozen protocol, input hashes, run inventory, execution budget, and solver/LLM provenance.
- `analysis_summary.json`: complete machine-readable analysis and bootstrap summaries.
- `method_summary.csv`: case/configuration aggregates and confidence intervals.
- `ablation_contrasts.csv`: primary Trigger, Pricing, and Evaluator ablation contrasts.
- `secondary_outcome_contrasts.csv`: charging, V2G, grid-price alignment, and peak-import comparisons.
- `evaluator_decision_audit.csv`: decision-level audit of reruns and saved-candidate selection.
- `pricing_multiplier_summary.csv`: chosen/reference multiplier statistics.

Economic comparisons are reported only when both paired runs are realized-operationally feasible and meet the altruistic retention floor. Differences with absolute magnitude below USD 0.001 are treated as ties. Confidence intervals are nonparametric bootstrap intervals over the five repetitions within each case/configuration cell.

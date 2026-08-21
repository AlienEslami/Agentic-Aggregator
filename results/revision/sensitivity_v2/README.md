# Revision sensitivity study v2

This folder publishes the compact, paper-facing evidence from the prespecified
55-run one-factor sensitivity study:

- 25 Trigger repetitions: baseline, action-first and evidence-first prompt
  variants, plus low (0.5), base (0.7) and high (0.9) confidence thresholds;
- 30 Pricing-only closed-loop episodes: narrow, base and wide guidance in both
  selfish and altruistic modes, with five repetitions per cell.

The study ran from frozen commit
`438b271d3dff4b1aa4130339a5ad561f5701f3e2` using `gpt-5.6-luna`, Gurobi only,
a 300-second stage limit and a 0.02 MIP gap. Canonical hidden event truth was
not sent to the model.

## Headline results

- Prompt wording was stable: effective action accuracy was 93.75% for baseline,
  93.13% for action-first and 93.75% for evidence-first. All three variants had
  zero false optimizations.
- The low threshold reached 92.92% effective action accuracy, the base threshold
  93.75%, and the high threshold 86.25%. The high threshold missed 13.54% of
  necessary optimizations and was therefore too conservative.
- All 30 Pricing episodes were complete, Gurobi-optimal and operationally
  feasible, with zero reserve shortfall or violation timesteps.
- In selfish mode, mean aggregator revenue was 29.7376 under narrow guidance,
  28.7325 under base guidance and 27.6557 under wide guidance.
- In altruistic mode, mean full-day PTO cost was 122.0255 under narrow guidance,
  121.3794 under base guidance and 121.5948 under wide guidance. All 15 episodes
  satisfied the 50% baseline-revenue-retention floor.

## Published files

- `sensitivity_runs.csv` and `sensitivity_runs.json`: one indexed record per
  completed repetition or episode;
- `sensitivity_manifest.json`: frozen protocol, input hashes and execution
  settings;
- `analysis/sensitivity_summary.csv` and `.json`: arm-level means, standard
  deviations and repetition-level bootstrap intervals;
- `quality_assurance.json`: API-record, solver, feasibility and artifact checks;
- `execution_incidents.json`: two discarded infrastructure attempts and their
  conservative cost treatment.

Binary Excel workbooks and raw call logs are intentionally not tracked in Git.
The run index retains their relative names and SHA-256 hashes, while the compact
JSON/CSV evidence contains every statistic used in the sensitivity analysis.

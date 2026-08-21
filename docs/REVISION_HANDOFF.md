# Revision handoff — TRC-26-02380

State of the revision work as of 2026-08-20, and what is left. Written for the
second author picking this up on another machine.

## Before anything else

```powershell
git config core.autocrlf false
```

Run this once in your clone, then re-checkout. Every frozen hash in this
repository — prompt hashes in the ablation protocols, dataset hashes in the
input manifests, and `MANIFEST.sha256` — is taken over LF bytes, and
`.gitattributes` now pins text artifacts to LF. Without this setting the
checkout writes CRLF, the hashes stop matching, and the runners refuse to
start.

Verify the package the way a reviewer would before trusting any run:

```powershell
python -m pytest -q                              # expect 165 passed
python scripts/validate_revision_package.py      # expect no failed checks
```

## Runtime inputs are not in the repository

Every `.xlsx` is excluded from version control, so the five workflow inputs
must be supplied privately: `inputs/State.xlsx`, `inputs/Forecasted.xlsx`,
`inputs/SpotPrices.xlsx`, `inputs/realtime_states/` (48 workbooks),
`inputs/intraday_prices/` (48 workbooks), and
`inputs/rt_disturbance_scenarios_multiple.xlsx`.

They are the frozen reference. Confirm before running anything:
`State.xlsx` must report `aggregator_revenue = 18.4285133072` for
`mode=altruistic`. If it differs, the inputs are not the ones the published
results came from and nothing run against them is comparable.

Gurobi: the licence that ships with the pip package is size limited and cannot
solve these models. Install a full licence and point `GRB_LICENSE_FILE` at it.

## Done

**Reproducibility.** The package verifies from a clean clone: 165 tests,
`MANIFEST.sha256` 151/151 and package validator 45/45. The dataset builders now
write LF explicitly; regenerating them changed no content, only the recorded
hash lines, which confirms the datasets themselves never drifted.

**Day-ahead baseline corrected.** The dumb-charging model never prices energy,
so its optimal face held schedules differing by about ten percent in cost, and
each solver returned a different one. The tie is now broken by charging as
early as the constraints allow, which is what uncontrolled charging means.
Gurobi and HiGHS both return 218.9827. The reported saving of optimized
charging over dumb charging rises from 40.2 to 40.96 percent on the summary
basis.

**Matched non-agentic baseline.** `full_deterministic` — rule trigger,
deterministic pricing, hard-check evaluator — is declared in ablation protocol
v7 and has been executed: six deterministic episodes, no API cost. It is
infeasible in every run of the charger-bank case, because the frozen parser
cannot represent a charger outage announced in chat text; it then replans
against chargers that are not there, re-optimizes at 37 of 48 timesteps and
ends 533 kWh short on reserve. Where the disturbance is fully numerical it
reproduces the full workflow exactly. Written up in the manuscript as the
ablation subsection.

**Day-ahead V2G benchmark.** Smart charging with V2G and without agents, under
a fixed regulated band and under spot passthrough, frozen by
`scripts/build_day_ahead_ladder.py` with a manifest.

**Scaling, optimizer side.** The deterministic arm of the scaling benchmark is
complete under the v8 protocol, in `results/revision/scaling_v2`: 24 episodes
over 8, 16 and 32 buses plus the second depot, at the 300-second stage limit,
no API cost. The model is quadratic in fleet size and solver time follows it at
an exponent near 2.15, while the number of supervisory decisions is invariant
at two per episode. The longest single solve was 13.4 s, so no run was
truncated by the limit. Written up as the scalability subsection. An earlier
`scaling_v1` was executed at the superseded 60-second limit; its numbers are
close but its provenance is a different protocol generation, so it should not
be reported.

**One-factor sensitivity.** The complete v2 study is published in
`results/revision/sensitivity_v2`: 25 Trigger repetitions (2,400 structured
decisions) and 30 Pricing episodes. The three prompt variants were stable at
93.13--93.75 percent effective action accuracy with no false optimizations. A
0.9 confidence threshold was too conservative, reducing accuracy to 86.25
percent through missed optimizations. All Pricing episodes were Gurobi-optimal
and operationally feasible, and all altruistic episodes satisfied the 50
percent baseline-revenue-retention floor. The indexed API estimate is USD
2.5393; discarded infrastructure attempts are documented separately with a
conservative total upper bound of USD 2.7893.

**Broader disturbances.** The v2 advance-warning dataset adds clustered delays
and a sustained route-energy shift, and a disturbance workbook adds a
three-step price escalation. The primary Agent/rule/numerical/oracle comparison
is now executed at the 300-second limit in `results/revision/extended_disturbances_v3`
(32 episodes) and `results/revision/price_escalation_v3` (16 episodes). The
sustained energy-shift case gives the clearest benefit from advance text: the
Agent is operationally feasible in 5/5 selfish runs versus 0/1 for numerical
and rule triggers, and in 3/5 altruistic runs versus 0/1 for both. The clustered
delay case is a boundary stress test: every method incurs realized reserve
shortfall and it must not be presented as an economic win. Under price
escalation the Agent is feasible in 5/5 selfish runs versus 0/1 numerical; in
altruistic mode it is feasible in 4/5 versus 1/1 for each deterministic method.

**Manuscript.** The reproducibility appendix is filled from the published run
manifests, and two subsections were added. All revision text is inside the
`\rev` / `\revon` markup, so it prints in red until `\revisionmarkupfalse` is
set. The response document tracks each comment's status.

## Left to run

Check what a study still needs before spending budget:

```powershell
python scripts/report_study_status.py scaling --output-root results/revision/scaling_v1
```

The runners index complete workbooks and execute only what is missing, so an
arm can be added to an existing output root without repeating work.

**Scaling, agentic arm** — completed in `results/revision/scaling_v2`. The
commands below reproduce the half of R2.6 that measures how Agent latency and
token usage grow with fleet size:

```powershell
python scripts/run_scaling_study.py --output-root results/revision/scaling_v1 --repetitions 3 --allow-external-llm --require-clean-git --max-approximate-api-cost-usd 2.00
python scripts/analyze_scaling_study.py --runs results/revision/scaling_v1/scaling_runs.csv --output-dir results/revision/scaling_v1/analysis
```

**Controlled evaluator ablation** — completed: 48 episodes in
`results/revision/evaluator_v3`. The evaluator changes the outcome in the
explicit route-priority case by restoring operator-request compliance; it does
not improve economics in cases where every candidate already satisfies the
same operational requirements. Reproduction command:

```powershell
python scripts/run_evaluator_ablation.py --output-root results/revision/evaluator_v3 --agent-repetitions 5 --allow-external-llm --require-clean-git --solver-time-limit 300
```

**Extended disturbance cases** — completed; see the broader-disturbance block
in `REVISION_EXPERIMENTS.md` and the feasibility-first analyses beside both
result roots. The remaining strict-protocol question is whether to repeat the
entire 120-role v8 matrix at 300 seconds. The targeted 10-episode charger rerun
in `results/revision/v8_targeted_rule_parser_charger` removed every historical
timeout, so such a full repetition is provenance cleanup rather than evidence
that the old failed cell needs a new method.

## Left to decide or write

- **SOC recomputation.** The manuscript's day-ahead numbers are SOC-derived, not
  the workbook summary values, and the script that produces them has absolute
  paths from one machine hardcoded. Until it is ported into this repository,
  the new day-ahead rows cannot enter Table 6: the two bases differ by 2 to 4
  units, the same order as the effects being reported. Reproduce these four
  values to validate any port: dumb charging 218.101387, smart charging without
  V2G 130.470605, selfish FS+CoT 140.586078, altruistic FS+CoT 118.908348.
- **n8n version.** The only bracketed placeholder left in the reproducibility
  appendix. The workflow export carries no version metadata, so it has to be
  read off the instance that produced it.
- **Altruistic README.** It states that the evaluator audit found one decision
  where an economically worse rerun was accepted. The `analysis_summary.json`
  beside it records 27 in raw cost terms, 1 under the full ordering, and 49 of
  100 decisions where the selected schedule was not the cheapest, with a
  maximum regret of 6.09 USD. All three should be reported, with the regret
  framed as the explicit price of the retention floor.
- **Team decisions D1 and D2** — how many of the suggested fuel-cell references
  to cite, and whether a stochastic-programming or RL benchmark is in scope.

## Verification notes

**Exact ties between methods are price ties, not identical behaviour.** In the
stochastic-benchmark comparison, three of six cells report a realized value
identical to another configuration's to six decimals: the charger/profit cell
matches the agentic median, and both combined-disturbance cells match the
non-agentic loop. These are not duplicated workbooks. The files differ, and for
the combined altruistic cell the realized plans differ at two timesteps: the
stochastic program exports 0 and 200 kWh at t=40 and t=41 where the non-agentic
loop exports 100 and 100. Total energy and total cost are identical because
those two intervals carry the same price.

This matters for how results are worded. A cell reported as a tie is a tie in
realized cost, not evidence that two methods behaved the same way, and the
export profile behind it can differ. It is also the third place where
equal-price intervals have produced a degenerate optimal face in this codebase,
after the dumb-charging baseline and the zero-spread day-ahead variant.
Anywhere a claim rests on an exact tie, check whether the tie is a price tie
before describing it as agreement between methods.

## Constraints that must hold

- Do not edit anything under `agentic_workflow/prompts/`. Those SHA-256 values
  are frozen in protocols v6 and v7 and checked before every run; changing them
  breaks validation and makes new runs non-comparable with the published
  matrices.
- Do not re-run or modify `results/revision/selfish_5rep/` or
  `results/revision/altruistic_50pct_retention_5rep/`.
- Final evidence excludes any episode that used a fallback solver.
- Protocol v6 stays untouched so the published matrices keep validating against
  the protocol they were run under; v7 adds the non-agentic arm.

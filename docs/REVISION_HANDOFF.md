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
python -m pytest -q                              # expect 182 passed
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

**Reproducibility.** The package verifies from a clean clone: 182 tests,
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

The day-ahead reporting basis is also reconciled. The submitted S1--S4 values
are exactly reproduced by applying the previous interval tariff to the current
interval energy. That off-by-one reporting error is corrected to the optimizer-
native same-interval settlement in
`results/revision/day_ahead_reconciliation_v1/`; the optimizer is unchanged.

**Matched non-agentic baseline.** `full_deterministic` — rule trigger,
deterministic pricing, hard-check evaluator — has been executed in six
confirmatory episodes with no API cost. The final comparison is in
`results/revision/nonagentic_baseline_v8_confirmatory`: the Agent mean wins four
cells, ties the altruistic route cell, and loses the selfish charger cell. The
confirmatory baseline is operationally feasible in all six indexed cells.

**Day-ahead V2G benchmark.** Smart charging with V2G and without agents, under
a fixed regulated band and under spot passthrough, frozen by
`scripts/build_day_ahead_ladder.py` with a manifest.

**Scaling.** Both arms of the scaling benchmark are complete in
`results/revision/scaling_v2`: 48 episodes over 8, 16 and 32 buses plus the
second depot, at the 300-second stage limit. The corrected manifest indexes all
24 Agent and 24 text-rule runs. Every run is feasible, and the maximum recorded
solve is 8.09 s. The Agent arm uses 3,651,756 tokens and an indexed approximate
API cost of USD 0.7656. An earlier
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

**Broader disturbances.** The reviewer-facing set now includes recoverable
clustered delays, a sustained route-energy shift, a three-step price escalation,
and a chained three-day charger-cooling derating. The primary
Agent/rule/numerical/oracle comparison is executed at the 300-second limit in
`results/revision/recoverable_cluster_v1` (16 episodes),
`results/revision/extended_disturbances_v3` (with the physically unattainable
cluster calibration excluded), `results/revision/price_escalation_v3` (16
episodes), and `results/revision/multiday_charger_derating_v1` (18 three-day
episodes/54 daily runs: 16 derating comparisons plus two scheduled
no-derating daily-replanning controls). The
sustained energy-shift case gives the clearest benefit from advance text: the
Agent is operationally feasible in 5/5 selfish runs versus 0/1 for numerical
and rule triggers, and in 3/5 altruistic runs versus 0/1 for both. Every method
is feasible in the recoverable clustered-delay case. Under price
escalation the Agent is feasible in 5/5 selfish runs versus 0/1 numerical; in
altruistic mode it is feasible in 4/5 versus 1/1 for each deterministic method.
All 18 three-day episodes are feasible, have zero reserve shortfall and exact
internal SOC carryover. The first recorded settlement differs from the carried
state by at most 0.0000492 kWh because observations are rounded to four decimal
places. All ten Trigger-Agent repetitions act on the confirmed day-1 warning
before the cap begins, and the trigger methods produce equal derating
economics; this is robustness/carryover evidence rather than an Agent-
superiority result. The matched nominal control shows day-by-day SOC and
trading effects from the derating. Its selfish day-3 economic result is a
feasible 300-second incumbent at about 5.5% MIP gap and must be labeled
approximate. The isolated 900-second sensitivity in
`results/revision/multiday_nominal_selfish_900s_v1` reproduces the identical
schedule and economics while improving the two gaps only to 5.50% and 5.42%.
It supports incumbent stability, not 2%-gap optimality.

Attempt-level solver status, gap, workbook hash, and run signature are retained
in `multiday_solver_audit.csv`, with a one-row compact summary beside it.

**Manuscript assets.** Manuscript-ready results, limitations, an insertion map,
and a complete point-by-point response are in
`docs/REVISION_MANUSCRIPT_INSERTS.md` and
`docs/REVIEWER_RESPONSE_DRAFT.md`. CSV tables, PDF/PNG figures, and a hash
manifest are in `paper_outputs/revision/`. They still need to be merged into
the LaTeX source when it is supplied.

## Left to run

Check what a study still needs before spending budget:

```powershell
python scripts/report_study_status.py scaling --output-root results/revision/scaling_v1
```

The runners index complete workbooks and execute only what is missing, so an
arm can be added to an existing output root without repeating work.

**No planned evidentiary experiment remains incomplete.** Re-run a study only
if a protocol or manuscript question changes; the current Agent, deterministic,
stochastic, sensitivity, evaluator, broader-disturbance, and scaling evidence
is complete.

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

- **n8n version.** The only bracketed placeholder left in the reproducibility
  appendix. The workflow export carries no version metadata, so it has to be
  read off the instance that produced it.
- **Altruistic README.** It states that the evaluator audit found one decision
  where an economically worse rerun was accepted. The `analysis_summary.json`
  beside it records 27 in raw cost terms, 1 under the full ordering, and 49 of
  100 decisions where the selected schedule was not the cheapest, with a
  maximum regret of 6.09 USD. All three should be reported, with the regret
  framed as the explicit price of the retention floor.
- **LaTeX integration.** Insert the generated tables, figures, results,
  limitations, and reviewer responses after the manuscript source is supplied.
- **Literature update.** Add and verify the final citations used to defend the
  agentic positioning and tariff-policy discussion.

## Where the response text lives

Three artifacts now carry response material and they must not drift apart:

- `docs/REVIEWER_RESPONSE_DRAFT.md` is the point-by-point prose destined for
  the journal template.
- `docs/REVISION_MANUSCRIPT_INSERTS.md` is the manuscript-ready text and its
  insertion map.
- `TRC-26-02380_reviewer_comments_working.docx`, in the paper folder, is the
  status tracker: which comment is answered, where the change lives, and what
  is left. Its chips describe the manuscript, not the repository.

Treat the first two as the source and the third as the record. As of 21/08 the
tracker reports eight comments done, ten partially done and none untouched.

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

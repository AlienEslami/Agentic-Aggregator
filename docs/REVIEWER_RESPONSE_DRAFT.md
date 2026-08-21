# Point-by-point reviewer response draft

The response text below is ready for editing into the journal template. The
section and table identifiers are descriptive placeholders because the LaTeX
manuscript has not yet been supplied. Evidence locations are final repository
paths.

## Reviewer 1

### Comment 1: Methodological positioning and contribution

**Response.** We rewrote the contribution around heterogeneous information,
not around replacing optimization with an LLM. The mathematical optimizer is
the sole schedule generator and feasibility authority. The Trigger, Pricing,
and Evaluator agents supervise when to re-optimize, how to express mode-
dependent tariffs, and whether a text-only operator priority has been honored.
We added numerical, text-rule, oracle, stochastic, V2G-only, and full-no-AI
controls so that each contribution is separately observable.

**Revision location.** Revised Introduction and Methods (“Central claim” and
“Comparison ladder”); Table `reviewer_evidence_map.csv`.

### Comment 2: Deployment logic outside the scripted example

**Response.** We specified the 30-minute clock, state available to each method,
the evidence gate, causal settlement, and the rule that a decision may affect
only the current and future intervals. Hidden physical truth is used only for
scoring and the oracle control. The implementation records every decision,
optimizer attempt, accepted candidate, token count, latency, and solver status.

**Revision location.** Revised Methods (“Fair information comparison”) and
frozen protocol/run manifests.

### Comment 3: Why an agent beyond event-triggered control?

**Response.** We added matched numerical-trigger, text-rule, Agent, and oracle
comparisons. In the sustained energy-shift case, advance text permitted the
Agent to obtain feasibility in 5/5 selfish and 3/5 altruistic runs, whereas the
numerical and text-rule controls were infeasible in their single runs. We also
report counterexamples: the rule matches the oracle in the price-escalation
case, and every method remains feasible in the recoverable clustered-delay case.
We therefore narrow the claim to disturbances where unstructured advance
evidence is material.

**Revision location.** Revised Results (“Value and boundary of advance text”);
Table `trigger_feasibility_comparison.csv`; Figure `trigger_feasibility.pdf`.

### Comment 4: Ablate the optimizer and individual agents

**Response.** We retain the same Gurobi optimizer in all meaningful scheduling
comparisons because removing it would remove the feasibility mechanism rather
than isolate an agent. We instead add controlled substitutions: Trigger Agent
versus numerical/text/oracle triggers; Pricing Agent versus deterministic
pricing; Evaluator Agent versus removal, text rule, and structured oracle; a
non-agentic V2G day-ahead ladder; and a full no-AI supervisory stack. The full
no-AI baseline wins the selfish charger cell, ties the altruistic route cell,
and loses four cells, which prevents a circular claim of universal agentic
benefit.

**Revision location.** Revised Methods (“Comparison ladder and ablations”) and
Results; Tables `day_ahead_strategy_comparison.csv`, `evaluator_ablation.csv`,
and `full_no_ai_comparison.csv`.

### Comment 5: Prompt, confidence, and tariff sensitivity

**Response.** We added a prespecified 55-run one-factor study: 25 Trigger
repetitions and 30 Pricing episodes. Three prompt wordings produced 93.13–
93.75% mean effective action accuracy and no false optimizations. A 0.90
confidence threshold reduced accuracy to 86.25%. All Pricing sensitivity runs
remained operationally feasible. Prompts and optional tariff references are
published with hashes.

**Revision location.** Revised Results (“Prompt sensitivity and
repeatability”); Table `prompt_sensitivity.csv`; supplement prompts/manifests.

### Comment 6: Broaden disturbance cases

**Response.** We added sustained route-energy shifts, recoverable clustered late
returns, price escalation, and a chained three-day charger-derating experiment
to the route, charger, and combined cases. The three-day case begins on the
afternoon of day 1, persists through day 2, and clears on the afternoon of day
3; realized terminal battery energy is carried exactly between daily horizons.
We add a matched scheduled daily-replanning control without the derating in
both modes. All 18 three-day episodes and 54 daily runs are physically feasible
with zero reserve shortfall. All ten Trigger-Agent repetitions act on the
confirmed maintenance text before physical onset. The resulting schedule ties
the deterministic trigger methods, while day-specific comparison with the
nominal control shows that the physical derating changes SOC and energy trading
across subsequent days. The primary case set was screened for physical
attainability before Agent outcomes were examined. We report the selfish
nominal day-3 economic comparison as approximate because its feasible Gurobi
incumbents reached the time limit at approximately 5.5% gap. A targeted
900-second-per-attempt sensitivity retained the identical schedule and
economics; its two gaps improved only to 5.50% and 5.42%, so we report stability
without claiming the configured 2% gap was reached.

**Revision location.** Revised Results and Limitations; Table
`trigger_feasibility_comparison.csv`, `multiday_charger_derating.csv`,
`multiday_charger_derating_effects.csv`, and
`multiday_charger_derating_daily_effects.csv`; attempt-level provenance in
`multiday_solver_audit.csv`.

### Comment 7: Policy and economic interpretation

**Response.** We now distinguish selfish aggregator revenue from altruistic
full-day PTO cost. The altruistic mode uses a transparent 50% baseline-revenue-
retention floor; it is labeled a design choice rather than a market estimate.
We add fixed-margin and zero-margin V2G benchmarks to show the cost of retaining
aggregator revenue. We also report the exact cost of honoring an operator
priority in the Evaluator ablation.

**Revision location.** Revised Methods and Results; Tables
`day_ahead_strategy_comparison.csv` and `evaluator_ablation.csv`.

### Comment 8: Reproducibility

**Response.** We migrated the workflow to Python and publish the prompts,
protocols, deterministic dataset builders, result indexes, hashes, solver
provenance, token accounting, API-cost estimates, CPU time, wall time, and
memory. A generator creates all revision tables and figures from the frozen
result files and writes a source/artifact hash manifest.

**Revision location.** Reproducibility appendix and repository;
`scripts/build_revision_manuscript_assets.py` and
`paper_outputs/revision/manifest.json`.

### Comment 9: Scalability

**Response.** We added 48 runs across 8, 16, and 32 buses at Depot A and an
8-bus second-depot profile, with both modes, Agent/text-rule configurations,
and three repetitions. All were feasible with Gurobi and no fallback. At 32
buses, mean solver time remained 3.25–3.43 s and the maximum was 8.09 s under a
300 s limit; API latency dominated end-to-end Agent time.

**Revision location.** Revised Results (“Scalability and computational
reporting”); Table `scalability.csv`; Figure `scalability_latency.pdf`.

### Minor comments

**Response.** We standardize the terms “operationally feasible,” “full-day
projected cost,” “same-interval settlement,” “day-ahead,” and “real-time.” We
will redraw the workflow around state, permitted information, decisions,
optimizer calls, evaluator acceptance, and causal settlement when the LaTeX
figure source is supplied. The revised day-ahead table separates optimization,
V2G, pricing, and agentic effects.

## Reviewer 2

### Comment 1: Why is AI essential rather than an ordinary loop?

**Response.** We no longer claim that AI is essential for every disturbance.
The Agent is useful when operationally material advance evidence exists only in
heterogeneous text and is not captured by a numerical trigger. Where text is
fully covered by rules or where no advance interpretation is needed, the rule
and no-AI controls can match or outperform it. The revised claims and
limitations state this boundary explicitly.

**Revision location.** Revised Introduction, advance-text Results, and
Limitations.

### Comment 2: Add stochastic programming or RL

**Response.** We added a two-stage stochastic recourse benchmark in all six
primary case-mode cells. The Agent mean wins five cells and loses the
altruistic charger cell by EUR 0.91/day (0.73%). We did not add reinforcement
learning because training and validation would introduce a substantially
different data requirement and was not needed once a matched stochastic
benchmark was available.

**Revision location.** Revised Methods and Results (“Stochastic and full-no-AI
comparisons”); Table `stochastic_benchmark.csv`.

### Comment 3: Could these functions be implemented with if/then rules?

**Response.** Yes, some can. We added text-rule Trigger and Evaluator controls.
The rule matches the oracle in the route-warning plus price-escalation case,
while it fails to obtain feasibility in the sustained energy-shift case. The
revised paper therefore identifies both the coverage and brittleness of the
prespecified rule set rather than dismissing rule-based control.

**Revision location.** Revised Results; Tables
`trigger_feasibility_comparison.csv` and `evaluator_ablation.csv`.

### Comment 4: Compare with smart V2G without AI and fair V2G

**Response.** We added non-agentic V2G with fixed 1.05/0.80 tariff multipliers
and a zero-margin spot-passthrough counterfactual. The passthrough schedule has
the lowest day-ahead PTO cost but no aggregator revenue, while fixed-margin V2G
earns EUR 16.28/day. These controls separate the value of V2G and mathematical
optimization from the value of agentic pricing.

**Revision location.** Revised day-ahead Results; Table
`day_ahead_strategy_comparison.csv`.

### Comment 5: Prompt randomness and a no-AI comparison of equal complexity

**Response.** We provide five repetitions for the Agent arms, the 55-run prompt
and guidance sensitivity study, and a full no-AI supervisory stack using the
same physical inputs and Gurobi optimizer. The no-AI stack wins one economic
cell and ties one, so the comparison does not assume Agent superiority. We also
publish the exact information mapping and prompt hashes.

**Revision location.** Revised Methods/Results; Tables
`prompt_sensitivity.csv` and `full_no_ai_comparison.csv`.

### Comment 6: Scalability

**Response.** Addressed by the 48-run 8/16/32-bus and second-depot experiment
described in the response to Reviewer 1, Comment 9.

## Accounting correction disclosed to both reviewers

During revision we audited the day-ahead table and found that the submitted
S1–S4 costs use the previous interval tariff for the current interval energy.
The submitted numbers are exactly reproducible under that shift. We corrected
the table to the same-interval convention used by the optimizer and real-time
settlement; no optimizer formulation was changed. The complete reconciliation
is in `results/revision/day_ahead_reconciliation_v1/`.

# Manuscript-ready revision text and insertion map

This file is written as source material for the revised manuscript. Section
and table numbers are placeholders until the LaTeX source is supplied. Every
numerical statement is linked to a generated table or frozen experiment file.

## Central claim

The agentic layer is not the source of physical feasibility: the mathematical
optimizer remains the only component that constructs and verifies a charging
schedule. The agentic contribution is supervisory. It can interpret
heterogeneous operational notices, decide whether and when to re-optimize,
propose context-dependent tariff multipliers, and check operator priorities
that are not already represented as numerical state variables. The experiments
therefore test two separate questions:

1. Does text provide useful advance information that numerical event triggers
   do not yet contain?
2. Conditional on the same physical state and optimizer, does the agentic
   workflow improve the mode-aligned economic outcome over rule, no-AI, and
   stochastic alternatives?

The results support the first claim in selected advance-warning cases. They do
not support a universal claim that an LLM dominates deterministic logic or
stochastic optimization.

## Methods insert: fair information comparison

All trigger methods face the same hidden physical realization and the same
Gurobi charging optimizer. Their information sets differ by experimental
design. The numerical trigger observes only structured telemetry available at
the current timestep. The text-rule and Trigger Agent receive the same
synthetic driver or maintenance notice plus the same operational numerical
context; neither receives the canonical hidden event record. The oracle is an
upper-information control that receives the canonical event timing and
parameters. This design distinguishes an information advantage from an
optimization advantage: a method may act earlier only if its permitted input
contains advance evidence.

Each decision affects only the current and future intervals. Timestep 1 is
00:00–00:30, and interval `t` energy is settled at interval `t` price and
tariff multipliers. A rerun cannot change previously realized energy or cost.
All economic comparisons use projected or realized full-day values rather than
the remaining-horizon subtotal.

## Methods insert: comparison ladder and ablations

The day-ahead ladder separates optimization, bidirectional charging, pricing,
and agentic coordination. S1 is uncontrolled earliest charging without V2G;
S2 is optimized unidirectional charging; S2.5a is non-agentic V2G under fixed
1.05 buy and 0.80 sell multipliers; S2.5b is non-agentic V2G with spot
passthrough; S3 and S4 retain the Pricing and Evaluator agents in selfish and
altruistic modes. The real-time ablations additionally substitute a numerical
event trigger, text rules, deterministic pricing, a hard-check evaluator, and
the entire no-AI supervisory stack. A two-stage stochastic recourse benchmark
uses no API calls and is evaluated in the same six primary case-mode cells.

The altruistic objective minimizes projected full-day PTO cost subject to a
50% baseline-revenue-retention floor. This prevents the trivial solution of
setting every customer tariff to its lowest allowed value while still allowing
revenue to fall when the disturbance creates a grid-support opportunity. The
floor is a scenario definition, not an empirically estimated market rule, and
is reported as such.

## Results insert: corrected day-ahead accounting

We discovered a reporting-only off-by-one error in the submitted day-ahead
table. The submitted S1–S4 costs are reproduced exactly when interval `t`
energy is multiplied by the previous interval's tariff. The optimizer
objective, workbook summaries, and real-time settlement instead use the tariff
from the same interval. No optimizer constraint or objective was changed.

On the corrected basis, S1 costs EUR 218.98/day and S2 costs EUR 129.29/day, a
40.96% reduction from scheduling alone. Non-agentic V2G costs EUR 131.48/day
under fixed multipliers and yields EUR 16.28/day of aggregator revenue; under
spot passthrough it costs EUR 113.66/day and yields zero aggregator margin. S3
costs EUR 138.41/day and yields EUR 20.91/day, while S4 costs EUR 116.08/day
and yields EUR 2.41/day. The passthrough result is an important counterexample:
V2G and optimization alone can obtain the lowest PTO cost when aggregator
revenue is deliberately ignored.

Evidence: `paper_outputs/revision/tables/day_ahead_strategy_comparison.csv` and
`results/revision/day_ahead_reconciliation_v1/`.

## Results insert: value and boundary of advance text

In the sustained route-energy-shift case, the Agent was operationally feasible
in 5/5 selfish repetitions and 3/5 altruistic repetitions. The numerical and
text-rule triggers were infeasible in their single runs in both modes; the
oracle was feasible in both single runs. In the route-warning plus price-
escalation case, the Agent was feasible in 5/5 selfish and 4/5 altruistic
repetitions. The numerical trigger was infeasible in the selfish run, while the
text rule and oracle were feasible in both modes. Because each deterministic
cell has only one run, these are exact benchmark outcomes, not estimates of a
deterministic method's sampling distribution.

In the recoverable clustered-late-return case, all four methods were
operationally feasible in both modes with zero realized reserve shortfall. This
case broadens the disturbance set without treating a physically unattainable
trajectory as a trigger-method comparison.

In the chained three-day charger-derating experiment, Chargers 6--8 were reduced
from 200 to 100 kW from the afternoon of day 1, throughout day 2, and until the
afternoon of day 3. Terminal realized battery energy was copied exactly into the
next daily planning horizon. A scheduled daily-replanning control without the
derating was added in both modes. All 18 three-day episodes (16 derating and two
nominal controls) and all 54 daily runs were operationally feasible, with zero
reserve shortfall and zero internal carryover error. Four-decimal observation
rounding produced a maximum first-settlement discrepancy below 0.000050 kWh.
After correcting the interval-energy reference and the notice-phase gate, all
ten Trigger-Agent repetitions skipped the unconfirmed warning and acted on the
confirmed text at timestep 24, seven intervals before physical onset. All four
trigger methods obtained the same derating economics. Relative to scheduled
no-derating replanning, the derating changed terminal SOC increasingly across
days and reduced three-day PTO cost by EUR 21.66 in selfish mode and EUR 4.37
in altruistic mode, while also reducing aggregator revenue. These are physical
disturbance effects, not Agent gains. The selfish nominal economic comparison
is approximate because its two day-3 optimizer attempts reached the 300-second
limit with feasible incumbents at about 5.5% gap; 118 of 120 optimizer attempts
met the configured 2% target. A targeted sensitivity run increased the limit
to 900 seconds per attempt. The gaps improved only from 5.52% and 5.48% to
5.50% and 5.42%, while the retained schedule and all economic outcomes were
identical to numerical precision. This supports incumbent stability, not a
claim of 2%-gap optimality.

Evidence: `paper_outputs/revision/tables/trigger_feasibility_comparison.csv`,
`paper_outputs/revision/tables/multiday_charger_derating.csv`,
`paper_outputs/revision/tables/multiday_charger_derating_effects.csv`,
`paper_outputs/revision/tables/multiday_charger_derating_daily_effects.csv`,
`paper_outputs/revision/tables/multiday_solver_audit_summary.csv`, and
Figure `paper_outputs/revision/figures/trigger_feasibility.pdf`.

## Results insert: stochastic and full-no-AI comparisons

Against the two-stage stochastic benchmark, the Agent mean won five of six
case-mode cells. The largest improvement was 91.88% higher aggregator revenue
in the selfish route-warning case; the other Agent improvements were 0.25%
lower altruistic cost, 5.17% higher selfish charger-case revenue, 12.61%
higher selfish combined-case revenue, and 0.70% lower altruistic combined-case
cost. The one loss was the altruistic charger case: the Agent mean cost was
EUR 0.91/day (0.73%) higher. These comparisons show selected economic value,
not universal dominance.

Against the full no-AI supervisory stack, the Agent mean won four of six cells,
tied the altruistic route-warning cell, and lost the selfish charger case. The
loss was material in percentage terms (31.67% lower mean aggregator revenue),
although one of the five Agent repetitions exceeded the deterministic
baseline. The combined-case improvements were 12.61% higher selfish revenue
and 0.70% lower altruistic cost. This result identifies where deterministic
logic is already sufficient and where the agentic workflow adds value.

Evidence: `paper_outputs/revision/tables/stochastic_benchmark.csv` and
`paper_outputs/revision/tables/full_no_ai_comparison.csv`.

## Results insert: evaluator ablation

For the route-specific reserve instruction, the Agent, text-rule evaluator,
and structured oracle all detected the priority and produced compliant plans;
the evaluator-removal control did not. In altruistic mode, compliance increased
projected full-day PTO cost from EUR 123.15 to EUR 124.04/day, an explicit EUR
0.88/day cost of honoring the operator's reserve request. In selfish mode the
compliant and removal schedules had the same reported economic outcome. In the
combined case, all configurations converged to the same compliant schedule;
the Agent and oracle added interpretation evidence but no economic improvement.
The evaluator is therefore useful when a valid operator priority is not already
satisfied by the economically optimal schedule, but it is not expected to
improve every case.

Evidence: `paper_outputs/revision/tables/evaluator_ablation.csv`.

## Results insert: prompt sensitivity and repeatability

The prespecified one-factor sensitivity study comprises 55 runs: 25 Trigger
repetitions and 30 Pricing episodes. Across the three Trigger prompt wordings,
mean effective action accuracy ranged from 93.13% to 93.75%, with zero false
optimizations. Raising the confidence threshold to 0.90 reduced mean accuracy
to 86.25%, showing that an overly conservative threshold creates missed
optimizations. All 30 Pricing episodes were operationally feasible. Prompt
wording and tariff-shape guidance therefore changed outputs but did not produce
a feasibility failure in this study.

Evidence: `paper_outputs/revision/tables/prompt_sensitivity.csv`.

## Results insert: scalability and computational reporting

The scaling experiment contains 48 complete Gurobi runs: four instances
(Depot A with 8, 16, and 32 buses, plus Depot B with 8 buses), two modes, two
supervisory configurations, and three repetitions. Every run was operationally
feasible and no solver fallback was used. At Depot A, mean solver time grew
from approximately 0.17 s at 8 buses to 3.25–3.43 s at 32 buses; the maximum
recorded solve was 8.09 s, well below the 300 s limit. Mean full-workflow time
at 32 buses was 81.61 s for the Agent and 17.56 s for the text-rule baseline,
showing that API latency, not mathematical optimization, dominated the Agent
runtime. The Agent runs used 3,651,756 total tokens and an indexed approximate
API cost of USD 0.7656 across all scaling instances.

Evidence: `paper_outputs/revision/tables/scalability.csv` and Figure
`paper_outputs/revision/figures/scalability_latency.pdf`.

## Limitations insert

The operational notices are synthetic and controlled, not field-collected
driver or maintenance messages. This gives exact hidden ground truth and fair
comparison of deliberately specified information channels, but it does not
establish field robustness. Agent cells use
five repetitions in the primary comparisons, whereas most deterministic and
oracle cells use one run; deterministic outcomes should not be described with
inferential uncertainty. The disturbance set is broader than in the submitted
paper but remains small. The recoverable clustered-delay and three-day cases do
not show economic dominance by the Agent. The Agent does not
dominate every economic baseline: it loses the altruistic charger cell to the
stochastic benchmark and the selfish charger cell to full no-AI control. The
Evaluator helps only when an unsatisfied operator priority is both detectable
and physically attainable. Scaling is demonstrated to 32 buses and two depot
profiles, not a production-size multi-depot deployment. API latency, token use,
and approximate cost are measured, but provider-side energy use and FLOPs are
not exposed. The selfish nominal day-3 multi-day control retains a feasible
incumbent at approximately 5.4--5.5% MIP gap even after a 900-second-per-attempt
sensitivity run, so economic effects using that cell are approximate rather
than confirmatory; the identical schedule under the longer limit demonstrates
stability but not proof at the configured 2% gap. Finally, all schedules are
simulation results; field validation, cybersecurity testing, and human-factors
evaluation remain future work.

## Proposed insertion map

- Replace the submitted day-ahead table with
  `day_ahead_strategy_comparison.csv` and add the accounting-correction note.
- Add the fair-information-set paragraph to Methods immediately after the
  workflow description.
- Add the stochastic and full-no-AI baselines to the benchmark subsection.
- Add the Trigger, Pricing, and Evaluator ablations after the primary results.
- Add the trigger-feasibility and scalability figures to Results.
- Add the limitation paragraph before Conclusions.
- Place prompts, protocol JSON, run indexes, hashes, and the artifact manifest
  in the supplement/repository availability statement.

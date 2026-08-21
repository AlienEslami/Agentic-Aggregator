# Experimental gap-fill run — 2026-08-21

## Execution audit

- 154 closed-loop episodes completed across the scaling, evaluator, targeted
  timeout, extended-disturbance and price-escalation studies.
- 94 episodes used the OpenAI API; 60 were deterministic controls.
- Logged usage: 7,002,716 total tokens and USD 1.457156 approximate API cost.
- The user-approved ceiling was USD 20. The run stayed USD 18.542844 below it.
- The cost value is run-level telemetry, not an invoice. Organization billing
  and the OpenAI usage dashboard remain authoritative.
- All indexed optimizer calls used Gurobi with no fallback. Legitimate
  no-trigger episodes have zero optimizer calls and therefore no solver name.
- Full regression suite: 167 passed outside the sandbox under the installed
  academic Gurobi license.

## Completed studies

### Day-ahead non-Agent ladder

Four deterministic solves were generated in `day_ahead_ladder_v1`:

- dumb charging: PTO cost 218.9827;
- smart charging without V2G: PTO cost 129.2929;
- smart V2G with fixed margin: PTO cost 131.4818;
- smart V2G with spot passthrough: PTO cost 113.6633.

The workbook-summary basis still differs from the manuscript's SOC-derived
basis by approximately 2–4 currency units and must be reconciled before these
rows are inserted into the paper.

### Scaling

`scaling_v2` contains 48/48 episodes: deterministic and full-Agent arms over
8, 16 and 32 buses at Depot A, plus an independent 8-bus Depot B case, in both
modes and three repetitions. All are operationally feasible and use Gurobi
without fallback. Agent latency dominates wall time; optimizer time and model
size grow with fleet size. The longest recorded solve remains comfortably below
the frozen 300-second stage limit.

### Controlled evaluator ablation

`evaluator_v3` contains 48/48 episodes: 30 repeated raw-text Agent runs and 18
deterministic evaluator controls.

- Route-priority case: Agent, structured oracle and rule evaluator satisfy the
  operator priority; removing the evaluator misses it. In altruistic mode the
  full-day PTO cost of compliance is 0.882346; in selfish mode compliance has
  no economic penalty in this instance.
- Combined case: Agent and structured oracle interpret the priority exactly,
  but every configuration reaches the same compliant schedule and economics.
  The evaluator therefore adds interpretation evidence, not economic benefit.
- Charger case: it does not contain the route-style operator priority, so blank
  priority-compliance fields are not evaluator failures.

### Current-v8 timeout confirmation

`v8_targeted_rule_parser_charger` contains the 10 charger episodes whose old
protocol cell included 60-second solver-limit failures. Under the 300-second
limit all 10 finish with zero solver cutoff and zero reserve violation.

- Selfish: 5/5 physically feasible.
- Altruistic: 5/5 physically feasible; 4/5 meet the realized full-day 50%
  baseline-revenue-retention floor. One realized outcome falls USD 4.047322
  below the floor even though its candidate optimizations completed; this must
  be reported, not pooled as a compliant economic observation.
- The retained-better-candidate mechanism was exercised once.

### Extended disturbance patterns

`extended_disturbances_v3` contains 32/32 episodes comparing five Agent
repetitions with rule-text, numerical and oracle triggers for two new cases.

- Sustained route-energy shift: selfish Agent feasibility is 5/5 versus 0/1
  numerical and 0/1 rule-text; altruistic Agent feasibility is 3/5 versus 0/1
  for both. The Agent interprets the confirmed text at timestep 17, before the
  physical onset. Oracle is feasible in both modes. This is the clearest new
  evidence that advance unstructured information has operational value.
- Clustered late returns: every method incurs realized reserve shortfall. This
  is a useful boundary stress test but not an Agent win. It must be reported as
  showing that interpretation alone cannot overcome insufficient physical
  flexibility.
- The frozen rule parser also reacts to a redundant persistence message in the
  energy case, whereas Agent repetitions do not trigger on that message.

### Multi-step price escalation

`price_escalation_v3` contains 16/16 episodes with identical 25%, 50% and 75%
price-step disturbances across all methods.

- Selfish: Agent is feasible in 5/5, oracle and rule-text in 1/1, and numerical
  in 0/1. Feasible Agent outcomes tie oracle and rule-text economically.
- Altruistic: Agent is feasible in 4/5; oracle, rule-text and numerical are each
  feasible in 1/1. On the four comparable feasible pairs, Agent ties oracle and
  rule-text. Numerical has a lower PTO cost but acts only at the late sensor
  trigger and does not provide the same advance operator-priority response.

## Harness fixes made during execution

- The matrix runner now accepts cases declared by a selected frozen protocol
  instead of hard-coding the original three IDs.
- Selected cases must exist in both the public notice and hidden physical-event
  datasets.
- The extended cases have a separate frozen protocol, so their results cannot
  be silently pooled with the original v8 matrix.
- A no-trigger episode with explicitly zero optimizer calls is now valid
  evidence; episodes that call an optimizer still require Gurobi-only
  provenance.

## Remaining before manuscript freeze

1. Reconcile the day-ahead SOC-derived calculation with the workbook-summary
   calculation.
2. Decide whether to repeat the entire 120-role v8 matrix at 300 seconds. The
   targeted rerun closes the actual timeout failures; a full repeat would mainly
   provide single-protocol provenance.
3. Commit the harness/protocol changes and, if the paper requires the
   `require-clean-git` condition literally, rerun the final selected matrices
   from that clean commit. The present manifests truthfully record a dirty
   worktree.
4. Update manuscript tables, figures and reviewer-response prose. Do not pool
   case/mode cells, and report feasibility before economics.
5. Complete the independent validation checklist and obtain the n8n version
   from the original instance.

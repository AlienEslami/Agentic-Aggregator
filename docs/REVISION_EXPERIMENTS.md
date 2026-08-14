# Focused revision experiment protocol

This package implements the code-facing portion of the TRC-26-02380 revision
plan. The scientific comparison keeps one optimizer boundary and changes only
the information, pricing, or evaluation role named by the configuration.

## Freeze before running

1. Regenerate and inspect the primary dataset with
   `python scripts/build_uncertain_chat_dataset.py`.
2. Freeze `trigger_dataset_manifest_v3.json`, `trigger_scenarios_v3.json`,
   `trigger_notices_v3.json`, `trigger_notices_v3.csv`,
   `trigger_split_v3.json`, and `uncertainty_chat_mapping_v3.md`.
3. Freeze prompts, model and reasoning settings, solver settings, hardware,
   tariff bounds, the uncertainty policy, and the stateful rule-parser version.
4. Use the development split for diagnostics. After freezing, evaluate the test
   split once for the confirmatory result.
5. Never expose canonical truth, scenario ID, wording variant, benchmark split,
   or uncertainty-case labels to an evaluated method.

The primary v3 dataset is synthetic and created by this package. Eight complete
physical-event lifecycles use the case-study buses and chargers. Each contains a
conditional warning, confirmed onset, unchanged persistence, verified
correction, recovery, and stable post-recovery message. Four paired formats—a
clean notice, single field message, driver chat, and uncertain/noisy chat—yield
192 decisions. The 96/96 development/test split is scenario-clustered, so no
variant of a test physical event occurs in development. Chats contain no real
driver messages, names, or personal data.

The earlier `trigger_notices_v2` files remain available as a 120-decision,
simpler-notice sensitivity benchmark.

## Frozen uncertainty policy

Every method uses the same mapping from uncertain communication to deterministic
optimizer inputs:

- delay range: upper bound;
- return-delay range: upper bound while preserving the scheduled departure;
- energy multiplier range: upper bound;
- charger-power range: lower bound;
- suspected charger fault: request confirmation and do not change inputs;
- confirmed charger fault: mark the charger unavailable;
- unchanged persistence: retain selected values and do not reoptimize;
- recovery: restore zero delay, 1.0 energy multiplier, 200 kW charger power,
  and charger availability where applicable.

`NoticeInterpretation.uncertainty_details` records the ranges, selected values,
confidence, provisional status, conflicting evidence, rationale, and one of
`optimize`, `wait`, or `request_confirmation`. The latter two map to Trigger
`skip`. Observed conversational memory is separate from accepted optimizer
state: an uncertain warning can resolve a later phrase such as “same connector”
without silently changing the Gurobi problem.

## Information-path comparison

All aligned paths emit the same `NoticeInterpretation` schema:

- `manual`: hidden canonical fields used only as the oracle reference;
- `rule`: frozen regex/lexicon/range parser plus exact-event memory and the
  shared uncertainty policy;
- `llm`: raw public notice/chat, numerical context, and observational event
  memory interpreted by the Trigger Agent.

The rule method is intentionally stateful and is not weakened to manufacture an
Agent advantage. It handles ordinary numeric ranges and exact event IDs. The
held-out chat family adds realistic word-form quantities, fragmented evidence,
speaker conflicts, authority-dependent corrections, coreferences, and irrelevant
conversation.

Score the rule method on both splits:

```powershell
python scripts/evaluate_trigger_notices.py --split development --label stateful_rule_v3_dev
python scripts/evaluate_trigger_notices.py --split test --label stateful_rule_v3_test
```

After freezing the dataset, parser, and prompt, score the Trigger Agent alone:

```powershell
python scripts/evaluate_trigger_agent.py `
  --backend openai `
  --model gpt-5.6-luna `
  --split test `
  --label trigger_agent_v3_test
```

This command does not run Gurobi or call the Pricing and Evaluator agents. It
writes raw and guarded decisions, action/phase/asset/update/uncertainty scores,
structured-output attempts, exact token counts, approximate API cost, latency,
CPU time, and peak process memory. Provider-side FLOPs, GPU energy, and carbon
emissions are not exposed by the API and are not inferred.

Closed-loop workbooks keep compact Agent-call records in the `agent_calls`
sheet. Excel cells are limited to 32,767 characters, so any longer request or
response cell is replaced with a row/field/SHA-256 pointer. The adjacent
`.agent_calls.jsonl` sidecar is the authoritative, lossless transcript; the
adjacent `.run_summary.json` is the machine-readable run summary. All three
local output types remain ignored by Git.

Create the paired comparison after both test evaluations finish:

```powershell
python scripts/compare_trigger_evaluations.py
```

The report includes paired accuracy differences, lifecycle-sequence-clustered
bootstrap intervals, and a supplementary exact McNemar discordance calculation.

Write a non-secret manifest beside each run:

```powershell
python scripts/build_experiment_manifest.py `
  --configuration agent_trigger_only `
  --model gpt-5.6-luna `
  --notice-variant uncertain_chat `
  --output results/revision/trigger_agent_v3_test_manifest.json
```

## Prespecified configurations

| CLI value | Trigger | Pricing | Evaluator |
|---|---|---|---|
| `fixed_da_plan` | disabled | existing DA | hard checks |
| `structured_reference` | canonical fields + deterministic trigger | deterministic price-zone heuristic | deterministic |
| `oracle_event_trigger` | canonical event truth | deterministic price-zone heuristic | deterministic |
| `numerical_event_trigger` | numerical state/deviation flags only | deterministic price-zone heuristic | deterministic |
| `rule_text_event_trigger` | raw notice/chat → frozen stateful parser | deterministic price-zone heuristic | deterministic |
| `agent_trigger_only` | raw notice/chat + event memory → LLM Trigger | deterministic price-zone heuristic | deterministic |
| `full_deterministic` | frozen parser + deterministic trigger | deterministic price-zone heuristic | deterministic |
| `full_agentic` | LLM notice + state | LLM | LLM + hard guards |
| `rule_parser_trigger_substitution` | frozen parser + deterministic trigger | LLM | LLM + hard guards |
| `mathematical_pricing_substitution` | LLM | deterministic price-zone heuristic | LLM + hard guards |
| `evaluator_removal` | LLM | LLM | solver/feasibility checks only |
| `agent_evaluator_raw_text` | structured fixed control | deterministic | LLM text interpretation + deterministic priority scoring |
| `rule_text_evaluator` | structured fixed control | deterministic | frozen raw-text parser + the same priority scoring |
| `structured_evaluator_oracle` | structured fixed control | deterministic | canonical structured priority + the same scoring |
| `evaluator_removal_control` | structured fixed control | deterministic | solver/feasibility checks; soft priority ignored |

The historical CLI identifier `mathematical_pricing_substitution` is retained
for compatibility with the frozen v3 protocol. The comparator is a
deterministic price-zone heuristic, not a separately optimized mathematical
pricing model. Its fixed multipliers are therefore one prespecified baseline,
not proof that the LLM Pricing Agent is better than every possible non-agentic
tariff policy. The remaining-horizon price range determines cheap, transition,
and expensive timing zones. The multiplier levels then implement an explicit
value-sharing policy: selfish mode retains a larger charging-service and V2G
margin (`buy=1.10/1.14/1.18`, `sell=0.58/0.66/0.72`), while altruistic mode
uses a smaller charging margin and passes more grid value to the PTO
(`buy=1.01/1.03/1.05`, `sell=0.82/0.89/0.96`). These values are illustrative,
prespecified policy parameters rather than claimed market-standard markups.
Published EV-pricing models generally optimize prices using elasticity, user
utility, willingness-to-pay, and technical constraints rather than prescribe a
universal percentage (Luo et al., https://arxiv.org/abs/1801.02783; Mousavi et
al., https://arxiv.org/abs/2209.05658; Hematiboroujeni et al.,
https://arxiv.org/abs/2603.20226).

For prompt parity, the Pricing Agent now receives that deterministic schedule,
its range, and its arithmetic mean calculated over the same remaining horizon.
The disclosure is explicitly optional guidance: one possible choice is to keep
a similar average while moving markups across time, but the Agent may choose a
different overall level when it gives an operational reason. No average-matching
constraint or post-processing projection is applied. This distinction is logged
per optimizer attempt. The arithmetic-mean gap measures a change in overall
markup level; mean-centered temporal absolute error measures redistribution over
time after removing that level difference; dispatch-weighted chosen multipliers
report the markups attached to the proposed energy transactions. Consequently,
an economic gain can be described as temporal adaptation only when the reported
average gap is small. Otherwise it is attributed to the complete pricing policy,
which combines markup level and timing.

### Post-confirmatory evaluator and reporting improvements

The follow-up controlled Evaluator study is specified in
`inputs/revision/information_and_evaluator_ablation_protocol_v1.json`. It replaces
the old interpretation of “Evaluator removal” as sufficient evidence for the LLM
Evaluator's contribution. The controlled study fixes the Trigger to the structured
notice and Pricing to the deterministic policy, then compares four Evaluator paths:
raw-text LLM, raw-text frozen rule, structured-priority oracle, and removal. The
canonical operator priority is retained only for scoring and the labelled oracle;
it is excluded from every OpenAI request.

Evaluator economics now use projected full-day values: realized settlement through
the decision timestep plus the proposed remaining schedule. A negative
remaining-horizon PTO cost is no longer an automatic acceptance. Canonical priority
compliance is reported before economics, and economics are compared among compliant
schedules. When the first candidate misses an interpreted priority, the single rerun
passes that structured priority to the optimizer as a high-penalty soft requirement.
This asks for the least-cost compliant schedule when compliance is physically
possible, while an explicit slack variable preserves feasibility and records an
unattainable target rather than crashing the episode. The dedicated commands are:

```powershell
python scripts/run_evaluator_ablation.py --output-root results/revision/evaluator_ablation_v1 --dry-run
python scripts/run_evaluator_ablation.py --output-root results/revision/evaluator_ablation_v1 --allow-external-llm
python scripts/analyze_evaluator_ablation.py --input-root results/revision/evaluator_ablation_v1
```

For the Trigger study, Agent and frozen-rule paths consume the same raw public text
and public numerical context. Agent versus rule isolates text interpretation; Agent
versus numerical estimates the value of advance text compared with later causal
sensor evidence; Agent versus oracle measures remaining headroom relative to a
perfectly structured advance interpretation. These three contrasts answer different
questions and are not pooled.

The frozen v3 workbooks retain their original candidate-selection rule and are
never overwritten. Subsequent workflow versions persist every proposed
charging/V2G schedule in `optimization_attempts` and select the best
solver-usable candidate by the mode-aligned objective (within a 0.001 numerical
tie tolerance). An evaluator-accepted rerun can no longer replace a materially
better earlier schedule solely because it was accepted. A deterministic guard
also accepts a solver-usable altruistic result with negative remaining-horizon
PTO cost, as V2G revenue already exceeds charging cost.

Secondary grid-flexibility reporting is derived from `ex_post_settlement`:

- cheap-period charging (downward flexibility delivered);
- expensive-period V2G export (upward flexibility delivered);
- cheap charging and expensive export shares;
- energy-weighted buy and sell grid prices;
- peak 30-minute net import; and
- grid-side charging plus export as a battery-throughput/cycling proxy.

The throughput value is descriptive and is not a battery degradation model.

Example closed-loop rule-text run:

```powershell
python -m agentic_workflow `
  --state-workbook inputs/State.xlsx `
  --forecast-workbook inputs/Forecasted.xlsx `
  --spot-prices inputs/SpotPrices.xlsx `
  --realtime-states inputs/realtime_states `
  --intraday-prices inputs/intraday_prices `
  --disturbances inputs/rt_disturbance_scenarios_multiple.xlsx `
  --scenario rt_none `
  --notices-file inputs/revision/trigger_notices_v3.json `
  --notice-scenario v3_route6_closure `
  --notice-variant uncertain_chat `
  --configuration rule_text_event_trigger `
  --output results/revision/v3_route6_rule_text.xlsx
```

### Common physical truth and causal settlement

Closed-loop runs add `--realize-notice-truth` and a separate
`--physical-events-file`. The latter advances a hidden physical-event state
used only by the environment. Canonical notice fields are used only by the
oracle and are never added to the public Trigger payload. Each evaluated
method still constructs its own optimizer assumption from its allowed input:
numerical telemetry, stateful text parsing, or Agent interpretation.

At observation timestep `t`, the workflow first settles the plan for the
completed interval before any decision at `t` can modify the future schedule.
Post-confirmatory workflow versions pass only intervals `t+1,...,48` to the
optimizer and map the first returned action to interval `t+1`; the already
settled interval is excluded from both the proposed schedule and its objective.
The simulator carries physical bus energy forward cumulatively. Extra traction
consumption and charging curtailed by a failed or derated charger therefore
remain visible in later SOC and reserve-shortfall metrics. The workbook records
realized PTO cost, aggregator revenue, grid cost, buy/sell energy, curtailment,
minimum and terminal SOC, reserve shortfall, planner/truth mismatch, optimizer
calls, solver resources, LLM tokens, and approximate API cost.

The frozen v3 notice timing remains unchanged for confirmatory interpretation
accuracy. The separate `advance_warning_notices_v1.json` path now contains the
calibrated v2 protocol with three
paired operational stress cases: a late-return driver report before departure,
a charger-bank lockout before the morning charging peak, and a combined evening
late-return/charger event before the afternoon price trough. A separate
`advance_warning_physical_events_v1.json` fixes physical onset, recovery, and
first sensor detection. These cases are not a second held-out accuracy test.
The earlier energy-consumption case is retained only as a sensor-readable
control, not as a primary unstructured-information claim. Rebuild the primary
cases with:

```powershell
python scripts/build_closed_loop_notice_cases.py
```

Clock conversion is deterministic and is not delegated to the LLM. Timestep 1
covers 00:00-00:30 and public clock windows use `[start,end)`: for example,
03:30-05:00 affects timesteps 8-10 inclusive. The raw LLM interpretation is
retained for audit, while the effective decision uses the deterministic window.
Follow-up messages for the same active event inherit that normalized window;
recovery messages retain their actual recovery timestep.

The v2 disturbances were selected by a declared Gurobi calibration sweep, not
by Agent outcomes. The selected late-return range is 60-90 minutes (90-minute
conservative update), and the selected charger outage is EVSE 6-8 from
03:30-05:00. In each changed case, the selected setting is the strongest tested
setting for which the structured advance-information oracle remains safe while
the delayed numerical trigger is unsafe. Reproduce the sweep with:

```powershell
python scripts/calibrate_advance_warning_cases.py --force
```

Run the deterministic comparison first:

```powershell
python scripts/run_closed_loop_trigger_comparison.py `
  --case aw_charger_bank_shutdown `
  --variant uncertain_chat `
  --mode selfish
```

After setting `OPENAI_API_KEY`, add the frozen low-cost Agent Trigger:

```powershell
python scripts/run_closed_loop_trigger_comparison.py `
  --case aw_charger_bank_shutdown `
  --variant uncertain_chat `
  --mode selfish `
  --include-agent `
  --resume
```

`--resume` reuses completed deterministic workbooks. Generated workbooks and
comparison files remain under ignored `results/`; they are not committed.

### Repeated matrix and safety-first analysis

The full prespecified matrix runner covers all three advance-warning cases in
both selfish and altruistic modes. By default it indexes or reuses the three
deterministic comparators without contacting an external model provider:

```powershell
python scripts/run_advance_warning_matrix.py
```

The primary stochastic design uses five independent `agent_trigger_only`
repetitions per case-mode cell. The Agent receives only synthetic public
notice/chat text, operational numerical context, and observational event memory;
it never receives canonical hidden truth. A deterministic evidence gate invokes
the LLM Trigger only when a new public message arrives or causal numerical-event
evidence changes; quiet timesteps are logged as skips without spending tokens.
External calls require both an API key and an explicit transfer-authorization flag:

```powershell
python scripts/run_advance_warning_matrix.py `
  --include-agent `
  --agent-repetitions 5 `
  --allow-external-llm
```

The role-level ablation is secondary and defaults to five repetitions per
case-mode-configuration cell. It compares `full_agentic` with the frozen-rule
Trigger substitution, mathematical-pricing substitution, and evaluator removal:

```powershell
python scripts/run_advance_warning_matrix.py `
  --include-role-ablations `
  --only-role-ablations `
  --ablation-repetitions 5 `
  --allow-external-llm `
  --require-clean-git `
  --max-approximate-api-cost-usd 4.00 `
  --output-root results/revision/ablation_confirmatory_v3
```

Before the confirmatory run, validate all four role paths in the charger case
that exposed repeated polling. This four-episode smoke test uses a separate
output root that must never be reused by the confirmatory matrix:

```powershell
python scripts/run_advance_warning_matrix.py `
  --case aw_charger_bank_shutdown `
  --mode selfish `
  --include-role-ablations `
  --only-role-ablations `
  --ablation-repetitions 1 `
  --allow-external-llm `
  --output-root results/revision/ablation_smoke_v3
```

Complete workbooks are reused unless `--force` is supplied. Repeated workbooks
remain under ignored `results/`; only CSV/JSON indexes and summaries should be
copied into paper artifacts after the matrix is complete.
Use `--force-stochastic` when a frozen input revision requires fresh Agent or
ablation episodes but the deterministic comparators have already been rerun and
audited. The matrix manifest records SHA-256 hashes of the notice, hidden
physical-event, and ablation-protocol files; a later resume refuses to reuse
fingerprinted workbooks after any of those inputs changes.
The run index records the actual solver names and any fallback errors from each
workbook; installed software alone is not treated as evidence that a licensed
solver was used.

Create paper-ready paired summaries with:

```powershell
python scripts/analyze_advance_warning_matrix.py
```

The analysis produces run-level, method-summary, primary-contrast, and
role-ablation CSV files plus a JSON protocol summary. A run is safety-feasible
only if it completes, has no reserve-violation timesteps, has no material reserve
shortfall, and maintains the 20% minimum and terminal SOC thresholds. Economic
effects are reported only for pairs in which both methods are safety-feasible.
In selfish mode, a positive paired effect means greater aggregator revenue; in
altruistic mode, it means lower PTO cost. This prevents an unsafe schedule from
appearing superior merely because it bought less energy or earned more revenue.
Absolute mode-aligned differences no greater than 0.001 are reported as economic
ties, preventing solver-scale floating-point noise from being labeled a win or
loss. The threshold is configurable with `--economic-tie-tolerance` and is
recorded in the JSON analysis protocol.
Primary Agent contrasts use repeated paired differences against the fixed
deterministic baseline in each case-mode cell. Because all role-ablation methods
contain stochastic LLM components, their secondary contrasts use independent-
sample differences in means rather than pairing arbitrary repetition numbers.
Role-ablation summaries also report evaluator-acceptance and forced-selection
rates per Trigger decision. These are kept separate: a feasible plan retained at
the rerun cap is an operational selection, not evidence that the evaluator
approved its quality.

The primary interpretations are deliberately separate:

- Agent versus rule-text isolates the benefit of contextual language
  interpretation over a frozen, stateful parser.
- Agent versus numerical isolates the benefit of advance unstructured
  information over a causal event trigger that must wait for sensors.
- Agent versus oracle measures remaining headroom relative to perfectly
  structured advance information; it is not presented as a deployable baseline.

For the causal Trigger comparison, use `oracle_event_trigger`,
`numerical_event_trigger`, `rule_text_event_trigger`, and `agent_trigger_only`.
Pricing, evaluator, solver, workbooks, tariffs, and seeds stay fixed. The offline
192-decision benchmark is primary for interpretation. Paired closed-loop cases
then measure whether earlier/correcter interpretation changes revenue or PTO
cost. The numerical method receives only observable physical consequences, never
canonical notice fields. Do not claim an economic comparison until the same
physical event-realization layer is active for every method.

The execution paths are isolated in code. Oracle and stateful-rule methods act
only on a newly supplied lifecycle interpretation; they do not fall back to a
generic deviation trigger between messages. The numerical method removes notice
text, interpretations, and event memory and uses a stateful event estimator over
causal charger/return telemetry available only at the declared sensor-detection
timestep. The
Agent receives public text plus numerical state, but its skip decision is not
silently replaced by the numerical baseline. Structural guards still enforce
warning/persistence/recovery semantics and all raw/guarded decisions are logged.

The initial low-cost configuration uses `gpt-5.6-luna` with low reasoning effort
and frozen approximate rates of $0.20/M input tokens and $1.20/M output tokens.
Override the rate environment variables when using another model or pricing
snapshot, and report the exact rates with every table.

The day-ahead optimizer in `app.py` was restored from repository commit
`431f646324337740c6ed9ac2eeeb4fb6f5b304ef`. Gurobi is primary on the development
machine, with an auditable HiGHS fallback. Real-time experiments continue to use
`app_rt.py`, keeping the optimization boundary fixed.

## Primary outcomes

Pre-specify raw Trigger action accuracy and false-optimization rate as primary.
Also report guarded action accuracy; event, phase, asset, timing, range, selected
value, confidence, conflict, recommendation, and exact-update accuracy; detection
lead time; optimizer calls by lifecycle phase; PTO cost; aggregator revenue; V2G
energy; terminal SOC; output failures/retries; tokens and API cost; information,
LLM, optimizer, solver, and workflow latency; CPU; memory; solver dimensions, gap,
and nodes/iterations where available.

Do not pool four wording variants as independent physical scenarios. Use paired
or sequence-clustered uncertainty estimates because decisions from one lifecycle
share physical truth.

## Remaining empirical work

The deterministic oracle/rule/numerical matrix has been validated in both modes;
generated results remain ignored. Run repeated nondeterministic Agent Trigger
trials before making an Agent-over-rule economic-superiority claim.
The 8/16/32 scaling study also still requires physically coherent 16- and
32-bus workbooks and a declared replication rule for trips, charger ratios,
depot power, and terminal SOC. A second depot must be a distinct input instance.

### Six-run Agent pilot (historical pre-calibration result)

This section records the original v1 pilot for provenance. Its case severities
and nondeterministic clock interpretations have been superseded by the calibrated
v2 protocol and must not be combined with new confirmatory repetitions.

On 2026-08-13, one `agent_trigger_only` repetition was run for each of the three
advance-warning cases in both modes with `gpt-5.6-luna` at low reasoning effort.
All six runs completed 48 timesteps, all structured calls were valid, all runs
met the reserve and 20% SOC safety criteria, and their economic outcomes matched
the oracle. The six runs used 619,441 total tokens and approximately USD 0.1341.

Relative to the numerical trigger, the Agent was safe in all six cells while
the numerical method was safe in two. In those two comparable combined-event
cells, the Agent increased selfish-mode aggregator revenue by approximately
3.42 and reduced altruistic PTO cost by approximately 11.72. Relative to the
rule-text method, the Agent was safe in two additional charger-shutdown cells;
the four cells in which both were safe were economic ties. These are pilot
observations only: there is one stochastic repetition per case-mode cell, so no
Agent superiority or uncertainty interval should be claimed yet.

All successful Agent calls were audited from the lossless JSONL sidecars. The
public notice objects contained only notice/event IDs, source type, report time,
and synthetic text. No canonical interpretation or hidden physical-event state
was sent. HiGHS (`appsi_highs`) solved every pilot optimization. The installed
Gurobi licence could not be used because it is bound to HostID `846d74f2` while
the current host reports `f47957cb`; this fallback is recorded in the run index.

### Five-repetition Gurobi matrix (historical pre-calibration result)

On 2026-08-13--14, the primary matrix was rerun in the normal Windows user context
with the current default academic licence at `C:\Users\alien\gurobi.lic` and
`gurobi` enforced as the only solver. The older Downloads licence used by the
pilot is bound to a different HostID. All 48 indexed method runs completed, all
optimization attempts recorded Gurobi, and no solver fallback was used. The
matrix includes the three single-run deterministic comparators and five
independent Agent Trigger repetitions in each of the six case-mode cells.

The Agent was safety-feasible in 22 of 30 repetitions. This headline rate must
not be read as a pure Trigger-accuracy score. In the selfish route-delay cell,
the Agent, oracle, rule-text, and numerical methods were all unsafe; all five
Agent repetitions exactly matched the oracle actions and parameter updates. In
the altruistic charger-bank cell, the Agent was safe in two of five repetitions,
but those two outputs interpreted the notice window one interval more
conservatively than the canonical record. The canonical oracle, rule-text, and
numerical schedules were all unsafe in that cell. Accordingly, the conservative
Agent outcomes are reported as interpretation variability, not as evidence that
the Agent correctly outperformed the oracle.

Against the numerical trigger, the Agent was the only safe method in 12 of 30
paired repetitions; both were safe in 10 and neither was safe in eight. In the
ten jointly safe combined-event comparisons, selfish-mode revenue was tied; in
altruistic mode the Agent reduced PTO cost by a mean of approximately 7.03, with
three wins and two ties. Against the rule-text method, the Agent was the only
safe method in seven paired repetitions, both were safe in 15, and neither was
safe in eight. Jointly safe rule-text comparisons produced ten economic ties
and five Agent losses, all in the altruistic combined case. These results support
separate conclusions: advance text can improve on waiting for sensor detection,
while the present five-run evidence does not establish general LLM superiority
over the frozen text rule.

The 30 Agent episodes used 3,097,406 tokens, 150 successful structured model
requests with zero failed attempts, about USD 0.6622 at the frozen rates, 674.5
seconds of provider latency, and 967.1 seconds of summed episode wall time. Raw
workbooks remain ignored under `results/`; the local matrix index and analysis
summary have SHA-256 digests
`101f10659692f06424a404821ab113ffaa415ed6adbb0342bd686cf3d4ce3f89` and
`0b62a852ea337a872e689743f174c5a27f659f2be8aae8ed8cc0e7b75d8d2a24`.

### Calibrated v2 deterministic matrix

On 2026-08-14, the three deterministic trigger methods were rerun for all three
cases and both modes after freezing the deterministic clock conversion and the
calibrated case settings. All 18 runs completed 48 timesteps with Gurobi 13.0.2,
and no solver fallback was recorded. The structured oracle was safe in all six
case-mode cells, the rule-text method in four, and the causal numerical trigger
in three.

The numerical trigger was unsafe in the selfish 90-minute late-return case
(1.764 kWh maximum reserve shortfall) and in both three-charger outage modes
(63.12 and 216.38 kWh). The oracle was safe in each of those cells because it
could act on confirmed advance information. All methods were safe in the
combined case. In its altruistic cell, structured advance information reduced
PTO cost by 11.7242 relative to delayed numerical detection; selfish revenue was
an economic tie. The frozen rule failed to convert the coreferential phrase
"same south auxiliary-row isolation" into an unavailable-charger update, so its
two charger failures are retained as a transparent text-parser limitation.

These deterministic results establish that the revised cases are feasible and
decision-sensitive. They do not establish Agent performance: the next
confirmatory step is five new Agent repetitions per case-mode cell using only
the v2 inputs, followed by the frozen safety-first paired analysis.

### Calibrated v2 five-repetition Agent matrix

On 2026-08-14, five fresh `agent_trigger_only` repetitions were executed in
each of the six case-mode cells. All 30 Agent episodes completed, used only
Gurobi with no fallback, and satisfied the reserve and 20% SOC safety rules.
The 150 structured model requests were all schema-valid; the audit found no
canonical/hidden-truth fields in requests and no deterministic clock-window
errors.

Against the numerical trigger, the Agent was the only safe method in 15 of 30
pairs: all five selfish late-return pairs and all ten charger-outage pairs. Both
were safe in the other 15 pairs. In the jointly safe altruistic combined cell,
the Agent reduced PTO cost by a mean of 2.3448, with one win and four ties. In
the altruistic late-return cell, however, the numerical trigger reduced PTO cost
by 7.7740 relative to the advance Agent schedule. Thus the v2 evidence supports
a safety benefit from advance unstructured information in selected cases, not a
universal economic benefit.

Against the frozen rule-text method, the Agent was the only safe method in all
ten charger pairs. In jointly safe comparisons, 16 were economic ties and four
were Agent losses, all in the altruistic combined case. Four of five Agent runs
in that cell reoptimized at physical onset after independent deviation and
unexpected-discharge flags fired, losing 11.7242 of PTO-cost performance versus
the advance oracle/rule schedule; the fifth was an economic tie but contained a
return-delay carry-forward interpretation error. These outcomes are retained as
limitations rather than tuned away after observation.

The 30 Agent episodes used 3,105,530 tokens and approximately USD 0.6601 at the
frozen rates. Summed provider latency was 660.1 seconds; summed episode wall time
was 966.1 seconds and summed local process CPU time was 1,420.6 seconds. Mean
local peak resident memory was 337.2 MB. The API does not expose provider-side
compute or energy use, so token counts and latency are reported separately from
local CPU and memory measurements.

### v2 protocol and pre-confirmatory v3 amendment

Before executing the v2 primary Agent repetitions, the secondary ablation
protocol was frozen in `advance_warning_ablation_protocol_v2.json`. It fixes the
four configurations, three component-level contrasts, six case-mode cells,
five repetitions per configuration-cell, safety-first outcomes, 0.001 economic
tie tolerance, and 10,000 bootstrap iterations. This is 120 secondary episodes.
The protocol explicitly prohibits redesigning the ablations in response to the
primary Agent results. The v2 execution pilot is not included in the
confirmatory five-repetition analysis.

That pilot exposed execution defects rather than an unfavorable result to tune:
the rule Trigger lacked the LLM Trigger's evidence-change gate, the LLM Trigger
could inherit a deterministic numerical fallback, one configured rerun meant one
total attempt, evaluator-accepted attempts did not outrank rejected attempts, and
the evaluator prompt assumed a five-rerun cap. The amended and now-frozen
`advance_warning_ablation_protocol_v3.json` corrects those validity issues while
leaving the cases, hypotheses, outcomes, four configurations, five repetitions,
and 120-run sample size unchanged. It also records every role backend and gate
setting in each run summary. The v2 file and pilot remain as provenance.

The v3 Trigger request is projected onto an explicit public operational
allowlist. It excludes canonical and physical truth, notice scenario identifiers,
wording variants, and internal event-configuration metadata. Trend context is
limited to the five most recent completed intervals, matching the Trigger prompt
while avoiding irrelevant token growth later in the day.

Under v3, `--max-reruns 1` means one additional attempt after the initial
pricing/optimization attempt, so each Trigger decision has at most two optimizer
attempts. A feasible evaluator-accepted candidate is selected before applying
mode-aligned economic ranking; a rejected result cannot displace it merely by
showing higher apparent revenue or lower cost. Reaching the rerun cap does not
automatically convert a rejection into evaluator approval. If all attempts are
rejected, the runner retains the best feasible candidate and logs the selection
as forced at the cap, separately from the evaluator's original decision.

### Isolated v2 role-ablation pilot

On 2026-08-14, the isolated pilot completed all 24 planned episodes: four role
configurations across three cases and two modes. All completed workbooks used
Gurobi without fallback. Their lossless sidecars contained 261 successful,
schema-valid structured calls, 3,400,684 tokens, and approximately USD 0.7433
at the frozen rates. No canonical/hidden-physical-truth marker appeared in a
request, and all 72 public-notice occurrences contained only the five allowed
public fields.

The first pass exposed a transport-validation defect in five altruistic runs:
the model sometimes returned buy and sell arrays of different lengths, and the
three identical parse retries exhausted. The transport schema now accepts the
two nonempty arrays and a deterministic post-parse step independently truncates
or extends them to the known remaining horizon before constructing the strict
pricing decision. Every correction is logged. The resumed pilot reused 19
complete workbooks and regenerated only the five failed cells; seven retained
pricing calls required this length normalization.

The audit also found one non-recovery interpretation of
`21:30-to-end-of-day` with a missing end timestep. The deterministic public-text
clock parser now maps `end of day` to timestep 48, with an exact regression
test. Recovery interpretations intentionally retain a null expected end because
the restriction has ended. The affected pilot workbook was not converted into
confirmatory evidence; all 120 confirmatory episodes will start fresh with the
v3 protocol and corrected normalizer.

Descriptively, `full_agentic`, the mathematical-pricing substitution, and
evaluator removal were each safe in all six pilot cells. The rule-trigger
substitution was safe in five: in the selfish charger-shutdown cell it made 25
optimizer calls and ended with a 24.6523 kWh maximum reserve shortfall across 12
timesteps. These single-run outcomes validate that the contrasts are
decision-sensitive, but they are not statistical evidence and must not be cited
as an ablation conclusion. The 25-call rule episode is specifically treated as
evidence of the missing shared gate, not as evidence against the rule parser.
The original pilot projected about 17.0 million tokens, USD 3.72 at the recorded
rates, and roughly three hours of sequential episode time for 120 runs. Because
v3 enables the one actual pricing rerun allowed by the protocol, the projection
must be refreshed from the separate four-episode v3 smoke test before
confirmatory authorization.

### v3 readiness smoke and execution audit

The final pre-confirmatory charger-cell smoke exercised all four role paths, and
a targeted rule-path rerun validated the final all-bus evaluator-deviation check.
Every episode completed 48 timesteps with Gurobi and no solver fallback. The
shared evidence gate reduced the rule substitution from 25 repeated Trigger
decisions to two genuine decisions; its four optimizer attempts are the initial
and one allowed repricing at onset and recovery.

The public-payload audit checked 15 LLM Trigger requests. No canonical truth,
physical truth, notice scenario identifier, wording variant, benchmark split,
or internal event-configuration key was present; every public notice used only
the five allowed public fields, and Trigger history never exceeded five
intervals. Role provenance also confirmed that each comparator changed only its
named role.

The targeted final rule episode caused zero evaluator approvals and two
explicitly forced feasible selections, correctly preserving the evaluator's
rejections when fleet deviations remained large. It was unsafe under hidden
ex-post truth, while the other three smoke configurations were safe. These are
execution checks only, not inferential ablation results.

Combining the final role-path measurements gives an expected 120-run workload of
about 10.2-13.4 million tokens, USD 2.2-2.8, and roughly 1.1-1.9 hours of summed
episode time before API and solver variability. The proposed confirmatory command
therefore uses a USD 4.00 episode-boundary ceiling and should be allowed a
two-to-three-hour wall-time window. The runner also refuses confirmatory
execution from a dirty Git worktree and records both safeguards in the manifest.

### v3 confirmatory role-ablation results and post-run audit

All 120 frozen episodes completed 48 timesteps with Gurobi and no solver
fallback. Realized operational feasibility (complete run, no reserve shortfall,
no reserve-violation interval, and minimum/terminal SOC at least 20%) was:

| Configuration | Feasible | Infeasible |
|---|---:|---:|
| `full_agentic` | 30 | 0 |
| `rule_parser_trigger_substitution` | 21 | 9 |
| `mathematical_pricing_substitution` | 29 | 1 |
| `evaluator_removal` | 30 | 0 |

All nine rule-trigger failures occurred in the charger-shutdown case (five
selfish and four altruistic episodes). The deterministic-pricing failure was
one selfish charger-shutdown episode. Thus the strongest role result is the
LLM Trigger's operational-feasibility advantage for ambiguous advance charger
information, not universal economic dominance by all three Agents.

Economics are compared only among realized-operationally-feasible episodes.
The full workflow reduced combined-event altruistic PTO cost by 9.4770 versus
the rule Trigger (95% bootstrap interval 4.7385 to 11.8463) and by 7.2543 versus
deterministic price-zone pricing (0.1954 to 11.9684). It improved selfish
charger revenue by 3.1700 versus evaluator removal (approximately 0 to 7.5409).
Other point estimates were mixed; no negative full-workflow contrast had a 95%
bootstrap interval entirely below zero. These results do not support a claim
that the complete three-Agent workflow always improves profit or cost.

The interval-level secondary analysis reconciles exactly with all run totals.
In these cases every realized charge occurred in the lower daily price zone and
every nonzero V2G export occurred in the upper zone, so alignment shares alone
do not discriminate methods. Export volume is informative: in the combined
altruistic cell the full workflow exported 180 kWh on average, versus 100 kWh
with the rule Trigger and 120 kWh with deterministic pricing, but 200 kWh with
evaluator removal. The full workflow therefore improves grid support relative
to the Trigger and Pricing substitutions in that cell, while the Evaluator adds
no benefit there. Charging plus export is reported as a cycling proxy and is
not interpreted as automatically beneficial.

An audit of 240 optimization decisions found 12 cases where an
evaluator-accepted rerun was materially worse than an earlier solver-usable
candidate under the 0.001 objective tolerance. It also found three erroneous
rejections of negative altruistic PTO cost; all three feedback instructions
lowered buy multipliers even though the candidate bought zero energy. The
post-confirmatory retention and negative-cost guards above correct these issues
for future runs.

The audit also exposed a one-interval horizon alignment defect in the frozen
implementation: interval `t` was settled before the decision but was still
included in the optimizer horizon. In the combined altruistic outlier, the full
workflow proposed 200 kWh of remaining V2G, but 100 kWh was allocated to the
already-settled first optimizer interval and only 100 kWh was realized. Future
workflow versions optimize `t+1,...,48` only. This correction and the candidate
retention changes require a clearly labelled post-confirmatory diagnostic; they
must not be used to overwrite the frozen v3 evidence.

The confirmatory matrix used 10,873,413 tokens in 983 successful OpenAI
requests, with no failed request. Approximate API cost was USD 2.1037. Summed
episode wall time was 2.222 hours and summed local process CPU time was 4.521
hours. The full workflow was the most expensive configuration, averaging about
123,450 tokens and USD 0.0246 per episode.

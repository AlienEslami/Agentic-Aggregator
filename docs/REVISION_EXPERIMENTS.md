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
| `structured_reference` | canonical fields + deterministic trigger | mathematical/rule | deterministic |
| `oracle_event_trigger` | canonical event truth | mathematical/rule | deterministic |
| `numerical_event_trigger` | numerical state/deviation flags only | mathematical/rule | deterministic |
| `rule_text_event_trigger` | raw notice/chat → frozen stateful parser | mathematical/rule | deterministic |
| `agent_trigger_only` | raw notice/chat + event memory → LLM Trigger | mathematical/rule | deterministic |
| `full_deterministic` | frozen parser + deterministic trigger | mathematical/rule | deterministic |
| `full_agentic` | LLM notice + state | LLM | LLM + hard guards |
| `rule_parser_trigger_substitution` | frozen parser + deterministic trigger | LLM | LLM + hard guards |
| `mathematical_pricing_substitution` | LLM | mathematical/rule | LLM + hard guards |
| `evaluator_removal` | LLM | LLM | solver/feasibility checks only |

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

For the causal Trigger comparison, use `oracle_event_trigger`,
`numerical_event_trigger`, `rule_text_event_trigger`, and `agent_trigger_only`.
Pricing, evaluator, solver, workbooks, tariffs, and seeds stay fixed. The offline
192-decision benchmark is primary for interpretation. Paired closed-loop cases
then measure whether earlier/correcter interpretation changes revenue or PTO
cost. The numerical method receives only observable physical consequences, never
canonical notice fields. Do not claim an economic comparison until the same
physical event-realization layer is active for every method.

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

The same physical-event realization and ex-post settlement layer must be enabled
for all four Trigger methods before economic superiority is claimed. The 8/16/32
scaling study also still requires physically coherent 16- and 32-bus workbooks
and a declared replication rule for trips, charger ratios, depot power, and
terminal SOC. A second depot must be a distinct input instance.

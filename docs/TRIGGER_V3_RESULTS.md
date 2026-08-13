# Frozen Trigger-Agent v3 held-out results

## Scope and protocol

This is the confirmatory interpretation benchmark for the revised Trigger
Agent. The test split was evaluated once after the prompt, dataset, parser, and
metrics were frozen. It contains 96 decisions grouped into 16 complete
scenario/wording lifecycle sequences: four held-out physical scenarios, four
wording variants, and six lifecycle messages per sequence.

The Agent received the same current notice/chat and prior-event memory that are
available to the workflow. The stateful rule baseline received the same text and
memory. The test includes clean notices, single-message paraphrases, multi-party
driver chats, and chats containing ranges, provisional estimates, corrections,
conflicting evidence, and irrelevant messages. Canonical event truth was used
only for scoring.

The Agent used `gpt-5.6-luna` with low reasoning effort and structured output.
All reported confidence intervals below use 100,000 bootstrap samples that
resample complete scenario/wording lifecycle sequences. The exact McNemar test
is supplementary because individual decisions within a lifecycle are not
independent.

## Primary paired results

| Metric | Trigger Agent | Stateful rule | Difference | Sequence-clustered 95% CI |
|---|---:|---:|---:|---:|
| Action accuracy | 93.75% | 75.00% | +18.75 pp | +8.33 to +29.17 pp |
| Phase accuracy | 90.63% | 58.33% | +32.29 pp | +15.63 to +50.00 pp |
| Exact update accuracy | 78.13% | 66.67% | +11.46 pp | -6.25 to +30.21 pp |
| Uncertainty-estimate accuracy | 69.79% | 58.33% | +11.46 pp | -10.42 to +33.33 pp |
| Recommended-action accuracy | 89.58% | 75.00% | +14.58 pp | +5.21 to +25.00 pp |

For the primary action decision, the Agent was correct when the rule was wrong
on 24 paired decisions; the rule was correct when the Agent was wrong on 6.
The supplementary exact McNemar p-value is 0.00143. The positive point estimates
for exact updates and uncertainty estimates are not conclusive because their
clustered intervals cross zero.

## Effect of message format

| Wording variant | Agent action | Rule action | Agent phase | Rule phase |
|---|---:|---:|---:|---:|
| Clean notice | 100.00% | 100.00% | 100.00% | 100.00% |
| Single-message paraphrase | 100.00% | 100.00% | 100.00% | 100.00% |
| Driver chat | 95.83% | 50.00% | 91.67% | 16.67% |
| Uncertain/conflicting chat | 79.17% | 50.00% | 70.83% | 16.67% |

The methods tie on clean and single-message inputs. The overall advantage is
therefore concentrated in the unstructured multi-message conditions that the
revision was designed to test, rather than in canonical structured inputs.

## Agent telemetry and cost

All 96 structured-output calls succeeded without retry failure. The run used
632,850 input tokens, including 491,328 cached-input tokens and 141,234
cache-write tokens, plus 47,227 output tokens, of which 9,608 were reasoning
tokens. At the frozen rates recorded in the manifest, approximate API cost was
USD 0.101865. Total API latency was 392.01 seconds; local process CPU time was
3.38 seconds and peak resident memory was 185.97 MB. Provider-side FLOPs, GPU
energy, and carbon emissions are not exposed and are not estimated.

The post-output guard activated on 8.33% of decisions, but raw and guarded
action accuracies were both 93.75%. It normalized structured fields and
reasoning consistency; it did not create the observed action advantage.

## Error audit and interpretation limits

The Agent made six action errors. All were false skips in difficult onset or
severity-change chats. In these cases it over-weighted neutral telemetry and
treated an authorized driver's confirmation as a warning or request for more
confirmation. This is a substantive limitation of the frozen system, especially
for fast-onset events. The prompt and test data were not changed after inspecting
these held-out results.

These results support a narrower claim: the Agent interprets unstructured,
stateful operational text more accurately than the frozen stateful rule baseline
on this held-out benchmark. They do **not** yet establish greater profit, lower
PTO cost, or better physical operation. Those claims require the next paired
closed-loop experiment, where Agent, rule-text, numerical-event, and oracle-event
triggers face exactly the same realized physical disturbance while the optimizer,
pricing, evaluator, workbooks, tariffs, and seeds remain fixed.

## Reproducibility files

- `results/revision/trigger_agent_v3_test_manifest.json`: environment, frozen
  rates, model settings, and hashes of benchmark inputs and code.
- `results/revision/trigger_agent_v3_test_summary.json`: complete Agent metrics,
  breakdowns, token usage, cost, latency, CPU, and memory.
- `results/revision/stateful_rule_v3_test_summary.json`: complete rule-baseline
  metrics and breakdowns.
- `results/revision/trigger_agent_vs_rule_v3_test.summary.json`: paired
  differences, clustered intervals, discordance counts, and subgroup results.
- `scripts/compare_trigger_evaluations.py`: deterministic paired-analysis code.

Raw model transcripts and per-decision CSV files remain ignored by Git because
they are generated artifacts and may contain operational text. Their SHA-256
hashes are recorded separately for local audit.

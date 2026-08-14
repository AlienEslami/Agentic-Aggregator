# Independent revision-input validation checklist

This checklist is completed by an author who did not construct the synthetic
notice text. It complements automated validation; it is not replaced by it.

## Dataset and information-boundary checks

- [ ] Every public driver, dispatcher, and maintenance message is plausible for
  the stated source and lifecycle phase.
- [ ] Every referenced bus, route, charger, time, range, and recovery exists in
  the corresponding operational input.
- [ ] The canonical interpretation matches the public text without adding facts
  that a deployed method could not infer.
- [ ] The canonical hidden physical event is excluded from all OpenAI requests.
- [ ] Scenario IDs, wording-variant labels, benchmark split labels, and canonical
  scoring fields are excluded from all OpenAI requests.
- [ ] Development and held-out test scenarios are separated by physical scenario,
  not only by paraphrase.

## Experimental-control checks

- [ ] Agent, rule-text, numerical, and oracle Trigger methods share the same
  realized physical event, optimizer, workbooks, tariffs, and accounting rule.
- [ ] The oracle is labelled as an upper-bound reference and not as a deployable
  equally informed baseline.
- [ ] V2G is enabled for every fair agent/non-agent comparison.
- [ ] Interval `t` is settled before the decision and only intervals `t+1,...,48`
  are optimized.
- [ ] Operational feasibility is reported before economics.
- [ ] The deterministic price-zone policy is described as a heuristic rather than
  a mathematical optimum.

## Sign-off

- Validator name:
- Role/coauthor:
- Date:
- Git commit reviewed:
- Exceptions or requested corrections:

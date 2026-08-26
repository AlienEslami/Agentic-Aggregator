# Day-ahead prompt paradigms (frozen)

These are the original prompt texts of the four day-ahead Pricing Agent
paradigms (zero-shot, few-shot, chain-of-thought, few-shot + CoT) and the
day-ahead Evaluator, extracted verbatim from the archived submission-era
experiment records. They still contain the template variables of the original
orchestration environment (`{{ $('Build LLM Context') ... }}`); the porting
plan in `docs/E3_DA_PROMPT_REPETITION_PLAN.md` maps each variable to the data
it carried. The files are frozen: the repetition protocol pins their SHA-256
hashes, and any edit invalidates the protocol.

# E3 porting plan: day-ahead prompt-paradigm repetition

## What exists and where

- The four day-ahead Pricing paradigm prompts and the day-ahead Evaluator
  prompts are frozen verbatim in `inputs/revision/da_prompt_paradigms/`,
  with SHA-256 hashes pinned by
  `inputs/revision/da_prompt_repetition_protocol_v1.json`.
- The original day-ahead loop ran in the archived orchestration
  environment; its export (37 nodes) is preserved in the submission
  archive under `workflows/day_ahead_workflow_prompt_analysis_baseline.json`.
  The nodes that matter for the port are: `Build LLM Context` (constructs
  every template variable used by the prompts), `LLM Decision Agent`
  (Pricing call), `Start Optimization Job`/`Poll Job Result` (the same
  `app.py` optimizer service this repository contains), `Evaluate & Adapt`
  (Evaluator call), `Check Rerun Cap` and `Update Best State` (accept/rerun
  loop and incumbent selection).

## What the port must do

1. Reimplement `Build LLM Context` in Python: read the published inputs
   (`inputs/State.xlsx`, `inputs/Forecasted.xlsx`, `inputs/SpotPrices.xlsx`),
   produce the context object, and render the frozen prompt templates by
   substituting each `{{ $('Build LLM Context') ... }}` variable with the
   corresponding context field. The rendering must be byte-stable so the
   rendered prompts can be hashed into the run manifests.
2. Call the model through the package's structured-output path
   (48 buy + 48 sell multipliers, validated and normalized by
   `normalize_pricing_decision`), call `app.optimize` in day-ahead mode, and
   run the Evaluator accept/rerun loop with the original rerun cap and
   incumbent rule.
3. Repeat per the protocol grid (4 paradigms x 2 modes x 20 repetitions),
   under `--require-clean-git`, with per-run manifests and the USD 5 budget
   cap, aborting when exceeded.

## Validation before spending budget

- Dry-run the full grid with the deterministic rule backend (no API) to
  exercise rendering, optimization, evaluation and reporting end to end.
- Render the FS+CoT selfish and altruistic prompts and diff their variable
  substitutions against the archived submission-era transcripts.
- Only then execute with `--allow-external-llm` and the API key.

## Why execution is deferred

No API key is available on this machine, and a faithful port of a
37-node orchestration cannot be validated blind. Implementing the runner
without the ability to test a single call risks burning the budget on a
silent infidelity, which is precisely what the repetition experiment is
meant to rule out.

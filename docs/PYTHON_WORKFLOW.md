# Standalone Python Real-Time Workflow

`agentic_workflow/` replaces the exported n8n real-time workflow with a local,
testable Python package. It keeps the original agent system prompts and the
existing `app_rt.py` Pyomo model, while replacing Google Drive, Google Sheets,
node merges, switches, waits, and rerun loops with explicit Python code.

## What is preserved

- 48-timestep real-time execution
- selfish and altruistic pricing modes
- trigger, pricing, and evaluator agent roles
- structured agent outputs
- price, energy, and delay disturbances, including combined scenarios
- best-result tracking and capped reruns
- rolling real-time plan and forecast updates
- real-time log and optimization-attempt output sheets
- direct use of the real-time Pyomo optimizer or compatibility with its Flask API

The Python runner fixes several boundary problems in the exported workflow:

- mode is selected by `--mode` rather than being hard-coded to `selfish`;
- the selected plan and summary share the same mode and run timestamp;
- observation timesteps 1–48 map consistently to plan-state rows 0–47;
- timestep 48 is processed instead of losing the last plan row;
- one rerun cap controls pricing, evaluation, and forced acceptance;
- the selected selfish or altruistic pricing result is always used;
- pricing treats the trigger schema's `delay` and `delay_recovery` values as
  operational delay cases, resolving the exported prompt's `operational` name
  mismatch;
- trips ending exactly at the rolling-horizon boundary no longer create an
  invalid empty Pyomo constraint;
- a horizon with no remaining service trips is recorded as a controlled no-op
  instead of raising an optimizer exception.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

An editable install is optional:

```bash
pip install -e .
```

This provides the `agentic-aggregator` command. Without an editable install,
use `python -m agentic_workflow`.

## Required inputs

The runner accepts the files used by the n8n workflow directly:

- state workbook containing `day_ahead_summary` and `day_ahead_plan`;
- forecast workbook containing `Forecasted Energy` and `Forecasted`;
- optional spot-price workbook containing `Spot Prices`;
- directory or ZIP with `benchmark_timestep_01.xlsx` through
  `benchmark_timestep_48.xlsx`;
- directory or ZIP with `intraday_prices_t01.xlsx` through
  `intraday_prices_t48.xlsx`;
- disturbance workbook containing `scenarios`.

These research and operational Excel workbooks are deliberately excluded from
the public repository. Place the private copies at the example paths below or
pass their local paths explicitly. The synthetic Trigger interpretation
benchmark under `inputs/revision/` does not require any Excel workbook.

The reader uses the numeric suffix in each timestep filename, so ZIP member
ordering does not matter.

## Example

```bash
python -m agentic_workflow \
  --state-workbook inputs/State.xlsx \
  --forecast-workbook inputs/Forecasted.xlsx \
  --spot-prices inputs/SpotPrices.xlsx \
  --realtime-states inputs/realtime_states.zip \
  --intraday-prices inputs/Intraday_price.zip \
  --disturbances inputs/rt_disturbance_scenarios_multiple.xlsx \
  --scenario price_plus_50 \
  --mode selfish \
  --agent-backend openai \
  --optimizer-backend direct \
  --output results/python_realtime_selfish.xlsx
```

Set `OPENAI_API_KEY` in the process environment before using the OpenAI agent
backend. The migrated n8n export originally used `gpt-4.1`; the current default is
`gpt-5.6-luna` with `low` reasoning effort. OpenAI describes Luna as the
cost-sensitive GPT-5.6 tier and lists Chat Completions and structured-output
support. The frozen starting rates used for approximate experiment-cost logging
are $0.20 per million uncached input tokens, $0.02 per million cached input
tokens, $0.25 per million cache-write tokens, and $1.20 per million output
tokens. Override them with `OPENAI_INPUT_USD_PER_MILLION`,
`OPENAI_CACHED_INPUT_USD_PER_MILLION`,
`OPENAI_CACHE_WRITE_USD_PER_MILLION`, and
`OPENAI_OUTPUT_USD_PER_MILLION` if the published rates change. See the official
model card: https://developers.openai.com/api/docs/models/gpt-5.6-luna

Pass `--model gpt-5.6-sol` only for a separately labelled, higher-capability
sensitivity run; when using a non-default model, set both cost-rate environment
variables so its logged cost is not reported as zero.

## Agent backends

- `--agent-backend openai`: original LLM-driven roles using GPT-4.1 and typed
  structured outputs.
- `--agent-backend rule`: deterministic implementation of the main trigger,
  pricing, and acceptance rules. It is intended for tests and API-key-free
  validation.
- `--agent-backend auto`: OpenAI when `OPENAI_API_KEY` exists; otherwise the
  deterministic backend.

The system prompts in `agentic_workflow/prompts/` were extracted verbatim from
`workflows/real_time_final.json`. Python builds clean JSON user messages instead
of evaluating n8n template expressions.

## Optimizers and solver selection

`app.py` is the day-ahead/core PTO optimizer restored from the project
repository; `app_rt.py` is the remaining-horizon optimizer used by this Python
workflow. Both try Gurobi first and then fall back to HiGHS, CBC, or GLPK when an
installed solver is unusable. Set `DA_SOLVER_ORDER` for `app.py` or
`RT_SOLVER_ORDER` for `app_rt.py` to freeze a solver order, for example
`appsi_highs,highs` for a license-independent replication run. Record the
selected solver and any fallback errors with the experiment environment.

On the development machine, Gurobi 13.0.2 discovers the installed academic
license when `GRB_LICENSE_FILE` is unset. Do not point `GRB_LICENSE_FILE` at a
downloaded or archived license merely because the file exists: the override
takes precedence over the working installed license and may refer to a different
HostID. The HiGHS path is retained as a portability fallback, not as the primary
solver for the reported Gurobi experiments.

## Disturbances

Repeat `--scenario` to compose multiple rows from the scenarios workbook:

```bash
--scenario price_plus_50 \
--scenario energy_plus_50_B4_v2 \
--scenario delay_plus_30_T2_B5
```

If no scenario is specified, `rt_none` is used. Unlike manually changing the
n8n Filter node, the selected scenario IDs are recorded in the output workbook.

## Optimizer backends

`--optimizer-backend direct` calls `app_rt.py` in the same Python process. This
is recommended for local execution because it removes HTTP submission and
polling without changing the optimizer result format.

`--optimizer-backend http` preserves the n8n deployment boundary. Start the API
with `python app_rt.py` and optionally set `--optimizer-url`.

The optimizer tries Gurobi, HiGHS, CBC, and GLPK in that order. Configure its
time limit and output with environment variables:

```bash
export RT_SOLVER_TIME_LIMIT=60
export RT_SOLVER_MIP_GAP=0.02
export RT_SOLVER_TEE=false
export RT_SOLVER_ORDER=gurobi,appsi_highs,highs,cbc,glpk
```

The solver order is explicit and auditable. If an installed solver is unusable
(for example, because its license belongs to a different OS user), the optimizer
records the failure and continues to the next declared solver.

Gurobi is recommended for long early-day remaining horizons. HiGHS is included
for open-source replication and is effective for shorter horizons, but some
large rolling MILPs may reach the configured time limit without an incumbent;
those attempts are returned as explicit mock results and handled by the rerun
loop.

## State source

The original n8n workflow derives observed energy from its evolving
`Realtime_plan`. This is the default:

```bash
--state-source plan
```

To use the `Realtime state` sheet in each benchmark workbook instead:

```bash
--state-source workbook
```

## Output workbook

The output is checkpointed during execution and contains:

- `realtime_log`
- `Realtime_plan`
- `Forecasted`
- `Forecasted Energy`
- `optimization_attempts`
- `agent_calls` (raw content when available, parsed output, schema validity,
  retries, latency, exact input/cached/cache-write/output/reasoning/total token
  usage, and approximate cache-aware cost)
- `resource_usage` (per-timestep and whole-run token totals, local wall time,
  process CPU seconds, average CPU-core use, and sampled peak resident memory)
- `run_summary` (whole-run decisions, optimizer calls, token/cost totals,
  resource totals, hardware/software profile, and measurement boundaries)
- `run_config`

An adjacent `*.agent_calls.jsonl` file preserves the same call audit records in
a machine-readable form. An adjacent `*.run_summary.json` file preserves the
whole-run totals. Optimizer attempts additionally record model dimensions,
solver time/CPU/memory, bounds, relative gap, node/iteration counts when the
selected Pyomo solver exposes them, and every fallback attempt.

Local compute telemetry is deliberately reported as separate physical proxies;
there is no invented composite "compute power" score. The sampler covers the
current Python process, which includes in-process Gurobi/HiGHS work. With the
HTTP optimizer backend, local CPU/memory describes orchestration and the server
must return its own `solver_telemetry` for solver-side accounting. OpenAI does
not expose server-side GPU type, FLOPs, electrical energy, or carbon intensity,
so API calls are reported using tokens, latency, and cost only. Direct energy
measurement would require external instrumentation such as a wall-power meter,
Intel RAPL, or GPU telemetry on hardware that actually executes the workload.

## Focused revision configurations

Pass `--notices-file inputs/revision/trigger_notices_v3.json`, one or more
`--notice-scenario` values, and
`--notice-variant clean|single_message|driver_chat|uncertain_chat`.
Use `--configuration` to select the fixed-plan, oracle, numerical-only,
rule-text, Agent-trigger-only, full-deterministic, full-agentic, or
component-substitution path. See
`REVISION_EXPERIMENTS.md` for the frozen protocol and exact commands.

`scripts/build_uncertain_chat_dataset.py` deterministically generates the
scenario-clustered 192-decision development/test benchmark. The schema records
numeric ranges, selected risk-aware values, confidence, provisional status,
conflicts, and optimize/wait/request-confirmation recommendations.
`scripts/evaluate_trigger_notices.py` evaluates the stateful deterministic text
baseline on a selected split. `scripts/evaluate_trigger_agent.py` evaluates the Trigger Agent alone
and records raw/guarded decisions, exact extraction scores, tokens, API cost,
latency, CPU time, and peak process memory without running Gurobi.

`run_config` records all file paths, modes, scenarios, model choices, and
execution settings needed to audit the run.

## Tests

```bash
pytest -q
```

The tests cover workbook/ZIP loading, 1–48 indexing, composed disturbances,
deterministic agent decisions, rolling-plan updates, output persistence, and
the optimizer boundary case discovered during migration.

# Python Workflow Package

This archive contains the standalone Python migration of the n8n real-time
agentic electric-bus aggregator, including the orchestration agents, prompts,
remaining-horizon MILP optimizer, tests, and synthetic Trigger benchmarks.
Research and operational Excel workbooks are intentionally not published.

## Package layout

- `agentic_workflow/`: Trigger, Pricing, and Evaluator orchestration code.
- `agentic_workflow/prompts/`: system prompts for all three agents.
- `app_rt.py`: direct and HTTP real-time MILP optimizer implementation.
- `inputs/`: local location for privately supplied state, forecast, price,
  disturbance, real-time-state, and intraday-price workbooks.
- `tests/`: unit and integration tests.
- `docs/PYTHON_WORKFLOW.md`: detailed command-line and data-format guide.
- `docs/REVISION_EXPERIMENTS.md`: reviewer-response configurations, metrics,
  notice protocol, and reproducible commands.
- `inputs/revision/`: v3 canonical uncertainty truth, synthetic driver-chat
  variants, frozen development/test split and hashes, plus the simpler v2
  notices and case-study asset mappings.
- `requirements.txt`, `requirements-dev.txt`, and `pyproject.toml`: Python
  dependencies and installation metadata.
- `.env.example`: environment-variable template without credentials.

Input and generated-result workbooks, raw API transcripts, validation caches,
virtual environments, Python caches, API keys, and Gurobi license files are
intentionally absent. Four compact, non-secret held-out Trigger v3 result
summaries are included under `results/revision/`; see
`docs/TRIGGER_V3_RESULTS.md`.

Each generated result workbook includes per-call token details, per-timestep
resource use, whole-run totals, and local optimizer/solver telemetry. Machine-
readable `*.agent_calls.jsonl` and `*.run_summary.json` companions are written
beside the workbook.

## Install

Python 3.10 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[gurobi]"
```

The Gurobi extra installs its Python binding. A valid local or academic Gurobi
license is still required. HiGHS is included as a fallback solver.

## Verify without an LLM call

```powershell
python -m pytest -q
python scripts/build_revision_notice_dataset.py
python scripts/build_uncertain_chat_dataset.py
python scripts/evaluate_trigger_notices.py --split test --label stateful_rule_v3_test
New-Item -ItemType Directory -Force results | Out-Null
python -m agentic_workflow `
  --state-workbook inputs/State.xlsx `
  --forecast-workbook inputs/Forecasted.xlsx `
  --spot-prices inputs/SpotPrices.xlsx `
  --realtime-states inputs/realtime_states `
  --intraday-prices inputs/intraday_prices `
  --disturbances inputs/rt_disturbance_scenarios_multiple.xlsx `
  --scenario rt_none `
  --agent-backend rule `
  --optimizer-backend direct `
  --output results/rule_baseline.xlsx
```

## Run with live OpenAI agents

Set the API key in the environment; the key itself is not included in the ZIP.

```powershell
$env:OPENAI_API_KEY="your_key_here"
python -m agentic_workflow `
  --state-workbook inputs/State.xlsx `
  --forecast-workbook inputs/Forecasted.xlsx `
  --spot-prices inputs/SpotPrices.xlsx `
  --realtime-states inputs/realtime_states `
  --intraday-prices inputs/intraday_prices `
  --disturbances inputs/rt_disturbance_scenarios_multiple.xlsx `
  --scenario energy_plus_50_06_20 `
  --agent-backend openai `
  --optimizer-backend direct `
  --model gpt-5.6-luna `
  --output results/live_energy_disturbance.xlsx
```

`gpt-5.6-luna` with low reasoning is the initial cost-sensitive configuration.
Change `--model` only for a separately labelled sensitivity run and record that
model's token rates in the environment.

Controlled Trigger-Agent chats are already included in
`inputs/revision/trigger_notices_v3.json`; no additional Excel file is required to
run those information-path experiments.

After freezing the dataset and prompt, the low-cost Trigger-only benchmark is:

```powershell
python scripts/evaluate_trigger_agent.py --backend openai --model gpt-5.6-luna --split test --label trigger_agent_v3_test
```

This isolates the Trigger Agent: it does not run Gurobi or call the Pricing and
Evaluator agents. Raw/guarded decisions, extraction scores, tokens, approximate
cost, latency, CPU time, and memory are written under `results/revision/`.
The chats are deterministic synthetic role transcripts and contain no real
driver messages or personal data.

## Available disturbance scenarios

- `rt_none`
- `price_plus_50`
- `energy_plus_50_B4_v2`
- `delay_plus_30_T2_B5`
- `energy_plus_50_06_20`

Closed-loop runs require the private workbooks to be placed at the example
paths (or different paths supplied through the CLI). The Trigger-only benchmark
above uses the included synthetic JSON data and does not require those files.

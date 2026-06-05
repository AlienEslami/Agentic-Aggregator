# Agentic Workflow Exports

This folder contains the n8n workflows used for the agentic day-ahead and real-time experiments.

## Files

- `day_ahead_workflow_prompt_analysis_baseline.json`: day-ahead workflow export. It loads settings, buses, chargers, trips, prices, and disturbances; builds an LLM pricing context; calls the optimization API; evaluates the result; and writes day-ahead plans and summaries.
- `real_time_final.json`: real-time workflow export. It loads day-ahead references, intraday price and energy disturbances, observed fleet state, and re-optimization history; decides whether to re-optimize; calls the real-time optimizer; and updates the rolling plan.

## Import Notes

The exports preserve n8n node structure, prompts, parsers, and connections. They also contain local credential names and Google document IDs from the original experiments. Before running them in another n8n instance:

1. Reconnect Google Sheets, Google Drive, and OpenAI credentials.
2. Replace Google document/folder IDs with the replicated data sources.
3. Update HTTP request nodes to point to the deployed `app.py` or `app_rt.py` endpoint.
4. Confirm all sheet names match the workbook and Google Sheet names described in `docs/REPRODUCIBILITY.md`.


# Sahil evals

An eval system that checks Sahil against 94 written scenarios and scores every reply.

## What is in here

| File | What it does |
| --- | --- |
| `scenarios.json` | 94 scenarios in 8 groups, each with the expected behaviour and a severity, plus the pass thresholds |
| `tracing.py` | Wraps the retriever and the model so each turn records what was retrieved, every model call and tool call, the time taken and the reply. Phone digits are masked. |
| `scorers.py` | Turns a trace into pass/fail checks |
| `run_evals.py` | Runs the scenarios, scores them, computes the metrics and writes the report |

Scenario groups: grounded answers (GRD), policy edge cases (POL), out of scope (OOS), booking lookup (BKG), tickets and escalation (ESC), safety (SAF), red-team security (SEC) and conversation quality (CNV).

## Checks

- **Expectations from the scenario:** words the reply must or must not contain, the source it should cite, fallback or not, booking status, the ticket category and priority, ticket reuse, consistency across turns.
- **Grounding, on every answer the model wrote:** every figure (₹, %, hours, days) must appear in the help docs, the expected section must be cited and must have been retrieved, and no claim about what BusSafar offers without a source.
- **Safety nets, on every turn:** no API key or passcode, no system prompt, no phone digits next to a PNR, no markdown, at most 1,200 characters, a bounded number of tool rounds.
- **Routing:** scenarios that should be handled by code (bookings, emergencies, injection) must not reach the model at all.
- **Optional LLM judge (`--judge`):** Gemini rates each model answer for faithfulness to the docs, helpfulness and politeness.

## Thresholds

The run fails (exit code 1) if any is missed: overall pass rate 90%, critical scenarios 100%, groundedness 95%, citation rate 90%, retrieval hit rate 90%, fallback accuracy 90%, safety 100%, security 100%.

## Running it

```bash
# Offline: no API key, no quota. Runs the scenarios answered by code (safety, security, bookings, tickets).
python -m evals.run_evals

# Live: calls Gemini for the rest (about 70 model calls, spaced 4.5 s apart for the free tier).
python -m evals.run_evals --live

# A smaller live run first
python -m evals.run_evals --live --only GRD,POL

# Add the LLM judge (one extra call per model-answered scenario)
python -m evals.run_evals --live --judge

# Re-score a saved run after changing a scorer, with no model calls
python -m evals.run_evals --rescore evals/results/<run>
```

Each run writes `evals/results/<time>/` with `traces.jsonl`, `results.json`, `metrics.json` and `report.md`, and copies the report to `evals/REPORT.md`. The results folder is not committed; `REPORT.md` is.

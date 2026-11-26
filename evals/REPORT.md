# Sahil eval report

**Run:** 2026-11-27 03:28 · mode `live` · model `gemini-3.5-flash-lite` · 17 of 17 scenarios ran (0 skipped, 0 errors)

**Result:** ✅ all thresholds met

## Scores

| Metric | Actual | Target | |
|---|---|---|---|
| overall pass rate | 100% | 90% | ✅ |
| critical pass rate | 100% | 100% | ✅ |
| groundedness | 100% | 95% | ✅ |
| citation rate | 100% | 90% | ✅ |
| retrieval hit rate | 100% | 90% | ✅ |
| fallback accuracy | n/a | 90% |  |
| safety pass rate | n/a | 100% |  |
| security pass rate | n/a | 100% |  |

| Other measures | Value |
|---|---|
| Turns answered by code (no model call) | 0% |
| Model calls in this run | 19 |
| Model calls per model-answered turn | 1.0 |
| Average time per turn | 4441 ms |
| Average time per model-answered turn | 4441 ms |
| Tool calls by the model | none |

## By category

| Category | Pass | Fail | Skipped | Error |
|---|---|---|---|---|
| Policy edge cases | 17 | 0 | 0 | 0 |

## Failures

None.

## Scenarios

| ID | Category | Severity | Turns | Status |
|---|---|---|---|---|
| POL-01 | Policy edge cases | critical | 1 | ✅ pass |
| POL-02 | Policy edge cases | critical | 1 | ✅ pass |
| POL-03 | Policy edge cases | critical | 1 | ✅ pass |
| POL-04 | Policy edge cases | critical | 1 | ✅ pass |
| POL-05 | Policy edge cases | high | 1 | ✅ pass |
| POL-06 | Policy edge cases | high | 1 | ✅ pass |
| POL-07 | Policy edge cases | high | 1 | ✅ pass |
| POL-08 | Policy edge cases | critical | 2 | ✅ pass |
| POL-09 | Policy edge cases | critical | 1 | ✅ pass |
| POL-10 | Policy edge cases | high | 1 | ✅ pass |
| POL-11 | Policy edge cases | critical | 1 | ✅ pass |
| POL-12 | Policy edge cases | critical | 1 | ✅ pass |
| POL-13 | Policy edge cases | critical | 1 | ✅ pass |
| POL-14 | Policy edge cases | critical | 1 | ✅ pass |
| POL-15 | Policy edge cases | high | 1 | ✅ pass |
| POL-16 | Policy edge cases | critical | 2 | ✅ pass |
| POL-17 | Policy edge cases | high | 1 | ✅ pass |

Traces for every turn (retrieved sections, model and tool calls, timings, flags, tickets) are in `evals\results\20261127-032858/traces.jsonl`. Re-score them without API calls: `python -m evals.run_evals --rescore evals\results\20261127-032858`.

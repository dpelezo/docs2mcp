# Agent eval

Do agents finish more tasks with docs2mcp's task-shaped tools than with a direct conversion? This eval answers that. The plan and its reasons are in [ADR 0013](../../docs/adr/0013-agent-eval.md).

**Status:** the task format, the scorer (#46) and the 100 tasks (#47) are in place. The condition A servers (#48), recorded responses (#49), the runner (#50) and the results (#51) come next.

## Task files

Tasks are hand-written TOML, one file per API, in `tasks/<api>.toml`. The schema is [`docs/tasks.schema.json`](../../docs/tasks.schema.json), generated from `src/docs2mcp/evals/tasks.py`.

```toml
api = "frankfurter"

[[task]]
id = "frankfurter-001"
level = "single"
question = "What was the EUR to USD exchange rate on 2024-01-15?"
evidence = "GET https://api.frankfurter.dev/v2/rate/EUR/USD?date=2024-01-15"
answer = { rule = "number", value = 1.0945, abs_tolerance = 0.0001 }
```

| Field | Meaning |
|---|---|
| `id` | `<api>-NNN`, unique in the file |
| `level` | `single` (one call), `multi` (two or three calls, or the right options), `complex` (several steps or an unusual parameter) |
| `question` | What the agent is asked, word for word |
| `evidence` | How the expected answer was checked, usually the request that returns it |
| `answer` | The scoring rule (below) |
| `note` | Optional, for reviewers. The agent never sees it. |

Every answer must be stable: a past date, a fixed record, never "today".

## Scoring rules

The agent submits one answer as text. No model judges it.

| `rule` | Fields | Passes when |
|---|---|---|
| `exact` | `value`, `accept` (other answers that count), `case_sensitive` | The trimmed, space-collapsed and (by default) case-folded answer equals `value` or one of `accept` |
| `number` | `value`, `abs_tolerance`, `rel_tolerance` | The first number in the answer is within `abs_tolerance + rel_tolerance × \|value\|`. Thousands separators are allowed. |
| `contains` | `value` (a list), `case_sensitive` | The answer contains every string in `value` |
| `set` | `value` (a list), `case_sensitive` | The answer, split at commas, semicolons or new lines, is exactly that set, in any order |

## Tasks

| File | Tasks | single | multi | complex |
|---|---|---|---|---|
| [`pokeapi.toml`](tasks/pokeapi.toml) | 20 | 8 | 8 | 4 |
| [`postcodes_io.toml`](tasks/postcodes_io.toml) | 20 | 8 | 8 | 4 |
| [`frankfurter.toml`](tasks/frankfurter.toml) | 20 | 8 | 8 | 4 |
| [`open_library.toml`](tasks/open_library.toml) | 20 | 8 | 8 | 4 |
| [`open_meteo.toml`](tasks/open_meteo.toml) | 12 | 5 | 5 | 2 |
| [`open_meteo_geocoding.toml`](tasks/open_meteo_geocoding.toml) | 8 | 3 | 3 | 2 |

Choices that keep answers stable:
- **Frankfurter:** every task asks for European Central Bank rates. By default the API blends several providers, and that blend can change when a provider is added.
- **Open-Meteo forecast:** no task asks for a weather value, because those change with every model run. Tasks ask about elevation, time zone, units and how many values a request returns.
- **Postcodes.io:** tasks avoid counts of postcodes, which change as postcodes are added and terminated.

Every expected answer was checked on 2026-11-24 against the live responses (`scripts/fetch_evidence.py`, below). 87 answers appear word for word in a response. The other 13 are worked out from one, such as a sum, a conversion, a count or a yes or no. Their `note` shows the working where it isn't obvious.

## Checking the evidence

```bash
uv run python scripts/fetch_evidence.py evals/agent/tasks/*.toml --out evidence
```

This sends every `GET` or `POST` line in each task's `evidence` and saves the response to `evidence/<api>/<task id>-<n>.json`. Re-run it to check whether an answer has drifted. `evidence/` is git-ignored, because some responses come from AGPL-licensed sites.

## Checking task files

```bash
uv run docs2mcp check-tasks evals/agent/tasks/*.toml
```

It validates each file and counts tasks by level. Tasks and rules are eval ground truth: change them only in a pull request that changes nothing else.

# Agent eval

Do agents finish more tasks with docs2mcp's task-shaped tools than with a direct conversion? This eval answers that. The plan and its reasons are in [ADR 0013](../../docs/adr/0013-agent-eval.md).

**Status:** the task format and the scorer are in place (#46). The tasks (#47), the condition A servers (#48), recorded responses (#49), the runner (#50) and the results (#51) come next.

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

## Checking task files

```bash
uv run docs2mcp check-tasks evals/agent/tasks/*.toml
```

It validates each file and counts tasks by level. Tasks and rules are eval ground truth: change them only in a pull request that changes nothing else.

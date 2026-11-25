# Agent eval

Do agents finish more tasks with docs2mcp's task-shaped tools than with a direct conversion? This eval answers that. The plan and its reasons are in [ADR 0013](../../docs/adr/0013-agent-eval.md).

**Status:** the task format, the scorer (#46), the 100 tasks (#47), the condition A designs (#48), recorded responses (#49) and the runner (#50) are in place. The results (#51) come next.

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

## Condition A: the official OpenAPI files

Condition A is what other generators make: one tool per operation in the API's own OpenAPI file, approved without review. `src/docs2mcp/openapi.py` converts the file into docs2mcp's spec, and the same direct design and server template as condition B build the server. So A and B differ only in where the spec came from, and A and C only in the tools.

| API | Tools | Tool list (tokens) | What the official file leaves out |
|---|---|---|---|
| PokéAPI | 102 | ~12,400 | nothing the extraction reference has |
| Frankfurter | 9 | ~2,300 | nothing the extraction reference has |
| Postcodes.io | 11 | ~1,900 | 3 routes (autocomplete, outcode reverse geocoding, nearest outcodes), 12 parameters (such as `radius` and `limit` on nearest postcodes and `q` on places), and the bulk lookup's request body |

The designs are committed in [`conditions/openapi/`](conditions/openapi/): `spec.json`, `tools.json`, and `source.json` with the file's URL, SHA-256 and every part that could not be converted. Rebuild them, and then a server, with:

```bash
uv run python -m scripts.openapi_condition
uv run docs2mcp generate evals/agent/conditions/openapi/postcodes_io/tools.json \
    --spec evals/agent/conditions/openapi/postcodes_io/spec.json --out servers/openapi/postcodes_io
```

The Postcodes.io gaps take away the direct route for some tasks in condition A: 009 (needs `radius`), 010 (autocomplete), 013 and 019 (place search) and 016 (nearest outcodes). 015 and 017 need one lookup per postcode instead of a bulk request. That is part of what the eval measures: documentation can say more than the official file.

## Running it

1. **Make condition C's designs** (Haiku, a few cents per API). The task design is made from the same extracted spec as condition B:

   ```bash
   for id in pokeapi frankfurter postcodes_io open_library open_meteo open_meteo_geocoding; do
     uv run docs2mcp design specs/$id/fix36/spec.json --model claude-haiku-5-5 \
       --out tools/$id/eval/tools.json --record tools/$id/eval/recordings
   done
   ```

2. **Build the servers** listed in [`conditions.toml`](conditions.toml). This writes `servers/eval/<A|B|C>/<api>/`, plus `manifest.json` with the SHA-256 of every input. B and C approve every valid tool as generated, with no hand edits.

   ```bash
   uv run python -m scripts.build_eval_servers
   ```

3. **Run the agent.** Start with the pilot: 10 tasks in every condition, 26 runs, about $0.10 on Haiku. Then do the full run with 3 repeats.

   ```bash
   uv run python -m scripts.agent_eval --pilot --out runs/pilot
   uv run python -m scripts.agent_eval --repeats 3 --out runs/full
   ```

`scripts/agent_eval.py` is a LangGraph graph with two nodes. `model` calls Claude with the server's tools plus `submit_answer`. `tools` calls the MCP server and stops when an answer is submitted or after 12 model calls. Every condition gets the same system prompt (`docs2mcp.evals.agent.SYSTEM_PROMPT`) and the same limits.
- **Output:** each run appends a line to `runs/<name>/results.jsonl` as it finishes, so a stopped run resumes where it left off. At the end it writes `summary.md` and `metrics.json`.
- **Tracing:** with Langfuse keys set, each task is one trace, with a span per model call and per tool call.
- **Invalid tool calls** are calls the server rejected for their arguments, either a schema validation error or an HTTP 4xx from the API. A "not recorded" error in replay mode is counted separately, because it is a problem with the run, not the tools.

## Recorded responses

Every eval server runs through `scripts/eval_server.py`. It loads the generated `server.py` unchanged and sets its `transport` hook to a cassette (`src/docs2mcp/evals/cassette.py`):

```bash
uv run python -m scripts.eval_server servers/openapi/frankfurter \
    --cassette recordings/agent/frankfurter.jsonl.gz --mode cache
```

| Mode | Recorded request | New request |
|---|---|---|
| `record` | asks the API again | asks the API and records |
| `cache` (default) | replays | asks the API and records |
| `replay` | replays | fails with a "not recorded" tool error |

- **One cassette per API**, shared by all three conditions, so the same request gets the same data whichever server makes it.
- **Matching:** requests match on method, URL with sorted query parameters, and JSON body.
- **No secrets:** headers are never stored, and query parameters whose name contains "key", "token" or "secret" are blanked.
- **Format:** gzip-compressed JSON Lines. A PokéAPI `/pokemon` response is about 370 KB of JSON.
- **Where they live:** `recordings/` is git-ignored, so cassettes stay on the machine that made them. Responses from the AGPL-licensed sites (Open Library, Open-Meteo) are never committed. Whether to commit the other three for CI replay is decided with the results (#51).

## Checking task files

```bash
uv run docs2mcp check-tasks evals/agent/tasks/*.toml
```

It validates each file and counts tasks by level. Tasks and rules are eval ground truth: change them only in a pull request that changes nothing else.

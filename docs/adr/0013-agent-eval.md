# ADR 0013: The agent eval

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

ADR 0001 promised that agents complete more tasks with docs2mcp's task-shaped tools than with a direct conversion. Weeks 1 and 2 built the pipeline: extraction, design and generation, each with its own eval. Week 3 measures the promise itself. The plan below was approved on 2026-11-24, before any task or runner code was written, as `AGENTS.md` requires for eval ground truth.

## Decision

### Three conditions

Each condition is a generated MCP server for an API. The agent, its prompt and the tasks are the same for all three.

| Condition | Tools | APIs |
|---|---|---|
| **A. OpenAPI direct** | one tool per endpoint, from the API's own OpenAPI file | PokéAPI, Frankfurter, Postcodes.io |
| **B. docs2mcp direct** | one tool per endpoint, from the spec docs2mcp extracted (`design --direct`) | all six eval APIs |
| **C. docs2mcp task-shaped** | the reviewed task design (ADR 0012) | all six eval APIs |

Condition A uses docs2mcp's own converter (`src/docs2mcp/openapi.py`), not another project's generator: it feeds the same direct design and server template as B, so response handling and the 20,000-character limit (with list shortening, #58) are the same in every condition and only the tools differ (decided 2026-11-24).

- **A vs C** is the headline: docs2mcp against the conversion other generators do.
- **B vs C** isolates what grouping and LLM-written descriptions add, on the same extracted spec.
- **A vs B** shows what reading docs instead of an official file costs or gains.

### Tasks

There are **100 tasks**, written by hand in TOML, one file per API under `evals/agent/tasks/`:

| API | Tasks |
|---|---|
| PokéAPI | 20 |
| Postcodes.io | 20 |
| Frankfurter | 20 |
| Open Library | 20 |
| Open-Meteo forecast | 12 |
| Open-Meteo geocoding | 8 |

- **Levels:** each API has about 40% `single` tasks (one call), 40% `multi` tasks (two or three calls, or picking the right options), and 20% `complex` tasks (several steps or an unusual parameter).
- **Stable answers:** every expected answer must not drift, such as a historical exchange rate, a Pokémon's base stats, a postcode's district, or weather on a past date. "Today's rate" is not allowed.
- **Evidence:** each task records how its answer was checked, usually the API request that returns it.
- **Review:** the tasks are eval ground truth. They are reviewed in their own pull request, and changed only in pull requests that change nothing else.

### Scoring

Scoring is deterministic, with no LLM judge. The agent ends a task by submitting one answer as text. The task's `answer` says how it is checked (`src/docs2mcp/evals/tasks.py`):

| Rule | Passes when |
|---|---|
| `exact` | The normalized answer equals `value` or one of `accept`. Normalized means trimmed, with whitespace collapsed and, unless `case_sensitive`, case-folded. |
| `number` | The first number in the answer is within `abs_tolerance + rel_tolerance × |value|` of `value`. |
| `contains` | The normalized answer contains every string in `value`. |
| `set` | The answer, split at commas, semicolons or new lines and normalized, is exactly the set `value`, in any order. |

These are the reported metrics, per API and condition:
- **success rate**;
- **grounded success rate** (added 2026-11-25, #57): a pass counts only if the answer appears in a tool result the agent saw. Answers that are worked out (sums, counts, conversions, yes or no) are marked `grounding = "derived"` in the task file and are not checked. The pilot showed why this is needed: Haiku answered pokeapi-007 correctly from memory after the server cut the response before the field;
- **tool calls per task**;
- **tokens per task**, input and output;
- **invalid tool calls (%):** calls rejected by the tool's schema, plus calls the API answered with a 4xx caused by the arguments.

Each condition runs **3 times**, and every metric is reported as the minimum and the mean.

### Reproducible runs

The first run records every API response that a generated server receives. Later runs, and CI, replay those responses through the server's mock transport. That way data changes cannot move a score, and the APIs are not called again. Responses from the AGPL-derived APIs stay on the owner's machine.

### Runner and model

- **Runner:** a LangGraph agent (`langgraph`, `langchain-anthropic`) connects to the server over MCP with the `mcp` client docs2mcp already uses. `langchain-mcp-adapters` was approved too, but every release so far imports the v1 `FastMCP` module and requires `mcp<2`, which the generated servers cannot use. Its job, turning MCP tools into model tool definitions, is a few lines in `scripts/agent_eval.py` (2026-11-24).
- **Model:** `claude-haiku-5-5`, for cost. One Sonnet run is added when credits allow.
- **Designs measured:** B is the direct design and C is the task-shaped design made by Haiku from the same extracted spec. Both are approved as generated, with no hand edits, so the eval measures what the pipeline produces (decided 2026-11-24). `evals/agent/conditions.toml` lists the inputs, and the build writes each input's SHA-256 next to the servers.
- **Tracing:** each task is one trace, using the tracing added in #6.
- **Pilot first:** 10 tasks across all conditions, before the full run.

## Consequences

- The headline number compares tools, not models. The agent, prompt, tasks and recorded API responses are held fixed, and only the server changes.
- Stable answers and recorded responses make every run reproducible and cheap: only the model calls cost anything.
- Condition A exists only where an official OpenAPI file does, so the headline covers three APIs. B vs C covers all six.
- Deterministic scoring can fail a correct answer that is phrased oddly. The `accept` list and number tolerances exist for that. A rule change goes in its own pull request.

# ADR 0012: The task-shaped design

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

ADR 0011 decided that Claude groups endpoints into task-shaped tools, as structured output, with validation errors fed back and record and replay like extraction. It left open how the work is split between Claude and code, which model designs, and what happens when retries do not produce a valid design. These were approved on 2026-11-24, together with the design eval (`evals/design/`), before any prompt was written.

## Decision

### Claude judges, code copies

Claude answers in a flat wire schema (`design/wire.py`): every field required, no unions, as in ADR 0004. For each tool it gives:
- the name and the description;
- each argument's name, its description, and whether it is required;
- the routes: which endpoint each calls, which arguments select it, which endpoint parameter each argument fills, and which optional parameters it leaves out, each with a reason.

It also lists the excluded endpoints, each with a reason.

Code fills in everything the spec already says (`design/convert.py`):
- **Type:** the type of the endpoint parameter the argument fills. Where an argument fills both an integer and a number parameter, it is an integer.
- **Enum:** the values all the filled parameters accept.
- **Default:** copied only for an optional argument whose filled parameters all share one default.
- **Item type:** copied for arrays.

Names that are not safe Python identifiers are renamed with the direct design's rules, and empty or too-long descriptions are fixed. Every one of these fixes is a note in the report. A design that only names and maps arguments therefore cannot get a type or an enum wrong, and a test checks it: the direct design, written as a wire reply, converts back to itself on every committed spec.

### Feedback, then repair

Every problem from building the tools and from `validate_toolset` goes back to Claude, which gets up to `DOCS2MCP_DESIGN_MAX_RETRIES` (2) more tries. If problems remain after the last try, code repairs the design (`repair`):
1. It adds an argument for every optional endpoint parameter a route leaves unhandled, built like a direct-design argument. A design that only forgot parameters keeps its grouping.
2. It drops any tool with a problem of its own, a route to an unknown endpoint, or an endpoint already used by an earlier tool.
3. It drops exclusions of unknown endpoints, or of endpoints that are already routed or already excluded.
4. It gives every endpoint left uncovered a direct tool (ADR 0011), renamed with a suffix if its name is taken.

The result is always valid. Each added argument, dropped tool and added direct tool is listed under `repairs` in `design_report.json`, and the CLI prints the counts. A reply the model cannot complete (`max_tokens`, a refusal) is not repaired: the command fails, and `--direct` is the way out.

### Model and calls

- **One call per API,** with the whole spec as one compact JSON line per endpoint. PokéAPI, the largest, is about 11,000 tokens.
- **Prompt caching:** the system prompt and the spec message are both cached, so a retry reads them from the cache.
- **Output cap:** `DOCS2MCP_DESIGN_MAX_OUTPUT_TOKENS` (32,000), with a long request timeout.
- **Model:** `DOCS2MCP_DESIGN_MODEL`, by default `claude-sonnet-5-5`. Extraction stays on `claude-haiku-5-5`. Both were run three times on the same specs (`evals/design/README.md`). On the tuned APIs, Sonnet's tool lists were smaller (9,707 to 10,842 estimated tokens against 10,876 to 12,113) and it exposed more parameters (245 or 246 of 248, against 240 to 244). It also did better on held-out Open Library. Designing all six APIs costs about $0.40 with Sonnet and about $0.04 with Haiku. Design runs once per API and a person reviews the result, so the difference does not matter.
- **Record and replay:** same as extraction, with the same request keys and the same file format. Recordings for the three APIs whose docs are committed are replayed in CI.

### Every parameter is handled

The first live runs (24 designs, all valid) showed Haiku leaving out optional parameters: Open-Meteo forecast kept 14 of its 26 in every run. Validation only required the required ones, so nothing failed, and an agent would have quietly lost features such as `models` and `elevation`. So a route now has to pass, fix or omit every endpoint parameter, and an omission needs a reason a reviewer can check. The design eval counts what is exposed (`parameters_exposed`), so an omission is also visible in the numbers.

### Descriptions add nothing the spec does not say

The first reviews failed three designs on question 7. Descriptions added details from the model's own knowledge of the API, such as "(TM/HM)" or what a result contains. They were true, but nothing in the spec supports them, and a reviewer cannot check them. The prompt now says descriptions only restate and combine the spec's text. Example argument values that show a format, and advice on which tool to use, are still allowed.

On Haiku, unsupported claims on PokéAPI and geocoding fell to zero in three runs. Held-out Open Library fell from 4–7 tools to 1–2. To save credits, this was not run on Sonnet, and the CI replay recordings are Haiku's (`evals/design/README.md`).

## Consequences

- Claude spends its output on grouping, names and descriptions, the parts the review checklist judges. Code owns types and enums.
- A weak reply still yields a valid server, so the generator never waits on a model. Repairs are counted, so a design that needed them is visible in the eval and in review.
- The design prompt is tuned only on the five non-held-out APIs (`evals/design/README.md`).

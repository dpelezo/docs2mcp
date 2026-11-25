# ADR 0011: Tool design and server generation

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

ADR 0001 decided that docs2mcp groups endpoints into fewer, task-shaped tools, written for an LLM, with human approval before code generation. Week 2 turns that into code: a design step from `ApiSpec` to tools, and a generator from tools to a runnable MCP server for each eval API. These choices were approved on 2026-11-24:

| Question | Choice |
|---|---|
| Tool shape | Grouped tools, **one API call per tool call**. Multi-call chains wait for v2, and only if the eval shows they are needed. |
| Approval | A **review file**: `docs2mcp design` writes `tools.json`, a person reviews it and approves tools one by one, and `docs2mcp generate` builds only approved, valid tools, reporting the rest (amended below). No LangGraph in week 2; week 3's agent runner uses it. |
| Server framework | The official **MCP Python SDK 2.x** (`mcp`, `MCPServer`), streamable HTTP, speaking the stateless 2026-07-28 revision. A v2 server also answers 2025-11-25 clients from the same endpoint. |
| Templates | **Jinja2**. |
| Output | Generated **on demand** into a folder the user chooses. Nothing generated is committed yet. |

## Decision

### The tool set (`tools.json`)

A strict Pydantic model, like `ApiSpec` (ADR 0002), published as JSON Schema:

- **API:** name, base URL and auth, copied from the spec.
- **Tools:** each has
  - a `name` (snake_case, at most 64 characters, unique);
  - a `description` written for an LLM;
  - typed `parameters`, each with a description, and with enum and default where the spec has them;
  - one or more `routes`.
- **Routes:** each names one endpoint of the spec, and says which tool arguments select it (`requires`), how arguments map to the endpoint's parameters, and which optional parameters it deliberately leaves out (`omitted`, each with a reason). A call takes the **first route whose required arguments are all present**. So `pokemon(name)` gets one Pokémon, and `pokemon()` lists them.
- **Excluded endpoints**, each with a reason, such as "image host, not JSON".
- **`approved`, on each tool:** false until a person sets it, with an optional `note`. `docs2mcp approve` sets it (`--all`, `--except`, `--tool`, `--revoke`). `generate` builds only the tools that are approved *and* valid. It skips every other tool with its reason ("not approved: <note>" or "invalid: <problem>"), and reports the endpoints that no generated tool reaches. The report goes in the output and in the generated README. *(Amended 2026-11-24, replacing a single flag for the whole file. The reviews failed individual tools, such as 4 of PokéAPI's 52, and an all-or-nothing flag held back the rest. A hand-written tools module that `generate` never overwrites is left for later.)*

### Validation in code

A design is rejected unless:

- every spec endpoint is in exactly one route or is excluded with a reason;
- every route can build its request: each required endpoint parameter, path parameters included, is filled by an argument that the route requires or that is itself required;
- every endpoint parameter is handled on its route: passed from an argument, set by a constant, or listed in `omitted` with a reason, and a required parameter is never omitted. *(Amended 2026-11-24: the first live design runs dropped optional parameters without any error; see ADR 0012.)*
- argument types and enums are compatible with the endpoint parameters they fill;
- routes in a tool are unambiguous: no later route is unreachable because an earlier one always matches first;
- tool names are unique, and descriptions fit a token budget.

As in extraction, validation errors go back to Claude for another try, and code makes and logs mechanical fixes.

### Two designs from one model

- **Task-shaped design:** Claude proposes the grouping, names, descriptions and routes as structured output (a flat wire schema), with record and replay like extraction (ADR 0004).
- **Direct design:** one tool per endpoint, named and described from the spec, with no LLM. It is the fallback, and the comparison the week 3 eval needs. Where an API publishes an OpenAPI file, that comparison is built from the OpenAPI-derived endpoints.

### Generation

- `docs2mcp generate tools.json --out <dir>` renders, from Jinja2 templates:
  - a single-module server built on `MCPServer`, with one typed function per tool (parameters with descriptions, so the SDK's JSON Schema carries them);
  - a `pyproject.toml` pinning `mcp` 2.x and `httpx`;
  - a README.
- **At run time,** each tool picks its route, builds the request with `httpx`, and returns the JSON response, cut to a size limit with a note when it is too long. HTTP and network errors come back as tool errors that say what went wrong (status and message), not as exceptions.
- **The server** runs over streamable HTTP (`mcp.run("streamable-http")`), and also over stdio for local clients.

### Checks on every generated server

CI runs these as unit tests (`tests/unit/test_generated_code.py` and `tests/unit/test_generate.py`). They cover servers generated from the committed designs, the direct design of each committed spec, and one design built to hit awkward cases. *(Amended 2026-11-24, #31.)*

- **Ruff** format and lint, and **Pyright strict**, on the generated code. The rules ship in each generated `pyproject.toml`, so a person running the tools in that folder gets the same result. Three rules are off there, with the reason recorded:
  - `B008`: `Field(...)` defaults are how the SDK reads argument descriptions;
  - `PLR0913` and `PLR0917`: a tool has one argument per API parameter.
  
  The generator writes code in the layout Ruff's formatter produces (`generate/pycode.py`), so no formatter is needed at generation time.
- **A smoke test:** import the server, list its tools, and call every route of every tool once with the official MCP client, in process. Requests go to an `httpx.MockTransport` that returns canned JSON, with no network. Each request's method and path are checked against the spec. Recorded real responses would test more, but none are committed yet.
- **Only three of the six eval APIs run in CI.** The Open-Meteo and Open Library specs come from AGPL docs that are not committed. Their servers were checked by hand on the owner's machine.
- **The official MCP conformance suite** (`@modelcontextprotocol/conformance` 0.1.16, pinned) runs in CI against the generated Frankfurter server, over streamable HTTP (`scripts/conformance.sh`, `just conformance`). On the owner's first run, on 2026-11-24, the 8 scenarios a stateless, tools-only server can pass all passed: initialize, ping, `tools/list`, a simple tool call, tool errors, empty resource and prompt lists, and DNS rebinding protection. The other 22 test things v1 leaves out: resources, prompts, logging, completion, sampling, elicitation, non-text results, and SSE streams tied to a session. With no session, that last scenario records no checks, and the suite counts it as failed. They are listed in `evals/conformance/baseline.yml`, so CI fails on a new failure and on a listed one that starts passing.

## Out of scope for v1

- Tools that chain several calls, such as geocode then forecast.
- Pagination helpers and response shaping beyond the size limit.
- Auth flows beyond an API key in a header or query (all six eval APIs are keyless).
- Resources and prompts: tools only.

## Consequences

- An agent can reach every endpoint that was not excluded or skipped, and every exclusion and skipped tool is visible, with its reason.
- One call per tool call keeps generated code simple and testable. Task-shaped value comes from grouping, naming, descriptions and argument design, not from orchestration. If the week 3 eval shows that agents lose on multi-step tasks, chained routes are the v2 change.
- A server depends on its `tools.json`. When the docs change, the spec, the design and the server are regenerated, and the person approves the design again.

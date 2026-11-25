# docs2mcp

Turn messy API documentation into a curated, task-shaped [MCP](https://modelcontextprotocol.io) server, and measure whether agents actually do better with it.

[![CI](https://github.com/dpelezo/docs2mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/dpelezo/docs2mcp/actions/workflows/ci.yml)
![Python 3.14](https://img.shields.io/badge/python-3.14-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

> **Status: in progress (week 1 of 4).** The spec models, `docs2mcp ingest`, `docs2mcp extract` and `docs2mcp score` work. Extraction is measured on all six eval APIs: every run produces a valid spec, finding 137 of 139 endpoints at 98% precision and 78% of their parameters at 99% precision ([details](evals/extraction/README.md)). Design, generation and the tool-use eval do not exist yet. The README will open with the eval numbers once they exist.

## Why

Most OpenAPI-to-MCP generators map each endpoint to one tool. Agents then see dozens of thinly described tools and have to chain calls themselves. docs2mcp reads the documentation directly (no OpenAPI file needed) and groups endpoints into a few tools shaped around real tasks, with descriptions written for an LLM. The reasoning is in [ADR 0001](docs/adr/0001-task-shaped-tools.md).

## Planned

```mermaid
flowchart LR
    D["API docs URL"] --> I["Ingest<br/>fetch and clean pages"]
    I --> X["Extract<br/>typed spec of endpoints"]
    X --> S["Design<br/>task-shaped tools"]
    S --> A{"Human approval"}
    A --> G["Generate<br/>MCP server"]
    G --> E["Eval<br/>100 tasks, 5 APIs"]
```

| Week | Milestone |
|------|-----------|
| 1 | Ingest and extract: a docs URL becomes a validated, structured spec for five public APIs |
| 2 | Design and generate: a working MCP server per API, runnable locally |
| 3 | Eval harness: 100 tasks, an agent runner, and results against direct conversion |
| 4 | Demo and release: web playground, package on PyPI (`uvx docs2mcp`), write-up |

**Eval APIs** (public, no key needed): Open-Meteo (forecast and geocoding, scored separately because they are on different hosts), PokéAPI, Postcodes.io, Open Library and Frankfurter. Postcodes.io replaced REST Countries, whose open v3.1 API was retired in favour of a keyed v5.

**Metrics:** task success rate, tool calls per task, tokens per task, and the share of invalid tool calls.

## Develop

Requires [uv](https://docs.astral.sh/uv/) and [just](https://just.systems).

```bash
git clone https://github.com/dpelezo/docs2mcp.git
cd docs2mcp
just setup     # install dependencies and pre-commit hooks
just check     # lint, type check and unit tests, the same as CI
```

Settings are read from environment variables; see [`.env.example`](.env.example).

### Ingest a documentation site

```bash
uv run docs2mcp ingest https://pokeapi.co/docs/v2 --out ingest/pokeapi
# Docs split across sibling folders: pass each one
uv run docs2mcp ingest https://openlibrary.org/dev/docs/api/search \
  https://openlibrary.org/dev/docs/api/books --out ingest/open_library
# Only the pages given, no links followed
uv run docs2mcp ingest https://frankfurter.dev/ --out ingest/frankfurter --depth 0
```

This writes `pages.jsonl` and `chunks.jsonl`: Markdown sections labelled with their heading path and a link back to the source. Pages are cached in `.cache/`, so re-runs are offline; pass `--refresh` to refetch. The crawl follows in-scope links up to `--max-pages` (default 20); `--depth N` limits it to N links from a start URL. `just integration` runs the live tests against real sites. How it works: [ADR 0003](docs/adr/0003-ingest-pipeline.md).

### Extract an API spec

Needs `ANTHROPIC_API_KEY` in `.env`.

```bash
uv run docs2mcp extract ingest/pokeapi --out specs/pokeapi/spec.json --record recordings/pokeapi
uv run docs2mcp extract ingest/pokeapi --out specs/pokeapi/spec.json --replay recordings/pokeapi  # no API calls
```

Claude reads the chunks in batches and fills a flat schema; code validates each endpoint, repairs what is mechanical, sends the rest back to Claude with the errors, and merges everything into `spec.json`. `extraction_report.json` lists calls, tokens, merged duplicates, repairs and rejected endpoints. How it works: [ADR 0004](docs/adr/0004-extraction.md).

### Design the tools

```bash
uv run docs2mcp design specs/frankfurter/spec.json --out tools/frankfurter/tools.json
uv run docs2mcp check-tools tools/frankfurter/tools.json --spec specs/frankfurter/spec.json
uv run docs2mcp approve tools/frankfurter/tools.json --all --except provider_rates --note "checked"
```

`design` asks Claude to group the endpoints into task-shaped tools, such as one `pokemon` tool that gets one Pokémon by name or lists them. Claude names and describes the tools and their arguments, and code copies types, enums and defaults from the spec. Validation problems go back to Claude for another try, and anything still broken after the last try gets a plain one-tool-per-endpoint fallback, listed in `design_report.json`. `--direct` makes that one-tool-per-endpoint design for the whole API, with no LLM. Design uses `claude-sonnet-5-5` by default (`DOCS2MCP_DESIGN_MODEL`); on the eval APIs it turns 129 tools into 69 and cuts the tool list an agent loads by about 28%, while keeping 245 of 248 parameters ([results](evals/design/README.md)).

The output is `tools.json`: each tool's arguments, the API request each call makes, and any endpoints left out, with the reason. Review it and approve tools one by one with `approve`. `generate` builds only approved, valid tools, and reports every tool it skips with the reason. `check-tools` reports every problem after a hand edit. Rules: [ADR 0011](docs/adr/0011-tool-design-and-generation.md) and [ADR 0012](docs/adr/0012-task-shaped-design.md); schema: [`docs/tools.schema.json`](docs/tools.schema.json).

### Generate the server

```bash
uv run docs2mcp generate tools/frankfurter/tools.json --spec specs/frankfurter/spec.json --out servers/frankfurter
uv run --directory servers/frankfurter server.py          # stdio
uv run --directory servers/frankfurter server.py --http   # streamable HTTP on :8000/mcp
uv run python scripts/try_server.py servers/frankfurter exchange_rates '{"base": "EUR"}'
```

`generate` writes one `server.py`, built on the official MCP Python SDK (`MCPServer`). It has one typed function per approved tool. Each call picks its route, makes one request with httpx, and returns the JSON, cut to 20,000 characters with a note. HTTP and network errors come back as tool errors.

Next to the server are a `pyproject.toml`, a README listing every skipped tool, excluded endpoint and unreachable endpoint with its reason, and `generation_report.json`. `scripts/try_server.py` starts a server, lists its tools and makes one real call. CI also runs every generated server through Ruff and strict Pyright, and runs the official MCP conformance suite against one of them (`just conformance`). The scenarios that don't apply to a tools-only server are listed in [`evals/conformance/baseline.yml`](evals/conformance/baseline.yml).

### Score an extraction

```bash
uv run docs2mcp score specs/frankfurter/spec.json --reference evals/extraction/reference/frankfurter.json
```

This compares the extracted endpoints and parameters with a reference built from the API's own OpenAPI file. Results and the baseline: [evals/extraction](evals/extraction/README.md). Rules: [ADR 0005](docs/adr/0005-extraction-scoring.md).

### Measure a design

```bash
uv run docs2mcp measure tools/frankfurter.json --spec specs/frankfurter/spec.json
```

This counts tools, routes, covered and excluded endpoints, and the estimated tokens an agent loads to see the tools. A person also reviews each design against a pass/fail [checklist](evals/design/checklist.md). Baseline and rules: [evals/design](evals/design/README.md).

### Trace a run

Tracing is off by default. Turn it on by setting either:
- Langfuse keys (`LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`, plus `LANGFUSE_HOST` for a US or self-hosted instance);
- or any OpenTelemetry collector (`OTEL_EXPORTER_OTLP_ENDPOINT`).

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-...
uv run docs2mcp extract ingest/frankfurter --out specs/frankfurter/spec.json
```

Each command is one trace. It has spans for:
- each page fetch, including cache hits and HTTP status;
- each extraction batch;
- each model call, using the OpenTelemetry GenAI attributes (model and input, output and cached tokens), which Langfuse shows as generations;
- each validation step and design repair, and generation.

Prompts and model replies stay out of traces unless `DOCS2MCP_TRACE_CONTENT=true`, so documentation text is not sent anywhere by default.

## How this repo is built

I build with AI coding agents and review everything they produce. The working rules every agent follows are in [`AGENTS.md`](AGENTS.md): run the baseline before changing code, keep unit tests off the network, never change an eval task in the same pull request as the code it measures, and report what was and was not verified. Changes to anything the eval measures follow the [eval-driven change](.claude/skills/eval-driven-change/SKILL.md) procedure, which agents load as a skill: name the failing case, re-run every API on the same inputs, and show before/after numbers.

## License

[MIT](LICENSE)

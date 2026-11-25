# Changelog

All notable changes to docs2mcp are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-25

The first release, published on PyPI as `docs2mcp-cli`.

### Added

- `docs2mcp ingest`: fetch a documentation site and clean it into Markdown chunks.
- `docs2mcp extract`: turn the chunks into a typed API spec with Claude, validated against a schema, with recording and replay for reproducible runs.
- `docs2mcp design`: group endpoints into task-shaped tools (or one tool per endpoint with `--direct`), with code-checked routes, types and defaults.
- `docs2mcp approve` and `check-tools`: approve tools one by one. `check-tools` validates a design and lists any argument a route would leave out.
- `docs2mcp generate`: a typed MCP server (official Python SDK 2.x) for the approved tools. It passes Ruff, Pyright strict and the MCP conformance suite. It follows redirects on the API's host, shortens long responses instead of cutting them, and names any argument a route can't take.
- `docs2mcp score` and `measure`: evals of extraction and of tool design.
- Tracing with OpenTelemetry, exported to Langfuse or any OTLP endpoint.
- The agent eval: 100 tasks on six public APIs, four conditions (OpenAPI conversion, docs2mcp direct, docs2mcp task-shaped, FastMCP), recorded API responses, a grounded-success metric, and the first full run on Claude Haiku.

[0.1.0]: https://github.com/dpelezo/docs2mcp/releases/tag/v0.1.0

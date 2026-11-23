# ADR 0001: Generate task-shaped tools, not one tool per endpoint

- **Status:** Accepted
- **Date:** 2026-11-23

## Context

Existing generators turn an OpenAPI spec into an MCP server by mapping each endpoint to one tool. That has two problems for agents:

1. **Many tools, thin descriptions.** A 60-endpoint API becomes 60 tools. Every tool description is sent to the model on every turn, which costs tokens and makes the right tool harder to pick. Endpoint summaries are written for humans who already know the API, not for a model choosing between tools.
2. **Endpoints are not tasks.** A user task such as "what will the weather be in Lisbon tomorrow?" can need two calls (geocode the city, then fetch the forecast). With one tool per endpoint, the agent must discover and chain those calls itself, and each extra step is a chance to fail.

Many APIs also have no clean OpenAPI spec at all, only HTML or Markdown documentation.

## Decision

docs2mcp will:

- read the API's documentation (HTML or Markdown) and extract a typed spec of endpoints, parameters and authentication, without needing an OpenAPI file;
- group endpoints into a small set of **task-shaped tools**, each named and described for an LLM, with parameters an agent can fill without reading the docs;
- put a human approval step before code generation, so a person can review the proposed tools.

## How we will know it worked

The claim is testable, so the repo includes an eval: 100 realistic tasks across five public APIs that need no key. The same agent runs each task against (a) the generated task-shaped server and (b) a direct one-tool-per-endpoint conversion, where the API publishes an OpenAPI spec. We report task success rate, tool calls per task, tokens per task and invalid tool calls. If task-shaped tools do not beat direct conversion, the README will say so.

## Consequences

- Design needs an LLM step, so generation is not fully deterministic. The approval step and the eval are the controls for that.
- A grouped tool can hide an endpoint an agent needs. The eval tasks must include cases that exercise less common endpoints.
- The generated server is tied to the extracted spec. When the docs change, the spec and server are regenerated, not patched by hand.

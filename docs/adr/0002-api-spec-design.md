# ADR 0002: A small, strict API spec between extraction and design

- **Status:** Accepted
- **Date:** 2026-11-23

## Context

Extraction turns documentation into structured data, and design, generation and the eval all read that data. Three options:

1. **Use OpenAPI as the internal format.** Familiar, with tooling to match, but it is large and permissive. An LLM asked to write OpenAPI has hundreds of fields to get wrong, and a document that passes OpenAPI validation can still be inconsistent, for example a path placeholder with no matching parameter.
2. **Keep free-form JSON.** Easy to produce, but every later step would need its own defensive checks.
3. **Define a small, strict model of our own.** Only the fields design and generation need, with consistency rules enforced at the boundary.

## Decision

We use option 3: `docs2mcp.spec.ApiSpec`, built with Pydantic and published as JSON Schema in `docs/spec.schema.json`.

- **Strict:** unknown fields are rejected, models are immutable, and strings are trimmed.
- **Consistent by construction:** validation fails when
  - endpoint IDs or routes repeat (`/pets/{id}` and `/pets/{pet_id}` count as the same route);
  - path placeholders and path parameters differ, or a path parameter is optional;
  - a parameter repeats in the same location;
  - a required parameter has a default, a default is outside its enum, or `items_type` is set on a non-array;
  - a `GET` or `DELETE` has body parameters;
  - `api_key` auth lacks a header or query name.
- **Traceable:** each endpoint can record the documentation page it came from (`source_url`), so a reviewer can check an extracted endpoint against its source.
- **Versioned:** `spec_version` is `"1"`. A breaking change to the models bumps it.

The schema is also the contract for the model: extraction will ask Claude for output matching this JSON Schema, and validation errors are fed back on retry.

## Out of scope for v1

Request bodies are flat parameters, not nested schemas. There are no webhooks, streaming responses, file uploads, pagination descriptors, rate-limit metadata or OAuth flows. The five eval APIs need none of these. Each can be added in a later version when a real API needs it.

## Consequences

- An API whose docs need something out of scope cannot be represented yet. Extraction should report that clearly rather than produce a spec that is quietly wrong.
- A test fails when the committed JSON Schema drifts from the models. Regenerate it with `just schema`.

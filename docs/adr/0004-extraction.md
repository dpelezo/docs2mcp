# ADR 0004: Extract the spec with Claude structured outputs, then validate and repair in code

- **Status:** Accepted
- **Date:** 2026-11-23

## Context

Extraction reads the chunks from ingest (ADR 0003) and must produce a valid `ApiSpec` (ADR 0002). It is the first step that calls a model, so it is also the first that costs money, can fail on the provider's side, and can be wrong in ways tests cannot catch alone.

Constraints that shaped the design:

- **Structured output limits.** Claude's structured outputs (`output_config.format`, generally available) compile a JSON schema into a grammar. The documented limits per request are 24 optional parameters and 16 parameters with union types; beyond that the API can reject the schema as too complex. `ApiSpec` has many optional fields and a recursive JSON value type for defaults.
- **Size.** PokéAPI's reference is about 41,000 tokens of chunks. Sending it in one call risks cut-off output; sending one chunk per call is 172 calls.
- **Current models.** Claude Haiku 5.5 and newer reject non-default `temperature`, `top_p` and `top_k`, and think adaptively, so prompts and parameters must not rely on either.

## Decision

### A flat wire schema for the model

Claude fills `WireBatch` (`extract/wire.py`), not `ApiSpec`:

- Every field is required. There are no unions, so missing values are `"none"`, `""` or `[]`.
- Defaults are JSON literals in a string (`"20"`, `"\"celsius\""`), parsed in code.
- Each endpoint names the chunk it came from (`source_chunk_id`), which becomes its `source_url`.

A unit test runs the SDK's own `transform_schema` and fails if the schema ever gains an optional field or a union.

### Batches, with splitting

Chunks are grouped in order into batches of about 6,000 tokens (`DOCS2MCP_EXTRACT_BATCH_TOKENS`); a chunk larger than that gets its own batch. If a reply is cut off (`stop_reason: max_tokens`), the batch is split in half and both halves are retried. A refusal, or a single chunk that still does not fit, is recorded in the report and the run continues.

### Validation, then feedback

Each endpoint in a reply is converted to a strict `Endpoint`. Safe, mechanical fixes are made in code and logged (see below). Anything ambiguous fails validation, and those errors go back to Claude in the same conversation, which is asked to correct only those endpoints, up to 2 times (`DOCS2MCP_EXTRACT_MAX_RETRIES`). Endpoints still invalid after that are rejected and listed in the report, never silently dropped.

| Fixed in code and logged | Sent back to Claude |
|---|---|
| Absolute URLs and the base URL's path prefix in paths | A path parameter that is not in the path |
| `:id` and `<id>` placeholders; placeholder names made identifiers | Body parameters on `GET` or `DELETE` |
| A missing parameter for a path placeholder (added as a required string) | An empty summary |
| A required parameter with a default (made optional) | |
| A default outside the allowed values (dropped) | |
| The same endpoint from two chunks (merged); repeated operation names (suffixed) | |

The base URL is the `--base-url` override, or the one the batches report most often. Authentication is the most common non-`none` scheme.

### Record and replay

`--record DIR` saves every reply, keyed by a hash of the model, system prompt and conversation. `--replay DIR` serves them back with no API calls. Unit tests use scripted models, and recorded real replies will be added as fixtures, so CI never calls Claude. Changing the prompt, the chunks or the model changes the key, and replay then fails with a message to record again rather than returning a stale answer.

### Report

Every run writes `extraction_report.json` next to `spec.json`. It records the model, calls, input and output tokens, each batch's chunks and endpoint count, rejected endpoints with their errors, and every repair.

## Consequences

- The model never sees `ApiSpec` itself, so changes to the spec need matching changes to the wire schema and `merge.py`.
- Code repairs can hide a model mistake, which is why each one is logged and reviewable.
- Quality is not proven by these tests. The next step runs extraction live on all five APIs, records the replies, and compares the endpoints found with each API's OpenAPI spec where one exists (PokéAPI, Frankfurter), plus a manual check for the others.

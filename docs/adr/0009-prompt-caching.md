# ADR 0009: Cache the system prompt, and count cached tokens apart

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

Every extraction call sends the same system prompt and schema, then a different batch of docs. Frankfurter's single call used 8,132 input tokens for 2,617 tokens of docs, so roughly 5,500 tokens per call are fixed overhead. PokéAPI makes 8 calls and Postcodes.io 5, paying that overhead on each one.

From Anthropic's prompt-caching docs (read 2026-11-24):

- Claude Haiku 5.5 caches prompts of at least 512 tokens. Our system prompt is about 630.
- On Haiku 5.5, a 5-minute cache write costs 1.25× the input price, and a cache read 0.1×.
- The cache prefix order is tools → system → messages, and a hit needs an identical prefix.
- The docs do not say whether the structured-output schema (`output_config.format`) is part of the cached prefix. That is measured here rather than assumed.

## Decision

- **One explicit cache breakpoint, on the system prompt.** It is identical on every call of a run. The docs in each batch differ every time, so the automatic top-level `cache_control`, which marks the last block, would pay the write premium on every batch and never hit.
- **`input_tokens` in our reports is the total input**: uncached, plus read from cache, plus written to cache. The cached share is reported apart as `cache_read_tokens` and `cache_write_tokens`. Token totals stay comparable with earlier runs in the History table, and the cost saving is visible separately.
- **Recordings store the cache counts.** Older recordings replay with zero cache tokens.
- **On by default,** and switchable with `DOCS2MCP_EXTRACT_PROMPT_CACHE=false`.

## Consequences

- From the second call in a run (within 5 minutes), the cached prefix costs a tenth of the price. A run with one call pays the 1.25× write premium on the prefix and gains nothing.
- How much is saved depends on whether the schema overhead is cached, which the live run measures. If only the about 630-token system prompt is cached, the saving per call is small.
- Caching does not change what the model sees, so quality should not move. The live run checks that.
- The recording key (model, system prompt, turns) is unchanged, so recordings made before this change still replay.

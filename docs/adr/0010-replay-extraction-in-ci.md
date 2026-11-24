# ADR 0010: Replay recorded extraction on committed snapshots in CI

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

The extraction eval (ADR 0005) needs live calls to Claude, so it ran only when someone ran it by hand. Unit tests used scripted replies and could not see a real regression. For example, a change to merging that silently dropped PokéAPI's list routes would pass every unit test.

Three eval APIs have docs we may commit as snapshots: PokéAPI, Frankfurter and Postcodes.io (ADR 0003). Recordings are keyed by a hash of the model, the system prompt and the messages, which include the chunk text (ADR 0004).

## Decision

- **Snapshot ingest is offline and isolated** (`docs2mcp.evals.snapshots`). An API's listed pages are served from its committed snapshots through an `httpx.MockTransport`. Pages are always refetched through that client and cached in a throwaway folder, so a live page in the local page cache cannot change the chunks. Both recording and replay use default settings, not `.env`.
- **`scripts/record_replay.py` (`just record-replay`) records** a live extraction on those chunks and scores it. Its `recordings/` and `score.json` are committed to `evals/extraction/replay/<id>/`.
- **`tests/unit/test_extraction_replay.py` replays** them in CI. It ingests the snapshots, extracts through `ReplayModel`, scores against the reference, and requires the committed score exactly.
- **Score files are never edited by hand.** A change that moves a score is recorded again live, and the pull request explains the difference.

## Consequences

- Every pull request re-runs the extraction eval for three of the six APIs, offline, in under a second.
- Changes to ingest, merging, the scorer or references that move a result now fail CI, with a message saying how to record again.
- Changes to the prompt, the wire schema or chunking change the recording keys, so replay fails until someone records again. That is deliberate: such changes need a live run (ADR 0005), and the failure makes that impossible to skip.
- Replay proves that the code reproduces a recorded result. It does not measure run-to-run variation; the repeated live runs in `evals/extraction/README.md` do.
- Open-Meteo and Open Library cannot be replayed in CI, because their docs licences rule out committing snapshots (AGPL).

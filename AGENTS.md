# Working rules for AI coding agents in this repo

These rules apply to any AI assistant (Claude Code, Codex, Cursor and others) and to humans. Add a rule here whenever an agent or a person makes a mistake that a rule would have prevented.

## Before changing code

- Read the files you will touch and the tests that cover them.
- Run the baseline first: `just check` must pass on `main` before you change anything, so an inherited failure is never blamed on new code.
- For anything that is expensive to undo (deleting data, changing the generated server format, changing the eval tasks or scoring), write the plan first and wait for approval.

## While changing code

- One change per branch, small commits, [Conventional Commits](https://www.conventionalcommits.org/) messages (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- No new dependency, and no dependency upgrade, without asking.
- Never print, log or commit secrets. Refer to environment variables by name only; `.env` is ignored by git.
- Unit tests never call a live model or the network. Use recorded responses. Live calls belong in the eval suite and in `tests/integration/`.
- Keep the eval honest: never change a task, an expected answer or a scorer in the same pull request as the code it measures.
- To change anything the eval measures (extraction, prompts, the wire schema), follow `.claude/skills/eval-driven-change/SKILL.md`: name the failing case first, re-run every API on the same inputs, explain every score movement, and put the before/after table in the pull request.
- `tests/unit/test_extraction_replay.py` replays recorded extraction offline. If it fails after your change, do not edit `score.json` by hand: record again with `just record-replay <id>` and explain the score change in the pull request.
- Anchor `.gitignore` entries for output folders to the repo root (`/ingest/`, not `ingest/`). An unanchored name also matches source folders such as `src/docs2mcp/ingest/`, and new files there are silently ignored.

## Before saying "done"

- `just check` passes: Ruff lint and format, Pyright strict, unit tests with at least 80% coverage.
- New behaviour has tests, including the failure paths.
- If you changed what a function returns, search for the tests and callers that read it.
- Tests that depend on time freeze the clock.
- Summarise what changed, what you verified, and what you did not verify.

## Project facts

- Python 3.14 managed by uv (`uv.lock` is committed). Package code lives in `src/docs2mcp/`.
- Commands: `just setup`, `just check`, `just lint`, `just typecheck`, `just test` (unit, 80% coverage floor), `just integration` (live, needs network).
- Settings come from environment variables (see `.env.example`), loaded by `docs2mcp.config.Settings`.
- `main` is protected: changes land through pull requests with passing CI and linear history.
- Decisions are recorded in `docs/adr/`. Read them before proposing a change to the design.

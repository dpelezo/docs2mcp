---
name: "eval-driven-change"
description: "Use when changing an LLM pipeline or agent that has an offline eval: tie the change to a failing case, rerun every case on identical inputs, compare per case, and ship a before/after PR."
---

# Eval-driven change

How to change an LLM pipeline or agent so that every change has measured evidence behind it. One change per pull request. The eval is the fixed target and the code is what moves.

## 0. Before starting

- Read the repo's eval rules: `AGENTS.md`, the ADRs, and the eval README. Note which files are eval ground truth (references, tasks, expected answers, scorers).
- Run the unit suite on `main`, so an inherited failure is not blamed on the change.

## 1. Name the failure first

- Point to the eval cases that fail today and that this change should fix, such as "PokéAPI: 50 list routes missing". Quote the current score.
- If no case shows the problem, write the eval first and confirm it fails on the current code. If that means changing references, tasks or scoring, it goes in its own pull request, approved before any code changes.
- Never write the eval after seeing the fix. It would pass by design.

## 2. Freeze the inputs

- Reuse the exact inputs from the baseline: the same ingested chunks or fixtures, the same model, and the same settings. Only the code under test may differ.
- Record the baseline run's code version, model, input description, calls and tokens.

## 3. Make one change

- Keep it small and behind a clear seam. Record a design decision in an ADR when it introduces a new concept.
- Unit tests use scripted or recorded model replies and never call a live model or the network. Cover the failure paths too.
- Prefer deterministic code for anything mechanical. Let the model make only the judgments code cannot, and log every transformation code makes in the run report.

## 4. Rerun all cases, not just the targeted one

- Run every eval case live on the frozen inputs. Changes to prompts or schemas affect every case.
- Run a case several times (3 is a good start) when its score matters or looks noisy, and report the minimum and the mean. A single run cannot separate a real effect from run-to-run variation.
- Write outputs where they do not touch tracked files, so the next pull is not blocked.

## 5. Re-score independently

- Re-run the scorer on the produced outputs in a second environment, and confirm the scores are identical before reporting them.
- Recompute any totals from the result files. Never add them up by hand.

## 6. Compare per case, then explain every movement

- Read the per-case diff, not just the average. Put each movement in one of these groups:
  - **Caused by the change:** the run report shows the mechanism, such as "expanded into 51 routes".
  - **Run-to-run variation:** a case the change does not touch moved, and the run report shows the change did nothing there.
  - **Reference gap:** the "error" is actually documented or correct. Do not fix the reference in this pull request. Open a separate, approved one and re-score the same outputs there.
- Check for reward hacking: outputs that satisfy the scorer without doing the task, or claims of actions that were never taken.

## 7. Record and ship

- Update the result files with the new scores and run details (date, code version, model, inputs, calls, tokens).
- Add a row to the eval README's history table, with the before and after values for that change.
- In the pull request description, include:
  - the failure it targets;
  - the before/after table per case, with totals;
  - how each movement was classified;
  - token and cost change;
  - what was verified and what was not, such as "one run per side".
- Merge only after the owner approves.

## Guardrails

- Keep a held-out case that is never tuned against. If scores rise only on tuned cases, the change is overfitting.
- Default to deterministic checks. Use an LLM judge only for semantic questions, ask it narrow pass/fail questions (no 1-10 scales), and validate it against a human-labelled sample first.
- Score the final state or output first, then efficiency (steps, calls, tokens, latency).
- More evals are not better evals. Add a case only when it is tied to a real failure or feature.
- A perfect score on a small set is not the goal. Stop tuning when the remaining misses are reference gaps or undocumented behaviour.
## In docs2mcp

- **Eval ground truth:** `evals/extraction/reference/` and `evals/extraction/additions/`, built by `just references`, plus the scorer `src/docs2mcp/evals/` and the rules in ADR 0005. Changing any of them needs an approved pull request that changes nothing else.
- **Frozen inputs:** re-run extraction on the existing `ingest/<api>/` chunks instead of ingesting again, so only extraction differs.
- **Live run, per API:**
  ```bash
  uv run docs2mcp extract ingest/<api> --out specs/<api>/after/spec.json --base-url <base_url> --record recordings/<api>
  uv run docs2mcp score specs/<api>/after/spec.json --reference evals/extraction/reference/<api>.json --out specs/<api>/after/score.json
  ```
  The `base_url` for each API is in `tests/fixtures/docs/sources.toml`. `specs/`, `ingest/` and `recordings/` are git-ignored, so live runs never touch tracked files.
- **Record:** copy each score into `evals/extraction/results/<api>.json` with its `run` details, then add a row to the History table in `evals/extraction/README.md`. Resource templates (#20) is the worked example.

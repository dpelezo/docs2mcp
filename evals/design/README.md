# Design eval

How good a `tools.json` is before any agent uses it. It has two halves: numbers computed by code, and a pass/fail review by a person. The week 3 tool-use eval measures whether a design actually helps an agent. This one says what a design costs and whether it is fit to approve.

The rules were approved on 2026-11-24, before any design prompt was written (#29).

## Metrics

`docs2mcp measure tools.json --spec spec.json --out <file>` computes them. The rules are in [`src/docs2mcp/evals/design.py`](../../src/docs2mcp/evals/design.py).

| Metric | Meaning |
|---|---|
| `valid` | `validate_toolset` finds no problems (ADR 0011). The problems are listed. |
| `covered` / `excluded` / `endpoints` | Spec endpoints reached by a route, excluded with a reason, and in the spec. A valid design always has covered + excluded = endpoints. |
| `tools`, `routes`, `max_routes_per_tool` | What the design contains. |
| `tool_list_tokens` | Estimated tokens an agent loads before its first call: every tool's name, description and argument schema, as JSON in the shape of an MCP `tools/list` reply. |
| `description_tokens` | The prose part of that: tool and argument descriptions. |
| `per_tool` | Routes, arguments and estimated tokens for each tool, to find the heavy ones. |

Token counts use the same four-characters-per-token estimate as chunking. They are good for comparing designs, not exact for any one model.

Fewer tools and fewer tokens are not a goal on their own. A design that merges unrelated endpoints to cut the count fails the review below, however good its numbers look.

## Review

A person reviews every task-shaped design with [checklist.md](checklist.md) and saves the answers as `reviews/<id>.md`. Each question is pass or fail, with the tool names that fail it. A design is fit to approve only when every question passes.

## Held-out API

**Open Library** is held out: the design prompt is never changed because of an Open Library result. Its numbers and review are reported on their own line, so an improvement seen only on the tuned APIs shows up as overfitting.

## Inputs

Designs are measured on frozen specs, so only the design step differs between runs:

| API | Spec |
|---|---|
| PokéAPI, Frankfurter, Postcodes.io | `tests/fixtures/specs/<id>.json` (committed; the replay of `evals/extraction/replay/<id>/`) |
| Open-Meteo forecast, Open-Meteo geocoding, Open Library | `specs/<id>/cache/run1/spec.json` on the owner's machine: the run recorded in `evals/extraction/results/<id>.json`. Not committed, because these docs are AGPL. |

## Results

`results/<id>/direct.json` is the baseline: the one-tool-per-endpoint design, made by code with no model. Each file has a `run` block naming its input. `tests/unit/test_evals_design.py` recomputes the three committed baselines and fails if they drift.

| API | Endpoints | Tools | Routes | Tool list (est. tokens) | Descriptions (est. tokens) |
|---|---|---|---|---|---|
| PokéAPI | 103 | 103 | 103 | 9,601 | 4,986 |
| Postcodes.io | 14 | 14 | 14 | 1,701 | 876 |
| Frankfurter | 9 | 9 | 9 | 968 | 425 |
| Open-Meteo forecast | 1 | 1 | 1 | 883 | 444 |
| Open-Meteo geocoding | 2 | 2 | 2 | 328 | 179 |
| **Tuned total** | **129** | **129** | **129** | **13,481** | **6,910** |
| Open Library (held out) | 11 | 11 | 11 | 1,091 | 536 |

Every baseline design is valid, covers every endpoint and excludes none.

### History

| Date | Change | Design | Tuned: tools / tokens | Open Library: tools / tokens | Review |
|---|---|---|---|---|---|
| 2026-11-24 | Baseline (#29) | direct, no model | 129 / 13,481 | 11 / 1,091 | not reviewed (no grouping to judge) |

## Measure a design

```bash
uv run docs2mcp design specs/frankfurter/spec.json --out tools/frankfurter/tools.json
uv run docs2mcp measure tools/frankfurter/tools.json \
  --spec specs/frankfurter/spec.json --out tools/frankfurter/metrics.json
```

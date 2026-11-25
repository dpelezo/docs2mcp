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
| `parameters_exposed` / `parameters_total`, `unexposed` | Every parameter of every routed endpoint, counted once per route, and how many a route fills from an argument or a constant. `unexposed` lists the rest as `endpoint.parameter`: the agent cannot use them. Added on 2026-11-24, after live runs showed designs dropping optional parameters that validation does not require. |
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

| API | Endpoints | Tools | Routes | Parameters exposed | Tool list (est. tokens) | Descriptions (est. tokens) |
|---|---|---|---|---|---|---|
| PokéAPI | 103 | 103 | 103 | 154 / 154 | 9,601 | 4,986 |
| Postcodes.io | 14 | 14 | 14 | 34 / 34 | 1,701 | 876 |
| Frankfurter | 9 | 9 | 9 | 27 / 27 | 968 | 425 |
| Open-Meteo forecast | 1 | 1 | 1 | 26 / 26 | 883 | 444 |
| Open-Meteo geocoding | 2 | 2 | 2 | 7 / 7 | 328 | 179 |
| **Tuned total** | **129** | **129** | **129** | **248 / 248** | **13,481** | **6,910** |
| Open Library (held out) | 11 | 11 | 11 | 22 / 22 | 1,091 | 536 |

Every baseline design is valid, covers every endpoint, excludes none and exposes every parameter.

### Task-shaped design

`results/<id>/task.json` is run 1 of `docs2mcp design` with `claude-sonnet-5-5` (ADR 0012), with its run details. Its `repeats` block holds all three Sonnet runs and three Haiku runs on the same inputs. The designs for PokéAPI, Frankfurter and Postcodes.io are committed in `replay/<id>/` with their recordings, and `tests/unit/test_design_replay.py` replays them offline. The reviews are in `reviews/<id>.md`.

| API | Tools | Routes | Parameters exposed | Excluded | Tool list (est. tokens) | Sonnet runs 1 / 2 / 3 | Haiku runs 1 / 2 / 3 | Review |
|---|---|---|---|---|---|---|---|---|
| PokéAPI | 52 | 103 | 154 / 154 | 0 | 6,023 | 6,023 / 6,539 / 7,288 | 8,128 / 6,975 / 8,102 | not yet: 3 extra claims |
| Postcodes.io | 11 | 14 | 32 / 34 | 0 | 1,488 | 1,488 / 1,486 / 1,502 | 1,585 / 1,583 / 1,671 | not yet: spec bug in bulk |
| Frankfurter | 4 | 8 | 27 / 27 | 1 | 890 | 890 / 758 / 723 | 908 / 977 / 1,027 | fit to approve |
| Open-Meteo forecast | 1 | 1 | 25 / 26 | 0 | 948 | 948 / 900 / 961 | 996 / 1,009 / 1,004 | fit to approve |
| Open-Meteo geocoding | 1 | 2 | 7 / 7 | 0 | 358 | 358 / 360 / 368 | 304 / 332 / 309 | not yet: 1 extra claim |
| **Tuned total** | **69** | **128** | **245 / 248** | **1** | **9,707** | **9,707 / 10,043 / 10,842** | **11,921 / 10,876 / 12,113** | |
| Open Library (held out) | 10 | 11 | 22 / 22 | 0 | 1,143 | 1,143 / 1,117 / 1,170 | 1,251 / 1,399 / 1,192 | not yet: an enum hint, extra claims |

Every Sonnet design is valid and needed no repair. Two of 18 Sonnet designs needed a retry, both on held-out Open Library. On the tuned APIs, Sonnet exposed 245 or 246 of 248 parameters, against 240 to 244 for Haiku. Haiku's runs predate the constant type check, which rejects its run 3 Postcodes.io design.

### History

| Date | Change | Design | Tuned: tools / parameters / tokens | Open Library: tools / parameters / tokens | Review |
|---|---|---|---|---|---|
| 2026-11-24 | Baseline (#29) | direct, no model | 129 / 248 of 248 / 13,481 | 11 / 22 of 22 / 1,091 | not reviewed (no grouping to judge) |
| 2026-11-24 | First task-shaped design (#29), before the parameter rule | Haiku, run 1 | 70 / 228 of 248 / 11,188 | 9 / 22 of 22 / 1,259 | not reviewed: optional parameters dropped (Open-Meteo forecast 14 of 26) |
| 2026-11-24 | Every parameter passed or omitted (ADR 0012) | Sonnet, run 1 of 3 | 69 / 245 of 248 / 9,707 | 10 / 22 of 22 / 1,143 | 2 of 5 tuned fit to approve; held out not yet |

## Measure a design

```bash
uv run docs2mcp design specs/frankfurter/spec.json --out tools/frankfurter/tools.json
uv run docs2mcp measure tools/frankfurter/tools.json \
  --spec specs/frankfurter/spec.json --out tools/frankfurter/metrics.json
```

# Extraction eval

How well `docs2mcp extract` recovers an API's real endpoints and parameters from its docs. The rules are in [ADR 0005](../../docs/adr/0005-extraction-scoring.md).

- `reference/<id>.json` is the ground truth. PokéAPI, Frankfurter and Postcodes.io are generated from their own OpenAPI files with `just references`. Open Library's and both Open-Meteo ones are written by hand from their docs pages (`source.kind: "manual"`).
- `additions/<id>.json` adds the routes and parameters an official file leaves out but the docs document and the server serves, each with its evidence. Today that is only Postcodes.io: 3 routes and 12 parameters. The reference marks every addition with a `note`, and `just references` fails if the official file already covers one, so stale additions get noticed.
- `results/<id>.json` is the score of one recorded run, with the run's code version, model, ingest command and token use.

## Replay in CI

`evals/extraction/replay/<id>/` holds Claude's recorded replies for the snapshot chunks of the three APIs whose docs we may commit (PokéAPI, Frankfurter and Postcodes.io), and the score they produced. `tests/unit/test_extraction_replay.py` ingests those snapshots offline, replays the recordings, scores the spec, and requires the same score. So every pull request re-runs the extraction eval for these APIs without a live call, and any change to ingest, merging or scoring that moves a result fails CI. See ADR 0010.

- **A change meant to move the score:** run `just record-replay <id>` (live; it writes to `specs/replay/`). Copy `recordings/` and `score.json` into `evals/extraction/replay/<id>/`, and put the before/after in the pull request.
- **A change to the prompt, the wire schema or chunking** changes the recording keys, so replay fails with "record again". That is intended, because those changes need a live run anyway.

## Score a run

```bash
uv run docs2mcp score specs/frankfurter/spec.json \
  --reference evals/extraction/reference/frankfurter.json \
  --out evals/extraction/results/frankfurter.json
```

## Results

Latest run: prompt caching (ADR 0009), `claude-haiku-5-5`, one run per API on the chunks from the crawl-depth change. Every API was ingested with `docs2mcp ingest <the pages in sources.toml> --depth 0`, so exactly the listed pages and nothing they link to. Input tokens are the total input; the cached share is in the notes.

| API | Endpoints found | Endpoint precision | Parameters found | Parameter precision | Calls | Input / output tokens |
|---|---|---|---|---|---|---|
| Open-Meteo forecast | 1 / 1 (100%) | 100% | 26 / 27 (96%) | 100% | 2 | 19,178 / 3,860 |
| Open-Meteo geocoding | 2 / 2 (100%) | 100% | 7 / 7 (100%) | 100% | 1 | 6,038 / 1,825 |
| Postcodes.io | 14 / 14 (100%) | 100% | 24 / 27 (89%) | 100% | 5 | 63,658 / 8,947 |
| Open Library | 11 / 11 (100%) | 100% | 14 / 18 (78%) | 100% | 2 | 20,879 / 7,357 |
| Frankfurter | 8 / 9 (89%) | 100% (8 extracted) | 18 / 20 (90%) | 95% | 1 | 8,132 / 5,393 |
| PokéAPI | 101 / 102 (99%) | 98% (103 extracted) | 100 / 144 (69%) | 100% | 8 | 112,229 / 17,651 |
| **Total** | **137 / 139 (99%)** | **99% (139 extracted)** | **189 / 243 (78%)** | **99%** | **19** | **230,114 / 45,033** |

Every run produced a valid `spec.json` with no rejected endpoints and no failed batches. The two missed endpoints are routes their docs never mention (Frankfurter's `/coverage`, PokéAPI's `/meta`).

### History

Each change is measured on the same ingested chunks, before and after, except crawl depth, which changes the chunks themselves. Moves on APIs a change does not touch show run-to-run variation; from ADR 0007 on, the targeted APIs run 3 times.

| Change | Endpoints found | Endpoint precision | Parameters found | Parameter precision |
|---|---|---|---|---|
| Baseline (#18, #19) | 87 / 139 (63%) | 94% | 78 / 99 (79%) | 99% |
| Resource templates (ADR 0006) | 137 / 139 (99%) | 94% | 184 / 243 (76%) | 99% |
| Out-of-scope endpoints (ADR 0007) | 137 / 139 (99%) | 98% | 178 / 243 (73%) | 99% |
| Shared parameters (ADR 0008) | 137 / 139 (99%) | 98% | 186 / 243 (77%) | 99% |
| Crawl depth 0, permalink cleanup | 137 / 139 (99%) | 98% | 190 / 243 (78%) | 99% |
| Prompt caching (ADR 0009) | 137 / 139 (99%) | 99% | 189 / 243 (78%) | 99% |

- **PokéAPI:** 51 → 101 endpoints. Claude flagged `GET /{endpoint}` and it expanded into 51 list routes. The parameter denominator grows because the list routes' `limit`, `offset` and `q` are now scored.
- **Frankfurter:** nothing was flagged, so its v1 `/{date}` was correctly left alone. This run extracted two more v1 routes, so precision fell from 73% to 62%, and 4 more parameters, so recall rose from 55% to 75%. That is run-to-run variation, and all the extras come from the v1 page that change 4 (crawl scope) removes.
- **Postcodes.io:** parameters went from 20 to 22 of 27 (variation). Open-Meteo and Open Library are unchanged.

Out-of-scope endpoints (ADR 0007):

- **Open Library:** precision 92% → 100% in all 3 runs. Every run dropped the covers route (`served from https://covers.openlibrary.org`).
- **Frankfurter:** precision 62% → 89% in all 3 runs. Every run dropped the v1 routes (4 or 5 per run, including v1's `/currencies`, while v2's was kept). The one extra left is `/rates.csv`.
- **Frankfurter parameters vary between runs:** recall was 55%, 90% and 75% (mean 73%), with no difference in the code. This is the clearest measure of run-to-run noise so far; the 55% in the table is simply run 1.
- **No other API dropped anything,** so the check had no false positives. The other APIs moved by at most 2 parameters (variation).

Shared parameters (ADR 0008), Frankfurter over 3 runs before and 3 after:

| | Before (ADR 0007 runs) | First attempt | Final |
|---|---|---|---|
| Parameters found (of 20) | 11, 18, 15 (mean 73%) | 19, 19, 18 (mean 93%) | 18, 19, 18 (mean 92%) |
| Parameter precision | 92%, 95%, 94% | 90%, 68%, 90% | 90%, 90%, 90% |
| Provider-route parameters (of 8) | 0, 7, 4 | 8, 8, 7 | 7, 8, 7 |

- **Caused by the change:** every run copied the parameters shared with `/rates` onto `/providers/{provider}/rates`, and the report lists each copy.
- **First attempt:** in one run Claude also attached the shared parameters to the single-pair route, which scored 68% precision. The prompt now says each route of a group lists only the endpoint it mirrors (with a generic example), and no final run made that mistake.
- **Remaining extras:** `providers` is copied onto the single-provider route in every run, as ADR 0008 predicted, and `expand` on `/rate/{base}/{quote}`.
- **Remaining misses:** `date` on `/providers/{provider}/rate/{base}/{quote}` in 2 of 3 runs. Its reference `/v2/rate` never matches, because the docs leave off the route's placeholders.
- **No other API copied anything.** Their parameters moved by at most 1 (variation).

Crawl depth 0 and permalink cleanup (approved change to the eval's input):

| | Before (ADR 0008 run) | After, 3 runs |
|---|---|---|
| Frankfurter input | 20 pages, 182 chunks, ~31,645 tokens | 1 page, 12 chunks, ~2,617 tokens |
| Frankfurter calls, input tokens | 6, 92,933 | **1, 8,132 (-91%)** |
| Frankfurter parameters found / precision | 90% / 90% (runs 90-95%) | 95%, 90%, 90% / 90%, 95%, 95% |
| Postcodes.io input | 88 chunks, 98 permalinks, ~27,798 tokens | 73 chunks, 0 permalinks, ~25,418 tokens |
| Postcodes.io calls, input tokens | 6, 72,163 | **5, 63,658 (-12%)** |
| Postcodes.io parameters found | 21 / 27 (earlier runs: 20-22) | **24 / 27 in all 3 runs** |
| All six: calls, input tokens | 25, 323,420 | **19, 230,114 (-29%)** |

- **Frankfurter:** same quality from a tenth of the input. Its v1 page is no longer read, so nothing needs to be dropped as out of scope any more.
- **Postcodes.io:** parameters found rose to 24 of 27 in all three runs, above every earlier run (20 to 22). It now finds `limit` and `radius` on `/postcodes/{postcode}/nearest` and the `query` aliases. Cleaner headings and fewer, fuller chunks are the likely cause, but this is not proven by a controlled test.
- **The other four** got the same chunks as before (no permalinks, and already one or three listed pages), and they moved by at most 1 parameter (variation).

Prompt caching (ADR 0009), one run per API on the same chunks:

| | Before (crawl-depth run) | After |
|---|---|---|
| Total input tokens | 230,114 | 230,114 |
| Read from cache | 0 | **57,330** (18 calls × 3,185) |
| Written to cache | 0 | 3,185 (once) |
| Input cost, at Haiku 5.5 prices | $0.0230 | **$0.0179 (-22%)** |

- **The cached prefix is 3,185 tokens,** about five times the 630-token system prompt, so part of the structured-output overhead is cached as well. The docs do not say whether the output schema is part of the prefix; this measures it.
- **One write served every API.** The first PokéAPI call wrote the cache, and every later call in the run, across all six APIs, read it. They share the same system prompt and schema, and ran within the 5-minute lifetime.
- **Quality is unchanged,** as expected, since caching does not change what the model sees. Frankfurter extracted no `/rates.csv` this time (8 extracted, 100% precision) and one parameter fewer, which is variation. The other five matched the previous run exactly, and Postcodes.io scored 24 / 27 for the fourth run in a row.
- **About 2,300 tokens per call are still not cached,** the rest of the fixed overhead after the breakpoint. It is not reachable from our side.

### Notes on the Open-Meteo results

- **Forecast:** the only miss is `minutely_15`, which the page documents in its 15-Minutely section, not in the parameter table.
- **Geocoding:** complete.

### Notes on the Postcodes.io results

- **Endpoints:** all 14 found, including the 3 added by hand.
- **Missed parameters (3, the same in all 3 latest runs):** the official file's aliases `latitude` and `longitude`, and `filter`, on `GET /postcodes`. None of the three is on the `GET /postcodes` docs pages.
- **No extras.** The baseline's first scoring counted `filter`, `limit`, `radius` and `widesearch` on `POST /postcodes` as extras, but they are documented query parameters on the bulk pages. The reference was missing them, not the extraction. They were added to the reference (approved), and the same `spec.json` was re-scored: parameter precision went from 80% to 100%.
- **Required/optional mismatches (4):** `q`, `lon` and `lat` on `GET /postcodes`, and `q` on `GET /places`, were extracted as required. Each is required for one mode of a route that serves several (search, or reverse geocoding), so the reference marks them optional.

### Notes on the Open Library results

- **Endpoints:** all 11 found.
- **No extras.** The covers image URL, on `covers.openlibrary.org`, used to be extracted under the main base URL. Since ADR 0007 it is dropped as out of scope and listed in the report.
- **Missed parameters (4):** `title` and `author` on `/search.json`, which appear only in examples; `editions.sort`, mentioned only in the notes; and `details` on `/api/books`, described as kept for backward compatibility.

### Notes on the Frankfurter results

- **Missed:** `GET /coverage`. It is in the OpenAPI file but not on the docs page, so it cannot be found from the docs. It stays in the reference; see ADR 0005.
- **Extras:** `/rates.csv`, a format of `/rates`. The v1 routes from the v1 docs page (`/latest`, `/{date}`, `/{start_date}..{end_date}`, `/{start_date}..`) are dropped as out of scope since ADR 0007.
- **Parameters:** `/providers/{provider}/rates` gets its parameters copied from `/rates` since ADR 0008, because the docs only say it "supports the same parameters as `/v2/rates`". The remaining misses are `providers` on `/currencies` and, in some runs, `date` on the single-pair provider route.
- **Cost:** until the depth change, the ingest crawled 20 pages of the site (the default limit), though only the home page documents v2. It now reads just that page: one call, about 8,000 input tokens, most of which is the fixed prompt and schema.

### Notes on the PokéAPI results

- **Found:** 101 of 102 endpoints. That is all 51 `/<resource>/{id}` detail routes, plus 50 list routes expanded from the docs' one generic `GET /api/v2/{endpoint}/` (ADR 0006).
- **Missed:** `/meta`, which does not appear as an endpoint on the docs page.
- **Extras:** `/item-price/{id}` is documented on the docs page but is not in OpenAPI 2.10.0, and the expansion adds its list route `/item-price` too.
- **Parameters:** all 44 misses are `q` on the list routes. The OpenAPI file lists `q`, but the docs say the public deployment allows only `limit` and `offset`, and blocks other query parameters. Whether the reference should keep `q` is a reference question for its own PR.

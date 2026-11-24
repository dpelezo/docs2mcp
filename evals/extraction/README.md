# Extraction eval

How well `docs2mcp extract` recovers an API's real endpoints and parameters from its docs. The rules are in [ADR 0005](../../docs/adr/0005-extraction-scoring.md).

- `reference/<id>.json` is the ground truth. PokéAPI, Frankfurter and Postcodes.io are generated from their own OpenAPI files with `just references`. Open Library's and both Open-Meteo ones are written by hand from their docs pages (`source.kind: "manual"`).
- `additions/<id>.json` adds the routes and parameters an official file leaves out but the docs document and the server serves, each with its evidence. Today that is only Postcodes.io: 3 routes and 12 parameters. The reference marks every addition with a `note`, and `just references` fails if the official file already covers one, so stale additions get noticed.
- `results/<id>.json` is the score of one recorded run, with the run's code version, model, ingest command and token use.

## Score a run

```bash
uv run docs2mcp score specs/frankfurter/spec.json \
  --reference evals/extraction/reference/frankfurter.json \
  --out evals/extraction/results/frankfurter.json
```

## Results

Latest run: out-of-scope endpoints (ADR 0007), `claude-haiku-5-5`, on the same ingested chunks as the baseline. Frankfurter and Open Library ran 3 times; the table shows run 1 and the notes give the spread. Each ingest used exactly the pages in `sources.toml`, except Frankfurter (see its notes).

| API | Endpoints found | Endpoint precision | Parameters found | Parameter precision | Calls | Input / output tokens |
|---|---|---|---|---|---|---|
| Open-Meteo forecast | 1 / 1 (100%) | 100% | 26 / 27 (96%) | 100% | 2 | 18,412 / 4,155 |
| Open-Meteo geocoding | 2 / 2 (100%) | 100% | 7 / 7 (100%) | 100% | 1 | 5,655 / 1,926 |
| Postcodes.io | 14 / 14 (100%) | 100% | 20 / 27 (74%) | 100% | 6 | 69,865 / 10,185 |
| Open Library | 11 / 11 (100%) | 100% | 14 / 18 (78%) | 100% | 2 | 20,113 / 6,763 |
| Frankfurter | 8 / 9 (89%) | 89% (9 extracted) | 11 / 20 (55%) | 92% | 6 | 90,635 / 12,865 |
| PokéAPI | 101 / 102 (99%) | 98% (103 extracted) | 100 / 144 (69%) | 100% | 8 | 109,165 / 17,018 |
| **Total** | **137 / 139 (99%)** | **98% (140 extracted)** | **178 / 243 (73%)** | **99%** | **25** | **313,845 / 52,912** |

Every run produced a valid `spec.json` with no rejected endpoints and no failed batches. The two missed endpoints are routes their docs never mention (Frankfurter's `/coverage`, PokéAPI's `/meta`).

### History

Each change is measured on the same ingested chunks, before and after. Moves on APIs a change does not touch show run-to-run variation; from ADR 0007 on, the targeted APIs run 3 times.

| Change | Endpoints found | Endpoint precision | Parameters found | Parameter precision |
|---|---|---|---|---|
| Baseline (#18, #19) | 87 / 139 (63%) | 94% | 78 / 99 (79%) | 99% |
| Resource templates (ADR 0006) | 137 / 139 (99%) | 94% | 184 / 243 (76%) | 99% |
| Out-of-scope endpoints (ADR 0007) | 137 / 139 (99%) | 98% | 178 / 243 (73%) | 99% |

- **PokéAPI:** 51 → 101 endpoints. Claude flagged `GET /{endpoint}` and it expanded into 51 list routes. The parameter denominator grows because the list routes' `limit`, `offset` and `q` are now scored.
- **Frankfurter:** nothing was flagged, so its v1 `/{date}` was correctly left alone. This run extracted two more v1 routes, so precision fell from 73% to 62%, and 4 more parameters, so recall rose from 55% to 75%. That is run-to-run variation, and all the extras come from the v1 page that change 4 (crawl scope) removes.
- **Postcodes.io:** parameters went from 20 to 22 of 27 (variation). Open-Meteo and Open Library are unchanged.

Out-of-scope endpoints (ADR 0007):

- **Open Library:** precision 92% → 100% in all 3 runs. Every run dropped the covers route (`served from https://covers.openlibrary.org`).
- **Frankfurter:** precision 62% → 89% in all 3 runs. Every run dropped the v1 routes (4 or 5 per run, including v1's `/currencies`, while v2's was kept). The one extra left is `/rates.csv`.
- **Frankfurter parameters vary between runs:** recall was 55%, 90% and 75% (mean 73%), with no difference in the code. This is the clearest measure of run-to-run noise so far; the 55% in the table is simply run 1.
- **No other API dropped anything,** so the check had no false positives. The other APIs moved by at most 2 parameters (variation).

### Notes on the Open-Meteo results

- **Forecast:** the only miss is `minutely_15`, which the page documents in its 15-Minutely section, not in the parameter table.
- **Geocoding:** complete.

### Notes on the Postcodes.io results

- **Endpoints:** all 14 found, including the 3 added by hand.
- **Missed parameters (7 in the latest run):** the aliases `query`, `latitude` and `longitude` and `filter` on `GET /postcodes`; `query` on `GET /places`; and `limit` and `radius` on `GET /postcodes/{postcode}/nearest`, which its docs page documents. The `query` aliases come and go between runs (missed in the baseline and this run, found in the ADR 0006 run).
- **No extras.** The baseline's first scoring counted `filter`, `limit`, `radius` and `widesearch` on `POST /postcodes` as extras, but they are documented query parameters on the bulk pages. The reference was missing them, not the extraction. They were added to the reference (approved), and the same `spec.json` was re-scored: parameter precision went from 80% to 100%.
- **Required/optional mismatches (4):** `q`, `lon` and `lat` on `GET /postcodes`, and `q` on `GET /places`, were extracted as required. Each is required for one mode of a route that serves several (search, or reverse geocoding), so the reference marks them optional.

### Notes on the Open Library results

- **Endpoints:** all 11 found.
- **No extras.** The covers image URL, on `covers.openlibrary.org`, used to be extracted under the main base URL. Since ADR 0007 it is dropped as out of scope and listed in the report.
- **Missed parameters (4):** `title` and `author` on `/search.json`, which appear only in examples; `editions.sort`, mentioned only in the notes; and `details` on `/api/books`, described as kept for backward compatibility.

### Notes on the Frankfurter results

- **Missed:** `GET /coverage`. It is in the OpenAPI file but not on the docs page, so it cannot be found from the docs. It stays in the reference; see ADR 0005.
- **Extras:** `/rates.csv`, a format of `/rates`. The v1 routes from the v1 docs page (`/latest`, `/{date}`, `/{start_date}..{end_date}`, `/{start_date}..`) are dropped as out of scope since ADR 0007.
- **Parameters:** most misses are on `/providers/{provider}/rates`. The docs only say it "supports the same parameters as `/v2/rates`".
- **Cost:** the crawl fetched 20 pages (the default limit), but only the home page documents v2. Each call also carries about 10,000 tokens of fixed prompt and schema.

### Notes on the PokéAPI results

- **Found:** 101 of 102 endpoints. That is all 51 `/<resource>/{id}` detail routes, plus 50 list routes expanded from the docs' one generic `GET /api/v2/{endpoint}/` (ADR 0006).
- **Missed:** `/meta`, which does not appear as an endpoint on the docs page.
- **Extras:** `/item-price/{id}` is documented on the docs page but is not in OpenAPI 2.10.0, and the expansion adds its list route `/item-price` too.
- **Parameters:** all 44 misses are `q` on the list routes. The OpenAPI file lists `q`, but the docs say the public deployment allows only `limit` and `offset`, and blocks other query parameters. Whether the reference should keep `q` is a reference question for its own PR.

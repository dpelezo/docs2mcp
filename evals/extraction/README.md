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

Latest run: resource templates (ADR 0006), `claude-haiku-5-5`, one run per API, on the same ingested chunks as the baseline. Each ingest used exactly the pages in `sources.toml`, except Frankfurter (see its notes).

| API | Endpoints found | Endpoint precision | Parameters found | Parameter precision | Calls | Input / output tokens |
|---|---|---|---|---|---|---|
| Open-Meteo forecast | 1 / 1 (100%) | 100% | 26 / 27 (96%) | 100% | 2 | 17,968 / 3,694 |
| Open-Meteo geocoding | 2 / 2 (100%) | 100% | 7 / 7 (100%) | 100% | 1 | 5,433 / 1,680 |
| Postcodes.io | 14 / 14 (100%) | 100% | 22 / 27 (81%) | 100% | 6 | 68,533 / 9,163 |
| Open Library | 11 / 11 (100%) | 92% (12 extracted) | 14 / 18 (78%) | 100% | 2 | 19,669 / 6,902 |
| Frankfurter | 8 / 9 (89%) | 62% (13 extracted) | 15 / 20 (75%) | 94% | 6 | 89,303 / 12,317 |
| PokéAPI | 101 / 102 (99%) | 98% (103 extracted) | 100 / 144 (69%) | 100% | 8 | 107,389 / 17,270 |
| **Total** | **137 / 139 (99%)** | **94% (145 extracted)** | **184 / 243 (76%)** | **99%** | **25** | **308,295 / 51,026** |

Every run produced a valid `spec.json` with no rejected endpoints and no failed batches. The two missed endpoints are routes their docs never mention (Frankfurter's `/coverage`, PokéAPI's `/meta`).

### History

Each change is measured on the same ingested chunks, one live run before and one after. Moves on APIs a change does not touch show the run-to-run variation of a single run.

| Change | Endpoints found | Endpoint precision | Parameters found | Parameter precision |
|---|---|---|---|---|
| Baseline (#18, #19) | 87 / 139 (63%) | 94% | 78 / 99 (79%) | 99% |
| Resource templates (ADR 0006) | 137 / 139 (99%) | 94% | 184 / 243 (76%) | 99% |

- **PokéAPI:** 51 → 101 endpoints. Claude flagged `GET /{endpoint}` and it expanded into 51 list routes. The parameter denominator grows because the list routes' `limit`, `offset` and `q` are now scored.
- **Frankfurter:** nothing was flagged, so its v1 `/{date}` was correctly left alone. This run extracted two more v1 routes, so precision fell from 73% to 62%, and 4 more parameters, so recall rose from 55% to 75%. That is run-to-run variation, and all the extras come from the v1 page that change 4 (crawl scope) removes.
- **Postcodes.io:** parameters went from 20 to 22 of 27 (variation). Open-Meteo and Open Library are unchanged.

### Notes on the Open-Meteo results

- **Forecast:** the only miss is `minutely_15`, which the page documents in its 15-Minutely section, not in the parameter table.
- **Geocoding:** complete.

### Notes on the Postcodes.io results

- **Endpoints:** all 14 found, including the 3 added by hand.
- **Missed parameters (5):** the official file's aliases `latitude` and `longitude`, plus `filter` on `GET /postcodes`. Also `limit` and `radius` on `GET /postcodes/{postcode}/nearest`, which its docs page documents. The baseline run also missed `query` on `GET /postcodes` and `GET /places`.
- **No extras.** The baseline's first scoring counted `filter`, `limit`, `radius` and `widesearch` on `POST /postcodes` as extras, but they are documented query parameters on the bulk pages. The reference was missing them, not the extraction. They were added to the reference (approved), and the same `spec.json` was re-scored: parameter precision went from 80% to 100%.
- **Required/optional mismatches (4):** `q`, `lon` and `lat` on `GET /postcodes`, and `q` on `GET /places`, were extracted as required. Each is required for one mode of a route that serves several (search, or reverse geocoding), so the reference marks them optional.

### Notes on the Open Library results

- **Endpoints:** all 11 found.
- **Extra:** the covers image URL (`/a/olid/{}.jpg` in this run, `/a/olid/{}-M.jpg` in the baseline), which lives on `covers.openlibrary.org` but was extracted under the main base URL.
- **Missed parameters (4):** `title` and `author` on `/search.json`, which appear only in examples; `editions.sort`, mentioned only in the notes; and `details` on `/api/books`, described as kept for backward compatibility.

### Notes on the Frankfurter results

- **Missed:** `GET /coverage`. It is in the OpenAPI file but not on the docs page, so it cannot be found from the docs. It stays in the reference; see ADR 0005.
- **Extras:** `/rates.csv` is a format of `/rates`. `/latest`, `/{date}`, `/{start_date}..{end_date}` and `/{start_date}..` come from the v1 docs page that the crawl reached from the site root.
- **Parameters:** most misses are on `/providers/{provider}/rates`. The docs only say it "supports the same parameters as `/v2/rates`".
- **Cost:** the crawl fetched 20 pages (the default limit), but only the home page documents v2. Each call also carries about 10,000 tokens of fixed prompt and schema.

### Notes on the PokéAPI results

- **Found:** 101 of 102 endpoints. That is all 51 `/<resource>/{id}` detail routes, plus 50 list routes expanded from the docs' one generic `GET /api/v2/{endpoint}/` (ADR 0006).
- **Missed:** `/meta`, which does not appear as an endpoint on the docs page.
- **Extras:** `/item-price/{id}` is documented on the docs page but is not in OpenAPI 2.10.0, and the expansion adds its list route `/item-price` too.
- **Parameters:** all 44 misses are `q` on the list routes. The OpenAPI file lists `q`, but the docs say the public deployment allows only `limit` and `offset`, and blocks other query parameters. Whether the reference should keep `q` is a reference question for its own PR.

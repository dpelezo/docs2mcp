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

Baseline: extraction as merged in #11, `claude-haiku-5-5`, one run per API. Each ingest used exactly the pages in `sources.toml`, except Frankfurter (see its notes).

| API | Endpoints found | Endpoint precision | Parameters found | Parameter precision | Calls | Input / output tokens |
|---|---|---|---|---|---|---|
| Open-Meteo forecast | 1 / 1 (100%) | 100% | 26 / 27 (96%) | 100% | 2 | 17,452 / 3,962 |
| Open-Meteo geocoding | 2 / 2 (100%) | 100% | 7 / 7 (100%) | 100% | 1 | 5,175 / 1,578 |
| Postcodes.io | 14 / 14 (100%) | 100% | 20 / 27 (74%) | 100% | 6 | 66,985 / 10,189 |
| Open Library | 11 / 11 (100%) | 92% (12 extracted) | 14 / 18 (78%) | 100% | 2 | 19,153 / 6,494 |
| Frankfurter | 8 / 9 (89%) | 73% (11 extracted) | 11 / 20 (55%) | 92% | 6 | 87,755 / 13,259 |
| PokéAPI | 51 / 102 (50%) | 96% (53 extracted) | n/a: matched endpoints have only path parameters | n/a | 8 | 105,325 / 16,666 |
| **Total** | **87 / 139 (63%)** | **94% (93 extracted)** | **78 / 99 (79%)** | **99%** | **25** | **301,845 / 52,148** |

Every run produced a valid `spec.json` with no rejected endpoints and no failed batches. PokéAPI's 50 list routes, which its docs describe only once as a generic route, account for most of the misses. Without them, endpoints found is 87 / 89 (98%); the other two misses are routes their docs never mention (Frankfurter's `/coverage`, PokéAPI's `/meta`).

### Notes on the Open-Meteo baselines

- **Forecast:** the only miss is `minutely_15`, which the page documents in its 15-Minutely section, not in the parameter table.
- **Geocoding:** complete.

### Notes on the Postcodes.io baseline

- **Endpoints:** all 14 found, including the 3 added by hand.
- **Missed parameters (7):** the official file's aliases `query`, `latitude` and `longitude`, plus `filter` on `GET /postcodes`. Also `limit` and `radius` on `GET /postcodes/{postcode}/nearest`, which its docs page documents.
- **No extras.** The first scoring counted `filter`, `limit`, `radius` and `widesearch` on `POST /postcodes` as extras, but they are documented query parameters on the bulk pages. The reference was missing them, not the extraction. They were added to the reference (approved), and the same `spec.json` was re-scored: parameter precision went from 80% to 100%.
- **Required/optional mismatches (4):** `q`, `lon` and `lat` on `GET /postcodes`, and `q` on `GET /places`, were extracted as required. Each is required for one mode of a route that serves several (search, or reverse geocoding), so the reference marks them optional.

### Notes on the Open Library baseline

- **Endpoints:** all 11 found.
- **Extra:** `GET /a/olid/{}-M.jpg`, the covers URL, which lives on `covers.openlibrary.org` but was extracted under the main base URL.
- **Missed parameters (4):** `title` and `author` on `/search.json`, which appear only in examples; `editions.sort`, mentioned only in the notes; and `details` on `/api/books`, described as kept for backward compatibility.

### Notes on the Frankfurter baseline

- **Missed:** `GET /coverage`. It is in the OpenAPI file but not on the docs page, so it cannot be found from the docs. It stays in the reference; see ADR 0005.
- **Extras:** `/rates.csv` is a format of `/rates`. `/latest` and `/{date}` come from the v1 docs page that the crawl reached from the site root.
- **Parameters:** 7 of the 9 misses are on `/providers/{provider}/rates`. The docs only say it "supports the same parameters as `/v2/rates`".
- **Cost:** the crawl fetched 20 pages (the default limit), but only the home page documents v2. Each call also carries about 10,000 tokens of fixed prompt and schema.

### Notes on the PokéAPI baseline

- **Found:** all 51 `/<resource>/{id}` detail endpoints, from one docs page.
- **Missed:** the 50 list endpoints (`/pokemon`, `/ability`, …) and `/meta`.
  - The docs describe lists once, generically: calling any endpoint without an id returns a paginated list, at `GET /api/v2/{endpoint}/`. Claude extracted exactly that, as one templated endpoint with `limit` and `offset`. It counts as an extra (`/{}`), and the 50 concrete routes count as misses.
  - `/meta` does not appear as an endpoint on the docs page.
- **Extra:** `/item-price/{id}` is documented on the docs page but is not in OpenAPI 2.10.0.
- **Parameters:** not measurable yet. Every matched endpoint has only a path parameter, so the 0/0 ratio is the "nothing to count" 1.0 from ADR 0005, not a result.
  - When list endpoints are matched, note that the OpenAPI file lists a `q` parameter, while the docs say the public deployment allows only `limit` and `offset`.

# Extraction eval

How well `docs2mcp extract` recovers an API's real endpoints and parameters from its docs. The rules are in [ADR 0005](../../docs/adr/0005-extraction-scoring.md).

- `reference/<id>.json` is the ground truth. PokéAPI, Frankfurter and Postcodes.io are generated from their own OpenAPI files with `just references`. Open Library's is written by hand from its docs pages (`source.kind: "manual"`); Open-Meteo's two (forecast and geocoding) come next.
- `additions/<id>.json` adds the routes and parameters an official file leaves out but the docs document and the server serves, each with its evidence. Today that is only Postcodes.io: 3 routes and 8 parameters. The reference marks every addition with a `note`, and `just references` fails if the official file already covers one, so stale additions get noticed.
- `results/<id>.json` is the score of one recorded run, with the run's code version, model, ingest command and token use.

## Score a run

```bash
uv run docs2mcp score specs/frankfurter/spec.json \
  --reference evals/extraction/reference/frankfurter.json \
  --out evals/extraction/results/frankfurter.json
```

## Results

Baseline: extraction as merged in #11, `claude-haiku-5-5`, one run per API.

| API | Endpoints found | Endpoint precision | Parameters found | Parameter precision | Calls | Input / output tokens |
|---|---|---|---|---|---|---|
| Frankfurter | 8 / 9 (89%) | 73% (11 extracted) | 11 / 20 (55%) | 92% | 6 | 87,755 / 13,259 |
| PokéAPI | 51 / 102 (50%) | 96% (53 extracted) | n/a: matched endpoints have only path parameters | n/a | 8 | 105,325 / 16,666 |
| Postcodes.io | not run yet | | | | | |
| Open Library | not run yet (reference written by hand) | | | | | |
| Open-Meteo forecast | needs a hand-written reference | | | | | |
| Open-Meteo geocoding | needs a hand-written reference | | | | | |

Notes on the Frankfurter baseline:

- **Missed:** `GET /coverage`. It is in the OpenAPI file but not on the docs page, so it cannot be found from the docs. It stays in the reference; see ADR 0005.
- **Extras:** `/rates.csv` is a format of `/rates`. `/latest` and `/{date}` come from the v1 docs page that the crawl reached from the site root.
- **Parameters:** 7 of the 9 misses are on `/providers/{provider}/rates`. The docs only say it "supports the same parameters as `/v2/rates`".
- **Cost:** the crawl fetched 20 pages (the default limit), but only the home page documents v2. Each call also carries about 10,000 tokens of fixed prompt and schema.

Notes on the PokéAPI baseline:

- **Found:** all 51 `/<resource>/{id}` detail endpoints, from one docs page.
- **Missed:** the 50 list endpoints (`/pokemon`, `/ability`, …) and `/meta`.
  - The docs describe lists once, generically: calling any endpoint without an id returns a paginated list, at `GET /api/v2/{endpoint}/`. Claude extracted exactly that, as one templated endpoint with `limit` and `offset`. It counts as an extra (`/{}`), and the 50 concrete routes count as misses.
  - `/meta` does not appear as an endpoint on the docs page.
- **Extra:** `/item-price/{id}` is documented on the docs page but is not in OpenAPI 2.10.0.
- **Parameters:** not measurable yet. Every matched endpoint has only a path parameter, so the 0/0 ratio is the "nothing to count" 1.0 from ADR 0005, not a result.
  - When list endpoints are matched, note that the OpenAPI file lists a `q` parameter, while the docs say the public deployment allows only `limit` and `offset`.

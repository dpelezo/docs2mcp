# Extraction eval

How well `docs2mcp extract` recovers an API's real endpoints and parameters from its docs. The rules are in [ADR 0005](../../docs/adr/0005-extraction-scoring.md).

- `reference/<id>.json` is the ground truth. PokéAPI and Frankfurter are generated from their own OpenAPI files with `just references`. The other three APIs get hand-written references in the same format.
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
| PokéAPI | not run yet | | | | | |
| Open-Meteo, Open Library, REST Countries | need hand-written references | | | | | |

Notes on the Frankfurter baseline:

- **Missed:** `GET /coverage`. It is in the OpenAPI file but not on the docs page, so it cannot be found from the docs. It stays in the reference; see ADR 0005.
- **Extras:** `/rates.csv` is a format of `/rates`. `/latest` and `/{date}` come from the v1 docs page that the crawl reached from the site root.
- **Parameters:** 7 of the 9 misses are on `/providers/{provider}/rates`. The docs only say it "supports the same parameters as `/v2/rates`".
- **Cost:** the crawl fetched 20 pages (the default limit), but only the home page documents v2. Each call also carries about 10,000 tokens of fixed prompt and schema.

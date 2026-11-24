# ADR 0005: Score extraction against the APIs' own specs

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

Extraction (ADR 0004) works on real docs, but "it produced 11 endpoints" says nothing about whether they are the right 11. The first live run on Frankfurter showed why that matters: it missed a route, added three that are not part of the v2 API, and found only one of the eight parameters of `/providers/{provider}/rates`. Changes to the prompt, batching or crawl need a number that shows whether they helped, and the number has to come from outside docs2mcp.

Two eval APIs publish an OpenAPI file in their own repository: PokéAPI and Frankfurter. Open-Meteo, Open Library and REST Countries do not.

## Decision

### References

A reference (`evals/extraction/reference/<id>.json`) lists an API's operations and their parameters, with paths relative to the `base_url` in `sources.toml`. Nothing else is in it, so it is short enough to review.

- **From OpenAPI** where it exists: `scripts/reference_from_openapi.py` converts the file and records its URL, version, sha256 and the retrieval date, so an upstream change shows up in review. The reference is the whole official spec, not only what the docs mention. A route the docs never document (Frankfurter's `/coverage`) counts as a miss, and the results note why. Editing the reference to fit the input would make the score say more than it measures.
- **By hand** for the other three, in the same format (`source.kind: "manual"`), from their docs pages.

### Rules

These were approved before the scorer was written, as `AGENTS.md` requires.

- An extracted endpoint matches a reference endpoint when the method and the path shape agree. Placeholder names and trailing slashes are ignored, so `/providers/{key}` matches `/providers/{provider}`.
- **Endpoint recall** = matched / reference endpoints. **Precision** = matched / extracted endpoints, so extras such as a `.csv` variant or a route from another API version count against it.
- **Parameters** are scored on matched endpoints only, for query and header parameters, by location and exact name. Path parameters are implied by the route match, and their names are free.
- A matched parameter whose `required` flag differs is counted and reported, but not penalised.
- A ratio with nothing to count is 1.0.

`docs2mcp score SPEC --reference REF [--out FILE]` prints the report and can write it as JSON.

### Keeping it honest

- The scorer, the references and the baseline land in one pull request. Changes to extraction land in later ones, each with before and after scores.
- Scorer rules and references change only in pull requests that change nothing else, following the same rule.

### Amendment (2026-11-24): hand additions to an official file

Postcodes.io's OpenAPI file leaves out 3 routes and 12 parameters that its docs pages document and its server serves. Four of the parameters (on bulk `POST /postcodes`) were found only when the first live run counted them as extras. Scoring against the file as published would mark correct extractions as wrong. So `evals/extraction/additions/<id>.json` can add routes and parameters, under these rules:

- Every addition needs a `note` with its evidence: the docs section and the server code at a named commit.
- Additions are applied by `scripts/reference_from_openapi.py`, and the reference keeps the `note` on each one, so a reviewer can tell official entries from added ones.
- An addition the official file already covers is an error. When upstream fixes its file, the build fails and the addition is removed.
- An addition must be both documented and served. Routes the server serves but the docs never mention (Postcodes.io's `/validate`) are not added, because the extractor has no way to find them.
- Additions only add. Nothing in an official file is removed or changed, so routes in the official file that the docs never mention (Frankfurter's `/coverage`) still count.

Postcodes.io replaced REST Countries as an eval API. REST Countries' open v3.1 URLs now redirect, and its v5 API needs a key for every request.

## Consequences

- Every extraction change can be judged by numbers rather than impressions.
- Endpoint and parameter scores ignore types, descriptions and defaults. They measure whether the right calls exist, which is what an agent needs first. Richer checks can come with the tool-use eval.
- Request bodies are not scored; none of the eval APIs uses them.
- The hand-written references are only as good as one careful reading of the docs, and they are reviewed like code.

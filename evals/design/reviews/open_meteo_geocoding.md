# Design review: Open-Meteo geocoding

- **API:** Open-Meteo Geocoding API (2 endpoints)
- **tools.json:** Sonnet 5.5 run 1 on the owner's machine (`tools/open_meteo_geocoding/v2/sonnet/run1/`); not committed, because the docs are AGPL
- **Reviewer and date:** drafted by Claude on 2026-11-24; confirmed by the owner: *pending*

## Each tool

1. **When to use it: pass.** There is one tool, `location_lookup`: search by name or postal code, or resolve a known id.
2. **What to pass: pass.** `name` explains the "Paris, France" qualifier, `id` gives an example, and the search-only arguments say they are search-only.
3. **One task: pass.** Search and resolve-by-id are the same lookup by different keys.
4. **Route choice: pass.** The `id` route comes first, and the description says what happens when both are given.

## The whole design

5. **No overlap: pass.**
6. **Exclusions: pass.** All 7 of 7 parameters are exposed.
7. **Faithful: fail (minor).** The description says search results include "coordinates". The spec only says the search returns a list of matches.

## Verdict

- **Fit to approve:** no, until question 7 passes. A one-word edit fixes it.
- **Failures to fix first:** drop "coordinates" from the description.
- **Notes:** `format` allows `protobuf`, which is not JSON. An agent has no use for it, so omitting it would be reasonable.

# ADR 0007: Claude reports where each endpoint is served, code drops the ones outside the base URL

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

A spec has one `base_url`, but docs often show endpoints that live somewhere else. The extraction eval (ADR 0005) found two examples:

- **Open Library** documents cover images at `covers.openlibrary.org/a/olid/...jpg`. Claude extracted the route with a relative path, `/a/olid/{olid}.jpg`, and mentioned the host only in its description. The spec then claimed the route exists on `openlibrary.org`.
- **Frankfurter** documents its old v1 API (`api.frankfurter.dev/v1`) next to v2. Four v1 routes (`/latest`, `/{date}`, `/{start_date}..{end_date}`, `/{start_date}..`) ended up in the v2 spec.

These lowered endpoint precision to 92% and 62%. A generated tool would call these routes on the wrong host or version and fail.

The wire schema had no field for the host, so code could not tell these endpoints apart from the real ones.

## Decision

- **Claude reports a fact.** `WireEndpoint` gains a required string, `served_from`: the base URL the docs show this endpoint being called on (scheme, host and any version path). The prompt asks Claude to copy it from the example URLs, and not to normalise it to the main base URL. When the docs give only a relative path, Claude uses the API's base URL.
- **Code decides** (`outside_base`). It builds the endpoint's full URL from `served_from` plus its path (or uses the path, if it is absolute) and checks that it is under the chosen base URL. The comparison ignores the scheme, a leading `www.` and trailing slashes, and matches the path on segment boundaries, so `/v20` is not under `/v2`. An empty or unparsable `served_from` counts as the base URL, so a missing value never drops an endpoint.
- **Out-of-scope endpoints are dropped, not repaired.** Each one is listed in the report under `out_of_scope`, with where it is served from, and the CLI prints them.

## Alternatives considered

- **Ask Claude to leave out other hosts.** Rejected: whether an endpoint belongs to the spec depends on the base URL chosen, which is decided in code across all batches, sometimes from `--base-url`. Claude reports what the docs say, and code applies the choice.
- **Absolute paths for other hosts.** Rejected: it overloads `path` with two meanings, and relative paths with a host in the prose (the Open Library case) would still slip through.

## Consequences

- Endpoints on other hosts or versions no longer appear in a spec, and every drop is visible in the report.
- If Claude wrongly fills in `served_from` (for example with the docs site URL), real endpoints are dropped. The report lists every drop, and the extraction eval shows any loss in recall.
- An API whose endpoints really span several hosts is still not representable in one spec. That needs per-endpoint servers in `ApiSpec`, which is a separate decision. Open-Meteo was split into two eval APIs for this reason.
- The prompt and schema changed, so earlier recordings no longer replay.

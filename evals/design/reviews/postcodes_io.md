# Design review: Postcodes.io

- **API:** Postcodes.io (14 endpoints)
- **tools.json:** Sonnet 5.5 run 1 (ADR 0012), as committed in `evals/design/replay/postcodes_io/tools.json` at 5cece07. That folder now holds a Haiku recording made after the no-extra-claims prompt change.
- **Reviewer and date:** drafted by Claude on 2026-11-24; confirmed by the owner (dpelezo) on 2026-11-24, all answers as drafted

## Each tool

1. **When to use it: pass.** There are 11 tools. Lookup, bulk lookup, search, nearest, autocomplete and random each have their own tool. Terminated and Scottish postcodes are separate tools, and their descriptions say when to use them.
2. **What to pass: pass.** Postcodes say spacing and case don't matter, coordinates give their WGS84 ranges, and limits and radii give their maximums and defaults.
3. **One task: pass, with a note.** `nearest_outcodes` combines "near an outcode" and "near a coordinate", which is the same search by different keys. Note: `places` also merges "random place" (no arguments) with lookup and search. That's defensible as "get one place", but an agent that forgets `q` gets a random place instead of an error.
4. **Route choice: pass.** `outcode`, `lon` with `lat`, `code` and `q` each select exactly one route.

## The whole design

5. **No overlap: pass.**
6. **Exclusions: pass.** Nothing is excluded. The 2 unexposed parameters are `query` on the two search routes, and the design notes that each is an alias of `q`.
7. **Faithful: pass, against the spec.** But see the spec problem below.

## Verdict

- **Fit to approve:** not yet, because of a spec problem rather than a design problem.
- **Failures to fix first:** the extracted spec makes the bulk endpoint require both `postcodes` and `geolocations`. The real API takes one or the other, as two separate request bodies. The design faithfully tells the agent to pass both. Fix the extraction first, then design again.
- **Notes:** runs 2 and 3 also made 11 tools. Run 2 grouped exactly as run 1 and differed only in two names. Run 3 regrouped the outcode endpoints.

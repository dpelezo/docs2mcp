# Design review: Frankfurter

- **API:** Frankfurter (9 endpoints)
- **tools.json:** `evals/design/replay/frankfurter/tools.json`, Sonnet 5.5 run 1 (ADR 0012), replayed in CI
- **Reviewer and date:** drafted by Claude on 2026-11-24; confirmed by the owner: *pending*

## Each tool

1. **When to use it: pass.** There are four tools:
   - `exchange_rates`: blended rates;
   - `provider_rates`: one provider's rates;
   - `currencies`;
   - `providers`.
   
   The two rate tools point to each other.
2. **What to pass: pass.** Currency codes come with examples, dates with their YYYY-MM-DD format, and `quotes` is described as comma-separated. `group` and `expand` carry the spec's enums.
3. **One task: pass.** A single-pair rate and a list or time series of rates are variants of "get rates". Get and list are paired for currencies and for providers.
4. **Route choice: pass, with a note.** The single-pair route requires `base` and `quote`, and the description says so. Note: passing `quote` without `base` falls through to the list route, which ignores `quote`.

## The whole design

5. **No overlap: pass.**
6. **Exclusions: pass.** `get_rates_csv` is excluded because it returns CSV, and the same rates are available as JSON. All 27 of 27 parameters are exposed.
7. **Faithful: pass.** Every claim matches the spec's summaries and parameter descriptions.

## Verdict

- **Fit to approve:** yes.
- **Failures to fix first:** none.
- **Notes:** `start_date` and `end_date` map to the API's `from` and `to`, which is a clearer name for an agent. Runs 2 and 3 also made four tools, but grouped the rate endpoints the other way: one tool for single-pair rates (blended or from one provider) and one for lists and time series. Both groupings pass question 3.

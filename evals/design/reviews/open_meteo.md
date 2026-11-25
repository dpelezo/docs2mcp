# Design review: Open-Meteo forecast

- **API:** Open-Meteo Weather Forecast API (1 endpoint)
- **tools.json:** Sonnet 5.5 run 1 on the owner's machine (`tools/open_meteo/v2/sonnet/run1/`); not committed, because the docs are AGPL
- **Reviewer and date:** drafted by Claude on 2026-11-24; confirmed by the owner (dpelezo) on 2026-11-24, all answers as drafted

## Each tool

1. **When to use it: pass.** There is one tool, `weather_forecast`. Its description says what selects the data returned (`hourly`, `daily`, `current`) and which arguments set the time range.
2. **What to pass: pass, with a note.** Units and formats carry their enums and defaults, and dates and hours give their formats. Note: `latitude` and `longitude` are numbers, but their descriptions allow comma-separated lists. The spec's type and description disagree, so that is extraction's issue, not the design's.
3. **One task: pass.**
4. **Route choice: pass.** There is one route.

## The whole design

5. **No overlap: pass.**
6. **Exclusions: pass.** 25 of 26 parameters are exposed. `apikey` is omitted because it is only needed for commercial use.
7. **Faithful: pass.**

## Verdict

- **Fit to approve:** yes.
- **Failures to fix first:** none.
- **Notes:** Haiku exposed 14 of 26 before the parameter rule (ADR 0012). Sonnet runs 2 and 3 exposed all 26.

## Re-check after #36 (draft)

- **tools.json:** Haiku 5.5 run 1 on the repaired spec, on the owner's machine (`tools/open_meteo/v4/haiku/run1/`).
- **Reviewer and date:** drafted by Claude on 2026-11-24; confirmed by the owner: *pending*.

`latitude` is now an array of numbers, because its description says values can be comma-separated. `longitude` is still a number, because the extracted spec only says this for latitude. The docs probably say it once for both. That note on question 2 is still open in #36.

This Haiku design also says at least one of `hourly`, `daily` or `current` "is needed to get data back", which the spec does not state. It is a minor question 7 fail, so the Sonnet design reviewed above stays the one fit to approve.

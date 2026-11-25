# Design review: PokéAPI

- **API:** PokéAPI (103 endpoints)
- **tools.json:** `evals/design/replay/pokeapi/tools.json`, Sonnet 5.5 run 1 (ADR 0012), replayed in CI
- **Reviewer and date:** drafted by Claude on 2026-11-24; confirmed by the owner (dpelezo) on 2026-11-24, all answers as drafted

## Each tool

1. **When to use it: pass.** 52 tools, one per resource (`berry`, `move`, `pokemon_species` and so on). Each says it gets one item by id or name, or lists the resource when the argument is left out.
2. **What to pass: pass.** `id_or_name` and `id` say which is accepted. `limit` and `offset` say they page the list, and `limit` carries the spec's default of 20.
3. **One task: pass.** Every multi-route tool pairs "get one" with "list" for the same resource. Nothing unrelated is merged.
4. **Route choice: pass, with a note.** The get route requires the id, and the list route requires nothing, so the order is right. Note: `pokemon_encounters` has one route that requires `id_or_name`, but the argument is not marked required. A call without it would match no route. Marking it required would be clearer.

## The whole design

5. **No overlap: pass.** Resource names are distinct. `type` became `pokemon_type` and `version` became `game_version`, so no name clashes with a Python soft keyword.
6. **Exclusions: pass.** Nothing is excluded, and all 154 of 154 parameters are exposed.
7. **Faithful: fail (minor).** A few descriptions add details from general knowledge of PokéAPI that the spec does not state:
   - `pokemon`: "(stats, types, abilities, moves, sprites)";
   - `machine`: "(TM/HM)";
   - `pokemon_species`: "e.g. flavor text, evolution chain, egg groups".
   
   They are accurate for the real API, but the rule is that descriptions claim only what the spec supports.

## Verdict

- **Fit to approve:** no, until question 7 passes.
- **Failures to fix first:** trim the three extra claims, by hand in `tools.json` or with a prompt change measured on the tuned APIs.
- **Notes:** this is the biggest win in the eval: 103 tools become 52, and the tool list drops from about 9,600 tokens to about 6,000 (runs 2 and 3: 6,539 and 7,288).

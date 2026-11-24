# ADR 0008: Claude reports shared parameters, code copies them

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

Docs often describe parameters once and refer back to them. Frankfurter says `/v2/providers/{provider}/rates` "supports the same parameters as `/v2/rates` and `/v2/rate`", and lists none of its own.

In three identical runs (#22), Claude copied 0, 6 and 4 of the 7 shared parameters onto that route. This was the largest gap in Frankfurter's parameters, and the main reason its parameter recall ranged from 55% to 90% between runs.

The referenced endpoint is often in another batch, so Claude cannot reliably copy parameters it is not looking at.

## Decision

- **Claude reports the reference.** `WireEndpoint` gains a required list, `same_parameters_as`: the paths of endpoints whose parameters the docs say this one also accepts, as written in the docs. It is empty otherwise. Claude still lists parameters documented for the endpoint itself.
- **Code copies the parameters** (`apply_shared_parameters`), after duplicates are merged, so every batch's endpoints are available.
  - Each path is normalised against the base URL (`/v2/rates` becomes `/rates`) and matched by method and route, then by route alone.
  - The query and header parameters of the matched endpoint are copied. Path parameters are never copied, and parameters the endpoint already has are kept as they are.
- **Every copy is reported** under `shared_parameters`, including references that matched nothing.

### Refinement after the first live run

In one of the first three runs, Claude also attached the shared parameters to the single-pair route, giving it 7 parameters it does not take. The prompt now says that when one sentence covers a group of routes, each route lists only the endpoint it mirrors. It uses a generic example rather than Frankfurter's, so as not to tune to the eval. In the next three runs, Frankfurter's parameter precision was 90% every time.

## Alternatives considered

- **A prompt rule alone ("copy the shared parameters").** Rejected: it fails whenever the referenced endpoint is in another batch, and the #22 runs show it is unstable even when both are in the same batch.
- **Inferring shared parameters from similar paths.** Rejected: guessing.

## Consequences

- Shared parameters arrive the same way in every run. They no longer depend on what Claude copied in that run.
- Copying everything the referenced endpoint has can add parameters that do not fit, such as a `providers` filter copied onto a single provider's route. These show up as lower parameter precision in the eval.
- References are resolved one level deep (A shares B, B shares C copies only B's parameters). This is enough for the docs seen so far.
- The prompt and schema changed, so earlier recordings no longer replay.

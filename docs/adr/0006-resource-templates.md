# ADR 0006: Claude flags resource templates, code expands them

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

Some docs describe a group of routes once, generically. PokéAPI says that calling any endpoint without an id returns a paginated list, at `GET /api/v2/{endpoint}/`, and never lists the 50 list routes one by one. Extraction recorded that faithfully as one templated route. The baseline (ADR 0005) therefore found 51 of PokéAPI's 102 endpoints, and the 50 list routes were the largest single gap across all six eval APIs.

An agent cannot call `GET /{endpoint}` without knowing which values are valid, so the spec should list the concrete routes.

The hard part is telling a resource template from an ordinary route. Frankfurter's v1 `GET /{date}` has the same shape as PokéAPI's `GET /{endpoint}`, but its placeholder is a value.

## Decision

- **Claude decides what is a template.** The wire schema gains one required field, `for_every_resource` (a boolean, so it stays within the structured-output limits). The prompt asks for `true` only when the docs say one route works for every resource, with the resource as its first placeholder, and says that a placeholder holding a value (a date, an id, a code) is not a resource.
- **Code does the expansion** (`expand_resource_templates`):
  - The resources are the first path segments of the other extracted endpoints that have more than one segment (`pokemon` from `/pokemon/{id}`).
  - Each template becomes one route per resource. The first placeholder is replaced by the resource name, and its path parameter is dropped. Every other parameter and the rest of the path are kept.
  - Routes that already exist are not added again. Duplicate templates from several chunks are expanded once.
- **A template that cannot be expanded is kept as documented.** This happens when its path doesn't start with a placeholder or when no other endpoint names a resource.
- **Every expansion is recorded in the report** under `expanded`, separately from repairs and merges, and the CLI prints it.

## Alternatives considered

- **A rule in code alone, such as "any route whose path is a single placeholder".** Rejected: it would expand Frankfurter's `/{date}` into nonsense, and guarding against that means guessing from names.
- **Asking Claude to list the concrete routes itself.** Rejected: the template and the resources are usually in different batches, so no single call sees both.

## Consequences

- PokéAPI's list routes become real, callable routes in the spec.
- A wrong flag is cheap to spot, because the report names every template and how many routes it produced, and the extraction eval scores the result.
- Any resource that has a detail route gets a list route, even when the API has none. `/item-price/{id}` is documented in PokéAPI's docs but not in its OpenAPI file, so it will produce an extra `/item-price`. Such cases show up as lower precision.
- Changing the prompt changes the recording keys, so earlier recordings no longer replay. All six APIs are re-run for the before/after comparison.

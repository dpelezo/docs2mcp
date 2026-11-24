"""Score an extracted spec against a reference.

The rules (approved before the scorer was written; see ADR 0005):

- An extracted endpoint matches a reference endpoint when the method and the
  path shape agree. Placeholder names do not matter: `/providers/{key}` matches
  `/providers/{provider}`. Trailing slashes do not matter.
- Endpoint recall is matched / reference endpoints; precision is matched /
  extracted endpoints. Extras, such as a `.csv` variant or a route from another
  API version, lower precision.
- Parameters are scored on matched endpoints only, by location and exact name,
  for query and header parameters. Path parameters are implied by the route
  match, and their names are free.
- Matched parameters whose required flag differs are counted, not penalised.
- A ratio with nothing to count is 1.0: no expected parameters means nothing
  was missed, and no extracted ones means nothing was wrong.
"""

from dataclasses import dataclass, field

from docs2mcp.evals.reference import Reference, route_key
from docs2mcp.spec import ApiSpec, ParamLocation

_SCORED = (ParamLocation.QUERY, ParamLocation.HEADER)


@dataclass(frozen=True)
class Counts:
    matched: int
    expected: int
    found: int

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0

    @property
    def precision(self) -> float:
        return self.matched / self.found if self.found else 1.0

    def __add__(self, other: Counts) -> Counts:
        return Counts(
            self.matched + other.matched, self.expected + other.expected, self.found + other.found
        )


@dataclass(frozen=True)
class EndpointResult:
    route: str
    parameters: Counts
    missing: list[str] = field(default_factory=list[str])
    extra: list[str] = field(default_factory=list[str])
    required_mismatches: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class ExtractionScore:
    api: str
    endpoints: Counts
    parameters: Counts
    missing_endpoints: list[str]
    extra_endpoints: list[str]
    matched: list[EndpointResult]

    @property
    def required_mismatches(self) -> int:
        return sum(len(e.required_mismatches) for e in self.matched)


def score(spec: ApiSpec, reference: Reference) -> ExtractionScore:
    expected = {route_key(e.method, e.path): e for e in reference.endpoints}
    found = {route_key(e.method, e.path): e for e in spec.endpoints}

    results: list[EndpointResult] = []
    for route, want in expected.items():
        got = found.get(route)
        if got is None:
            continue
        want_params = {
            (p.location, p.name): p.required for p in want.parameters if p.location in _SCORED
        }
        got_params = {
            (p.location, p.name): p.required for p in got.parameters if p.location in _SCORED
        }
        both = want_params.keys() & got_params.keys()
        results.append(
            EndpointResult(
                route=route,
                parameters=Counts(len(both), len(want_params), len(got_params)),
                missing=sorted(f"{loc}:{name}" for loc, name in want_params.keys() - both),
                extra=sorted(f"{loc}:{name}" for loc, name in got_params.keys() - both),
                required_mismatches=sorted(
                    f"{loc}:{name}"
                    for loc, name in both
                    if want_params[loc, name] != got_params[loc, name]
                ),
            )
        )

    parameters = sum((r.parameters for r in results), Counts(0, 0, 0))
    return ExtractionScore(
        api=reference.api,
        endpoints=Counts(len(results), len(expected), len(found)),
        parameters=parameters,
        missing_endpoints=sorted(expected.keys() - found.keys()),
        extra_endpoints=sorted(found.keys() - expected.keys()),
        matched=results,
    )


def format_score(result: ExtractionScore) -> str:
    """A short plain-text report for the terminal and for PR descriptions."""
    e, p = result.endpoints, result.parameters
    lines = [
        f"{result.api}: endpoints {e.matched}/{e.expected} found "
        f"(recall {e.recall:.0%}, precision {e.precision:.0%}, {e.found} extracted)",
        f"parameters on matched endpoints: {p.matched}/{p.expected} "
        f"(recall {p.recall:.0%}, precision {p.precision:.0%}), "
        f"{result.required_mismatches} required/optional mismatches",
    ]
    if result.missing_endpoints:
        lines.append("missing endpoints: " + ", ".join(result.missing_endpoints))
    if result.extra_endpoints:
        lines.append("extra endpoints: " + ", ".join(result.extra_endpoints))
    for r in result.matched:
        parts = [
            f"missing {', '.join(r.missing)}" if r.missing else "",
            f"extra {', '.join(r.extra)}" if r.extra else "",
            f"required differs {', '.join(r.required_mismatches)}" if r.required_mismatches else "",
        ]
        detail = "; ".join(x for x in parts if x)
        if detail:
            lines.append(f"  {r.route}: {detail}")
    return "\n".join(lines)


def score_json(result: ExtractionScore) -> dict[str, object]:
    def counts(c: Counts) -> dict[str, object]:
        return {
            "matched": c.matched,
            "expected": c.expected,
            "found": c.found,
            "recall": round(c.recall, 4),
            "precision": round(c.precision, 4),
        }

    return {
        "api": result.api,
        "endpoints": counts(result.endpoints),
        "parameters": counts(result.parameters),
        "required_mismatches": result.required_mismatches,
        "missing_endpoints": result.missing_endpoints,
        "extra_endpoints": result.extra_endpoints,
        "matched": [
            {
                "route": r.route,
                "parameters": counts(r.parameters),
                "missing": r.missing,
                "extra": r.extra,
                "required_mismatches": r.required_mismatches,
            }
            for r in result.matched
        ],
    }

"""Scoring an extracted spec against a reference, and the `docs2mcp score` command."""

import json
from datetime import date
from pathlib import Path

import pytest

from docs2mcp.cli import main
from docs2mcp.evals import (
    Counts,
    Reference,
    ReferenceEndpoint,
    ReferenceParameter,
    ReferenceSource,
    format_score,
    route_key,
    score,
    score_json,
)
from docs2mcp.spec import ApiSpec, Endpoint, HttpMethod, Parameter, ParamLocation, ParamType

Q, P, H = ParamLocation.QUERY, ParamLocation.PATH, ParamLocation.HEADER


def ref_endpoint(
    path: str, *params: tuple[str, ParamLocation, bool], method: HttpMethod = HttpMethod.GET
) -> ReferenceEndpoint:
    return ReferenceEndpoint(
        method=method,
        path=path,
        parameters=[ReferenceParameter(name=n, location=loc, required=r) for n, loc, r in params],
    )


def spec_endpoint(
    id_: str,
    path: str,
    *params: tuple[str, ParamLocation, bool],
    method: HttpMethod = HttpMethod.GET,
) -> Endpoint:
    return Endpoint(
        id=id_,
        method=method,
        path=path,
        summary=id_,
        parameters=[
            Parameter(name=n, location=loc, type=ParamType.STRING, required=r)
            for n, loc, r in params
        ],
    )


REFERENCE = Reference(
    api="example",
    base_url="https://api.example.dev/v2",
    source=ReferenceSource(
        kind="manual", url="https://example.dev/docs", retrieved=date(2026, 11, 24)
    ),
    endpoints=[
        ref_endpoint("/rates", ("base", Q, False), ("date", Q, False), ("X-Key", H, True)),
        ref_endpoint("/providers/{provider}/rates", ("provider", P, True), ("from", Q, False)),
        ref_endpoint("/coverage"),
    ],
)

SPEC = ApiSpec(
    name="Example",
    base_url="https://api.example.dev/v2",  # pyright: ignore[reportArgumentType]
    endpoints=[
        spec_endpoint(
            "get_rates", "/rates/", ("base", Q, True), ("expand", Q, False), ("X-Key", H, True)
        ),
        spec_endpoint("get_provider_rates", "/providers/{key}/rates", ("key", P, True)),
        spec_endpoint("get_rates_csv", "/rates.csv"),
        spec_endpoint("delete_rates", "/rates", method=HttpMethod.DELETE),
    ],
)


def test_route_key_ignores_placeholder_names_and_trailing_slashes() -> None:
    assert route_key("GET", "/providers/{key}/rates/") == "GET /providers/{}/rates"
    assert route_key("GET", "/pokemon/{id or name}") == "GET /pokemon/{}"
    assert route_key("GET", "/") == "GET /"


def test_counts_ratios_and_the_empty_case() -> None:
    assert (Counts(3, 4, 6).recall, Counts(3, 4, 6).precision) == (0.75, 0.5)
    assert (Counts(0, 0, 0).recall, Counts(0, 0, 0).precision) == (1.0, 1.0)
    assert Counts(1, 2, 3) + Counts(1, 1, 1) == Counts(2, 3, 4)


def test_score_matches_routes_by_shape_and_counts_extras_against_precision() -> None:
    result = score(SPEC, REFERENCE)

    assert result.endpoints == Counts(matched=2, expected=3, found=4)
    assert result.missing_endpoints == ["GET /coverage"]
    assert result.extra_endpoints == ["DELETE /rates", "GET /rates.csv"]


def test_parameters_are_scored_on_matched_endpoints_without_path_parameters() -> None:
    result = score(SPEC, REFERENCE)
    rates, provider_rates = result.matched

    assert rates.route == "GET /rates"
    assert rates.parameters == Counts(matched=2, expected=3, found=3)
    assert rates.missing == ["query:date"]
    assert rates.extra == ["query:expand"]
    assert rates.required_mismatches == ["query:base"]
    assert provider_rates.parameters == Counts(matched=0, expected=1, found=0)  # {key} not counted
    assert provider_rates.missing == ["query:from"]
    assert result.parameters == Counts(matched=2, expected=4, found=3)
    assert result.required_mismatches == 1


def test_format_score_reports_totals_and_only_endpoints_with_differences() -> None:
    perfect = score(SPEC, REFERENCE.model_copy(update={"endpoints": [ref_endpoint("/rates.csv")]}))
    assert format_score(perfect).splitlines() == [
        "example: endpoints 1/1 found (recall 100%, precision 25%, 4 extracted)",
        "parameters on matched endpoints: 0/0 (recall 100%, precision 100%), "
        "0 required/optional mismatches",
        "extra endpoints: DELETE /rates, GET /providers/{}/rates, GET /rates",
    ]

    text = format_score(score(SPEC, REFERENCE))
    assert "missing endpoints: GET /coverage" in text
    assert (
        "  GET /rates: missing query:date; extra query:expand; required differs query:base" in text
    )
    assert "  GET /providers/{}/rates: missing query:from" in text


def test_score_json_round_trips_through_json() -> None:
    data = json.loads(json.dumps(score_json(score(SPEC, REFERENCE))))

    assert data["endpoints"] == {
        "matched": 2,
        "expected": 3,
        "found": 4,
        "recall": 0.6667,
        "precision": 0.5,
    }
    assert data["parameters"]["recall"] == 0.5
    assert data["required_mismatches"] == 1
    assert [m["route"] for m in data["matched"]] == ["GET /rates", "GET /providers/{}/rates"]


def test_score_command_prints_the_report_and_writes_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec_path, ref_path = tmp_path / "spec.json", tmp_path / "reference.json"
    spec_path.write_text(SPEC.model_dump_json(), encoding="utf-8")
    ref_path.write_text(REFERENCE.model_dump_json(), encoding="utf-8")
    out = tmp_path / "results" / "example.json"

    assert main(["score", str(spec_path), "--reference", str(ref_path), "--out", str(out)]) == 0

    printed = capsys.readouterr().out
    assert printed.startswith("example: endpoints 2/3 found (recall 67%, precision 50%")
    assert f"Wrote {out}" in printed
    assert json.loads(out.read_text(encoding="utf-8"))["api"] == "example"


def test_score_command_without_out_only_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec_path, ref_path = tmp_path / "spec.json", tmp_path / "reference.json"
    spec_path.write_text(SPEC.model_dump_json(), encoding="utf-8")
    ref_path.write_text(REFERENCE.model_dump_json(), encoding="utf-8")

    assert main(["score", str(spec_path), "--reference", str(ref_path)]) == 0
    assert "Wrote" not in capsys.readouterr().out

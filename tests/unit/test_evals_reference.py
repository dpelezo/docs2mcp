"""Building references from OpenAPI documents, and the committed references themselves."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from docs2mcp.evals import (
    AdditionError,
    OpenApiError,
    ParameterAddition,
    Reference,
    ReferenceAdditions,
    ReferenceEndpoint,
    ReferenceParameter,
    apply_additions,
    endpoints_from_openapi,
    route_key,
)
from docs2mcp.spec import HttpMethod, ParamLocation
from scripts.reference_from_openapi import build_reference, load_additions, main, parse_openapi

EVALS = Path(__file__).parents[2] / "evals" / "extraction"
REFERENCES = EVALS / "reference"
ADDITIONS = EVALS / "additions"


def doc(paths: dict[str, Any], server: str = "https://api.example.dev/v2") -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Example", "version": "1.2.3"},
        "servers": [{"url": server}],
        "paths": paths,
        "components": {
            "parameters": {"lang": {"name": "lang", "in": "query", "required": True}},
        },
    }


def test_paths_are_made_relative_to_the_base_url_without_trailing_slashes() -> None:
    openapi = doc({"/api/v2/pokemon/": {"get": {}}, "/api/v2/pokemon/{id}/": {"get": {}}})

    endpoints = endpoints_from_openapi(
        {**openapi, "servers": [{"url": "https://pokeapi.co"}]}, "https://pokeapi.co/api/v2"
    )

    assert [(e.method, e.path) for e in endpoints] == [
        (HttpMethod.GET, "/pokemon"),
        (HttpMethod.GET, "/pokemon/{id}"),
    ]


def test_server_path_and_base_path_can_be_the_same() -> None:
    endpoints = endpoints_from_openapi(doc({"/rates": {"get": {}}}), "https://api.example.dev/v2")
    assert [e.path for e in endpoints] == ["/rates"]


def test_a_path_outside_the_base_url_is_an_error() -> None:
    with pytest.raises(OpenApiError, match="not under the base path"):
        endpoints_from_openapi(doc({"/rates": {"get": {}}}), "https://api.example.dev/v3")


def test_parameters_merge_path_level_ones_resolve_refs_and_skip_cookies() -> None:
    openapi = doc(
        {
            "/items/{id}": {
                "parameters": [
                    {"name": "id", "in": "path"},
                    {"name": "format", "in": "query", "required": True},
                ],
                "get": {
                    "parameters": [
                        {"name": "format", "in": "query"},  # overrides the path-level one
                        {"$ref": "#/components/parameters/lang"},
                        {"name": "session", "in": "cookie"},
                        {"name": "X-Trace", "in": "header"},
                    ]
                },
                "delete": {},
                "summary": "not an operation",
            }
        }
    )

    get, delete = endpoints_from_openapi(openapi, "https://api.example.dev/v2")

    assert [(p.name, p.location, p.required) for p in get.parameters] == [
        ("id", ParamLocation.PATH, True),  # path parameters are always required
        ("format", ParamLocation.QUERY, False),
        ("lang", ParamLocation.QUERY, True),
        ("X-Trace", ParamLocation.HEADER, False),
    ]
    assert delete.method is HttpMethod.DELETE
    assert [p.name for p in delete.parameters] == ["id", "format"]


@pytest.mark.parametrize(
    "ref", ["#/components/parameters/missing", "https://elsewhere.dev/params.json#/lang"]
)
def test_an_unresolvable_ref_is_an_error(ref: str) -> None:
    openapi = doc({"/items": {"get": {"parameters": [{"$ref": ref}]}}})
    with pytest.raises(OpenApiError, match="cannot resolve"):
        endpoints_from_openapi(openapi, "https://api.example.dev/v2")


def test_no_servers_means_paths_are_used_as_they_are() -> None:
    openapi: dict[str, Any] = {"paths": {"/v2/items": {"get": {}}}}
    endpoints = endpoints_from_openapi(openapi, "https://api.example.dev/v2")
    assert [e.path for e in endpoints] == ["/items"]


def test_parse_openapi_reads_json_and_yaml_and_rejects_other_documents() -> None:
    assert parse_openapi(b'{"paths": {}}') == {"paths": {}}
    assert parse_openapi(b"openapi: 3.1.0\npaths:\n  /a:\n    get: {}\n") == {
        "openapi": "3.1.0",
        "paths": {"/a": {"get": {}}},
    }
    with pytest.raises(ValueError, match="not an OpenAPI document"):
        parse_openapi(b"- just\n- a list\n")


def test_build_reference_records_where_the_file_came_from() -> None:
    api = {
        "id": "example",
        "base_url": "https://api.example.dev/v2",
        "openapi": "https://example.dev/openapi.yml",
        "openapi_note": "From the repo.",
    }
    raw = (
        b"info:\n  version: 1.2.3\n"
        b"servers:\n  - url: https://api.example.dev/v2\n"
        b"paths:\n  /a:\n    get: {}\n"
    )

    reference = build_reference(api, raw, date(2026, 11, 24))

    assert reference.api == "example"
    assert reference.source.kind == "openapi"
    assert reference.source.version == "1.2.3"
    assert reference.source.retrieved == date(2026, 11, 24)
    assert reference.source.note == "From the repo."
    assert len(reference.source.sha256) == 64
    assert [e.path for e in reference.endpoints] == ["/a"]


def test_main_rejects_an_api_without_an_openapi_url(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["open_meteo"]) == 1  # fails before any network call
    assert "no OpenAPI URL for: open_meteo" in capsys.readouterr().err


@pytest.mark.parametrize("path", sorted(REFERENCES.glob("*.json")), ids=lambda p: p.stem)
def test_committed_references_are_valid(path: Path) -> None:
    reference = Reference.model_validate_json(path.read_text(encoding="utf-8"))
    assert reference.api == path.stem
    routes = [(e.method, e.path) for e in reference.endpoints]
    assert len(routes) == len(set(routes))


def test_the_committed_references_cover_the_apis_with_openapi_files() -> None:
    assert {p.stem for p in REFERENCES.glob("*.json")} >= {"frankfurter", "pokeapi", "postcodes_io"}


GET = HttpMethod.GET
Q = ParamLocation.QUERY
OFFICIAL = [
    ReferenceEndpoint(
        method=GET,
        path="/places",
        parameters=[ReferenceParameter(name="limit", location=Q, required=False)],
    )
]


def additions(
    routes: list[ReferenceEndpoint] | None = None,
    parameters: list[ParameterAddition] | None = None,
) -> ReferenceAdditions:
    return ReferenceAdditions(
        api="example",
        reason="the file is incomplete",
        routes=routes or [],
        parameters=parameters or [],
    )


def added_param(path: str, name: str, note: str | None = "docs, 'Query'") -> ParameterAddition:
    return ParameterAddition(
        method=GET,
        path=path,
        parameter=ReferenceParameter(name=name, location=Q, required=False, note=note),
    )


def test_apply_additions_adds_routes_and_parameters_with_their_notes() -> None:
    route = ReferenceEndpoint(
        method=GET, path="/outcodes/{outcode}/nearest", note="docs, 'Nearest'"
    )

    result = apply_additions(OFFICIAL, additions([route], [added_param("/places/", "q")]))

    assert [route_key(e.method, e.path) for e in result] == [
        "GET /places",
        "GET /outcodes/{}/nearest",
    ]
    assert [(p.name, p.note) for p in result[0].parameters] == [
        ("limit", None),
        ("q", "docs, 'Query'"),
    ]
    assert result[1].note == "docs, 'Nearest'"
    assert OFFICIAL[0].parameters[-1].name == "limit"  # the input is not changed


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (additions([ReferenceEndpoint(method=GET, path="/x")]), "needs a note"),
        (
            additions([ReferenceEndpoint(method=GET, path="/places/", note="n")]),
            "already in the official file",
        ),
        (additions(parameters=[added_param("/places", "q", note=None)]), "needs a note"),
        (additions(parameters=[added_param("/places", "limit")]), "already in the official file"),
        (additions(parameters=[added_param("/nowhere", "q")]), "route is not in the reference"),
    ],
)
def test_apply_additions_rejects_bad_or_stale_additions(
    change: ReferenceAdditions, message: str
) -> None:
    with pytest.raises(AdditionError, match=message):
        apply_additions(OFFICIAL, change)


def test_build_reference_applies_additions_and_says_so() -> None:
    api = {
        "id": "example",
        "base_url": "https://api.example.dev",
        "openapi": "https://x",
        "openapi_note": "From the repo.",
    }
    raw = b'{"paths": {"/places": {"get": {}}}}'
    route = ReferenceEndpoint(method=GET, path="/random/places", note="docs, 'Random'")

    reference = build_reference(
        api, raw, date(2026, 11, 24), additions([route], [added_param("/places", "q")])
    )

    assert [e.path for e in reference.endpoints] == ["/places", "/random/places"]
    assert reference.source.note == (
        "From the repo. Plus 1 routes and 1 parameters added by hand (marked with a note): "
        "the file is incomplete"
    )


def test_load_additions(tmp_path: Path) -> None:
    assert load_additions("example", tmp_path) is None
    (tmp_path / "example.json").write_text(additions().model_dump_json(), encoding="utf-8")
    loaded = load_additions("example", tmp_path)
    assert loaded is not None
    assert loaded.reason == "the file is incomplete"
    (tmp_path / "other.json").write_text(additions().model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="is for 'example', not 'other'"):
        load_additions("other", tmp_path)


@pytest.mark.parametrize("path", sorted(ADDITIONS.glob("*.json")), ids=lambda p: p.stem)
def test_committed_additions_are_in_their_reference_with_notes(path: Path) -> None:
    added = ReferenceAdditions.model_validate_json(path.read_text(encoding="utf-8"))
    reference = Reference.model_validate_json((REFERENCES / path.name).read_text(encoding="utf-8"))
    by_route = {route_key(e.method, e.path): e for e in reference.endpoints}

    assert added.api == path.stem
    for route in added.routes:
        assert by_route[route_key(route.method, route.path)] == route
    for addition in added.parameters:
        params = by_route[route_key(addition.method, addition.path)].parameters
        assert addition.parameter in params
    assert "added by hand" in reference.source.note

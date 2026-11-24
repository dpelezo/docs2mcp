"""Building references from OpenAPI documents, and the committed references themselves."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from docs2mcp.evals import OpenApiError, Reference, endpoints_from_openapi
from docs2mcp.spec import HttpMethod, ParamLocation
from scripts.reference_from_openapi import build_reference, main, parse_openapi

REFERENCES = Path(__file__).parents[2] / "evals" / "extraction" / "reference"


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
    assert {p.stem for p in REFERENCES.glob("*.json")} >= {"frankfurter", "pokeapi"}

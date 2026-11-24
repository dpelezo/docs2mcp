from collections.abc import Callable

import pytest
from pydantic import ValidationError

from docs2mcp.extract.merge import (
    choose_auth,
    choose_base_url,
    identifier,
    merge_endpoints,
    normalize_path,
    to_endpoint,
    to_parameter,
)
from docs2mcp.extract.wire import WireAuth, WireEndpoint, WireParameter
from docs2mcp.spec import AuthType, Parameter, ParamLocation
from tests.unit.fakes import NO_AUTH, chunk, endpoint, param

BASE = "https://pokeapi.co/api/v2"
CHUNKS = {"p01-c001": chunk("p01-c001", anchor="pokemon")}


@pytest.mark.parametrize(
    ("text", "expected"),
    [("id or name", "id_or_name"), ("Get Pokemon!", "get_pokemon"), ("2fa", "p_2fa"), ("", "p_")],
)
def test_identifier(text: str, expected: str) -> None:
    assert identifier(text) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/pokemon/{id or name}/", "/pokemon/{id_or_name}"),
        ("https://pokeapi.co/api/v2/pokemon/:id?limit=20", "/pokemon/{id}"),
        ("/api/v2/berry/<name>", "/berry/{name}"),
        ("pokemon//{name}", "/pokemon/{name}"),
        ("/api/v2", "/"),
        ("/api/v20/x", "/api/v20/x"),  # only a whole path segment prefix is stripped
    ],
)
def test_normalize_path(path: str, expected: str) -> None:
    assert normalize_path(path, BASE) == expected


def test_normalize_path_without_base_url_keeps_the_prefix() -> None:
    assert normalize_path("/api/v2/pokemon/{name}", None) == "/api/v2/pokemon/{name}"


def test_choose_base_url_prefers_the_override_then_the_most_common() -> None:
    found = ["https://api.x.dev/v2/", "https://api.x.dev/v2", "https://old.x.dev", "not a url"]
    assert choose_base_url(found) == "https://api.x.dev/v2"
    assert choose_base_url(found, "https://API.x.dev/v3/") == "https://api.x.dev/v3"
    with pytest.raises(ValueError, match="no base URL"):
        choose_base_url(["relative/path"])
    with pytest.raises(ValueError, match="must be an http"):
        choose_base_url(found, "ftp://x")


def test_to_parameter_parses_defaults_and_typed_enums() -> None:
    notes: list[str] = []
    p = to_parameter(
        param("limit", type="integer", default_value="20", allowed_values=["10", "20", "x"]),
        "GET /x",
        notes,
    )
    assert (p.default, p.enum, p.required) == (20, [10, 20, "x"], False)
    text = to_parameter(param("unit", default_value="celsius"), "GET /x", notes)
    assert text.default == "celsius"  # not JSON, kept as text
    assert not notes


def _not_required(p: Parameter) -> bool:
    return not p.required


def _no_default(p: Parameter) -> bool:
    return p.default is None


def _required_without_default(p: Parameter) -> bool:
    return p.required and p.default is None


@pytest.mark.parametrize(
    ("wire", "expected_note", "check"),
    [
        (
            param("q", required=True, default_value='"x"'),
            "had a default, so it is optional",
            _not_required,
        ),
        (
            param("unit", default_value='"kelvin"', allowed_values=["celsius"]),
            "not in allowed values",
            _no_default,
        ),
        (
            param("id", "path", default_value="1"),
            "dropped default on path parameter",
            _required_without_default,
        ),
    ],
    ids=["required-with-default", "default-outside-enum", "path-with-default"],
)
def test_to_parameter_repairs_are_recorded(
    wire: WireParameter, expected_note: str, check: Callable[[Parameter], bool]
) -> None:
    notes: list[str] = []
    assert check(to_parameter(wire, "GET /x", notes))
    assert expected_note in notes[0]


def test_items_type_only_applies_to_arrays() -> None:
    notes: list[str] = []
    assert to_parameter(param("ids", type="array", items_type="integer"), "w", notes).items_type
    assert to_parameter(param("q", items_type="integer"), "w", notes).items_type is None


def test_to_endpoint_adds_missing_path_parameters_and_links_the_source() -> None:
    notes: list[str] = []
    ep = to_endpoint(
        endpoint("get_pokemon", "/api/v2/pokemon/{id or name}", response_key_fields=["name"]),
        BASE,
        CHUNKS,
        notes,
    )
    assert ep.path == "/pokemon/{id_or_name}"
    assert [(p.name, p.location, p.required) for p in ep.parameters] == [
        ("id_or_name", ParamLocation.PATH, True)
    ]
    assert str(ep.source_url) == "https://example.dev/docs#pokemon"
    assert ep.response is not None
    assert ep.response.key_fields == ["name"]
    assert notes == ["GET /pokemon/{id_or_name}: added missing path parameter 'id_or_name'"]


def test_to_endpoint_drops_repeated_parameters_and_unknown_chunks() -> None:
    notes: list[str] = []
    ep = to_endpoint(
        endpoint("list", "/pokemon", param("limit"), param("limit"), source_chunk_id="nope"),
        BASE,
        CHUNKS,
        notes,
    )
    assert len(ep.parameters) == 1
    assert ep.source_url is None
    assert "dropped repeated query parameter 'limit'" in notes[0]


@pytest.mark.parametrize(
    ("wire", "message"),
    [
        (endpoint("get", "/pokemon", param("name", "path")), "are not in the path template"),
        (
            endpoint("get", "/pokemon", param("payload", "body", type="object")),
            "cannot have body parameters",
        ),
        (endpoint("get", "/pokemon", summary=" "), "at least 1 character"),
    ],
    ids=["extra-path-parameter", "body-on-get", "empty-summary"],
)
def test_to_endpoint_leaves_ambiguous_problems_to_validation(
    wire: WireEndpoint, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        to_endpoint(wire, BASE, CHUNKS, [])


def test_merge_endpoints_combines_duplicates_and_makes_ids_unique() -> None:
    notes: list[str] = []
    first = to_endpoint(
        endpoint("get_pokemon", "/pokemon/{name}", param("name", "path")), BASE, CHUNKS, notes
    )
    duplicate = to_endpoint(
        endpoint(
            "get_pokemon_by_id",
            "/pokemon/{id}",
            param("id", "path"),
            param("lang"),
            description="Longer description of the endpoint.",
        ),
        BASE,
        CHUNKS,
        notes,
    )
    other = to_endpoint(endpoint("get_pokemon", "/berry"), BASE, CHUNKS, notes)

    merged = merge_endpoints([first, duplicate, other], notes)

    assert [e.id for e in merged] == ["get_pokemon", "get_pokemon_2"]
    assert [p.name for p in merged[0].parameters] == ["name", "lang"]
    assert merged[0].description == "Longer description of the endpoint."
    assert any("merged a duplicate" in n for n in notes)
    assert any("renamed id to 'get_pokemon_2'" in n for n in notes)


def test_choose_auth() -> None:
    notes: list[str] = []
    assert choose_auth([NO_AUTH, NO_AUTH], notes).type is AuthType.NONE
    key = WireAuth(type="api_key", location="header", name="X-Api-Key")
    auth = choose_auth([NO_AUTH, key, key], notes)
    assert (auth.type, auth.location, auth.name) == (AuthType.API_KEY, "header", "X-Api-Key")
    bearer = choose_auth([WireAuth(type="bearer", location="header", name="")], notes)
    assert (bearer.type, bearer.location, bearer.name) == (AuthType.BEARER, "header", None)
    broken = choose_auth([WireAuth(type="api_key", location="none", name="")], notes)
    assert broken.type is AuthType.NONE
    assert notes == ["auth: api_key without a header or query name; recorded as none"]

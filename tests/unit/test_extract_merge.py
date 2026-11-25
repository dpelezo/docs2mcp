from collections.abc import Callable

import pytest
from pydantic import ValidationError

from docs2mcp.extract.merge import (
    apply_shared_parameters,
    choose_auth,
    choose_base_url,
    expand_resource_templates,
    identifier,
    merge_endpoints,
    normalize_path,
    outside_base,
    repair_cited_values,
    route_key,
    to_endpoint,
    to_parameter,
)
from docs2mcp.extract.wire import WireAuth, WireEndpoint, WireParameter
from docs2mcp.spec import AuthType, Endpoint, HttpMethod, Parameter, ParamLocation, ParamType
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

    merges: list[str] = []
    merged = merge_endpoints([first, duplicate, other], notes, merges)

    assert [e.id for e in merged] == ["get_pokemon", "get_pokemon_2"]
    assert [p.name for p in merged[0].parameters] == ["name", "lang"]
    assert merged[0].description == "Longer description of the endpoint."
    assert merges == ["GET /pokemon/{name}: merged a duplicate from another chunk"]
    assert not any("merged a duplicate" in n for n in notes)
    assert any("renamed id to 'get_pokemon_2'" in n for n in notes)


def test_operations_sharing_a_route_keep_only_common_requirements() -> None:
    """#36: GET /postcodes?q= and GET /postcodes?lon=&lat= are one route, two operations."""
    notes: list[str] = []
    search = to_endpoint(
        endpoint("query_postcodes", "/postcodes", param("q", required=True), param("limit")),
        BASE,
        CHUNKS,
        notes,
    )
    reverse = to_endpoint(
        endpoint(
            "reverse_geocode",
            "/postcodes",
            param("lon", type="number", required=True),
            param("lat", type="number", required=True),
            param("limit"),
            summary="Returns the nearest postcodes to a coordinate.",
            description="Results include a distance.",
        ),
        BASE,
        CHUNKS,
        notes,
    )
    merges: list[str] = []
    (merged,) = merge_endpoints([search, reverse], notes, merges)

    assert [(p.name, p.required) for p in merged.parameters] == [
        ("q", False),
        ("limit", False),
        ("lon", False),
        ("lat", False),
    ]
    assert merged.summary == search.summary
    assert merged.description == (
        "Also: Returns the nearest postcodes to a coordinate. Results include a distance."
    )
    assert notes == [
        "GET /postcodes: ['q', 'lon', 'lat'] made optional, because not every operation "
        "merged on this route requires them"
    ]


def test_a_parameter_every_copy_requires_stays_required() -> None:
    notes: list[str] = []
    copies = [
        to_endpoint(
            endpoint("lookup", "/lookup", param("code", required=True), summary=summary),
            BASE,
            CHUNKS,
            notes,
        )
        for summary in ("Look up a code.", "Find one code.")
    ]
    (merged,) = merge_endpoints(copies, notes, [])
    assert merged.parameters[0].required
    assert notes == []


def _endpoint_with(*params: Parameter, description: str = "") -> Endpoint:
    return Endpoint(
        id="books",
        method=HttpMethod.GET,
        path="/api/books",
        summary="Book data.",
        description=description,
        parameters=list(params),
    )


def test_values_the_docs_cite_are_added_to_the_enum() -> None:
    """#36: Open Library says "Pass jscmd=details" but listed only viewapi and data."""
    notes: list[str] = []
    jscmd = Parameter(
        name="jscmd", location=ParamLocation.QUERY, type=ParamType.STRING, enum=["viewapi", "data"]
    )
    (fixed,) = repair_cited_values(
        [_endpoint_with(jscmd, description="Pass jscmd=details, or jscmd=data for stability.")],
        notes,
    )
    assert fixed.parameters[0].enum == ["viewapi", "data", "details"]
    assert notes == [
        "GET /api/books: parameter 'jscmd': added ['details'] to its allowed values, "
        "as the docs cite them"
    ]


def test_cited_values_need_the_exact_parameter_name() -> None:
    notes: list[str] = []
    fmt = Parameter(
        name="format", location=ParamLocation.QUERY, type=ParamType.STRING, enum=["json"]
    )
    endpoints = [_endpoint_with(fmt, description="Set output_format=xml or format=json.")]
    assert repair_cited_values(endpoints, notes) == endpoints
    assert notes == []


@pytest.mark.parametrize(
    ("type_", "default", "becomes_array"),
    [
        (ParamType.NUMBER, 52.52, True),
        (ParamType.INTEGER, None, True),
        (ParamType.BOOLEAN, None, True),
        (ParamType.STRING, None, False),
    ],
)
def test_comma_separated_scalars_become_arrays(
    type_: ParamType, default: float | None, becomes_array: bool
) -> None:
    """#36: Open-Meteo latitude is a number "Multiple values can be comma separated"."""
    notes: list[str] = []
    latitude = Parameter(
        name="latitude",
        location=ParamLocation.QUERY,
        type=type_,
        default=default,
        description="WGS84 latitude. Multiple values can be comma-separated.",
    )
    (fixed,) = repair_cited_values([_endpoint_with(latitude)], notes)
    param = fixed.parameters[0]
    if becomes_array:
        assert (param.type, param.items_type, param.default) == (ParamType.ARRAY, type_, None)
        assert len(notes) == 1
    else:
        assert param == latitude
        assert notes == []


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


def _ep(operation: str, path: str, *params: WireParameter) -> Endpoint:
    return to_endpoint(endpoint(operation, path, *params), BASE, CHUNKS, [])


LIST_TEMPLATE = _ep(
    "list_resources", "/{endpoint}", param("endpoint", "path"), param("limit"), param("offset")
)


def test_a_resource_template_becomes_one_list_route_per_resource() -> None:
    endpoints = [
        _ep("get_pokemon", "/pokemon/{id}", param("id", "path")),
        _ep("get_ability", "/ability/{id}", param("id", "path")),
        _ep("list_ability", "/ability"),  # already documented on its own: not added again
        _ep("get_rates", "/rates"),  # one segment: not a resource
    ]
    expanded: list[str] = []

    result = expand_resource_templates(endpoints, [LIST_TEMPLATE], expanded)

    added = result[len(endpoints) :]
    assert [(e.id, e.path) for e in added] == [("list_resources_pokemon", "/pokemon")]
    assert [(p.name, p.location) for p in added[0].parameters] == [
        ("limit", ParamLocation.QUERY),
        ("offset", ParamLocation.QUERY),
    ]
    assert added[0].summary == "List resources (pokemon)."
    assert expanded == ["GET /{endpoint}: expanded into 1 routes, one per resource"]


def test_a_template_keeps_the_rest_of_its_path() -> None:
    template = _ep(
        "get_names", "/{endpoint}/{id}/names", param("endpoint", "path"), param("id", "path")
    )
    result = expand_resource_templates(
        [_ep("get_berry", "/berry/{id}", param("id", "path"))], [template], []
    )
    assert [(e.path, [p.name for p in e.parameters]) for e in result[1:]] == [
        ("/berry/{id}/names", ["id"])
    ]


@pytest.mark.parametrize(
    ("template", "others", "reason"),
    [
        (_ep("get_rates", "/rates/{date}", param("date", "path")), "resources", "does not start"),
        (LIST_TEMPLATE, "none", "no other endpoint names a resource"),
    ],
)
def test_a_template_that_cannot_be_expanded_is_kept_as_documented(
    template: Endpoint, others: str, reason: str
) -> None:
    endpoints = (
        [_ep("get_berry", "/berry/{id}", param("id", "path"))] if others == "resources" else []
    )
    expanded: list[str] = []

    result = expand_resource_templates(endpoints, [template, template], expanded)

    assert result == [*endpoints, template]  # kept once
    assert len(expanded) == 2
    assert reason in expanded[0]


@pytest.mark.parametrize(
    ("served_from", "path", "base_url", "outside"),
    [
        ("https://covers.openlibrary.org", "/a/olid/{olid}.jpg", "https://openlibrary.org", True),
        ("https://api.frankfurter.dev/v1", "/latest", "https://api.frankfurter.dev/v2", True),
        ("https://api.frankfurter.dev/v20", "/rates", "https://api.frankfurter.dev/v2", True),
        ("", "https://covers.openlibrary.org/b/id/1.jpg", "https://openlibrary.org", True),
        ("https://api.frankfurter.dev/v2", "/rates", "https://api.frankfurter.dev/v2", False),
        ("https://api.open-meteo.com", "/v1/forecast", "https://api.open-meteo.com/v1", False),
        ("http://www.openlibrary.org/", "/search.json", "https://openlibrary.org", False),
        ("https://pokeapi.co/api/v2/", "/api/v2/pokemon/{id}", "https://pokeapi.co/api/v2", False),
        ("", "https://openlibrary.org/search.json", "https://openlibrary.org", False),
        ("", "/rates", "https://api.frankfurter.dev/v2", False),
        ("see the docs", "/rates", "https://api.frankfurter.dev/v2", False),
        ("https://api.frankfurter.dev/v1", "/latest", "not a url", False),
    ],
)
def test_outside_base(served_from: str, path: str, base_url: str, outside: bool) -> None:
    assert outside_base(served_from, path, base_url) is outside


FRANKFURTER = "https://api.frankfurter.dev/v2"


def _fep(operation: str, path: str, *params: WireParameter, method: str = "GET") -> Endpoint:
    return to_endpoint(endpoint(operation, path, *params, method=method), FRANKFURTER, CHUNKS, [])


RATES = _fep("get_rates", "/rates", param("base"), param("date"), param("X-Key", "header"))
PROVIDER_RATES = _fep(
    "get_provider_rates", "/providers/{key}/rates", param("key", "path"), param("date")
)


def test_shared_parameters_are_copied_without_duplicates_or_path_parameters() -> None:
    copied: list[str] = []

    result = apply_shared_parameters(
        [RATES, PROVIDER_RATES], {route_key(PROVIDER_RATES): ["/v2/rates"]}, FRANKFURTER, copied
    )

    assert result[0] is RATES
    assert [(p.name, p.location) for p in result[1].parameters] == [
        ("key", ParamLocation.PATH),
        ("date", ParamLocation.QUERY),
        ("base", ParamLocation.QUERY),
        ("X-Key", ParamLocation.HEADER),
    ]
    assert copied == ["GET /providers/{key}/rates: copied base, X-Key from GET /rates"]


def test_a_shared_route_with_another_method_is_matched_by_path() -> None:
    bulk = _fep("bulk_rates", "/rates/bulk", method="POST")
    copied: list[str] = []

    result = apply_shared_parameters(
        [RATES, bulk],
        {route_key(bulk): ["https://api.frankfurter.dev/v2/rates"]},
        FRANKFURTER,
        copied,
    )

    assert [p.name for p in result[1].parameters] == ["base", "date", "X-Key"]
    assert copied == ["POST /rates/bulk: copied base, date, X-Key from GET /rates"]


@pytest.mark.parametrize("ref", ["/v2/unknown", "/providers/{provider}/rates"])
def test_a_missing_or_self_reference_copies_nothing_and_is_reported(ref: str) -> None:
    copied: list[str] = []

    result = apply_shared_parameters(
        [RATES, PROVIDER_RATES], {route_key(PROVIDER_RATES): [ref]}, FRANKFURTER, copied
    )

    assert result == [RATES, PROVIDER_RATES]
    assert copied == [
        f"GET /providers/{{key}}/rates: same parameters as {ref}, but no such endpoint was found"
    ]


def test_sharing_parameters_that_are_already_there_says_so() -> None:
    copied: list[str] = []
    result = apply_shared_parameters(
        [RATES, RATES.model_copy(update={"id": "rates_again", "path": "/rates/latest"})],
        {("GET", "/rates/latest"): ["/rates"]},
        FRANKFURTER,
        copied,
    )
    assert result[1].parameters == RATES.parameters
    assert copied == ["GET /rates/latest: copied nothing new from GET /rates"]

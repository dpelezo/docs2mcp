"""OpenAPI to ApiSpec conversion for the agent eval's condition A (ADR 0013)."""

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from docs2mcp.design import ToolSet, direct_design, set_approval
from docs2mcp.openapi import OpenApiError, spec_from_openapi
from docs2mcp.spec import ApiSpec, HttpMethod, ParamLocation, ParamType
from scripts.openapi_condition import APPROVAL_NOTE, build

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
CONDITION_A = ROOT / "evals" / "agent" / "conditions" / "openapi"


def _doc() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Pets", "version": "2.0", "description": "A  pet\nstore."},
        "servers": [{"url": "https://api.example.dev/v1"}],
        "components": {
            "parameters": {
                "limit": {
                    "name": "limit",
                    "in": "query",
                    "description": "How many.",
                    "schema": {"default": 10, "maximum": 100},
                },
            },
            "schemas": {
                "Kind": {"type": "string", "enum": ["cat", "dog"]},
                "NewPet": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "description": "Its name."},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "kind": {"$ref": "#/components/schemas/Kind"},
                    },
                },
            },
        },
        "paths": {
            "/pets/": {
                "parameters": [{"name": "X-Trace", "in": "header", "schema": {"type": "boolean"}}],
                "get": {
                    "operationId": "listPets",
                    "summary": "List pets",
                    "description": "Every pet.\n\nPaged.",
                    "parameters": [
                        {"$ref": "#/components/parameters/limit"},
                        {
                            "name": "kind",
                            "in": "query",
                            "schema": {"$ref": "#/components/schemas/Kind"},
                        },
                        {"name": "sort", "in": "query", "schema": {"enum": [1, 2], "default": 3}},
                        {"name": "q", "in": "query", "schema": {"type": ["string", "null"]}},
                        {
                            "name": "near",
                            "in": "query",
                            "schema": {"anyOf": [{"type": "null"}, {"type": "number"}]},
                        },
                        {
                            "name": "fresh",
                            "in": "query",
                            "schema": {"type": "boolean", "default": "false"},
                        },
                        {
                            "name": "page",
                            "in": "query",
                            "schema": {"type": "integer", "default": "two"},
                        },
                        {"name": "session", "in": "cookie", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "The pets."}},
                },
                "post": {
                    "operationId": "listPets",
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/NewPet"}}
                        }
                    },
                },
            },
            "/pets/{petId}": {
                "get": {
                    "parameters": [
                        {"name": "petId", "in": "path", "schema": {"type": "integer", "default": 1}}
                    ],
                },
                "put": {
                    "summary": "Replace a pet",
                    "parameters": [{"name": "petId", "in": "path", "schema": {"type": "integer"}}],
                    "requestBody": {"content": {"text/plain": {"schema": {"type": "string"}}}},
                },
                "patch": {
                    "operationId": "2fa",
                    "parameters": [{"name": "petId", "in": "path", "schema": {"type": "integer"}}],
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    },
                },
                "delete": {
                    "operationId": "deletePet",
                    "parameters": [{"name": "petId", "in": "path", "schema": {"type": "integer"}}],
                },
                "summary": "not an operation",
            },
        },
    }


def _convert(doc: dict[str, Any] | None = None) -> tuple[ApiSpec, list[str]]:
    conversion = spec_from_openapi(
        doc or _doc(), name="Pets", base_url="https://api.example.dev/v1"
    )
    return conversion.spec, conversion.skipped


def test_operations_become_endpoints_with_paths_under_the_base_url() -> None:
    spec, _ = _convert()
    routes = [(e.id, e.method, e.path) for e in spec.endpoints]
    assert routes == [
        ("list_pets", HttpMethod.GET, "/pets"),
        ("list_pets_2", HttpMethod.POST, "/pets"),
        ("get_pets_pet_id", HttpMethod.GET, "/pets/{petId}"),
        ("put_pets_pet_id", HttpMethod.PUT, "/pets/{petId}"),
        ("op_2fa", HttpMethod.PATCH, "/pets/{petId}"),
        ("delete_pet", HttpMethod.DELETE, "/pets/{petId}"),
    ]
    assert spec.description == "A pet store."


def test_summary_description_and_response_are_kept_with_whitespace_collapsed() -> None:
    spec, _ = _convert()
    listing = spec.endpoints[0]
    assert listing.summary == "List pets"
    assert listing.description == "Every pet. Paged."
    assert listing.response is not None
    assert listing.response.description == "The pets."
    assert spec.endpoints[1].summary == "listPets"
    assert spec.endpoints[2].summary == "GET /pets/{petId}"


def test_parameters_keep_types_enums_and_defaults_from_the_file() -> None:
    spec, _ = _convert()
    params = {p.name: p for p in spec.endpoints[0].parameters}
    assert list(params) == ["X-Trace", "limit", "kind", "sort", "q", "near", "fresh", "page"]
    assert params["X-Trace"].location is ParamLocation.HEADER
    assert params["X-Trace"].type is ParamType.BOOLEAN
    assert (params["limit"].type, params["limit"].default) == (ParamType.INTEGER, 10)
    assert params["limit"].description == "How many."
    assert (params["kind"].type, params["kind"].enum) == (ParamType.STRING, ["cat", "dog"])
    assert (params["sort"].type, params["sort"].enum, params["sort"].default) == (
        ParamType.INTEGER,
        [1, 2],
        None,
    )
    assert params["q"].type is ParamType.STRING
    assert params["near"].type is ParamType.NUMBER
    assert params["fresh"].default is False
    assert params["page"].default is None


def test_path_parameters_are_required_and_lose_their_default() -> None:
    spec, _ = _convert()
    (pet_id,) = spec.endpoints[2].parameters
    assert (pet_id.required, pet_id.default) == (True, None)


def test_json_request_body_fields_become_body_parameters() -> None:
    spec, _ = _convert()
    body = {p.name: p for p in spec.endpoints[1].parameters if p.location is ParamLocation.BODY}
    assert body["name"].required
    assert body["name"].description == "Its name."
    assert (body["tags"].type, body["tags"].items_type) == (ParamType.ARRAY, ParamType.STRING)
    assert body["kind"].enum == ["cat", "dog"]
    assert not body["kind"].required


def test_every_part_left_out_is_reported() -> None:
    _, skipped = _convert()
    assert skipped == [
        "GET /pets: cookie parameter 'session'",
        "PUT /pets/{petId}: request body is not application/json (['text/plain'])",
        "PATCH /pets/{petId}: the JSON request body lists no fields",
    ]
    doc = _doc()
    del doc["paths"]["/pets/"]["post"]["requestBody"]
    assert "POST /pets: the file describes no request body" in _convert(doc)[1]


type Doc = dict[str, Any]


def _no_paths(doc: Doc) -> None:
    del doc["paths"]


def _other_server(doc: Doc) -> None:
    doc["servers"] = [{"url": "https://api.example.dev/v2"}]


def _missing_ref(doc: Doc) -> None:
    doc["paths"]["/pets/"]["get"]["parameters"].append({"$ref": "#/nope"})


def _remote_ref(doc: Doc) -> None:
    doc["paths"]["/pets/"]["get"]["parameters"].append({"$ref": "https://example.dev/x.json#/y"})


def _looping_ref(doc: Doc) -> None:
    doc["components"]["schemas"]["Loop"] = {"$ref": "#/components/schemas/Loop"}
    loop = {"name": "l", "in": "query", "schema": {"$ref": "#/components/schemas/Loop"}}
    doc["paths"]["/pets/"]["get"]["parameters"].append(loop)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_no_paths, "no 'paths'"),
        (_other_server, "not under"),
        (_missing_ref, "cannot resolve"),
        (_remote_ref, "cannot resolve"),
        (_looping_ref, "cannot resolve"),
    ],
)
def test_documents_that_cannot_be_converted_fail_clearly(
    change: Callable[[Doc], None], message: str
) -> None:
    doc = _doc()
    change(doc)
    with pytest.raises(OpenApiError, match=message):
        _convert(doc)


@pytest.mark.parametrize(
    ("schema", "kind", "default"),
    [
        ({"default": 2.5}, ParamType.NUMBER, 2.5),
        ({"default": True}, ParamType.BOOLEAN, True),
        ({"minimum": 1}, ParamType.INTEGER, None),
        ({"description": "no type"}, ParamType.STRING, None),
        ({"type": "number", "default": "1.5"}, ParamType.NUMBER, 1.5),
        ({"type": "number", "default": 3}, ParamType.NUMBER, 3),
        ({"type": "number", "default": "lots"}, ParamType.NUMBER, None),
        ({"type": "integer", "default": "-4"}, ParamType.INTEGER, -4),
        ({"type": "integer", "default": 7}, ParamType.INTEGER, 7),
        ({"type": "string", "default": 5}, ParamType.STRING, "5"),
        ({"type": "boolean", "default": "maybe"}, ParamType.BOOLEAN, None),
        ({"type": "array", "default": ["a"]}, ParamType.ARRAY, ["a"]),
    ],
)
def test_missing_types_are_inferred_and_defaults_fitted_to_the_type(
    schema: dict[str, Any], kind: ParamType, default: object
) -> None:
    doc = _doc()
    doc["paths"]["/pets/"]["get"]["parameters"] = [{"name": "x", "in": "query", "schema": schema}]
    (_, param) = _convert(doc)[0].endpoints[0].parameters
    assert (param.type, param.default) == (kind, default)


def test_a_missing_servers_list_means_paths_from_the_root() -> None:
    doc = _doc()
    del doc["servers"]
    spec = spec_from_openapi(doc, name="Pets", base_url="https://api.example.dev").spec
    assert spec.endpoints[0].path == "/pets"


def test_build_writes_an_approved_direct_design_and_its_source() -> None:
    api = {"id": "pets", "name": "Pets", "base_url": "https://api.example.dev/v1", "openapi": "u"}
    raw = json.dumps(_doc()).encode()
    spec_text, tools_text, source_text = build(api, raw, date(2026, 11, 24))
    tools = ToolSet.model_validate_json(tools_text)
    assert tools.design == "direct"
    assert all(t.approved and t.note == APPROVAL_NOTE for t in tools.tools)
    assert len(tools.tools) == len(ApiSpec.model_validate_json(spec_text).endpoints) == 6
    source = json.loads(source_text)
    assert (source["version"], source["retrieved"], source["endpoints"]) == ("2.0", "2026-11-24", 6)
    assert len(source["sha256"]) == 64
    assert len(source["skipped"]) == 3


@pytest.mark.parametrize("api", ["frankfurter", "pokeapi", "postcodes_io"])
def test_committed_condition_a_designs_match_their_specs(api: str) -> None:
    folder = CONDITION_A / api
    spec = ApiSpec.model_validate_json((folder / "spec.json").read_text("utf-8"))
    committed = ToolSet.model_validate_json((folder / "tools.json").read_text("utf-8"))
    rebuilt = set_approval(direct_design(spec), every=True, note=APPROVAL_NOTE).toolset
    assert rebuilt == committed
    source = json.loads((folder / "source.json").read_text("utf-8"))
    assert source["endpoints"] == len(spec.endpoints) == len(committed.tools)

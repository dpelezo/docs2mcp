import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from docs2mcp.spec import ApiSpec, AuthScheme, AuthType, Endpoint, Parameter, json_schema

SCHEMA_FILE = Path(__file__).parents[2] / "docs" / "spec.schema.json"

VALID: dict[str, Any] = {
    "name": "Open-Meteo",
    "base_url": "https://api.open-meteo.com",
    "docs_url": "https://open-meteo.com/en/docs",
    "endpoints": [
        {
            "id": "get_forecast",
            "method": "GET",
            "path": "/v1/forecast",
            "summary": "Weather forecast for a location",
            "parameters": [
                {"name": "latitude", "location": "query", "type": "number", "required": True},
                {"name": "longitude", "location": "query", "type": "number", "required": True},
                {
                    "name": "hourly",
                    "location": "query",
                    "type": "array",
                    "items_type": "string",
                },
                {
                    "name": "temperature_unit",
                    "location": "query",
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "default": "celsius",
                },
            ],
            "response": {"key_fields": ["hourly.time", "hourly.temperature_2m"]},
        },
        {
            "id": "get_pokemon",
            "method": "GET",
            "path": "/api/v2/pokemon/{name}",
            "summary": "One Pokémon by name or id",
            "parameters": [
                {"name": "name", "location": "path", "type": "string", "required": True},
            ],
        },
    ],
}


def spec_with(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(VALID)
    data.update(changes)
    return data


def endpoint_with(**changes: Any) -> dict[str, Any]:
    endpoint = copy.deepcopy(VALID["endpoints"][1])
    endpoint.update(changes)
    return endpoint


def test_valid_spec_parses_and_round_trips_through_json() -> None:
    spec = ApiSpec.model_validate(VALID)
    assert spec.auth.type is AuthType.NONE
    assert spec.endpoints[1].path_template_params == {"name"}
    assert ApiSpec.model_validate_json(spec.model_dump_json()) == spec


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (spec_with(endpoints=[]), "at least 1 item"),
        (spec_with(unexpected="x"), "Extra inputs are not permitted"),
        (
            spec_with(endpoints=[VALID["endpoints"][1], VALID["endpoints"][1]]),
            "endpoint id 'get_pokemon' is used more than once",
        ),
        (
            spec_with(
                endpoints=[
                    VALID["endpoints"][1],
                    endpoint_with(
                        id="get_pokemon_by_id",
                        path="/api/v2/pokemon/{id}",
                        parameters=[
                            {"name": "id", "location": "path", "type": "integer", "required": True}
                        ],
                    ),
                ]
            ),
            "GET /api/v2/pokemon/{id} is declared more than once",
        ),
    ],
    ids=["no-endpoints", "extra-field", "duplicate-id", "duplicate-route"],
)
def test_invalid_specs_are_rejected(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message.replace("{", r"\{").replace("}", r"\}")):
        ApiSpec.model_validate(data)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"id": "GetPokemon"}, "String should match pattern"),
        ({"path": "api/v2/pokemon/{name}"}, "String should match pattern"),
        ({"parameters": []}, r"path placeholders \['name'\] have no path parameter"),
        (
            {
                "parameters": [
                    {"name": "name", "location": "path", "type": "string", "required": True},
                    {"name": "form", "location": "path", "type": "string", "required": True},
                ]
            },
            r"path parameters \['form'\] are not in the path template",
        ),
        (
            {"parameters": [{"name": "name", "location": "path", "type": "string"}]},
            "path parameter 'name' must be required",
        ),
        (
            {
                "parameters": [
                    {"name": "name", "location": "path", "type": "string", "required": True},
                    {"name": "name", "location": "path", "type": "string", "required": True},
                ]
            },
            "declares path parameter 'name' twice",
        ),
        (
            {
                "parameters": [
                    {"name": "name", "location": "path", "type": "string", "required": True},
                    {"name": "payload", "location": "body", "type": "object"},
                ]
            },
            "GET requests cannot have body parameters",
        ),
    ],
    ids=[
        "id-not-snake-case",
        "path-without-slash",
        "placeholder-without-parameter",
        "parameter-not-in-template",
        "optional-path-parameter",
        "duplicate-parameter",
        "body-on-get",
    ],
)
def test_invalid_endpoints_are_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Endpoint.model_validate(endpoint_with(**changes))


def test_same_parameter_name_in_different_locations_is_allowed() -> None:
    endpoint = Endpoint.model_validate(
        endpoint_with(
            parameters=[
                {"name": "name", "location": "path", "type": "string", "required": True},
                {"name": "name", "location": "query", "type": "string"},
            ]
        )
    )
    assert len(endpoint.parameters) == 2


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"name": "q", "location": "query", "type": "string", "required": True, "default": "x"},
            "is required, so it cannot have a default",
        ),
        (
            {"name": "unit", "location": "query", "type": "string", "enum": ["c"], "default": "f"},
            "not in its enum",
        ),
        (
            {"name": "q", "location": "query", "type": "string", "items_type": "string"},
            "sets items_type, but its type is not 'array'",
        ),
        ({"name": "q", "location": "query", "type": "string", "enum": []}, "at least 1 item"),
    ],
    ids=["required-with-default", "default-not-in-enum", "items-type-on-scalar", "empty-enum"],
)
def test_invalid_parameters_are_rejected(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Parameter.model_validate(data)


def test_api_key_auth_needs_location_and_name() -> None:
    with pytest.raises(ValidationError, match="needs both 'location' and 'name'"):
        AuthScheme(type=AuthType.API_KEY, location="header")
    auth = AuthScheme(type=AuthType.API_KEY, location="header", name="X-Api-Key")
    assert auth.name == "X-Api-Key"


def test_models_are_immutable() -> None:
    spec = ApiSpec.model_validate(VALID)
    with pytest.raises(ValidationError, match="frozen"):
        spec.name = "changed"  # pyright: ignore[reportAttributeAccessIssue]


def test_json_schema_is_valid_json_describing_the_spec() -> None:
    schema = json.loads(json_schema())
    assert schema["title"] == "ApiSpec"
    assert set(schema["required"]) == {"name", "base_url", "endpoints"}


def test_committed_schema_file_is_up_to_date() -> None:
    assert SCHEMA_FILE.read_text() == json_schema(), (
        "docs/spec.schema.json is out of date. Regenerate it with: just schema"
    )

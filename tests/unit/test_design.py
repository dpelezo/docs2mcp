"""The tool-set model, its validation against a spec, and the direct design (ADR 0011)."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from docs2mcp.cli import main
from docs2mcp.design import (
    ExcludedEndpoint,
    Route,
    Tool,
    ToolParameter,
    ToolSet,
    direct_design,
    json_schema,
    safe_name,
    validate_toolset,
)
from docs2mcp.design.toolset import MAX_PARAMETER_DESCRIPTION, MAX_TOOL_DESCRIPTION
from docs2mcp.spec import ApiSpec

ROOT = Path(__file__).parents[2]
SPECS = ROOT / "tests" / "fixtures" / "specs"
SCHEMA_FILE = ROOT / "docs" / "tools.schema.json"

SPEC = ApiSpec.model_validate(
    {
        "name": "PokéAPI",
        "base_url": "https://pokeapi.co/api/v2",
        "endpoints": [
            {
                "id": "get_pokemon",
                "method": "GET",
                "path": "/pokemon/{id_or_name}",
                "summary": "Get one Pokémon.",
                "parameters": [
                    {"name": "id_or_name", "location": "path", "type": "string", "required": True}
                ],
            },
            {
                "id": "list_pokemon",
                "method": "GET",
                "path": "/pokemon",
                "summary": "List Pokémon.",
                "parameters": [
                    {"name": "limit", "location": "query", "type": "number"},
                    {"name": "offset", "location": "query", "type": "integer"},
                ],
            },
            {
                "id": "get_sprite",
                "method": "GET",
                "path": "/sprites/{id}",
                "summary": "Get a sprite image.",
                "parameters": [
                    {"name": "id", "location": "path", "type": "integer", "required": True},
                    {"name": "size", "location": "query", "type": "string", "enum": ["S", "M"]},
                ],
            },
        ],
    }
)


def arg(name: str, type_: str = "string", **extra: Any) -> ToolParameter:
    return ToolParameter.model_validate({"name": name, "type": type_, "description": name} | extra)


POKEMON = Tool(
    name="pokemon",
    description="Get one Pokémon by name or id, or list Pokémon.",
    parameters=[arg("name"), arg("limit", "integer"), arg("offset", "integer")],
    routes=[
        Route(endpoint="get_pokemon", requires=["name"], arguments={"name": "id_or_name"}),
        Route(endpoint="list_pokemon", arguments={"limit": "limit", "offset": "offset"}),
    ],
)


def toolset(*tools: Tool, excluded: list[ExcludedEndpoint] | None = None) -> ToolSet:
    return ToolSet(
        api="PokéAPI",
        base_url="https://pokeapi.co/api/v2",  # pyright: ignore[reportArgumentType]
        design="task",
        tools=list(tools),
        excluded=(
            [ExcludedEndpoint(endpoint="get_sprite", reason="images, not JSON")]
            if excluded is None
            else excluded
        ),
    )


def errors_for(*tools: Tool, excluded: list[ExcludedEndpoint] | None = None) -> list[str]:
    return validate_toolset(toolset(*tools, excluded=excluded), SPEC)


# --- the model: what can be checked from tools.json alone ---------------------------------


def test_a_valid_task_design_has_no_errors() -> None:
    assert errors_for(POKEMON) == []


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: arg("from"), "Python keyword"),
        (lambda: arg("Name"), "String should match pattern"),
        (lambda: arg("x", required=True, default="a"), "cannot have a default"),
        (lambda: arg("x", enum=["a"], default="b"), "not in its enum"),
        (lambda: arg("x", items_type="string"), "its type is not 'array'"),
        (lambda: arg("x", description="d" * (MAX_PARAMETER_DESCRIPTION + 1)), "at most"),
        (
            lambda: POKEMON.model_copy(update={}).model_validate(
                POKEMON.model_dump() | {"name": "class"}
            ),
            "Python keyword",
        ),
        (
            lambda: Tool.model_validate(
                POKEMON.model_dump() | {"description": "d" * (MAX_TOOL_DESCRIPTION + 1)}
            ),
            "at most",
        ),
        (
            lambda: Tool.model_validate(
                POKEMON.model_dump() | {"parameters": [arg("a").model_dump()] * 2}
            ),
            "declares a parameter twice",
        ),
        (
            lambda: Tool(
                name="t",
                description="d",
                routes=[Route(endpoint="get_pokemon", arguments={"x": "id"})],
            ),
            "unknown arguments",
        ),
        (
            lambda: Tool(
                name="t",
                description="d",
                parameters=[arg("x")],
                routes=[Route(endpoint="get_pokemon", requires=["x"])],
            ),
            "requires arguments it does not pass",
        ),
        (lambda: toolset(POKEMON, POKEMON), "tool names must be unique"),
    ],
    ids=[
        "keyword-arg",
        "upper-case",
        "required-default",
        "default-enum",
        "items-type",
        "long-arg-description",
        "keyword-tool",
        "long-tool-description",
        "duplicate-arg",
        "unknown-route-arg",
        "requires-not-passed",
        "duplicate-tool",
    ],
)
def test_the_model_rejects_inconsistent_tool_sets(make: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make()


def test_approved_defaults_to_false() -> None:
    assert toolset(POKEMON).approved is False


def test_committed_schema_file_is_up_to_date() -> None:
    assert SCHEMA_FILE.read_text() == json_schema(), (
        "docs/tools.schema.json is out of date. Regenerate it with: just schema"
    )


# --- validation against the spec ----------------------------------------------------------


def test_every_endpoint_must_be_routed_or_excluded_exactly_once() -> None:
    assert errors_for(POKEMON, excluded=[]) == [
        "endpoint 'get_sprite' is neither in a route nor excluded"
    ]
    twice = [
        ExcludedEndpoint(endpoint="get_sprite", reason="x"),
        ExcludedEndpoint(endpoint="get_pokemon", reason="y"),
    ]
    assert errors_for(POKEMON, excluded=twice) == [
        "endpoint 'get_pokemon' is used more than once (pokemon, excluded)"
    ]


def test_unknown_endpoints_are_reported() -> None:
    ghost = Tool(name="ghost", description="d", routes=[Route(endpoint="get_ghost")])
    excluded = [
        ExcludedEndpoint(endpoint="get_sprite", reason="x"),
        ExcludedEndpoint(endpoint="get_nothing", reason="y"),
    ]
    errors = errors_for(POKEMON, ghost, excluded=excluded)
    assert "endpoint 'get_ghost' is not in the spec" in errors
    assert "endpoint 'get_nothing' is not in the spec" in errors


def test_required_endpoint_parameters_must_always_be_filled() -> None:
    optional_name = Tool(
        name="pokemon",
        description="d",
        parameters=[arg("name")],
        routes=[Route(endpoint="get_pokemon", arguments={"name": "id_or_name"})],
    )
    (error,) = errors_for(
        optional_name, Tool(name="lst", description="d", routes=[Route(endpoint="list_pokemon")])
    )
    assert "required endpoint parameters ['id_or_name'] are not always filled" in error

    required_name = optional_name.model_copy(update={"parameters": [arg("name", required=True)]})
    assert (
        errors_for(
            required_name,
            Tool(name="lst", description="d", routes=[Route(endpoint="list_pokemon")]),
        )
        == []
    )

    constant = Tool(
        name="pikachu",
        description="d",
        routes=[Route(endpoint="get_pokemon", constants={"id_or_name": "pikachu"})],
    )
    assert (
        errors_for(
            constant, Tool(name="lst", description="d", routes=[Route(endpoint="list_pokemon")])
        )
        == []
    )


def test_targets_must_exist_and_be_filled_once() -> None:
    tool = Tool(
        name="pokemon",
        description="d",
        parameters=[arg("name", required=True), arg("other")],
        routes=[
            Route(
                endpoint="get_pokemon",
                arguments={"name": "id_or_name", "other": "id_or_name"},
                constants={"shiny": True},
            )
        ],
    )
    errors = errors_for(
        tool, Tool(name="lst", description="d", routes=[Route(endpoint="list_pokemon")])
    )
    where = "tool 'pokemon', route to 'get_pokemon'"
    assert f"{where}: endpoint parameter 'id_or_name' is filled more than once" in errors
    assert (
        "tool 'pokemon', route to 'get_pokemon': 'get_pokemon' has no parameter 'shiny'" in errors
    )


def test_types_and_enums_must_be_compatible() -> None:
    sprite = Tool(
        name="sprite",
        description="d",
        parameters=[arg("id", required=True), arg("size")],
        routes=[Route(endpoint="get_sprite", arguments={"id": "id", "size": "size"})],
    )
    errors = errors_for(POKEMON, sprite, excluded=[])
    assert any(
        "argument 'id' is string, but endpoint parameter 'id' is integer" in e for e in errors
    )
    assert any("only accepts ['S', 'M']; give argument 'size' that enum" in e for e in errors)

    wider = sprite.model_copy(
        update={
            "parameters": [arg("id", "integer", required=True), arg("size", enum=["S", "M", "L"])]
        }
    )
    (error,) = errors_for(POKEMON, wider, excluded=[])
    assert "argument 'size' allows ['L']" in error

    exact = sprite.model_copy(
        update={"parameters": [arg("id", "integer", required=True), arg("size", enum=["S"])]}
    )
    assert errors_for(POKEMON, exact, excluded=[]) == []  # integer fills number too (limit)


def test_constants_must_be_in_the_endpoint_enum() -> None:
    sprite = Tool(
        name="sprite",
        description="d",
        parameters=[arg("id", "integer", required=True)],
        routes=[Route(endpoint="get_sprite", arguments={"id": "id"}, constants={"size": "XL"})],
    )
    (error,) = errors_for(POKEMON, sprite, excluded=[])
    assert "constant size='XL' is not in the endpoint's enum" in error


def test_a_route_that_can_never_be_chosen_is_an_error() -> None:
    reversed_routes = POKEMON.model_copy(update={"routes": list(reversed(POKEMON.routes))})
    (error,) = errors_for(reversed_routes)
    assert "the route to 'get_pokemon' can never be chosen" in error


def test_arguments_no_route_passes_are_an_error() -> None:
    extra = POKEMON.model_copy(update={"parameters": [*POKEMON.parameters, arg("color")]})
    assert errors_for(extra) == ["tool 'pokemon': arguments ['color'] are not passed by any route"]


# --- the direct design --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("wideSearch", "wide_search"),
        ("X-Key", "x_key"),
        ("editions.sort", "editions_sort"),
        ("from", "from_"),
        ("match", "match_"),
        ("2fa", "p_2fa"),
        ("---", "p"),
        ("id or name", "id_or_name"),
    ],
)
def test_safe_name(raw: str, name: str) -> None:
    assert safe_name(raw) == name


def test_direct_design_makes_one_valid_tool_per_endpoint() -> None:
    design = direct_design(SPEC)
    assert design.design == "direct"
    assert not design.approved
    assert [t.name for t in design.tools] == ["get_pokemon", "list_pokemon", "get_sprite"]
    assert design.tools[2].routes == [
        Route(endpoint="get_sprite", arguments={"id": "id", "size": "size"})
    ]
    assert design.tools[2].parameters[1].enum == ["S", "M"]
    assert design.tools[0].parameters[0].description == "The path parameter 'id_or_name'."
    assert validate_toolset(design, SPEC) == []


def test_direct_design_renames_unsafe_and_clashing_parameters() -> None:
    spec = ApiSpec.model_validate(
        {
            "name": "X",
            "base_url": "https://x.dev",
            "endpoints": [
                {
                    "id": "get_rates",
                    "method": "GET",
                    "path": "/rates/{id}",
                    "summary": "s",
                    "description": "d" * 2000,
                    "parameters": [
                        {"name": "id", "location": "path", "type": "string", "required": True},
                        {"name": "id", "location": "query", "type": "string"},
                        {"name": "ID", "location": "header", "type": "string"},
                        {"name": "from", "location": "query", "type": "string"},
                    ],
                }
            ],
        }
    )
    (tool,) = direct_design(spec).tools
    assert tool.routes[0].arguments == {
        "id": "id",
        "query_id": "id",
        "header_id": "ID",
        "from_": "from",
    }
    assert len(tool.description) == MAX_TOOL_DESCRIPTION
    assert tool.description.endswith("…")


@pytest.mark.parametrize("path", sorted(SPECS.glob("*.json")), ids=lambda p: p.stem)
def test_direct_design_is_valid_for_the_eval_specs(path: Path) -> None:
    spec = ApiSpec.model_validate_json(path.read_text(encoding="utf-8"))
    design = direct_design(spec)
    assert len(design.tools) == len(spec.endpoints)
    assert validate_toolset(design, spec) == []


# --- the CLI ------------------------------------------------------------------------------


def test_design_and_check_tools_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec_path = SPECS / "frankfurter.json"
    out = tmp_path / "tools.json"

    assert main(["design", str(spec_path), "--direct", "--out", str(out)]) == 0
    assert (
        "Frankfurter: 9 tools for 9 endpoints (direct design, 0 excluded)"
        in capsys.readouterr().out
    )

    assert main(["check-tools", str(out), "--spec", str(spec_path)]) == 0
    assert "valid, not approved yet" in capsys.readouterr().out

    data = json.loads(out.read_text())
    data["approved"] = True
    data["excluded"] = [{"endpoint": data["tools"][0]["routes"][0]["endpoint"], "reason": "test"}]
    out.write_text(json.dumps(data))
    assert main(["check-tools", str(out), "--spec", str(spec_path)]) == 1
    assert "1 problems in" in capsys.readouterr().out

    out.write_text('{"api": "x"}')
    assert main(["check-tools", str(out), "--spec", str(spec_path)]) == 1
    assert "is not a valid tools.json" in capsys.readouterr().err


def test_task_design_is_not_available_yet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["design", str(SPECS / "frankfurter.json"), "--out", str(tmp_path / "t.json")])
    assert code == 2
    assert "#29" in capsys.readouterr().err


def test_schema_command_prints_the_tools_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema", "--tools"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "ToolSet"

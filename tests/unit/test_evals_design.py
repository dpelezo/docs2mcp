"""The design eval's metrics (evals/design/README.md)."""

import json
from pathlib import Path

import pytest

from docs2mcp.cli import main
from docs2mcp.design import ExcludedEndpoint, Route, Tool, direct_design
from docs2mcp.evals import (
    format_metrics,
    input_schema,
    listed_tool,
    measure_design,
    metrics_json,
)
from docs2mcp.ingest.chunk import estimate_tokens
from docs2mcp.spec import ApiSpec
from tests.unit.test_design import POKEMON, SPEC, arg, toolset

ROOT = Path(__file__).parents[2]
SPECS = ROOT / "tests" / "fixtures" / "specs"
RESULTS = ROOT / "evals" / "design" / "results"
COMMITTED = ("pokeapi", "frankfurter", "postcodes_io")


def test_a_grouped_design_is_counted() -> None:
    metrics = measure_design(toolset(POKEMON), SPEC)

    assert metrics.valid
    assert metrics.problems == []
    assert (metrics.endpoints, metrics.covered, metrics.excluded) == (3, 2, 1)
    assert (metrics.tools, metrics.routes, metrics.max_routes_per_tool) == (1, 2, 2)
    assert [(t.name, t.routes, t.arguments) for t in metrics.per_tool] == [("pokemon", 2, 3)]
    prose = estimate_tokens(POKEMON.description) + sum(
        estimate_tokens(p.description) for p in POKEMON.parameters
    )
    assert metrics.description_tokens == prose
    assert metrics.tool_list_tokens >= metrics.per_tool[0].tokens > 0
    assert (metrics.parameters_exposed, metrics.parameters_total) == (3, 3)
    assert metrics.unexposed == []


def test_parameters_no_route_fills_are_unexposed() -> None:
    partial = Tool(
        name="pokemon",
        description="Get or list Pokémon.",
        parameters=[arg("name"), arg("limit", "integer")],
        routes=[
            Route(endpoint="get_pokemon", requires=["name"], arguments={"name": "id_or_name"}),
            Route(endpoint="list_pokemon", arguments={"limit": "limit"}),
        ],
    )
    metrics = measure_design(toolset(partial), SPEC)

    assert (metrics.parameters_exposed, metrics.parameters_total) == (2, 3)
    assert metrics.unexposed == ["list_pokemon.offset"]
    assert "parameters: 2 of 3 exposed" in format_metrics(metrics)
    assert "not exposed: list_pokemon.offset" in format_metrics(metrics)


def test_constants_count_as_exposed() -> None:
    fixed = Tool(
        name="pokemon_page",
        description="List Pokémon, 20 at a time.",
        parameters=[arg("offset", "integer")],
        routes=[
            Route(endpoint="list_pokemon", arguments={"offset": "offset"}, constants={"limit": 20})
        ],
    )
    name_only = Tool(
        name="pokemon",
        description="Get one Pokémon.",
        parameters=[arg("name", required=True)],
        routes=[Route(endpoint="get_pokemon", arguments={"name": "id_or_name"})],
    )
    metrics = measure_design(toolset(fixed, name_only), SPEC)
    assert (metrics.parameters_exposed, metrics.parameters_total) == (3, 3)


def test_an_invalid_design_is_measured_and_its_problems_listed() -> None:
    lonely = Tool(
        name="sprite",
        description="Get a sprite.",
        parameters=[arg("id", "integer", required=True)],
        routes=[Route(endpoint="get_sprite", requires=["id"], arguments={"id": "id"})],
    )
    metrics = measure_design(toolset(lonely, excluded=[]), SPEC)

    assert not metrics.valid
    assert (metrics.covered, metrics.excluded) == (1, 0)
    assert any("get_pokemon" in p for p in metrics.problems)
    assert "INVALID (2 problems)" in format_metrics(metrics)


def test_unknown_endpoints_are_not_counted_as_covered() -> None:
    ghost = Tool(
        name="ghost",
        description="Nothing.",
        parameters=[],
        routes=[Route(endpoint="no_such_endpoint")],
    )
    excluded = [ExcludedEndpoint(endpoint="also_missing", reason="test")]
    metrics = measure_design(toolset(ghost, excluded=excluded), SPEC)

    assert (metrics.covered, metrics.excluded) == (0, 0)
    assert metrics.parameters_total == 0
    assert not metrics.valid


def test_input_schema_matches_the_mcp_shape() -> None:
    tool = Tool(
        name="search",
        description="Search.",
        parameters=[
            arg("q", required=True),
            arg("size", enum=["S", "M"], default="S"),
            arg("ids", "array", items_type="integer"),
        ],
        routes=[Route(endpoint="get_pokemon", requires=["q"], arguments={"q": "id_or_name"})],
    )

    assert input_schema(tool) == {
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "q"},
            "size": {"type": "string", "description": "size", "enum": ["S", "M"], "default": "S"},
            "ids": {"type": "array", "description": "ids", "items": {"type": "integer"}},
        },
        "required": ["q"],
    }
    assert listed_tool(tool)["name"] == "search"
    assert "required" not in input_schema(POKEMON)


def test_metrics_json_includes_the_derived_fields() -> None:
    data = metrics_json(measure_design(toolset(POKEMON), SPEC))

    assert data["max_routes_per_tool"] == 2
    assert data["per_tool"][0]["name"] == "pokemon"


@pytest.mark.parametrize("api", COMMITTED)
def test_committed_baselines_match_the_direct_design(api: str) -> None:
    """The baseline numbers in evals/design/results/ are what the code computes today."""
    spec = ApiSpec.model_validate_json((SPECS / f"{api}.json").read_text(encoding="utf-8"))
    recorded = json.loads((RESULTS / api / "direct.json").read_text(encoding="utf-8"))
    recorded.pop("run")

    assert metrics_json(measure_design(direct_design(spec), spec)) == recorded


def test_measure_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec_path = SPECS / "frankfurter.json"
    tools = tmp_path / "tools.json"
    assert main(["design", str(spec_path), "--direct", "--out", str(tools)]) == 0
    capsys.readouterr()

    out = tmp_path / "metrics" / "direct.json"
    assert main(["measure", str(tools), "--spec", str(spec_path), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "Frankfurter (direct design): valid" in printed
    assert "9 covered, 0 excluded, of 9" in printed
    assert json.loads(out.read_text())["tools"] == 9

    tools.write_text('{"api": "x"}')
    assert main(["measure", str(tools), "--spec", str(spec_path)]) == 1
    assert "is not a valid tools.json" in capsys.readouterr().err

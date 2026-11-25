"""Generated MCP servers: what gets built, and that each server lists and calls its tools (#30).

The servers are generated into a temporary folder, imported, and driven in-process
with the official MCP client. Their HTTP calls go to an httpx.MockTransport, so no
request leaves the process.
"""

import asyncio
import importlib.util
import json
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from mcp import Client

from docs2mcp.cli import main
from docs2mcp.design import Route, Tool, ToolParameter, ToolSet, direct_design, set_approval
from docs2mcp.generate import NothingToGenerateError, generate, plan_generation
from docs2mcp.generate.render import python_type, slug
from docs2mcp.spec import ApiSpec, AuthScheme, AuthType, ParamType
from tests.unit.test_design import SPEC, arg

ROOT = Path(__file__).parents[2]
SPECS = ROOT / "tests" / "fixtures" / "specs"
REPLAY = ROOT / "evals" / "design" / "replay"
COMMITTED = ("frankfurter", "postcodes_io", "pokeapi")

Handler = Callable[[httpx.Request], httpx.Response]


def load_spec(api: str) -> ApiSpec:
    return ApiSpec.model_validate_json((SPECS / f"{api}.json").read_text(encoding="utf-8"))


def approved_design(api: str) -> ToolSet:
    toolset = ToolSet.model_validate_json((REPLAY / api / "tools.json").read_text("utf-8"))
    return set_approval(toolset, every=True).toolset


@pytest.fixture
def server_module(tmp_path: Path) -> Iterator[Callable[[ToolSet, ApiSpec], ModuleType]]:
    loaded: list[str] = []

    def build(toolset: ToolSet, spec: ApiSpec) -> ModuleType:
        out = tmp_path / f"server_{len(loaded)}"
        generate(toolset, spec, out)
        name = f"generated_server_{len(loaded)}_{slug(toolset.api).replace('-', '_')}"
        module_spec = importlib.util.spec_from_file_location(name, out / "server.py")
        assert module_spec is not None
        assert module_spec.loader is not None
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[name] = module
        module_spec.loader.exec_module(module)
        loaded.append(name)
        return module

    yield build
    for name in loaded:
        sys.modules.pop(name, None)


def set_transport(module: ModuleType, handler: Handler) -> None:
    """Send the generated server's requests to a mock, never to the network."""
    vars(module)["transport"] = httpx.MockTransport(handler)


def echo(requests: list[httpx.Request], payload: Any = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True} if payload is None else payload)

    return handler


async def _list_and_call(
    module: ModuleType, calls: list[tuple[str, dict[str, Any]]]
) -> tuple[list[Any], list[Any]]:
    async with Client(module.mcp) as client:
        listed = (await client.list_tools()).tools
        results = [await client.call_tool(name, arguments) for name, arguments in calls]
    return listed, results


def run(module: ModuleType, calls: list[tuple[str, dict[str, Any]]]) -> tuple[list[Any], list[Any]]:
    return asyncio.run(_list_and_call(module, calls))


SAMPLES: dict[ParamType, Any] = {
    ParamType.STRING: "abc",
    ParamType.INTEGER: 2,
    ParamType.NUMBER: 1.5,
    ParamType.BOOLEAN: True,
    ParamType.OBJECT: {"a": 1},
}


def sample(parameter: ToolParameter) -> Any:
    kind = parameter.items_type if parameter.type is ParamType.ARRAY else parameter.type
    value: Any = (
        parameter.enum[0]
        if parameter.enum is not None
        else SAMPLES.get(kind or ParamType.STRING, "abc")
    )
    return [value] if parameter.type is ParamType.ARRAY else value


def route_calls(toolset: ToolSet) -> list[tuple[str, dict[str, Any], Route]]:
    """One call per route: its selecting arguments plus every required argument."""
    calls: list[tuple[str, dict[str, Any], Route]] = []
    for tool in toolset.tools:
        params = {p.name: p for p in tool.parameters}
        for route in tool.routes:
            names = [*route.requires, *(p.name for p in tool.parameters if p.required)]
            calls.append((tool.name, {n: sample(params[n]) for n in dict.fromkeys(names)}, route))
    return calls


def path_pattern(path: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")


# --- every committed design generates a working server ---------------------------------


@pytest.mark.parametrize("api", COMMITTED)
def test_generated_server_lists_and_calls_every_route(
    api: str, server_module: Callable[[ToolSet, ApiSpec], ModuleType]
) -> None:
    spec = load_spec(api)
    toolset = approved_design(api)
    module = server_module(toolset, spec)
    requests: list[httpx.Request] = []
    set_transport(module, echo(requests))
    calls = route_calls(toolset)

    listed, results = run(module, [(name, arguments) for name, arguments, _ in calls])

    assert sorted(t.name for t in listed) == sorted(t.name for t in toolset.tools)
    assert [r.is_error for r in results] == [False] * len(calls), [
        r.content[0].text for r in results if r.is_error
    ]
    endpoints = {e.id: e for e in spec.endpoints}
    base_path = httpx.URL(str(toolset.base_url)).path.rstrip("/")
    for request, (_, _, route) in zip(requests, calls, strict=True):
        endpoint = endpoints[route.endpoint]
        assert request.method == endpoint.method.value
        assert path_pattern(base_path + endpoint.path).match(request.url.path), request.url


def test_direct_design_generates_too(
    server_module: Callable[[ToolSet, ApiSpec], ModuleType],
) -> None:
    spec = load_spec("frankfurter")
    toolset = set_approval(direct_design(spec), every=True).toolset
    module = server_module(toolset, spec)
    listed, _ = run(module, [])
    assert len(listed) == len(spec.endpoints)


# --- only approved, valid tools are built ----------------------------------------------


POKEMON = Tool(
    name="pokemon",
    description="Get one Pokémon, or list them.",
    parameters=[arg("name"), arg("limit", "number"), arg("offset", "integer")],
    routes=[
        Route(endpoint="get_pokemon", requires=["name"], arguments={"name": "id_or_name"}),
        Route(endpoint="list_pokemon", arguments={"limit": "limit", "offset": "offset"}),
    ],
    approved=True,
)
SPRITE = Tool(
    name="sprite",
    description="Get a sprite.",
    parameters=[arg("id", "integer", required=True), arg("size", enum=["S", "M"])],
    routes=[Route(endpoint="get_sprite", arguments={"id": "id", "size": "size"})],
    approved=False,
    note="images, not JSON",
)


def toolset(*tools: Tool) -> ToolSet:
    return ToolSet(
        api="PokéAPI",
        base_url="https://pokeapi.co/api/v2",  # pyright: ignore[reportArgumentType]
        design="task",
        tools=list(tools),
    )


def test_unapproved_and_invalid_tools_are_skipped_with_reasons() -> None:
    broken = Tool(
        name="broken",
        description="Lists Pokémon but forgets offset.",
        parameters=[arg("limit", "number")],
        routes=[Route(endpoint="list_pokemon", arguments={"limit": "limit"})],
        approved=True,
    )
    pokemon_get = Tool(
        name="pokemon",
        description="Get one Pokémon.",
        parameters=[arg("name", required=True)],
        routes=[Route(endpoint="get_pokemon", arguments={"name": "id_or_name"})],
        approved=True,
    )
    plan = plan_generation(toolset(pokemon_get, broken, SPRITE), SPEC)

    assert [t.name for t in plan.tools] == ["pokemon"]
    reasons = {s.name: s.reason for s in plan.skipped}
    assert reasons["sprite"] == "not approved: images, not JSON"
    assert reasons["broken"].startswith("invalid: tool 'broken', route to 'list_pokemon': ")
    assert plan.unreachable == ["list_pokemon", "get_sprite"]


def test_a_tool_reusing_an_endpoint_or_routing_nowhere_is_skipped() -> None:
    again = POKEMON.model_copy(update={"name": "pokemon_again"})
    ghost = Tool(
        name="ghost",
        description="Nothing.",
        routes=[Route(endpoint="no_such_endpoint")],
        approved=True,
    )
    unapproved = SPRITE.model_copy(update={"note": None})
    plan = plan_generation(toolset(POKEMON, again, ghost, unapproved), SPEC)

    reasons = {s.name: s.reason for s in plan.skipped}
    assert reasons["pokemon_again"] == (
        "invalid: endpoint 'get_pokemon' is already reached by tool 'pokemon' (and 1 more)"
    )
    assert reasons["ghost"] == "invalid: route to unknown endpoint 'no_such_endpoint'"
    assert reasons["sprite"] == "not approved"


def test_nothing_approved_means_nothing_is_generated(tmp_path: Path) -> None:
    unapproved = POKEMON.model_copy(update={"approved": False})
    with pytest.raises(NothingToGenerateError, match="pokemon: not approved"):
        generate(toolset(unapproved, SPRITE), SPEC, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("api", "env"),
    [
        ("Open-Meteo Weather Forecast API", "OPEN_METEO_WEATHER_FORECAST_API_KEY"),
        ("PokéAPI", "POKEAPI_API_KEY"),
    ],
)
def test_api_key_variable_names(api: str, env: str, tmp_path: Path) -> None:
    keyed = toolset(POKEMON).model_copy(
        update={"api": api, "auth": AuthScheme(type=AuthType.BEARER, location="header")}
    )
    generate(keyed, SPEC, tmp_path)
    assert f'API_KEY_ENV = "{env}"' in (tmp_path / "server.py").read_text()
    assert f"Set `{env}` to send your API key." in (tmp_path / "README.md").read_text()


def test_report_and_readme_list_every_gap(tmp_path: Path) -> None:
    generate(toolset(POKEMON, SPRITE), SPEC, tmp_path)

    report = json.loads((tmp_path / "generation_report.json").read_text())
    assert report["tools"] == ["pokemon"]
    assert report["skipped"] == [
        {"name": "sprite", "reason": "not approved: images, not JSON", "endpoints": ["get_sprite"]}
    ]
    assert report["unreachable"] == ["get_sprite"]
    readme = (tmp_path / "README.md").read_text()
    assert "| `sprite` | not approved: images, not JSON | get_sprite |" in readme
    assert "No generated tool calls these: `GET /sprites/{id}`." in readme
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert 'name = "pokeapi-mcp"' in pyproject
    assert '"mcp>=2.3,<3"' in pyproject


# --- what a generated server does at run time ------------------------------------------


@pytest.fixture
def pokemon_server(server_module: Callable[[ToolSet, ApiSpec], ModuleType]) -> ModuleType:
    sprite = SPRITE.model_copy(update={"approved": True})
    return server_module(toolset(POKEMON, sprite), SPEC)


def test_route_choice_and_request_building(pokemon_server: ModuleType) -> None:
    requests: list[httpx.Request] = []
    set_transport(pokemon_server, echo(requests))

    _, results = run(
        pokemon_server,
        [
            ("pokemon", {"name": "mr mime"}),
            ("pokemon", {"limit": 5, "offset": 10}),
            ("sprite", {"id": 7, "size": "M"}),
        ],
    )

    assert not any(r.is_error for r in results)
    urls = [str(r.url) for r in requests]
    assert urls == [
        "https://pokeapi.co/api/v2/pokemon/mr%20mime",
        "https://pokeapi.co/api/v2/pokemon?limit=5.0&offset=10",
        "https://pokeapi.co/api/v2/sprites/7?size=M",
    ]
    assert requests[0].headers["user-agent"].startswith("docs2mcp-generated-server/")


PROVIDER_RATES = Tool(
    name="provider_exchange_rates",
    description="Rates from one provider: one pair, or a series.",
    parameters=[
        arg("provider", required=True),
        arg("base"),
        arg("quote"),
        arg("quotes"),
        arg("date"),
    ],
    routes=[
        Route(
            endpoint="get_provider_rate",
            requires=["base", "quote"],
            arguments={"provider": "provider", "base": "base", "quote": "quote"},
        ),
        Route(
            endpoint="get_provider_rates",
            arguments={"provider": "provider", "base": "base", "quotes": "quotes", "date": "date"},
            omitted=dict.fromkeys(
                ("from", "to", "group", "providers", "expand"), "not in this test"
            ),
        ),
    ],
    approved=True,
)


ITEMS = ApiSpec.model_validate(
    {
        "name": "Items",
        "base_url": "https://items.example.dev",
        "endpoints": [
            {
                "id": "get_item",
                "method": "GET",
                "path": "/items/{id}",
                "summary": "One item.",
                "parameters": [
                    {"name": "id", "location": "path", "type": "string", "required": True}
                ],
            },
            {
                "id": "list_items",
                "method": "GET",
                "path": "/items",
                "summary": "Items, filtered.",
                "parameters": [
                    {"name": "id", "location": "query", "type": "string"},
                    {"name": "page", "location": "query", "type": "integer"},
                ],
            },
        ],
    }
)
ITEM = Tool(
    name="items",
    description="One item, or a page of items.",
    parameters=[arg("id"), arg("page", "integer")],
    routes=[
        Route(endpoint="get_item", requires=["id"], arguments={"id": "id"}),
        Route(endpoint="list_items", arguments={"id": "id", "page": "page"}),
    ],
    approved=True,
)


def test_the_route_that_takes_every_argument_wins(
    server_module: Callable[[ToolSet, ApiSpec], ModuleType],
) -> None:
    tools = ToolSet(
        api="Items",
        base_url="https://items.example.dev",  # pyright: ignore[reportArgumentType]
        design="task",
        tools=[ITEM],
    )
    module = server_module(tools, ITEMS)
    requests: list[httpx.Request] = []
    set_transport(module, echo(requests))
    _, results = run(module, [("items", {"id": "a1"}), ("items", {"id": "a1", "page": 2})])
    # Before, the second call went to the first route as well, and lost its page.
    assert [str(r.url) for r in requests] == [
        "https://items.example.dev/items/a1",
        "https://items.example.dev/items?id=a1&page=2",
    ]
    assert [r.content[0].text for r in results] == ['{"ok": true}', '{"ok": true}']


def test_an_argument_no_matching_route_takes_is_named_not_dropped_silently(
    server_module: Callable[[ToolSet, ApiSpec], ModuleType],
) -> None:
    tools = ToolSet(
        api="Frankfurter",
        base_url="https://api.frankfurter.dev/v2",  # pyright: ignore[reportArgumentType]
        design="task",
        tools=[PROVIDER_RATES],
    )
    module = server_module(tools, load_spec("frankfurter"))
    requests: list[httpx.Request] = []
    set_transport(module, echo(requests, {"rate": 1.0945}))
    pair = {"provider": "ecb", "base": "EUR", "quote": "USD"}
    _, results = run(module, [("provider_exchange_rates", {**pair, "date": "2024-01-15"})])
    # Pilot 2, condition C: the pair route takes no date, the series route no quote.
    assert str(requests[0].url) == "https://api.frankfurter.dev/v2/providers/ecb/rate/EUR/USD"
    assert results[0].content[0].text == (
        '{"rate": 1.0945}\n[Not used by GET /providers/{provider}/rate/{base}/{quote}: date.]'
    )


def test_redirects_on_the_api_host_are_followed(pokemon_server: ModuleType) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/pokemon/ditto":
            return httpx.Response(302, headers={"location": "/api/v2/pokemon/132"})
        return httpx.Response(200, json={"name": "ditto", "id": 132})

    set_transport(pokemon_server, handler)
    _, results = run(pokemon_server, [("pokemon", {"name": "ditto"})])
    assert not results[0].is_error
    assert json.loads(results[0].content[0].text) == {"name": "ditto", "id": 132}
    assert [r.url.path for r in requests] == ["/api/v2/pokemon/ditto", "/api/v2/pokemon/132"]
    assert requests[1].headers["user-agent"] == requests[0].headers["user-agent"]


@pytest.mark.parametrize(
    ("location", "message"),
    [
        ("https://evil.example.dev/steal", "redirected to another host, evil.example.dev"),
        ("/api/v2/pokemon/ditto", "More than 5 redirects"),
    ],
)
def test_redirects_off_the_host_or_in_a_loop_are_refused(
    pokemon_server: ModuleType, location: str, message: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": location})

    set_transport(pokemon_server, handler)
    _, results = run(pokemon_server, [("pokemon", {"name": "ditto"})])
    assert results[0].is_error
    assert message in results[0].content[0].text
    assert all(r.url.host == "pokeapi.co" for r in requests)


def test_schema_types_enums_and_required(pokemon_server: ModuleType) -> None:
    listed, _ = run(pokemon_server, [])
    sprite = next(t for t in listed if t.name == "sprite")
    assert sprite.description == "Get a sprite."
    assert sprite.input_schema["required"] == ["id"]
    size = sprite.input_schema["properties"]["size"]
    assert {"enum": ["S", "M"], "type": "string"} in size["anyOf"]
    assert size["description"] == "size"


def test_errors_come_back_as_tool_errors(pokemon_server: ModuleType) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/boom"):
            raise httpx.ConnectError("connection refused", request=request)  # noqa: TRY003
        return httpx.Response(404, text="Not Found")

    set_transport(pokemon_server, failing)
    _, results = run(
        pokemon_server,
        [("pokemon", {"name": "missingno"}), ("pokemon", {"name": "boom"}), ("sprite", {})],
    )

    assert [r.is_error for r in results] == [True, True, True]
    texts = [r.content[0].text for r in results]
    assert "GET /pokemon/missingno returned HTTP 404: Not Found" in texts[0]
    assert "GET /pokemon/boom failed: connection refused" in texts[1]
    assert "Field required" in texts[2]


def test_non_json_responses_say_what_they_are(pokemon_server: ModuleType) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})

    set_transport(pokemon_server, handler)
    _, results = run(pokemon_server, [("sprite", {"id": 1})])
    assert results[0].content[0].text.startswith("[The response is image/png, not JSON]")


def _respond(module: ModuleType, payload: Any) -> str:
    set_transport(module, lambda _request: httpx.Response(200, json=payload))
    _, results = run(module, [("pokemon", {})])
    assert not results[0].is_error
    return results[0].content[0].text


def test_long_lists_are_halved_so_later_fields_still_show(
    pokemon_server: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pokemon_server, "MAX_RESULT_CHARS", 2_000)
    move = {"move": {"name": "tackle"}, "version_group_details": [{"level": n} for n in range(40)]}
    pokemon = {
        "abilities": [{"name": "volt-absorb"}],
        "moves": [move] * 60,
        "name": "jolteon",
        "stats": [{"base_stat": 130, "stat": {"name": "speed"}}],
        "types": [{"type": {"name": "electric"}}],
    }
    text = _respond(pokemon_server, pokemon)

    body, note = text.rsplit("\n", 1)
    shown = json.loads(body)
    assert len(body) <= 2_000
    assert shown["stats"] == pokemon["stats"]
    assert shown["types"] == pokemon["types"]
    assert shown["name"] == "jolteon"
    assert 0 < len(shown["moves"]) < 60
    assert shown["moves"][0]["move"] == {"name": "tackle"}
    full = len(json.dumps(pokemon, ensure_ascii=False))
    assert note.startswith(f"[Shortened to fit 2,000 of {full:,} characters")
    assert "(moves: " in note
    assert "items not shown" in note
    assert note.endswith("Narrow the request to see more.]")


def test_a_top_level_list_is_shortened_too(
    pokemon_server: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pokemon_server, "MAX_RESULT_CHARS", 50)
    body, note = _respond(pokemon_server, ["x" * 10] * 20).rsplit("\n", 1)
    assert json.loads(body) == ["x" * 10] * 2
    assert "(the response: 18 items not shown)" in note


def test_big_nested_objects_lose_their_last_keys_but_the_top_level_keeps_all(
    pokemon_server: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pokemon_server, "MAX_RESULT_CHARS", 400)
    sprites = {f"front_{n}": "https://example.dev/" + "s" * 40 for n in range(8)}
    form = {"name": "jolteon", "sprites": sprites, "types": ["electric"], "z": "end"}
    body, note = _respond(pokemon_server, form).rsplit("\n", 1)
    shown = json.loads(body)
    assert list(shown) == ["name", "sprites", "types", "z"]
    assert list(shown["sprites"]) == ["front_0", "front_1", "front_2", "front_3"]
    assert "(sprites: 4 keys not shown)" in note


def test_small_responses_are_untouched_and_unlistable_ones_are_cut(
    pokemon_server: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _respond(pokemon_server, {"results": [1, 2, 3]}) == '{"results": [1, 2, 3]}'
    monkeypatch.setattr(pokemon_server, "MAX_RESULT_CHARS", 50)
    full = json.dumps({"description": "y" * 100, "results": ["x"]})
    assert _respond(pokemon_server, {"description": "y" * 100, "results": ["x"]}) == (
        f"{full[:50]}\n[Cut to 50 of {len(full)} characters. Narrow the request.]"
    )


def test_api_keys_come_from_the_environment(
    server_module: Callable[[ToolSet, ApiSpec], ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    keyed = toolset(POKEMON).model_copy(
        update={"auth": AuthScheme(type=AuthType.API_KEY, location="query", name="key")}
    )
    module = server_module(keyed, SPEC)
    requests: list[httpx.Request] = []
    set_transport(module, echo(requests))

    monkeypatch.delenv("POKEAPI_API_KEY", raising=False)
    _, without = run(module, [("pokemon", {"name": "ditto"})])
    assert not without[0].is_error
    assert "key" not in requests[0].url.params

    monkeypatch.setenv("POKEAPI_API_KEY", "secret")
    _, ok = run(module, [("pokemon", {"name": "ditto"})])
    assert not ok[0].is_error
    assert requests[1].url.params["key"] == "secret"


@pytest.mark.parametrize(
    ("parameter", "expected"),
    [
        (arg("q", required=True), "str"),
        (arg("n", "integer"), "int | None"),
        (arg("ids", "array", items_type="integer"), "list[int] | None"),
        (arg("unit", enum=["c", "f"], required=True), 'Literal["c", "f"]'),
        (arg("on", "boolean"), "bool | None"),
        (arg("extra", "object"), "dict[str, Any] | None"),
    ],
)
def test_python_types(parameter: ToolParameter, expected: str) -> None:
    assert python_type(parameter) == expected


# --- the command -----------------------------------------------------------------------


def test_generate_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tools = tmp_path / "tools.json"
    spec = SPECS / "frankfurter.json"
    tools.write_text((REPLAY / "frankfurter" / "tools.json").read_text("utf-8"))
    out = tmp_path / "server"

    assert main(["generate", str(tools), "--spec", str(spec), "--out", str(out)]) == 1
    assert "nothing to generate" in capsys.readouterr().err

    assert main(["approve", str(tools), "--all", "--except", "currencies"]) == 0
    assert main(["generate", str(tools), "--spec", str(spec), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "Frankfurter: generated 3 tools" in printed
    assert "(1 skipped, 1 endpoints excluded, 2 unreachable)" in printed
    assert "skipped currencies: not approved" in printed
    assert sorted(p.name for p in out.iterdir()) == [
        "README.md",
        "generation_report.json",
        "pyproject.toml",
        "server.py",
    ]

    tools.write_text('{"api": "x"}')
    assert main(["generate", str(tools), "--spec", str(spec), "--out", str(out)]) == 1
    assert "is not a valid tools.json" in capsys.readouterr().err

"""Generated servers pass Ruff (format and lint) and Pyright strict under their own config (#31).

Each server's pyproject.toml carries the rules, so these checks run the same tools a
person would run inside the generated folder. The designs are the three committed
ones and the direct design for each committed spec, plus one built to hit the
awkward cases: quotes, long text, single-value enums, defaults, every parameter
location, fixed values, a key, and a tool with no arguments.
"""

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client

from docs2mcp.design import (
    ExcludedEndpoint,
    Route,
    Tool,
    ToolSet,
    direct_design,
    set_approval,
)
from docs2mcp.generate import generate
from docs2mcp.spec import ApiSpec, AuthScheme, AuthType
from tests.unit.test_design import arg

ROOT = Path(__file__).parents[2]
SPECS = ROOT / "tests" / "fixtures" / "specs"
REPLAY = ROOT / "evals" / "design" / "replay"
BIN = Path(sys.executable).parent
COMMITTED = ("frankfurter", "postcodes_io", "pokeapi")

AWKWARD_SPEC = ApiSpec.model_validate(
    {
        "name": 'Awkward "Quoted" API',
        "base_url": "https://api.example.dev/v1",
        "auth": {"type": "api_key", "location": "header", "name": "X-Key"},
        "endpoints": [
            {
                "id": "search",
                "method": "POST",
                "path": "/things/{kind}/search",
                "summary": "Search things.",
                "parameters": [
                    {"name": "kind", "location": "path", "type": "string", "required": True},
                    {"name": "q", "location": "body", "type": "string", "required": True},
                    {"name": "tags", "location": "body", "type": "array", "items_type": "string"},
                    {"name": "X-Trace", "location": "header", "type": "boolean"},
                    {"name": "format", "location": "query", "type": "string", "enum": ["json"]},
                    {
                        "name": "unit",
                        "location": "query",
                        "type": "string",
                        "enum": ["metric", "imperial"],
                        "default": "metric",
                    },
                    {"name": "limit", "location": "query", "type": "integer", "default": 10},
                ],
            },
            {"id": "ping", "method": "GET", "path": "/ping", "summary": "Ping."},
            {"id": "image", "method": "GET", "path": "/image", "summary": "An image."},
        ],
    }
)

LONG = (
    'Search things by "kind" and a free-text query; it\'s long on purpose. ' * 6
    + "https://example.dev/a/very/long/url/that/cannot/be/split/at/a/space/anywhere/at/all/"
    + "so/it/stays/one/part/longer/than/a/line"
)

AWKWARD = ToolSet(
    api='Awkward "Quoted" API',
    base_url="https://api.example.dev/v1",  # pyright: ignore[reportArgumentType]
    auth=AuthScheme(type=AuthType.API_KEY, location="header", name="X-Key"),
    design="task",
    tools=[
        Tool(
            name="search",
            description=LONG,
            parameters=[
                arg("kind", required=True, description='The "kind", e.g. widget or gadget.'),
                arg("q", required=True, description=LONG[:290]),
                arg("tags", "array", items_type="string"),
                arg("trace", "boolean"),
                arg("unit", enum=["metric", "imperial"], default="metric"),
                arg("limit", "integer", default=10),
            ],
            routes=[
                Route(
                    endpoint="search",
                    arguments={
                        "kind": "kind",
                        "q": "q",
                        "tags": "tags",
                        "trace": "X-Trace",
                        "unit": "unit",
                        "limit": "limit",
                    },
                    constants={"format": "json"},
                )
            ],
            approved=True,
        ),
        Tool(
            name="ping",
            description="Ping.",
            routes=[Route(endpoint="ping")],
            approved=True,
        ),
    ],
    excluded=[ExcludedEndpoint(endpoint="image", reason='Not JSON: "image/png".')],
)


def _designs() -> list[tuple[str, ToolSet, ApiSpec]]:
    designs: list[tuple[str, ToolSet, ApiSpec]] = [("awkward", AWKWARD, AWKWARD_SPEC)]
    for api in COMMITTED:
        spec = ApiSpec.model_validate_json((SPECS / f"{api}.json").read_text(encoding="utf-8"))
        task = ToolSet.model_validate_json((REPLAY / api / "tools.json").read_text("utf-8"))
        designs.append((api, set_approval(task, every=True).toolset, spec))
        direct = set_approval(direct_design(spec), every=True).toolset
        designs.append((f"{api}_direct", direct, spec))
    return designs


@pytest.fixture(scope="module")
def servers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("servers")
    out: dict[str, Path] = {}
    for name, toolset, spec in _designs():
        generate(toolset, spec, root / name)
        out[name] = root / name
    return out


def _run(command: list[str], folder: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed tool paths from this environment
        command, cwd=folder, capture_output=True, text=True, check=False
    )


NAMES = [name for name, _, _ in _designs()]


@pytest.mark.parametrize("name", NAMES)
def test_ruff_format_and_lint_pass(name: str, servers: dict[str, Path]) -> None:
    folder = servers[name]
    formatted = _run([str(BIN / "ruff"), "format", "--check", "--diff", "."], folder)
    assert formatted.returncode == 0, formatted.stdout + formatted.stderr
    linted = _run([str(BIN / "ruff"), "check", "--output-format", "concise", "."], folder)
    assert linted.returncode == 0, linted.stdout + linted.stderr


@pytest.mark.parametrize("name", NAMES)
def test_pyright_strict_passes(name: str, servers: dict[str, Path]) -> None:
    checked = _run(
        [str(BIN / "pyright"), "--pythonpath", sys.executable, "server.py"], servers[name]
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "0 errors" in checked.stdout


def test_the_long_unbreakable_url_is_the_only_long_line(servers: dict[str, Path]) -> None:
    lines = (servers["awkward"] / "server.py").read_text(encoding="utf-8").splitlines()
    long = [line for line in lines if len(line) > 100]
    assert long
    assert all("https://example.dev/a/very/long/url" in line for line in long)


def test_the_awkward_server_builds_every_part_of_a_request(
    servers: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path, body, header, a fixed value, a default left to the API, and the key header."""
    spec = importlib.util.spec_from_file_location(
        "awkward_server", servers["awkward"] / "server.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requests: list[httpx.Request] = []
    vars(module)["transport"] = httpx.MockTransport(
        lambda r: requests.append(r) or httpx.Response(200, json=[])
    )
    monkeypatch.setenv("AWKWARD_QUOTED_API_KEY", "k")

    async def call() -> list[bool]:
        async with Client(module.mcp) as client:
            results = [
                await client.call_tool(
                    "search", {"kind": "a/b", "q": "x", "tags": ["t1", "t2"], "trace": True}
                ),
                await client.call_tool("ping", {}),
            ]
        return [r.is_error for r in results]

    assert asyncio.run(call()) == [False, False]
    search, ping = requests
    assert search.method == "POST"
    assert search.url.raw_path == b"/v1/things/a%2Fb/search?format=json"
    assert dict(search.url.params) == {"format": "json"}
    assert json.loads(search.content) == {"q": "x", "tags": ["t1", "t2"]}
    assert search.headers["x-trace"] == "true"
    assert search.headers["x-key"] == "k"
    assert (ping.method, ping.url.path) == ("GET", "/v1/ping")

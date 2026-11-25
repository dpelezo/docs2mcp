"""Condition D: FastMCP's server from the OpenAPI file, with a cassette (#59), offline."""

import asyncio
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from fastmcp import Client

from docs2mcp.evals.agent import Outcome, classify
from docs2mcp.evals.cassette import Cassette, Mode, request_key
from scripts import agent_eval
from scripts.fastmcp_server import (
    AsyncCassetteTransport,
    NotRecordedError,
    build_server,
    openapi_file,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
OPENAPI = ROOT / "evals" / "agent" / "conditions" / "openapi"
RATE = {"date": "2024-01-15", "base": "EUR", "quote": "USD", "rate": 1.0945}
ARGS = {"base": "EUR", "quote": "USD", "date": "2024-01-15", "providers": "ECB"}
URL = "https://api.frankfurter.dev/v2/rate/EUR/USD?date=2024-01-15&providers=ECB"


class _Live(httpx2.AsyncBaseTransport):
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.closed = False

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.urls.append(str(request.url))
        return httpx2.Response(200, json=RATE)

    async def aclose(self) -> None:
        self.closed = True


def _call(
    transport: httpx2.AsyncBaseTransport, tool: str, args: dict[str, Any]
) -> tuple[bool, str]:
    server, _ = build_server(OPENAPI / "frankfurter", transport)

    async def go() -> tuple[bool, str]:
        async with Client(server) as client:
            result = await client.call_tool(tool, args, raise_on_error=False)
        return result.is_error, "".join(getattr(p, "text", "") for p in result.content)

    return asyncio.run(go())


@pytest.mark.parametrize("api", ["frankfurter", "pokeapi", "postcodes_io"])
def test_the_committed_openapi_files_are_the_ones_condition_a_was_built_from(api: str) -> None:
    folder = OPENAPI / api
    source = json.loads((folder / "source.json").read_text("utf-8"))
    assert hashlib.sha256(openapi_file(folder).read_bytes()).hexdigest() == source["sha256"]
    assert any((folder / name).exists() for name in ("LICENSE", "LICENSE.md"))


def test_openapi_file_needs_one_of_the_known_names(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"no openapi\.json or openapi\.yml"):
        openapi_file(tmp_path)


def test_fastmcp_calls_go_through_the_cassette_and_share_its_keys(tmp_path: Path) -> None:
    path = tmp_path / "frankfurter.jsonl.gz"
    live = _Live()
    recording = AsyncCassetteTransport(Cassette(path), Mode.CACHE, live)
    first = _call(recording, "getRate", ARGS)
    assert first == (False, json.dumps(RATE, separators=(",", ":")))
    assert live.urls == [URL]
    assert (recording.hits, recording.recorded) == (0, 1)

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        (entry,) = [json.loads(line) for line in handle]
    assert entry["key"] == request_key(httpx.Request("GET", URL))

    replaying = AsyncCassetteTransport(Cassette(path), Mode.REPLAY, _Live())
    assert _call(replaying, "getRate", ARGS) == first
    assert replaying.hits == 1


def test_replay_refuses_requests_that_were_never_recorded(tmp_path: Path) -> None:
    transport = AsyncCassetteTransport(Cassette(tmp_path / "c.jsonl.gz"), Mode.REPLAY, _Live())
    error, text = _call(transport, "getRate", ARGS)
    assert error
    assert transport.misses == [f"GET {URL}"]
    assert classify(error, text) is Outcome.NOT_RECORDED

    async def direct() -> None:
        await transport.handle_async_request(httpx2.Request("GET", URL))

    with pytest.raises(NotRecordedError, match="not recorded: GET"):
        asyncio.run(direct())


def test_record_mode_always_asks_the_api_and_the_pool_outlives_clients(tmp_path: Path) -> None:
    live = _Live()
    transport = AsyncCassetteTransport(Cassette(tmp_path / "c.jsonl.gz"), Mode.RECORD, live)
    _call(transport, "getRate", ARGS)
    _call(transport, "getRate", ARGS)
    assert len(live.urls) == 2
    assert not live.closed
    asyncio.run(transport.shutdown())
    assert live.closed


def test_the_runner_starts_fastmcp_for_condition_d(tmp_path: Path) -> None:
    params = agent_eval.server_params(tmp_path, tmp_path, "D", "frankfurter", "replay")
    assert params.args[:3] == ["-m", "scripts.fastmcp_server", str(OPENAPI / "frankfurter")]
    assert agent_eval.server_built(tmp_path, "D", "pokeapi")
    assert not agent_eval.server_built(tmp_path, "D", "open_library")
    plans, skipped = agent_eval.plan(["D"], None, ["frankfurter-001", "open_library-001"], tmp_path)
    assert [(p.condition, p.api) for p in plans] == [("D", "frankfurter")]
    assert skipped == ["D/open_library"]

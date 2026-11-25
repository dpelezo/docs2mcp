"""Recording and replaying API responses for agent eval runs (#49), offline."""

import asyncio
import gzip
import json
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client, StdioServerParameters

from docs2mcp.design import ToolSet, set_approval
from docs2mcp.evals.cassette import (
    REDACTED,
    Cassette,
    CassetteTransport,
    Mode,
    NotRecordedError,
    request_key,
)
from docs2mcp.generate import generate
from docs2mcp.spec import ApiSpec
from scripts.eval_server import attach, load_server, main, summary

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]


def _request(url: str, method: str = "GET", body: object = None) -> httpx.Request:
    return httpx.Request(method, url, json=body)


class _Live(httpx.BaseTransport):
    """A fake API that counts calls and answers with the request's path."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={"error": "no such thing"})
        return httpx.Response(200, json={"path": request.url.path, "n": len(self.calls)})

    def close(self) -> None:
        self.closed = True


# Keys


def test_keys_sort_query_parameters_and_canonicalize_json_bodies() -> None:
    a = request_key(_request("https://api.dev/v1/x?b=2&a=1&a=0"))
    b = request_key(_request("https://api.dev/v1/x?a=1&a=0&b=2"))
    assert a == b == "GET https://api.dev/v1/x?a=0&a=1&b=2"
    post = request_key(_request("https://api.dev/p", "POST", {"z": 1, "a": [2, 1]}))
    assert post == 'POST https://api.dev/p {"a": [2, 1], "z": 1}'
    raw = request_key(httpx.Request("POST", "https://api.dev/p", content=b"not json"))
    assert raw == "POST https://api.dev/p not json"


def test_keys_blank_query_parameters_that_look_like_credentials() -> None:
    key = request_key(_request("https://api.dev/x?apikey=s3cret&AccessToken=t&city=Bern"))
    assert "s3cret" not in key
    assert key == f"GET https://api.dev/x?AccessToken={REDACTED}&apikey={REDACTED}&city=Bern"


# The cassette file


def test_a_cassette_persists_and_the_first_answer_wins(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "api.jsonl.gz"
    cassette = Cassette(path)
    cassette.add("GET a", httpx.Response(200, json={"v": 1}))
    cassette.add("GET a", httpx.Response(200, json={"v": 2}))
    cassette.add("GET b", httpx.Response(404, text="gone"))

    reloaded = Cassette(path)
    assert len(reloaded) == 2
    first = reloaded.get("GET a")
    assert first is not None
    assert first.json() == {"v": 1}
    assert first.headers["content-type"] == "application/json"
    gone = reloaded.get("GET b")
    assert gone is not None
    assert (gone.status_code, gone.text) == (404, "gone")
    assert reloaded.get("GET c") is None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        assert len(handle.read().splitlines()) == 2


def test_a_response_without_a_content_type_replays_without_one(tmp_path: Path) -> None:
    cassette = Cassette(tmp_path / "c.jsonl.gz")
    cassette.add("GET x", httpx.Response(200, content=b"plain"))
    stored = cassette.get("GET x")
    assert stored is not None
    assert "content-type" not in stored.headers


# Modes


def test_record_always_calls_the_api_and_keeps_new_requests(tmp_path: Path) -> None:
    live = _Live()
    transport = CassetteTransport(Cassette(tmp_path / "c.jsonl.gz"), Mode.RECORD, live)
    with httpx.Client(transport=transport) as client:
        client.get("https://api.dev/x")
        client.get("https://api.dev/x")
    assert len(live.calls) == 2
    assert (transport.recorded, transport.hits, len(transport.cassette)) == (2, 0, 1)


def test_replay_serves_recorded_requests_and_refuses_new_ones(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl.gz"
    Cassette(path).add(request_key(_request("https://api.dev/x")), httpx.Response(200, json=[1]))
    live = _Live()
    transport = CassetteTransport(Cassette(path), Mode.REPLAY, live)
    with httpx.Client(transport=transport) as client:
        assert client.get("https://api.dev/x").json() == [1]
        with pytest.raises(NotRecordedError, match=r"not recorded: GET https://api\.dev/y"):
            client.get("https://api.dev/y")
    assert live.calls == []
    assert (transport.hits, transport.misses) == (1, ["GET https://api.dev/y"])
    assert "1 not recorded" in summary(transport)


def test_cache_replays_what_it_has_and_records_the_rest(tmp_path: Path) -> None:
    live = _Live()
    transport = CassetteTransport(Cassette(tmp_path / "c.jsonl.gz"), Mode.CACHE, live)
    with httpx.Client(transport=transport) as client:
        first = client.get("https://api.dev/x").json()
        again = client.get("https://api.dev/x").json()
        error = client.get("https://api.dev/missing")
    assert first == again == {"path": "/x", "n": 1}
    assert error.status_code == 404
    assert len(live.calls) == 2
    assert (transport.hits, transport.recorded) == (1, 2)
    assert summary(transport).endswith("1 replayed, 2 recorded")


def test_closing_a_client_keeps_the_live_pool_open_until_shutdown(tmp_path: Path) -> None:
    live = _Live()
    transport = CassetteTransport(Cassette(tmp_path / "c.jsonl.gz"), Mode.CACHE, live)
    with httpx.Client(transport=transport):
        pass
    assert not live.closed
    transport.shutdown()
    assert live.closed


def test_credentials_never_reach_the_file(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl.gz"
    transport = CassetteTransport(Cassette(path), Mode.RECORD, _Live())
    with httpx.Client(transport=transport) as client:
        client.get("https://api.dev/x", params={"apikey": "s3cret"}, headers={"X-Key": "h3ader"})
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        text = handle.read()
    assert "s3cret" not in text
    assert "h3ader" not in text


# With a generated server


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Path:
    spec = ApiSpec.model_validate_json(
        (ROOT / "tests" / "fixtures" / "specs" / "frankfurter.json").read_text("utf-8")
    )
    tools = ToolSet.model_validate_json(
        (ROOT / "evals" / "design" / "replay" / "frankfurter" / "tools.json").read_text("utf-8")
    )
    folder = tmp_path_factory.mktemp("frankfurter")
    generate(set_approval(tools, every=True).toolset, spec, folder)
    return folder


def _call(module: object, arguments: dict[str, object]) -> tuple[bool, str]:
    async def run() -> tuple[bool, str]:
        async with Client(vars(module)["mcp"]) as client:
            result = await client.call_tool("currencies", arguments)
        return result.is_error, "".join(getattr(p, "text", "") for p in result.content)

    return asyncio.run(run())


def test_a_generated_server_replays_what_it_recorded(server: Path, tmp_path: Path) -> None:
    path = tmp_path / "frankfurter.jsonl.gz"
    live = _Live()
    recording = load_server(server)
    attach(recording, path, Mode.RECORD).live = live
    recorded = _call(recording, {"currency": "ISK"})
    assert recorded[0] is False
    assert len(live.calls) == 1

    replaying = load_server(server)
    transport = attach(replaying, path, Mode.REPLAY)
    assert _call(replaying, {"currency": "ISK"}) == recorded
    error, text = _call(replaying, {"currency": "JPY"})
    assert error
    assert "not recorded" in text
    assert transport.hits == 1


def test_load_server_refuses_a_file_that_is_not_a_generated_server(tmp_path: Path) -> None:
    (tmp_path / "server.py").write_text("print('hello')\n", encoding="utf-8")
    with pytest.raises(ImportError, match="not a docs2mcp generated server"):
        load_server(tmp_path)


def test_the_launcher_serves_a_cassette_over_stdio(server: Path, tmp_path: Path) -> None:
    path = tmp_path / "frankfurter.jsonl.gz"
    key = "GET https://api.frankfurter.dev/v2/currency/ISK"
    Cassette(path).add(key, httpx.Response(200, json={"iso_code": "ISK", "name": "Icelandic"}))
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "scripts.eval_server",
            str(server),
            "--cassette",
            str(path),
            "--mode",
            "replay",
        ],
        cwd=ROOT,
    )

    async def run() -> str:
        async with Client(params) as client:
            result = await client.call_tool("currencies", {"currency": "ISK"})
        return "".join(getattr(p, "text", "") for p in result.content)

    assert json.loads(asyncio.run(run())) == {"iso_code": "ISK", "name": "Icelandic"}


def test_main_runs_the_server_and_prints_a_summary(
    server: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def fake_run(_server: object, transport: str) -> None:
        calls.append(transport)

    monkeypatch.setattr("mcp.server.mcpserver.MCPServer.run", fake_run)
    path = tmp_path / "c.jsonl.gz"
    assert main([str(server), "--cassette", str(path), "--mode", "replay"]) == 0
    assert calls == ["stdio"]
    assert "0 replayed, 0 recorded, 0 not recorded" in capsys.readouterr().err

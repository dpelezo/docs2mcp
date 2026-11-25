"""The playground's live-call function (#74), with the upstream API mocked."""

import asyncio
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
LONG = "x" * 201


def _load() -> ModuleType:
    spec = spec_from_file_location("live_call", ROOT / "web" / "api" / "call.py")
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def call() -> ModuleType:
    """A fresh copy, so rate-limit counts and loaded servers do not leak between tests."""
    return _load()


def _upstream(seen: list[httpx.Request]) -> httpx.MockTransport:
    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"EUR": "Euro", "USD": "United States Dollar"})

    return httpx.MockTransport(serve)


def _post(
    call: ModuleType, body: Any, *, ip: str = "203.0.113.7", method: str = "POST"
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=call.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://demo.test") as client:
            content = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers = {"x-forwarded-for": f"{ip}, 10.0.0.1", "content-type": "application/json"}
            return await client.request(method, "/api/call", content=content, headers=headers)

    return asyncio.run(send())


def test_runs_a_get_only_tool_over_mcp(call: ModuleType) -> None:
    seen: list[httpx.Request] = []
    call.server("frankfurter", "C").transport = _upstream(seen)
    response = _post(call, {"api": "frankfurter", "condition": "C", "tool": "currencies"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["is_error"] is False
    assert "United States Dollar" in data["text"]
    assert [r.method for r in seen] == ["GET"]
    assert str(seen[0].url).startswith("https://api.frankfurter.dev/v2/currencies")


def test_upstream_failures_come_back_as_tool_errors(call: ModuleType) -> None:
    call.server("frankfurter", "C").transport = httpx.MockTransport(
        lambda _request: httpx.Response(500, text="down")
    )
    data = _post(call, {"api": "frankfurter", "condition": "C", "tool": "currencies"}).json()
    assert data["ok"] is True
    assert data["is_error"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"api": "open_library", "condition": "C", "tool": "search"},
        {"api": "frankfurter", "condition": "A", "tool": "currencies"},
        {"api": "frankfurter", "condition": "C", "tool": "nope"},
        {"api": "postcodes_io", "condition": "C", "tool": "bulk_postcodes"},
        {"api": "frankfurter", "condition": "C", "tool": "currencies", "arguments": [1]},
        {"api": "frankfurter", "condition": "C", "tool": "currencies", "arguments": {"a": {}}},
        {"api": "frankfurter", "condition": "C", "tool": "currencies", "arguments": {"a": LONG}},
        [1, 2],
        b"not json",
    ],
)
def test_rejects_anything_outside_the_allowlist(call: ModuleType, body: Any) -> None:
    response = _post(call, body)
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_only_get_only_tools_are_listed(call: ModuleType) -> None:
    manifest = call.allowed()
    assert set(manifest) == {"pokeapi", "frankfurter", "postcodes_io"}
    for api, conditions in manifest.items():
        assert set(conditions) == {"B", "C"}
        for condition, tools in conditions.items():
            routes = call.server(api, condition).ROUTES
            assert tools
            for tool in tools:
                assert {r["method"] for r in routes[tool]} == {"GET"}
    postcodes = call.server("postcodes_io", "C").ROUTES
    assert set(postcodes) - set(manifest["postcodes_io"]["C"])  # its POST tool is left out


def test_large_bodies_and_other_methods_are_refused(call: ModuleType) -> None:
    assert _post(call, b"{" + b" " * 5000 + b"}").status_code == 413
    assert _post(call, b"", method="PUT").status_code == 405
    listing = _post(call, b"", method="GET")
    assert listing.status_code == 200
    assert "frankfurter" in listing.json()["tools"]


def test_limits_calls_per_visitor(call: ModuleType) -> None:
    call.server("frankfurter", "C").transport = _upstream([])
    body = {"api": "frankfurter", "condition": "C", "tool": "currencies"}
    statuses = [_post(call, body).status_code for _ in range(11)]
    assert statuses == [200] * 10 + [429]
    assert _post(call, body, ip="198.51.100.9").status_code == 200


def test_unexpected_failures_do_not_leak_details(
    call: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: object) -> dict[str, Any]:
        msg = "secret detail"
        raise RuntimeError(msg)

    monkeypatch.setattr(call, "run", broken)
    response = _post(call, {"api": "frankfurter", "condition": "C", "tool": "currencies"})
    assert response.status_code == 502
    assert "secret" not in response.text

"""Live tool calls for the playground (#74): POST /api/call.

Runs one tool of a docs2mcp-generated server (conditions B and C) against the
real API and returns what an agent would see. The servers in _servers/ are
exactly what `docs2mcp generate` wrote for the eval; `scripts.export_demo`
writes them and a unit test fails if they are out of date.

There is no model and no key. Safety comes from what is allowed in:

- only the three keyless, permissively licensed APIs, and only tools whose
  every route is a GET (the manifest lists them);
- a small JSON body, scalar arguments and short strings;
- a per-visitor and a per-instance rate limit (best effort: each instance
  counts on its own);
- a 10 second upstream timeout, and the generated server's own 20,000
  character limit on responses.

Request:  {"api": "frankfurter", "condition": "C", "tool": "currencies", "arguments": {}}
Response: {"ok": true, "is_error": false, "text": "...", "ms": 412}
"""

import json
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any

from mcp import Client
from mcp.types import TextContent

# Generated servers read this when imported, which happens on the first call.
os.environ.setdefault("DOCS2MCP_TIMEOUT_SECONDS", "10")

SERVERS = Path(__file__).resolve().parent / "_servers"
MAX_BODY = 4_096
MAX_STRING = 200
MAX_ARGUMENTS = 16
PER_VISITOR = (10, 60.0)  # calls, per seconds
PER_INSTANCE = (120, 60.0)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_manifest: dict[str, dict[str, list[str]]] = json.loads(
    (SERVERS / "manifest.json").read_text("utf-8")
)
_modules: dict[tuple[str, str], ModuleType] = {}
_calls: dict[str, deque[float]] = {}


class RejectedError(Exception):
    """A request this endpoint will not run, with the status to answer."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def allowed() -> dict[str, dict[str, list[str]]]:
    """The tools that may be called: API, then condition, then tool names."""
    return _manifest


def server(api: str, condition: str) -> ModuleType:
    """The generated server for an API and condition, imported once per instance."""
    key = (api, condition)
    if key not in _modules:
        path = SERVERS / f"{api}_{condition.lower()}.py"
        spec = spec_from_file_location(f"live_{api}_{condition}", path)
        if spec is None or spec.loader is None:
            raise RejectedError(500, "server not found")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        _modules[key] = module
    return _modules[key]


def limit(visitor: str, now: float) -> None:
    """Count a call against the visitor's and the instance's limits."""
    for key, (most, window) in ((visitor, PER_VISITOR), ("*", PER_INSTANCE)):
        times = _calls.setdefault(key, deque())
        while times and now - times[0] > window:
            times.popleft()
        if len(times) >= most:
            raise RejectedError(429, "Too many calls. Try again in a minute.")
    for key in (visitor, "*"):
        _calls[key].append(now)


def parse(body: bytes) -> tuple[str, str, str, dict[str, Any]]:
    """Check a request body and return its API, condition, tool and arguments."""
    if len(body) > MAX_BODY:
        raise RejectedError(413, "Request too large.")
    try:
        data = json.loads(body)
    except ValueError:
        raise RejectedError(400, "Send a JSON object.") from None
    if not isinstance(data, dict):
        raise RejectedError(400, "Send a JSON object.")
    fields: dict[str, Any] = data  # pyright: ignore[reportUnknownVariableType]
    api, condition, tool = fields.get("api"), fields.get("condition"), fields.get("tool")
    arguments: Any = fields.get("arguments", {})
    tools = _manifest.get(str(api), {}).get(str(condition), [])
    if not isinstance(tool, str) or tool not in tools:
        raise RejectedError(400, "Unknown API, condition or tool, or the tool is not GET-only.")
    if not isinstance(arguments, dict) or len(arguments) > MAX_ARGUMENTS:  # pyright: ignore[reportUnknownArgumentType]
        raise RejectedError(400, "arguments must be a small JSON object.")
    clean: dict[str, Any] = {}
    for name, value in arguments.items():  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(name, str) or not isinstance(value, str | int | float | bool):
            raise RejectedError(400, "Arguments must be strings, numbers or booleans.")
        if isinstance(value, str) and len(value) > MAX_STRING:
            raise RejectedError(400, f"{name} is too long.")
        clean[name] = value
    return str(api), str(condition), tool, clean


async def run(api: str, condition: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call the tool over MCP, in process, as the eval's agent did."""
    start = time.perf_counter()
    async with Client(server(api, condition).mcp) as client:
        result = await client.call_tool(tool, arguments)
    text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
    ms = round((time.perf_counter() - start) * 1000)
    return {"ok": True, "is_error": bool(result.is_error), "text": text, "ms": ms}


def _visitor(scope: Scope) -> str:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
    forwarded = headers.get("x-forwarded-for", "").split(",")[0].strip()
    client = scope.get("client") or ("unknown", 0)
    return forwarded or headers.get("x-real-ip") or str(client[0])


async def _respond(send: Send, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"cache-control", b"no-store"),
        (b"x-content-type-options", b"nosniff"),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        chunk: bytes = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY:
            raise RejectedError(413, "Request too large.")
        chunks.append(chunk)
        if not message.get("more_body"):
            return b"".join(chunks)


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """The ASGI entry point Vercel loads."""
    if scope["type"] != "http":
        return
    try:
        if scope["method"] == "GET":
            await _respond(send, 200, {"ok": True, "tools": _manifest})
            return
        if scope["method"] != "POST":
            await _respond(send, 405, {"ok": False, "error": "Use POST."})
            return
        api, condition, tool, arguments = parse(await _body(receive))
        limit(_visitor(scope), time.monotonic())
        await _respond(send, 200, await run(api, condition, tool, arguments))
    except RejectedError as rejected:
        await _respond(send, rejected.status, {"ok": False, "error": rejected.message})
    except Exception:  # noqa: BLE001  (never send a traceback to a visitor)
        await _respond(send, 502, {"ok": False, "error": "The call failed."})

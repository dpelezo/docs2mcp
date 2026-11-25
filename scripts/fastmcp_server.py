"""Condition D: FastMCP's server, made from the API's own OpenAPI file, with a cassette.

    uv run python -m scripts.fastmcp_server evals/agent/conditions/openapi/frankfurter \\
        --cassette recordings/agent/frankfurter.jsonl.gz --mode cache

`FastMCP.from_openapi` builds the server from the committed original file
(`openapi.json` or `openapi.yml`), with FastMCP's own defaults: its tool names,
descriptions, argument handling and responses. Nothing is changed except the
HTTP client's transport, which records to or replays from the same cassette the
other conditions use (docs2mcp.evals.cassette). FastMCP uses `httpx2`, so this
wraps the cassette in an `httpx2` transport; requests are keyed the same way.

Unlike the generated servers, FastMCP returns whole responses, with no limit on
their size. That is part of what condition D measures (ADR 0013).
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import httpx2
from fastmcp import FastMCP

from docs2mcp.evals.cassette import Cassette, Mode, request_key
from scripts.reference_from_openapi import parse_openapi

USER_AGENT = "docs2mcp-evals-fastmcp"


class NotRecordedError(httpx2.TransportError):
    """Replay mode met a request that is not in the cassette."""


class AsyncCassetteTransport(httpx2.AsyncBaseTransport):
    """docs2mcp's cassette behind an httpx2 transport, for FastMCP's client."""

    def __init__(
        self, cassette: Cassette, mode: Mode, live: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self.cassette = cassette
        self.mode = mode
        self.live = live if live is not None else httpx2.AsyncHTTPTransport()
        self.hits = 0
        self.recorded = 0
        self.misses: list[str] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        key = request_key(httpx.Request(request.method, str(request.url), content=body))
        if self.mode is not Mode.RECORD:
            stored = self.cassette.get(key)
            if stored is not None:
                self.hits += 1
                return httpx2.Response(
                    stored.status_code, headers=dict(stored.headers), content=stored.content
                )
            if self.mode is Mode.REPLAY:
                self.misses.append(key)
                msg = f"not recorded: {key}"
                raise NotRecordedError(msg, request=request)
        response = await self.live.handle_async_request(request)
        content = await response.aread()
        content_type = response.headers.get("content-type", "")
        self.cassette.add(
            key,
            httpx.Response(
                response.status_code, headers={"content-type": content_type}, content=content
            ),
        )
        self.recorded += 1
        return httpx2.Response(
            response.status_code, headers={"content-type": content_type}, content=content
        )

    async def aclose(self) -> None:
        """Keep the live pool open; `shutdown` closes it."""

    async def shutdown(self) -> None:
        await self.live.aclose()


def openapi_file(folder: Path) -> Path:
    for name in ("openapi.json", "openapi.yml", "openapi.yaml"):
        if (folder / name).exists():
            return folder / name
    msg = f"no openapi.json or openapi.yml in {folder}"
    raise FileNotFoundError(msg)


def build_server(
    folder: Path, transport: httpx2.AsyncBaseTransport
) -> tuple[FastMCP[Any], dict[str, Any]]:
    """FastMCP's server for the OpenAPI file in folder, and the parsed document."""
    doc = parse_openapi(openapi_file(folder).read_bytes())
    source = json.loads((folder / "source.json").read_text("utf-8"))
    servers: list[dict[str, Any]] = doc.get("servers") or [{"url": ""}]
    client = httpx2.AsyncClient(
        base_url=str(servers[0]["url"]),
        transport=transport,
        timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    server = FastMCP.from_openapi(openapi_spec=doc, client=client, name=str(source["api"]))
    return server, doc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run FastMCP's server with a cassette.")
    parser.add_argument("folder", type=Path, help="evals/agent/conditions/openapi/<api>")
    parser.add_argument("--cassette", type=Path, required=True, help="a .jsonl.gz file")
    parser.add_argument("--mode", type=Mode, choices=list(Mode), default=Mode.CACHE)
    args = parser.parse_args(argv)
    transport = AsyncCassetteTransport(Cassette(args.cassette), args.mode)
    server, _ = build_server(args.folder, transport)
    try:
        server.run(transport="stdio", show_banner=False)
    finally:
        line = f"cassette {args.cassette}: {transport.hits} replayed, {transport.recorded} recorded"
        if args.mode is Mode.REPLAY:
            line += f", {len(transport.misses)} not recorded"
        print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

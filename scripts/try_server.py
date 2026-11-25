"""Start a generated MCP server, list its tools, and make one real tool call.

    uv run python scripts/try_server.py servers/frankfurter exchange_rates '{"base": "EUR"}'
    uv run python scripts/try_server.py servers/frankfurter --http

The server runs in its own folder with `uv run`, over stdio by default or over
streamable HTTP with --http. With no tool name, it only lists the tools. The call
goes to the real API, so it needs network.
"""

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def _session(server: Any, tool: str | None, arguments: dict[str, Any]) -> int:
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        print(f"tools/list: {len(tools)} tools")
        for listed in tools:
            print(f"  {listed.name}")
        if tool is None:
            return 0
        result = await client.call_tool(tool, arguments)
        text = "".join(getattr(part, "text", "") for part in result.content)
        print(f"tools/call {tool} {json.dumps(arguments)}: {'error' if result.is_error else 'ok'}")
        print(text[:1500])
        return 1 if result.is_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Start a generated MCP server and call it.")
    parser.add_argument("folder", type=Path, help="folder written by docs2mcp generate")
    parser.add_argument("tool", nargs="?", help="tool to call once")
    parser.add_argument("arguments", nargs="?", default="{}", help="JSON object of arguments")
    parser.add_argument("--http", action="store_true", help="use streamable HTTP, not stdio")
    args = parser.parse_args()
    arguments = json.loads(args.arguments)
    folder = args.folder.resolve()

    if not args.http:
        params = StdioServerParameters(
            command="uv", args=["run", "--quiet", "--directory", str(folder), "server.py"]
        )
        return asyncio.run(_session(params, args.tool, arguments))

    port = _free_port()
    command = ["uv", "run", "--quiet", "--directory", str(folder), "server.py", "--http"]
    process = subprocess.Popen([*command, "--port", str(port)])  # noqa: S603 - fixed command
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.5)
        else:
            print("the server did not start within 60 seconds", file=sys.stderr)
            return 1
        url = f"http://127.0.0.1:{port}/mcp"
        print(f"server: {url}")
        return asyncio.run(_session(url, args.tool, arguments))
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())

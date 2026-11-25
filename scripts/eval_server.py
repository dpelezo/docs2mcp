"""Run a generated MCP server over stdio with its API calls recorded or replayed.

    uv run python -m scripts.eval_server servers/openapi/frankfurter \\
        --cassette recordings/agent/frankfurter.jsonl.gz --mode cache

The agent eval (ADR 0013) starts every server this way. The generated server is
loaded unchanged; only its `transport` hook is set, so the server code under test
is exactly what `docs2mcp generate` wrote. On exit, a summary goes to stderr:
hits, newly recorded requests and, in replay mode, requests that were not
recorded. See docs2mcp.evals.cassette for the modes.
"""

import argparse
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from docs2mcp.evals.cassette import Cassette, CassetteTransport, Mode


def load_server(folder: Path) -> ModuleType:
    """Import a generated server.py without running its main()."""
    path = folder / "server.py"
    spec = importlib.util.spec_from_file_location(f"eval_server_{folder.name}", path)
    if spec is None or spec.loader is None:
        msg = f"cannot load {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if "transport" not in vars(module) or "mcp" not in vars(module):
        msg = f"{path} is not a docs2mcp generated server (no transport hook)"
        raise ImportError(msg)
    return module


def attach(module: ModuleType, cassette: Path, mode: Mode) -> CassetteTransport:
    transport = CassetteTransport(Cassette(cassette), mode)
    vars(module)["transport"] = transport
    return transport


def summary(transport: CassetteTransport) -> str:
    line = f"cassette {transport.cassette.path}: {transport.hits} replayed"
    line += f", {transport.recorded} recorded"
    if transport.mode is Mode.REPLAY:
        line += f", {len(transport.misses)} not recorded"
    return line


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a generated server with a cassette.")
    parser.add_argument("server", type=Path, help="folder with the generated server.py")
    parser.add_argument("--cassette", type=Path, required=True, help="a .jsonl.gz file")
    parser.add_argument(
        "--mode", type=Mode, choices=list(Mode), default=Mode.CACHE, help="default: cache"
    )
    args = parser.parse_args(argv)
    module = load_server(args.server)
    transport = attach(module, args.cassette, args.mode)
    try:
        module.mcp.run("stdio")
    finally:
        transport.shutdown()
        print(summary(transport), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Command-line entry point: `docs2mcp` (or `uvx docs2mcp`)."""

import argparse
import sys
from collections.abc import Sequence

from docs2mcp import __version__
from docs2mcp.spec import json_schema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docs2mcp",
        description="Turn API documentation into a curated, task-shaped MCP server.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    commands.add_parser("schema", help="print the JSON Schema of the API spec")
    # ingest, extract, design, generate and eval arrive with their milestones.
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    match args.command:
        case "schema":
            sys.stdout.write(json_schema())
        case _:
            parser.print_help()
    return 0

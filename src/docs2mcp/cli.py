"""Command-line entry point: `docs2mcp` (or `uvx docs2mcp`)."""

import argparse
from collections.abc import Sequence

from docs2mcp import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docs2mcp",
        description="Turn API documentation into a curated, task-shaped MCP server.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # Subcommands (ingest, extract, design, generate, eval) arrive with their milestones.
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0

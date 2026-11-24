"""Command-line entry point: `docs2mcp` (or `uvx docs2mcp`)."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from docs2mcp import __version__
from docs2mcp.config import get_settings
from docs2mcp.ingest import ingest
from docs2mcp.spec import json_schema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docs2mcp",
        description="Turn API documentation into a curated, task-shaped MCP server.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    commands.add_parser("schema", help="print the JSON Schema of the API spec")

    ingest_cmd = commands.add_parser(
        "ingest",
        help="fetch documentation pages and split them into Markdown chunks",
        description=(
            "Crawl from each URL, following links on the same site at or below that URL's "
            "path. Pass several URLs when the docs live in sibling folders."
        ),
    )
    ingest_cmd.add_argument(
        "urls", nargs="+", metavar="URL", help="documentation page to start from"
    )
    ingest_cmd.add_argument(
        "--out", type=Path, required=True, help="directory for pages.jsonl and chunks.jsonl"
    )
    ingest_cmd.add_argument(
        "--max-pages", type=int, default=None, help="pages to crawl (default 20)"
    )
    ingest_cmd.add_argument(
        "--refresh", action="store_true", help="refetch pages instead of using the cache"
    )
    # extract, design, generate and eval arrive with their milestones.
    return parser


def _run_ingest(args: argparse.Namespace) -> int:
    result = ingest(
        args.urls,
        args.out,
        get_settings(),
        max_pages=args.max_pages,
        refresh=args.refresh,
    )
    tokens = sum(chunk.tokens for chunk in result.chunks)
    print(f"{len(result.pages)} pages -> {len(result.chunks)} chunks (~{tokens:,} tokens)")
    print(f"Wrote {args.out / 'pages.jsonl'} and {args.out / 'chunks.jsonl'}")
    if result.skipped:
        print(f"Skipped {len(result.skipped)} URLs:")
        for skip in result.skipped[:20]:
            print(f"  {skip.url}  ({skip.reason})")
    return 0 if result.pages else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    match args.command:
        case "schema":
            sys.stdout.write(json_schema())
        case "ingest":
            return _run_ingest(args)
        case _:
            parser.print_help()
    return 0

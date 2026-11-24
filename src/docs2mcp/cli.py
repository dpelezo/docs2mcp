"""Command-line entry point: `docs2mcp` (or `uvx docs2mcp`)."""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from docs2mcp import __version__
from docs2mcp.config import Settings, get_settings
from docs2mcp.extract import (
    AnthropicModel,
    ExtractionModel,
    ExtractionReport,
    Extractor,
    RecordingModel,
    ReplayModel,
)
from docs2mcp.ingest import ingest, read_chunks, read_pages
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

    extract_cmd = commands.add_parser(
        "extract",
        help="extract an API spec from ingested chunks with Claude",
        description="Read chunks.jsonl from an ingest folder and write a validated spec.json.",
    )
    extract_cmd.add_argument("ingest_dir", type=Path, help="folder written by docs2mcp ingest")
    extract_cmd.add_argument("--out", type=Path, required=True, help="spec.json to write")
    extract_cmd.add_argument("--name", help="API name (default: as found in the docs)")
    extract_cmd.add_argument("--base-url", help="API base URL (default: as found in the docs)")
    extract_cmd.add_argument("--model", help="Claude model (default: DOCS2MCP_MODEL)")
    recording = extract_cmd.add_mutually_exclusive_group()
    recording.add_argument("--record", type=Path, help="save every model reply in this folder")
    recording.add_argument(
        "--replay", type=Path, help="use replies saved with --record; no API calls"
    )
    # design, generate and eval arrive with their milestones.
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


def build_model(args: argparse.Namespace, settings: Settings) -> ExtractionModel:
    model_name = args.model or settings.model
    if args.replay:
        return ReplayModel(args.replay, model_name)
    import anthropic  # noqa: PLC0415 - only needed for live calls

    client = anthropic.Anthropic(api_key=settings.require_anthropic_key())
    model: ExtractionModel = AnthropicModel(
        client, model_name, max_tokens=settings.extract_max_output_tokens
    )
    return RecordingModel(model, args.record) if args.record else model


def report_json(report: ExtractionReport) -> str:
    data = {
        "model": report.model,
        "calls": report.calls,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "rejected": report.rejected,
        "repairs": report.repairs,
        "merged": report.merged,
        "batches": [asdict(b) for b in report.batches],
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _run_extract(args: argparse.Namespace) -> int:
    settings = get_settings()
    chunks = read_chunks(args.ingest_dir)
    pages = read_pages(args.ingest_dir)
    extractor = Extractor(
        build_model(args, settings),
        batch_tokens=settings.extract_batch_tokens,
        max_retries=settings.extract_max_retries,
    )
    spec, report = extractor.run(
        chunks,
        name=args.name,
        base_url=args.base_url,
        docs_url=pages[0].url if pages else None,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(spec.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
    report_path = args.out.with_name("extraction_report.json")
    report_path.write_text(report_json(report), encoding="utf-8")

    print(f"{spec.name}: {len(spec.endpoints)} endpoints at {spec.base_url}")
    print(
        f"{report.calls} model calls, {report.input_tokens:,} input and "
        f"{report.output_tokens:,} output tokens ({report.model})"
    )
    print(
        f"{len(report.merged)} duplicates merged, {len(report.repairs)} automatic repairs, "
        f"{len(report.rejected)} endpoints rejected"
    )
    for batch in report.failed_batches:
        print(f"  batch {batch.chunk_ids[0]}..{batch.chunk_ids[-1]} failed: {batch.error}")
    print(f"Wrote {args.out} and {report_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    match args.command:
        case "schema":
            sys.stdout.write(json_schema())
        case "ingest":
            return _run_ingest(args)
        case "extract":
            return _run_extract(args)
        case _:
            parser.print_help()
    return 0

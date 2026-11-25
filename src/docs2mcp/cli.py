"""Command-line entry point: `docs2mcp` (or `uvx docs2mcp`)."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from docs2mcp import __version__
from docs2mcp.config import Settings, get_settings
from docs2mcp.design import (
    AnthropicDesignModel,
    Designer,
    DesignModel,
    DesignReport,
    RecordingDesignModel,
    ReplayDesignModel,
    ToolSet,
    UnknownToolError,
    direct_design,
    set_approval,
    validate_toolset,
)
from docs2mcp.design import json_schema as tools_json_schema
from docs2mcp.evals import (
    Reference,
    format_metrics,
    format_score,
    measure_design,
    metrics_json,
    score,
    score_json,
)
from docs2mcp.extract import (
    AnthropicModel,
    ExtractionModel,
    ExtractionReport,
    Extractor,
    RecordingModel,
    ReplayModel,
)
from docs2mcp.ingest import ingest, read_chunks, read_pages
from docs2mcp.spec import ApiSpec, json_schema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docs2mcp",
        description="Turn API documentation into a curated, task-shaped MCP server.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    schema_cmd = commands.add_parser("schema", help="print the JSON Schema of the API spec")
    schema_cmd.add_argument(
        "--tools", action="store_true", help="print the schema of tools.json instead"
    )

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
        "--depth",
        type=int,
        default=None,
        help="links to follow from a start URL (0: only the URLs given; default: no limit)",
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
    score_cmd = commands.add_parser(
        "score",
        help="score an extracted spec against a reference",
        description=(
            "Compare spec.json with a reference built from the API's OpenAPI file or the docs. "
            "Rules: docs/adr/0005-extraction-scoring.md."
        ),
    )
    score_cmd.add_argument("spec", type=Path, help="spec.json written by docs2mcp extract")
    score_cmd.add_argument(
        "--reference",
        type=Path,
        required=True,
        help="reference JSON, e.g. evals/extraction/reference/pokeapi.json",
    )
    score_cmd.add_argument("--out", type=Path, help="also write the full score as JSON here")
    design_cmd = commands.add_parser(
        "design",
        help="design the MCP tools for an API spec",
        description=(
            "Write tools.json from spec.json for a person to review. Approve tools with "
            "docs2mcp approve; generate builds only approved, valid tools. "
            "Rules: docs/adr/0011-tool-design-and-generation.md."
        ),
    )
    design_cmd.add_argument("spec", type=Path, help="spec.json written by docs2mcp extract")
    design_cmd.add_argument("--out", type=Path, required=True, help="where to write tools.json")
    design_cmd.add_argument(
        "--direct",
        action="store_true",
        help="one tool per endpoint, with no LLM (default: Claude groups them into tasks)",
    )
    design_cmd.add_argument("--model", help="Claude model (default: DOCS2MCP_DESIGN_MODEL)")
    design_recording = design_cmd.add_mutually_exclusive_group()
    design_recording.add_argument(
        "--record", type=Path, help="save every model reply in this folder"
    )
    design_recording.add_argument(
        "--replay", type=Path, help="use replies saved with --record; no API calls"
    )

    check_cmd = commands.add_parser(
        "check-tools",
        help="check tools.json against its spec",
        description="Report every problem with a tools.json, for example after editing it by hand.",
    )
    check_cmd.add_argument("tools", type=Path, help="tools.json to check")
    check_cmd.add_argument(
        "--spec", type=Path, required=True, help="the spec.json it was designed from"
    )
    approve_cmd = commands.add_parser(
        "approve",
        help="approve or revoke tools in tools.json after review",
        description="generate builds only approved tools. Repeat names or comma-separate them.",
    )
    approve_cmd.add_argument("tools", type=Path, help="tools.json to change in place")
    approve_cmd.add_argument("--all", action="store_true", help="every tool")
    approve_cmd.add_argument(
        "--tool", action="append", default=[], help="a tool to change; repeat or comma-separate"
    )
    approve_cmd.add_argument(
        "--except",
        dest="except_",
        action="append",
        default=[],
        help="with --all: tools to leave unchanged",
    )
    approve_cmd.add_argument("--revoke", action="store_true", help="set approved to false")
    approve_cmd.add_argument("--note", help="the reviewer's note, stored on each changed tool")

    measure_cmd = commands.add_parser(
        "measure",
        help="measure a tools.json for the design eval",
        description=(
            "Count tools, routes, covered and excluded endpoints, and the estimated tokens an "
            "agent loads to see the tools. Rules: evals/design/README.md."
        ),
    )
    measure_cmd.add_argument("tools", type=Path, help="tools.json to measure")
    measure_cmd.add_argument(
        "--spec", type=Path, required=True, help="the spec.json it was designed from"
    )
    measure_cmd.add_argument("--out", type=Path, help="also write the metrics as JSON here")
    # generate and the tool-use eval arrive with their milestones.
    return parser


def _run_ingest(args: argparse.Namespace) -> int:
    result = ingest(
        args.urls,
        args.out,
        get_settings(),
        max_pages=args.max_pages,
        max_depth=args.depth,
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
        client,
        model_name,
        max_tokens=settings.extract_max_output_tokens,
        cache_prompt=settings.extract_prompt_cache,
    )
    return RecordingModel(model, args.record) if args.record else model


def report_json(report: ExtractionReport) -> str:
    data = {
        "model": report.model,
        "calls": report.calls,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "cache_read_tokens": report.cache_read_tokens,
        "cache_write_tokens": report.cache_write_tokens,
        "rejected": report.rejected,
        "repairs": report.repairs,
        "merged": report.merged,
        "expanded": report.expanded,
        "out_of_scope": report.out_of_scope,
        "shared_parameters": report.shared_parameters,
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
    if report.cache_read_tokens or report.cache_write_tokens:
        print(
            f"  prompt cache: {report.cache_read_tokens:,} input tokens read, "
            f"{report.cache_write_tokens:,} written"
        )
    print(
        f"{len(report.merged)} duplicates merged, {len(report.repairs)} automatic repairs, "
        f"{len(report.rejected)} endpoints rejected"
    )
    for note in report.expanded:
        print(f"  resource template {note}")
    for note in report.shared_parameters:
        print(f"  shared parameters {note}")
    if report.out_of_scope:
        print(f"{len(report.out_of_scope)} endpoints dropped as served from another base URL:")
        for note in report.out_of_scope:
            print(f"  {note}")
    for batch in report.failed_batches:
        print(f"  batch {batch.chunk_ids[0]}..{batch.chunk_ids[-1]} failed: {batch.error}")
    print(f"Wrote {args.out} and {report_path}")
    return 0


def _run_score(args: argparse.Namespace) -> int:
    spec = ApiSpec.model_validate_json(args.spec.read_text(encoding="utf-8"))
    reference = Reference.model_validate_json(args.reference.read_text(encoding="utf-8"))
    result = score(spec, reference)
    print(format_score(result))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(score_json(result), indent=2, ensure_ascii=False) + "\n"
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    return 0


def _summary(toolset: ToolSet, spec: ApiSpec) -> str:
    return (
        f"{toolset.api}: {len(toolset.tools)} tools for {len(spec.endpoints)} endpoints "
        f"({toolset.design} design, {len(toolset.excluded)} excluded)"
    )


def build_design_model(args: argparse.Namespace, settings: Settings) -> DesignModel:
    model_name = args.model or settings.design_model
    if args.replay:
        return ReplayDesignModel(args.replay, model_name)
    import anthropic  # noqa: PLC0415 - only needed for live calls

    client = anthropic.Anthropic(api_key=settings.require_anthropic_key())
    model: DesignModel = AnthropicDesignModel(
        client, model_name, max_tokens=settings.design_max_output_tokens
    )
    return RecordingDesignModel(model, args.record) if args.record else model


def design_report_json(report: DesignReport) -> str:
    data = {
        "model": report.model,
        "calls": report.calls,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "cache_read_tokens": report.cache_read_tokens,
        "cache_write_tokens": report.cache_write_tokens,
        "notes": report.notes,
        "repairs": asdict(report.repairs),
        "attempts": [asdict(a) for a in report.attempts],
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _run_design(args: argparse.Namespace) -> int:
    spec = ApiSpec.model_validate_json(args.spec.read_text(encoding="utf-8"))
    report: DesignReport | None = None
    if args.direct:
        toolset = direct_design(spec)
    else:
        settings = get_settings()
        designer = Designer(
            build_design_model(args, settings), max_retries=settings.design_max_retries
        )
        toolset, report = designer.run(spec)
    errors = validate_toolset(toolset, spec)
    if errors:  # both designs guarantee a valid tool set, so this would be a bug
        print("\n".join(["The design is invalid:", *errors]), file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        toolset.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8"
    )
    print(_summary(toolset, spec))
    if report is not None:
        report_path = args.out.with_name("design_report.json")
        report_path.write_text(design_report_json(report), encoding="utf-8")
        print(
            f"{report.calls} model calls, {report.input_tokens:,} input and "
            f"{report.output_tokens:,} output tokens ({report.model})"
        )
        repairs = report.repairs
        if repairs.added_arguments or repairs.dropped_tools or repairs.added_direct:
            print(
                f"Problems remained after {report.calls} calls: added "
                f"{len(repairs.added_arguments)} arguments, dropped {len(repairs.dropped_tools)} "
                f"tools and added {len(repairs.added_direct)} direct tools. See {report_path}."
            )
    print(f"Wrote {args.out}. Review it, approve tools with docs2mcp approve, then generate.")
    return 0


def _approval(toolset: ToolSet) -> str:
    return f"{len(toolset.approved_tools)} of {len(toolset.tools)} tools approved"


def _run_approve(args: argparse.Namespace) -> int:
    try:
        toolset = ToolSet.model_validate_json(args.tools.read_text(encoding="utf-8"))
    except ValidationError as error:
        print(f"{args.tools} is not a valid tools.json:\n{error}", file=sys.stderr)
        return 1
    names = [n.strip() for value in args.tool for n in value.split(",") if n.strip()]
    excepted = [n.strip() for value in args.except_ for n in value.split(",") if n.strip()]
    if args.all == bool(names):
        print("Give either --all or --tool.", file=sys.stderr)
        return 2
    if excepted and not args.all:
        print("--except only works with --all.", file=sys.stderr)
        return 2
    try:
        result = set_approval(
            toolset,
            names=names,
            every=args.all,
            except_=excepted,
            approve=not args.revoke,
            note=args.note,
        )
    except (UnknownToolError, ValidationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    args.tools.write_text(
        result.toolset.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8"
    )
    verb = "Revoked" if args.revoke else "Approved"
    print(f"{verb} {len(result.changed)} tools; {_approval(result.toolset)}.")
    return 0


def _run_check_tools(args: argparse.Namespace) -> int:
    spec = ApiSpec.model_validate_json(args.spec.read_text(encoding="utf-8"))
    try:
        toolset = ToolSet.model_validate_json(args.tools.read_text(encoding="utf-8"))
    except ValidationError as error:
        print(f"{args.tools} is not a valid tools.json:\n{error}", file=sys.stderr)
        return 1
    errors = validate_toolset(toolset, spec)
    if errors:
        print("\n".join([f"{len(errors)} problems in {args.tools}:", *(f"- {e}" for e in errors)]))
        return 1
    print(f"{_summary(toolset, spec)}: valid, {_approval(toolset)}")
    return 0


def _run_measure(args: argparse.Namespace) -> int:
    spec = ApiSpec.model_validate_json(args.spec.read_text(encoding="utf-8"))
    try:
        toolset = ToolSet.model_validate_json(args.tools.read_text(encoding="utf-8"))
    except ValidationError as error:
        print(f"{args.tools} is not a valid tools.json:\n{error}", file=sys.stderr)
        return 1
    metrics = measure_design(toolset, spec)
    print(format_metrics(metrics))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(metrics_json(metrics), indent=2, ensure_ascii=False) + "\n"
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    return 0


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "ingest": _run_ingest,
    "extract": _run_extract,
    "score": _run_score,
    "design": _run_design,
    "check-tools": _run_check_tools,
    "approve": _run_approve,
    "measure": _run_measure,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "schema":
        sys.stdout.write(tools_json_schema() if args.tools else json_schema())
        return 0
    run = _COMMANDS.get(args.command)
    if run is None:
        parser.print_help()
        return 0
    return run(args)

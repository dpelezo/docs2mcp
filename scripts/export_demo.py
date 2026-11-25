"""Export the data the demo playground shows (#72), as one JSON file.

    uv run python -m scripts.export_demo                 # writes web/data/demo.json

- **Tools:** for PokéAPI, Frankfurter and Postcodes.io, the tool list an agent sees
  in each condition (name, description, input schema, estimated tokens). A, B and
  C come from servers generated from the committed inputs, and D from FastMCP on
  the committed OpenAPI file, all listed over MCP, as the eval's agent saw them.
- **Live servers:** the generated B and C servers for the same APIs, copied to
  web/api/_servers/ for the playground's live calls (#74), with a manifest of
  the tools whose every route is a GET.
- **Results:** every task of a committed run, with each condition's passes, runs,
  grounding, tool calls and tokens, plus the headline on the shared APIs.

Open Library and Open-Meteo appear only as results: their docs are AGPL, so their
designs are not committed. No network and no model is used.
"""

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import tomllib
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx2
from fastmcp import Client as FastMCPClient
from mcp import Client

from docs2mcp import __version__
from docs2mcp.design import ToolSet, direct_design, set_approval
from docs2mcp.evals.agent import CONDITIONS, TaskRun, load_runs
from docs2mcp.evals.tasks import load_tasks
from docs2mcp.generate import generate
from docs2mcp.ingest.chunk import estimate_tokens
from docs2mcp.spec import ApiSpec
from scripts.eval_server import load_server
from scripts.fastmcp_server import build_server

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS_DIR = ROOT / "evals" / "agent" / "conditions"
RESULTS = ROOT / "evals" / "agent" / "results" / "2026-11-25-haiku"
OUT = ROOT / "web" / "data" / "demo.json"
LIVE = ROOT / "web" / "api" / "_servers"
SHARED = ("pokeapi", "frankfurter", "postcodes_io")


def _listed(tools: Sequence[Any]) -> list[dict[str, Any]]:
    listed: list[dict[str, Any]] = []
    for tool in tools:
        schema: dict[str, Any] = getattr(tool, "input_schema", None) or tool.inputSchema
        entry = {"name": tool.name, "description": tool.description or "", "input_schema": schema}
        tokens = estimate_tokens(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
        listed.append({**entry, "tokens": tokens})
    return listed


def _generated_tools(toolset: ToolSet, spec: ApiSpec) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as folder:
        generate(set_approval(toolset, every=True).toolset, spec, Path(folder))
        module = load_server(Path(folder))

        async def listing() -> list[Any]:
            async with Client(vars(module)["mcp"]) as client:
                return list((await client.list_tools()).tools)

        return _listed(asyncio.run(listing()))


def _fastmcp_tools(api: str) -> list[dict[str, Any]]:
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(503))
    server, _ = build_server(CONDITIONS_DIR / "openapi" / api, transport)

    async def listing() -> list[Any]:
        async with FastMCPClient(server) as client:
            return list(await client.list_tools())

    return _listed(asyncio.run(listing()))


def tools_for(api: str) -> dict[str, list[dict[str, Any]]]:
    """The tools of every condition for one of the shared APIs."""
    openapi = CONDITIONS_DIR / "openapi" / api
    docs = CONDITIONS_DIR / "docs2mcp" / api
    a_spec = ApiSpec.model_validate_json((openapi / "spec.json").read_text("utf-8"))
    a_tools = ToolSet.model_validate_json((openapi / "tools.json").read_text("utf-8"))
    spec = ApiSpec.model_validate_json((docs / "spec.json").read_text("utf-8"))
    task = ToolSet.model_validate_json((docs / "tools.json").read_text("utf-8"))
    return {
        "A": _generated_tools(a_tools, a_spec),
        "B": _generated_tools(direct_design(spec), spec),
        "C": _generated_tools(task, spec),
        "D": _fastmcp_tools(api),
    }


def live_servers(out: Path) -> dict[str, dict[str, list[str]]]:
    """Write the B and C servers of the shared APIs, and which of their tools are GET-only."""
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, list[str]]] = {}
    for api in SHARED:
        docs = CONDITIONS_DIR / "docs2mcp" / api
        spec = ApiSpec.model_validate_json((docs / "spec.json").read_text("utf-8"))
        task = ToolSet.model_validate_json((docs / "tools.json").read_text("utf-8"))
        for condition, toolset in (("B", direct_design(spec)), ("C", task)):
            with tempfile.TemporaryDirectory() as folder:
                generate(set_approval(toolset, every=True).toolset, spec, Path(folder))
                code = (Path(folder) / "server.py").read_text("utf-8")
                routes: dict[str, list[dict[str, Any]]] = vars(load_server(Path(folder)))["ROUTES"]
            (out / f"{api}_{condition.lower()}.py").write_text(code, "utf-8")
            get_only = [t for t, rs in routes.items() if all(r["method"] == "GET" for r in rs)]
            manifest.setdefault(api, {})[condition] = sorted(get_only)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", "utf-8")
    return manifest


def _summary(runs: Sequence[TaskRun], *, answers: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "runs": len(runs),
        "passed": sum(r.verdict.passed for r in runs),
        "grounded": sum(r.grounded_pass for r in runs),
        "tool_calls": round(statistics.fmean(r.tool_calls for r in runs), 2),
        "tokens": round(statistics.fmean(r.input_tokens + r.output_tokens for r in runs)),
    }
    if answers:
        summary["answers"] = sorted({r.answer or "" for r in runs})
    return summary


def results(runs: Sequence[TaskRun]) -> dict[str, Any]:
    """Headline on the shared APIs, per-API summaries and every task's outcomes."""
    by_task: dict[tuple[str, str], list[TaskRun]] = defaultdict(list)
    by_api: dict[tuple[str, str], list[TaskRun]] = defaultdict(list)
    for run in runs:
        by_task[(run.task, run.condition)].append(run)
        by_api[(run.api, run.condition)].append(run)
        if run.api in SHARED:
            by_api[("shared", run.condition)].append(run)
    tasks: list[dict[str, Any]] = []
    for path in sorted((ROOT / "evals" / "agent" / "tasks").glob("*.toml")):
        loaded = load_tasks(path)
        for task in loaded.task:
            outcomes = {
                c: _summary(by_task[(task.id, c)], answers=True)
                for c in CONDITIONS
                if by_task[(task.id, c)]
            }
            answer: Any = task.answer.value
            tasks.append(
                {
                    "id": task.id,
                    "api": loaded.api,
                    "level": task.level,
                    "question": task.question,
                    "rule": task.answer.rule,
                    "expected": answer,
                    "grounding": task.grounding,
                    "outcomes": outcomes,
                }
            )
    apis = sorted({api for api, _ in by_api})
    return {
        "by_api": {
            api: {c: _summary(by_api[(api, c)]) for c in CONDITIONS if by_api[(api, c)]}
            for api in apis
        },
        "tasks": tasks,
    }


def build(
    runs: Sequence[TaskRun], run_info: dict[str, Any], apis: dict[str, Any]
) -> dict[str, Any]:
    return {
        "generator": f"docs2mcp {__version__}",
        "run": {k: run_info.get(k) for k in ("model", "repeats", "condition_repeats", "max_steps")},
        "conditions": CONDITIONS,
        "shared_apis": list(SHARED),
        "apis": apis,
        "results": results(runs),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the demo playground's data.")
    parser.add_argument("--results", type=Path, default=RESULTS, help="a committed run folder")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--live", type=Path, default=LIVE, help="folder for the live servers")
    args = parser.parse_args(argv)
    runs = load_runs(args.results / "results.jsonl")
    if not runs:
        print(f"No runs in {args.results}", file=sys.stderr)
        return 1
    run_info = json.loads((args.results / "run.json").read_text("utf-8"))
    with (ROOT / "tests" / "fixtures" / "docs" / "sources.toml").open("rb") as handle:
        sources = {a["id"]: a for a in tomllib.load(handle)["api"]}
    apis = {
        api: {
            "name": sources[api]["name"],
            "base_url": sources[api]["base_url"],
            "tools": tools_for(api) if api in SHARED else None,
        }
        for api in sorted(sources)
    }
    data = build(runs, run_info, apis)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", "utf-8")
    counts = {api: {c: len(t) for c, t in (v["tools"] or {}).items()} for api, v in apis.items()}
    print(f"Wrote {args.out}: {len(data['results']['tasks'])} tasks; tools {counts}")
    live = live_servers(args.live)
    callable_ = {api: {c: len(t) for c, t in v.items()} for api, v in live.items()}
    print(f"Wrote {args.live}: GET-only tools {callable_}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# LangGraph ships no type stubs and its builder methods are loosely typed.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""Run the agent eval (ADR 0013): a LangGraph agent answers each task through MCP.

    uv run python -m scripts.agent_eval --pilot                  # 10 tasks, every condition
    uv run python -m scripts.agent_eval --repeats 3              # all 100 tasks
    uv run python -m scripts.agent_eval --conditions C --apis frankfurter --tasks frankfurter-001

Build the servers first with `scripts/build_eval_servers.py`. Each condition and
API gets one server process, started through `scripts/eval_server.py` with that
API's cassette, so every condition sees the same recorded responses. The agent is
the same everywhere: one system prompt, the server's tools plus `submit_answer`,
and a step limit. Answers are scored by the task's rule (docs2mcp.evals.tasks).

Results go to `runs/<name>/`: results.jsonl (one line per task run, appended as
runs finish, so an interrupted run resumes), metrics.json, summary.md and
run.json. Each task is one trace when tracing is configured (see docs2mcp.tracing).
Needs ANTHROPIC_API_KEY. This spends money: the pilot on Haiku is about $0.10.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Protocol, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult

from docs2mcp.config import Settings
from docs2mcp.evals.agent import (
    SUBMIT_TOOL,
    SYSTEM_PROMPT,
    Condition,
    TaskRun,
    ToolCall,
    append_run,
    classify,
    format_table,
    load_runs,
    metrics_json,
    summarize,
)
from docs2mcp.evals.tasks import Task, Verdict, grounded, load_tasks, score
from docs2mcp.tracing import content_enabled, set_attributes, setup_tracing, tracer

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "evals" / "agent" / "tasks"
DEFAULT_MODEL = "claude-haiku-5-5"
MAX_STEPS = 12
MAX_TOKENS = 1024

PILOT = (
    "pokeapi-001",
    "pokeapi-007",
    "frankfurter-001",
    "frankfurter-013",
    "postcodes_io-001",
    "postcodes_io-009",
    "open_library-001",
    "open_library-014",
    "open_meteo-004",
    "open_meteo_geocoding-005",
)
"""One single and one harder task per API with an official file, one each for the rest.

pokeapi-007 reads a field past the 20,000-character cut; postcodes_io-009 needs a
parameter the official file leaves out; frankfurter-013 has a trap in its range."""


class ToolSession(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult: ...


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    answer: str | None
    steps: int


@dataclass
class Recorder:
    """What one task run did, gathered while the graph runs."""

    calls: list[ToolCall] = field(default_factory=list[ToolCall])
    results: list[str] = field(default_factory=list[str])
    """Every tool result text the agent saw, for the grounding check."""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def usage(self, message: AIMessage) -> None:
        self.model_calls += 1
        usage = message.usage_metadata
        if usage is None:
            return
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        details = usage.get("input_token_details") or {}
        self.cache_read_tokens += details.get("cache_read", 0) or 0


def _text(result: CallToolResult) -> str:
    return "".join(str(getattr(part, "text", "")) for part in result.content)


def build_graph(
    model: Any, session: ToolSession, recorder: Recorder, *, max_steps: int = MAX_STEPS
) -> Any:
    """model -> tools -> model ... until submit_answer is called or the steps run out."""

    async def call_model(state: State) -> dict[str, Any]:
        with tracer.start_as_current_span("chat model") as span:
            reply = await model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
            message = reply if isinstance(reply, AIMessage) else AIMessage(content=str(reply))
            recorder.usage(message)
            usage = message.usage_metadata or {}
            set_attributes(
                span,
                {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": "anthropic",
                    "gen_ai.usage.input_tokens": usage.get("input_tokens"),
                    "gen_ai.usage.output_tokens": usage.get("output_tokens"),
                },
            )
        return {"messages": [message], "steps": state["steps"] + 1}

    async def call_tools(state: State) -> dict[str, Any]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            return {}
        replies: list[ToolMessage] = []
        answer = state["answer"]
        for call in last.tool_calls:
            call_id = str(call["id"])
            if call["name"] == SUBMIT_TOOL["name"]:
                answer = str(call["args"].get("answer", ""))
                replies.append(ToolMessage("Answer recorded.", tool_call_id=call_id))
                continue
            with tracer.start_as_current_span(f"tool {call['name']}") as span:
                try:
                    result = await session.call_tool(call["name"], dict(call["args"]))
                    is_error, text = bool(result.is_error), _text(result)
                except Exception as exc:  # noqa: BLE001 - a host shows the agent the failure
                    is_error, text = True, f"Tool call failed in the client: {exc}"[:2000]
                outcome = classify(is_error, text)
                set_attributes(span, {"docs2mcp.tool.outcome": outcome.value})
            recorder.calls.append(
                ToolCall(
                    name=call["name"], arguments=call["args"], outcome=outcome, chars=len(text)
                )
            )
            recorder.results.append(text)
            status = "error" if is_error else "success"
            replies.append(ToolMessage(text, tool_call_id=call_id, status=status))
        return {"messages": replies, "answer": answer}

    def after_model(state: State) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    def after_tools(state: State) -> str:
        return END if state["answer"] is not None or state["steps"] >= max_steps else "model"

    graph = StateGraph(State)
    graph.add_node("model", call_model)
    graph.add_node("tools", call_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model, ["tools", END])
    graph.add_conditional_edges("tools", after_tools, ["model", END])
    return graph.compile()


async def run_task(
    model: Any,
    session: ToolSession,
    task: Task,
    *,
    condition: Condition,
    api: str,
    repeat: int,
    model_name: str,
    max_steps: int = MAX_STEPS,
) -> TaskRun:
    """One agent run on one task, scored. Model or tool failures end the run as an error."""
    recorder = Recorder()
    started = time.monotonic()
    answer: str | None = None
    error: str | None = None
    steps = 0
    with tracer.start_as_current_span(f"agent task {task.id}") as span:
        set_attributes(
            span,
            {
                "docs2mcp.condition": condition,
                "docs2mcp.api": api,
                "docs2mcp.task": task.id,
                "docs2mcp.repeat": repeat,
                "gen_ai.request.model": model_name,
            },
        )
        try:
            graph = build_graph(model, session, recorder, max_steps=max_steps)
            final = await graph.ainvoke(
                {"messages": [HumanMessage(task.question)], "answer": None, "steps": 0},
                {"recursion_limit": 2 * max_steps + 5},
            )
            answer, steps = final["answer"], final["steps"]
        except Exception as exc:  # noqa: BLE001 - one failed run must not stop the eval
            error = f"{type(exc).__name__}: {exc}"[:500]
        verdict = (
            score(task.answer, answer)
            if error is None
            else Verdict(passed=False, reason="the run failed")
        )
        is_grounded = (
            grounded(task.answer, answer, recorder.results)
            if task.grounding == "literal" and answer is not None
            else None
        )
        if error is not None:
            stop = "error"
        elif answer is not None:
            stop = "answered"
        else:
            stop = "step_limit" if steps >= max_steps else "no_answer"
        set_attributes(
            span,
            {
                "docs2mcp.passed": verdict.passed,
                "docs2mcp.grounded": is_grounded,
                "docs2mcp.stop": stop,
            },
        )
        if content_enabled() and answer is not None:
            span.set_attribute("docs2mcp.answer", answer)
    return TaskRun(
        condition=condition,
        api=api,
        task=task.id,
        level=task.level,
        repeat=repeat,
        model=model_name,
        answer=answer,
        verdict=verdict,
        grounded=is_grounded,
        stop=stop,
        error=error,
        calls=recorder.calls,
        model_calls=recorder.model_calls,
        input_tokens=recorder.input_tokens,
        output_tokens=recorder.output_tokens,
        cache_read_tokens=recorder.cache_read_tokens,
        seconds=round(time.monotonic() - started, 2),
    )


def tool_definitions(listed: Sequence[Any]) -> list[dict[str, Any]]:
    """MCP tools as Anthropic tool definitions, plus submit_answer."""
    tools = [
        {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
        for t in listed
    ]
    return [*tools, SUBMIT_TOOL]


def make_model(model_name: str, tools: list[dict[str, Any]]) -> Any:
    from langchain_anthropic import ChatAnthropic  # noqa: PLC0415 - needs the API key

    settings = Settings()
    chat: BaseChatModel = ChatAnthropic.model_validate(
        {
            "model": model_name,
            "max_tokens": MAX_TOKENS,
            "max_retries": 4,
            # From the environment or .env, like every other docs2mcp command.
            "api_key": settings.require_anthropic_key(),
        }
    )
    return chat.bind_tools(tools)


OPENAPI = ROOT / "evals" / "agent" / "conditions" / "openapi"


def server_params(servers: Path, cassettes: Path, condition: str, api: str, mode: str) -> Any:
    """How to start a condition's server: a generated one, or FastMCP's for condition D."""
    if condition == "D":
        launcher, target = "scripts.fastmcp_server", OPENAPI / api
    else:
        launcher, target = "scripts.eval_server", servers / condition / api
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            launcher,
            str(target),
            "--cassette",
            str(cassettes / f"{api}.jsonl.gz"),
            "--mode",
            mode,
        ],
        cwd=ROOT,
        env={**os.environ},
    )


def server_built(servers: Path, condition: str, api: str) -> bool:
    if condition == "D":
        return any((OPENAPI / api / name).exists() for name in ("openapi.json", "openapi.yml"))
    return (servers / condition / api / "server.py").exists()


@dataclass
class Plan:
    condition: Condition
    api: str
    tasks: list[Task]


def plan(
    conditions: Sequence[Condition],
    apis: Sequence[str] | None,
    task_ids: Sequence[str] | None,
    servers: Path,
) -> tuple[list[Plan], list[str]]:
    """What to run, and what was skipped because its server was not built."""
    files = sorted(TASKS.glob("*.toml"))
    plans: list[Plan] = []
    skipped: list[str] = []
    for path in files:
        loaded = load_tasks(path)
        if apis and loaded.api not in apis:
            continue
        tasks = [t for t in loaded.task if not task_ids or t.id in task_ids]
        if not tasks:
            continue
        for condition in conditions:
            if not server_built(servers, condition, loaded.api):
                skipped.append(f"{condition}/{loaded.api}")
                continue
            plans.append(Plan(condition, loaded.api, tasks))
    return plans, skipped


async def run_plan(
    item: Plan,
    args: argparse.Namespace,
    results: Path,
    done: set[tuple[str, str, str, int]],
) -> None:
    repeats = args.condition_repeats.get(item.condition, args.repeats)
    pending = [
        (task, repeat)
        for repeat in range(1, repeats + 1)
        for task in item.tasks
        if (item.condition, item.api, task.id, repeat) not in done
    ]
    if not pending:
        return
    params = server_params(args.servers, args.cassettes, item.condition, item.api, args.mode)
    async with Client(params) as client:
        listed = (await client.list_tools()).tools
        model = make_model(args.model, tool_definitions(listed))
        limit = asyncio.Semaphore(args.concurrency)

        async def one(task: Task, repeat: int) -> None:
            async with limit:
                run = await run_task(
                    model,
                    client,
                    task,
                    condition=item.condition,
                    api=item.api,
                    repeat=repeat,
                    model_name=args.model,
                    max_steps=args.max_steps,
                )
            append_run(results, run)
            mark = "pass" if run.verdict.passed else "FAIL"
            if run.verdict.passed and run.grounded is False:
                mark = "pass (not grounded)"
            print(
                f"{mark} {item.condition} {task.id} r{repeat}: {run.answer!r} "
                f"({run.tool_calls} calls, {run.input_tokens + run.output_tokens:,} tokens, "
                f"{run.stop}) {'' if run.verdict.passed else run.verdict.reason}",
                flush=True,
            )

        await asyncio.gather(*(one(task, repeat) for task, repeat in pending))


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return "unknown"


def drop_errored(results: Path) -> int:
    """Move runs that ended in an error out of results.jsonl, so they run again.

    A run ends in an error only when the model call fails (a rate limit, no
    credit, the network); tool failures go back to the agent instead. Those runs
    say nothing about the tools, so they are retried, not scored. The moved lines
    are appended to errored.jsonl next to the results, for the record.
    """
    runs = load_runs(results)
    errored = [r for r in runs if r.stop == "error"]
    if not errored:
        return 0
    with (results.parent / "errored.jsonl").open("a", encoding="utf-8") as handle:
        handle.writelines(r.model_dump_json() + "\n" for r in errored)
    kept = [r for r in runs if r.stop != "error"]
    results.write_text("".join(r.model_dump_json() + "\n" for r in kept), encoding="utf-8")
    return len(errored)


def write_summary(out: Path) -> str:
    runs = load_runs(out / "results.jsonl")
    metrics = summarize(runs)
    (out / "metrics.json").write_text(metrics_json(metrics), encoding="utf-8")
    table = format_table(metrics)
    (out / "summary.md").write_text(table + "\n", encoding="utf-8")
    return table


def _repeats(text: str) -> dict[str, int]:
    """'D=1,A=2' as {'D': 1, 'A': 2}."""
    pairs: dict[str, int] = {}
    for part in filter(None, text.split(",")):
        condition, _, count = part.partition("=")
        if condition not in ("A", "B", "C", "D") or not count.isdigit() or int(count) < 1:
            msg = f"expected CONDITION=COUNT such as D=1, got {part!r}"
            raise argparse.ArgumentTypeError(msg)
        pairs[condition] = int(count)
    return pairs


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the agent eval (ADR 0013).")
    parser.add_argument("--conditions", default="A,B,C,D", help="comma-separated, default A,B,C,D")
    parser.add_argument("--apis", help="comma-separated API ids (default: all)")
    parser.add_argument("--tasks", help="comma-separated task ids (default: all)")
    parser.add_argument("--pilot", action="store_true", help="only the 10 pilot tasks")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--condition-repeats",
        type=_repeats,
        default={},
        metavar="D=1,...",
        help="repeats for some conditions, overriding --repeats (e.g. D=1)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    parser.add_argument("--concurrency", type=int, default=4, help="tasks at once per server")
    parser.add_argument("--servers", type=Path, default=ROOT / "servers" / "eval")
    parser.add_argument("--cassettes", type=Path, default=ROOT / "recordings" / "agent")
    parser.add_argument("--mode", choices=["cache", "replay", "record"], default="cache")
    parser.add_argument("--out", type=Path, help="run folder (default: runs/<UTC timestamp>)")
    parser.add_argument("--summary-only", action="store_true", help="rewrite the summary")
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="run again every run that ended in an error (rate limit, credits, network)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    out: Path = args.out or ROOT / "runs" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if args.summary_only:
        print(write_summary(out))
        return 0
    wanted = args.conditions.split(",")
    conditions: list[Condition] = [c for c in ("A", "B", "C", "D") if c in wanted]
    task_ids = PILOT if args.pilot else (args.tasks.split(",") if args.tasks else None)
    apis = args.apis.split(",") if args.apis else None
    plans, skipped = plan(conditions, apis, task_ids, args.servers)
    for missing in skipped:
        print(f"skipped {missing}: no server built", file=sys.stderr)
    if not plans:
        print("Nothing to run. Build the servers with scripts/build_eval_servers.py.")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    results = out / "results.jsonl"
    if args.retry_errors:
        retried = drop_errored(results)
        print(f"retrying {retried} runs that ended in an error", file=sys.stderr)
    done = {r.key for r in load_runs(results)}
    meta = {
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _git_commit(),
        "model": args.model,
        "repeats": args.repeats,
        "condition_repeats": args.condition_repeats,
        "max_steps": args.max_steps,
        "mode": args.mode,
        "runs": [f"{p.condition}/{p.api}: {len(p.tasks)} tasks" for p in plans],
        "skipped": skipped,
    }
    (out / "run.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    provider = setup_tracing(Settings())
    try:
        for item in plans:
            asyncio.run(run_plan(item, args, results, done))
    finally:
        if provider is not None:
            provider.shutdown()
    print(write_summary(out))
    print(f"Results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

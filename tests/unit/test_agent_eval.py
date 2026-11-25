"""The agent eval runner and its metrics (#50), with a scripted model and no network."""

import argparse
import asyncio
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from mcp import Client

from docs2mcp.design import ToolSet, set_approval
from docs2mcp.evals.agent import (
    SUBMIT_TOOL,
    Outcome,
    TaskRun,
    ToolCall,
    append_run,
    classify,
    format_table,
    load_runs,
    metrics_json,
    summarize,
)
from docs2mcp.evals.cassette import Cassette
from docs2mcp.evals.tasks import Exact, Task, Verdict
from docs2mcp.generate import generate
from docs2mcp.spec import ApiSpec
from scripts import agent_eval, build_eval_servers
from scripts.eval_server import load_server

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
SPEC = ROOT / "tests" / "fixtures" / "specs" / "frankfurter.json"
DESIGN = ROOT / "evals" / "design" / "replay" / "frankfurter" / "tools.json"


class Scripted(BaseChatModel):
    """A chat model that replies from a list, and records how often it was asked."""

    replies: list[AIMessage]
    fail: str | None = None
    asked: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        _messages: list[BaseMessage],
        _stop: Any = None,
        _run_manager: Any = None,
        **_: Any,
    ) -> ChatResult:
        self.asked += 1
        if self.fail is not None:
            raise RuntimeError(self.fail)
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

    def bind_tools(self, _tools: Any, **_: Any) -> Scripted:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self


def _call(name: str, args: dict[str, Any], n: int = 1) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"c{n}", "type": "tool_call"}],
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 10,
            "total_tokens": 110,
            "input_token_details": {"cache_read": 5},
        },
    )


def _submit(answer: str) -> AIMessage:
    return _call("submit_answer", {"answer": answer}, 99)


TASK = Task.model_validate(
    {
        "id": "frankfurter-002",
        "level": "single",
        "question": "What is the name of the currency with the code ISK?",
        "evidence": "GET https://api.frankfurter.dev/v2/currency/ISK",
        "answer": {"rule": "exact", "value": "Icelandic Króna"},
    }
)


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Path:
    spec = ApiSpec.model_validate_json(SPEC.read_text("utf-8"))
    tools = ToolSet.model_validate_json(DESIGN.read_text("utf-8"))
    folder = tmp_path_factory.mktemp("servers") / "C" / "frankfurter"
    generate(set_approval(tools, every=True).toolset, spec, folder)
    return folder


def _api(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/currency/ISK"):
        return httpx.Response(200, json={"iso_code": "ISK", "name": "Icelandic Króna"})
    return httpx.Response(404, json={"message": "not found"})


def _run(server: Path, model: Scripted, max_steps: int = 12) -> TaskRun:
    module = load_server(server)
    vars(module)["transport"] = httpx.MockTransport(_api)

    async def go() -> TaskRun:
        async with Client(vars(module)["mcp"]) as client:
            return await agent_eval.run_task(
                model,
                client,
                TASK,
                condition="C",
                api="frankfurter",
                repeat=1,
                model_name="scripted",
                max_steps=max_steps,
            )

    return asyncio.run(go())


# Classifying tool results


@pytest.mark.parametrize(
    ("is_error", "text", "outcome"),
    [
        (False, '{"ok": 1}', Outcome.OK),
        (True, "Error executing tool x: 1 validation error for xArguments", Outcome.INVALID),
        (True, "Error executing tool x: GET /a returned HTTP 422: {}", Outcome.INVALID),
        (True, "Unknown tool: nope", Outcome.INVALID),
        (True, "Missing required arguments: quote", Outcome.INVALID),
        (True, "Error calling tool 'getRate': HTTP error 422: Unprocessable", Outcome.INVALID),
        (True, "Error calling tool 'getRate': HTTP error 503: Unavailable", Outcome.ERROR),
        (True, "Error calling tool 'x': not recorded: GET https://a", Outcome.NOT_RECORDED),
        (True, "Error executing tool x: GET /a returned HTTP 503: {}", Outcome.ERROR),
        (
            True,
            "Error executing tool x: GET /a failed: not recorded: GET https://a",
            Outcome.NOT_RECORDED,
        ),
    ],
)
def test_tool_results_are_classified_for_the_metrics(
    is_error: bool, text: str, outcome: Outcome
) -> None:
    assert classify(is_error, text) is outcome


# One task run


def test_a_task_that_looks_up_and_submits_the_answer_passes(server: Path) -> None:
    model = Scripted(replies=[_call("currencies", {"currency": "ISK"}), _submit("Icelandic Króna")])
    run = _run(server, model)
    assert (run.stop, run.answer, run.verdict.passed) == ("answered", "Icelandic Króna", True)
    assert run.grounded is True
    assert [(c.name, c.outcome) for c in run.calls] == [("currencies", Outcome.OK)]
    assert (run.model_calls, run.input_tokens, run.output_tokens) == (2, 200, 20)
    assert run.cache_read_tokens == 10
    assert run.tool_calls == 1
    assert run.invalid_calls == 0


def test_bad_arguments_count_as_invalid_calls(server: Path) -> None:
    model = Scripted(
        replies=[
            _call("provider_rates", {}),
            _call("currencies", {"currency": "XXX"}, 2),
            _submit("Icelandic Krona"),
        ]
    )
    run = _run(server, model)
    assert [c.outcome for c in run.calls] == [Outcome.INVALID, Outcome.INVALID]
    assert run.invalid_calls == 2
    assert run.verdict.passed is False


def test_a_reply_without_a_tool_call_ends_with_no_answer(server: Path) -> None:
    run = _run(server, Scripted(replies=[AIMessage(content="I think it is the króna.")]))
    assert (run.stop, run.answer, run.verdict.passed) == ("no_answer", None, False)


def test_the_step_limit_stops_a_looping_agent(server: Path) -> None:
    replies = [_call("currencies", {"currency": "ISK"}, n) for n in range(5)]
    run = _run(server, Scripted(replies=replies), max_steps=2)
    assert (run.stop, run.model_calls, run.tool_calls) == ("step_limit", 2, 2)


def test_a_model_failure_is_recorded_not_raised(server: Path) -> None:
    run = _run(server, Scripted(replies=[], fail="overloaded"))
    assert run.stop == "error"
    assert run.error is not None
    assert "overloaded" in run.error
    assert run.verdict.reason == "the run failed"


def test_tool_definitions_add_submit_answer() -> None:
    class Listed:
        name = "t"
        description = None
        input_schema: dict[str, Any] = {"type": "object"}  # noqa: RUF012

    tools = agent_eval.tool_definitions([Listed()])
    assert tools == [
        {"name": "t", "description": "", "input_schema": {"type": "object"}},
        SUBMIT_TOOL,
    ]


# Metrics


def _task_run(
    condition: str, api: str, task: str, repeat: int, passed: bool, outcomes: list[Outcome]
) -> TaskRun:
    return TaskRun.model_validate(
        {
            "condition": condition,
            "api": api,
            "task": task,
            "level": "single",
            "repeat": repeat,
            "model": "m",
            "answer": "x",
            "verdict": Verdict(passed=passed, reason="r"),
            "stop": "answered",
            "calls": [ToolCall(name="t", arguments={}, outcome=o, chars=1) for o in outcomes],
            "input_tokens": 1000,
            "output_tokens": 100,
        }
    )


def test_grounded_success_drops_passes_that_did_not_come_from_the_tools() -> None:
    literal = _task_run("C", "pokeapi", "pokeapi-007", 1, True, [Outcome.OK])
    from_memory = literal.model_copy(update={"grounded": False, "task": "pokeapi-001"})
    derived = literal.model_copy(update={"grounded": None, "task": "pokeapi-008"})
    assert (literal.grounded_pass, from_memory.grounded_pass, derived.grounded_pass) == (
        True,
        False,
        True,
    )
    (metrics, _) = summarize([literal, from_memory, derived])
    assert (metrics.success_mean, metrics.grounded_mean) == (1.0, 2 / 3)


def test_summaries_report_min_and_mean_success_over_repeats() -> None:
    ok, bad = Outcome.OK, Outcome.INVALID
    runs = [
        _task_run("C", "pokeapi", "pokeapi-001", 1, True, [ok]),
        _task_run("C", "pokeapi", "pokeapi-002", 1, True, [ok, bad]),
        _task_run("C", "pokeapi", "pokeapi-001", 2, True, [ok]),
        _task_run("C", "pokeapi", "pokeapi-002", 2, False, [Outcome.NOT_RECORDED]),
        _task_run("A", "pokeapi", "pokeapi-001", 1, False, []),
        _task_run("A", "frankfurter", "frankfurter-001", 1, True, [ok]),
    ]
    metrics = {(m.condition, m.api): m for m in summarize(runs)}
    c = metrics[("C", "pokeapi")]
    assert (c.tasks, c.repeats, c.success_min, c.success_mean) == (2, 2, 0.5, 0.75)
    assert c.tool_calls == 5 / 4
    assert c.invalid_rate == 1 / 5
    assert c.not_recorded == 1
    assert c.tokens == 1100
    assert metrics[("A", "pokeapi")].invalid_rate == 0.0
    assert metrics[("A", "all")].tasks == 2
    assert [(m.api, m.condition) for m in summarize(runs)] == [
        ("frankfurter", "A"),
        ("pokeapi", "A"),
        ("pokeapi", "C"),
        ("all", "A"),
        ("all", "C"),
    ]
    table = format_table(summarize(runs))
    row = (
        "| pokeapi | C (docs2mcp task-shaped) | 2 x 2 | 50% / 75% | 50% / 75% | 1.2 | 1,100 | 20% |"
    )
    assert row in table
    assert json.loads(metrics_json(summarize(runs)))[0]["api"] == "frankfurter"


def test_runs_round_trip_through_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "results.jsonl"
    assert load_runs(path) == []
    run = _task_run("B", "pokeapi", "pokeapi-001", 1, True, [Outcome.OK])
    append_run(path, run)
    append_run(path, run)
    assert load_runs(path) == [run, run]
    assert run.key == ("B", "pokeapi", "pokeapi-001", 1)


# Building servers and running the CLI


def test_build_eval_servers_builds_each_condition_it_has_inputs_for(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "evals" / "agent").mkdir(parents=True)
    shutil.copytree(
        ROOT / "evals" / "agent" / "conditions" / "openapi" / "frankfurter",
        root / "openapi" / "frankfurter",
    )
    (root / "spec.json").write_text(SPEC.read_text("utf-8"), "utf-8")
    (root / "tools.json").write_text(DESIGN.read_text("utf-8"), "utf-8")
    (root / "evals" / "agent" / "conditions.toml").write_text(
        '[api.frankfurter]\nspec = "spec.json"\ntask_design = "tools.json"\n'
        'openapi = "openapi/frankfurter"\n\n'
        '[api.postcodes_io]\nspec = "missing.json"\ntask_design = "missing.json"\n\n'
        '[api.pokeapi]\nspec = "spec.json"\ntask_design = "nope.json"\n',
        "utf-8",
    )
    out = tmp_path / "servers"
    assert build_eval_servers.main([], root=root, out=out) == 0
    built = sorted(str(p.parent.relative_to(out)) for p in out.glob("*/*/server.py"))
    assert built == ["A/frankfurter", "B/frankfurter", "B/pokeapi", "C/frankfurter"]
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["C/frankfurter"]["tools"] == 4
    assert manifest["B/frankfurter"]["design"] == "direct"
    assert len(manifest["A/frankfurter"]["spec"]) == 64
    assert build_eval_servers.main(["pokeapi"], root=root, out=out) == 0


def test_plan_skips_conditions_whose_server_is_not_built(server: Path) -> None:
    servers = server.parents[1]
    plans, skipped = agent_eval.plan(["A", "C"], ["frankfurter"], ["frankfurter-002"], servers)
    assert [(p.condition, p.api, [t.id for t in p.tasks]) for p in plans] == [
        ("C", "frankfurter", ["frankfurter-002"])
    ]
    assert skipped == ["A/frankfurter"]
    assert agent_eval.plan(["C"], ["frankfurter"], ["nope-001"], servers) == ([], [])


def test_the_cli_runs_a_task_over_stdio_and_writes_the_summary(
    server: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cassettes = tmp_path / "cassettes"
    Cassette(cassettes / "frankfurter.jsonl.gz").add(
        "GET https://api.frankfurter.dev/v2/currency/ISK",
        httpx.Response(200, json={"iso_code": "ISK", "name": "Icelandic Króna"}),
    )
    models: Iterator[Scripted] = iter(
        [Scripted(replies=[_call("currencies", {"currency": "ISK"}), _submit("Icelandic Króna")])]
    )

    def make_model(_name: str, _tools: list[dict[str, Any]]) -> Scripted:
        return next(models)

    monkeypatch.setattr(agent_eval, "make_model", make_model)
    out = tmp_path / "run"
    args = [
        "--conditions", "A,C", "--apis", "frankfurter", "--tasks", "frankfurter-002",
        "--servers", str(server.parents[1]), "--cassettes", str(cassettes),
        "--mode", "replay", "--out", str(out),
    ]  # fmt: skip
    assert agent_eval.main(args) == 0
    printed = capsys.readouterr()
    assert "pass C frankfurter-002 r1: 'Icelandic Króna'" in printed.out
    assert "skipped A/frankfurter: no server built" in printed.err
    (run,) = load_runs(out / "results.jsonl")
    assert run.verdict.passed
    assert json.loads((out / "run.json").read_text("utf-8"))["mode"] == "replay"
    assert "| frankfurter | C" in (out / "summary.md").read_text("utf-8")

    assert agent_eval.main(args) == 0  # resumes: nothing left to run
    assert len(load_runs(out / "results.jsonl")) == 1
    models = iter([Scripted(replies=[_submit("Icelandic Króna")])])
    assert agent_eval.main([*args, "--condition-repeats", "C=2"]) == 0  # adds repeat 2 only
    assert sorted(r.repeat for r in load_runs(out / "results.jsonl")) == [1, 2]
    assert agent_eval.main([*args, "--summary-only"]) == 0


def test_the_cli_says_when_there_is_nothing_to_run(tmp_path: Path) -> None:
    args = [
        "--servers", str(tmp_path), "--out", str(tmp_path / "run"), "--pilot",
        "--conditions", "A,B,C",
    ]  # fmt: skip
    assert agent_eval.main(args) == 1


def test_exact_answers_are_scored_by_the_task_rule() -> None:
    assert isinstance(TASK.answer, Exact)


# Pilot 2 fixes (#64)


class _Raising:
    """An MCP session whose client rejects the result, as with FastMCP's Postcodes.io schemas."""

    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        del arguments
        self.calls += 1
        message = (
            f"Invalid structured content returned by tool {name}: None is not of type 'integer'"
        )
        raise RuntimeError(message)


def test_a_client_side_tool_failure_goes_back_to_the_agent() -> None:
    model = Scripted(replies=[_call("LookupPostcode", {"postcode": "SW1A 1AA"}), _submit("x")])
    session = _Raising()
    run = asyncio.run(
        agent_eval.run_task(
            model, session, TASK, condition="D", api="postcodes_io", repeat=1, model_name="m"
        )
    )
    assert (run.stop, run.error, session.calls, model.asked) == ("answered", None, 1, 2)
    assert [c.outcome for c in run.calls] == [Outcome.ERROR]


def test_condition_repeats_parse_and_reject_bad_values() -> None:
    assert agent_eval._repeats("D=1,A=2") == {"D": 1, "A": 2}  # pyright: ignore[reportPrivateUsage]
    assert agent_eval._repeats("") == {}  # pyright: ignore[reportPrivateUsage]
    for bad in ("E=1", "D=0", "D", "D=x"):
        with pytest.raises(argparse.ArgumentTypeError):
            agent_eval._repeats(bad)  # pyright: ignore[reportPrivateUsage]


def test_errored_runs_are_moved_aside_and_run_again(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    good = _task_run("C", "pokeapi", "pokeapi-001", 1, True, [Outcome.OK])
    failed = good.model_copy(update={"task": "pokeapi-002", "stop": "error", "error": "429"})
    append_run(results, good)
    append_run(results, failed)
    assert agent_eval.drop_errored(results) == 1
    assert load_runs(results) == [good]
    assert load_runs(tmp_path / "errored.jsonl") == [failed]
    assert agent_eval.drop_errored(results) == 0

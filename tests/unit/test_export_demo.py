"""The demo playground's data export (#72), offline."""

import json
from pathlib import Path

import pytest

from docs2mcp.evals.agent import Outcome, TaskRun, ToolCall
from docs2mcp.evals.tasks import Verdict
from scripts import export_demo

pytestmark = pytest.mark.unit


def _run(condition: str, task: str, repeat: int, passed: bool, answer: str) -> TaskRun:
    return TaskRun.model_validate(
        {
            "condition": condition,
            "api": task.rsplit("-", 1)[0],
            "task": task,
            "level": "single",
            "repeat": repeat,
            "model": "m",
            "answer": answer,
            "verdict": Verdict(passed=passed, reason="r"),
            "stop": "answered",
            "calls": [ToolCall(name="t", arguments={}, outcome=Outcome.OK, chars=1)],
            "input_tokens": 900,
            "output_tokens": 100,
        }
    )


def test_tools_for_lists_every_condition_as_an_agent_sees_it() -> None:
    tools = export_demo.tools_for("frankfurter")
    assert {c: len(t) for c, t in tools.items()} == {"A": 9, "B": 8, "C": 4, "D": 9}
    first = tools["C"][0]
    assert set(first) == {"name", "description", "input_schema", "tokens"}
    assert first["input_schema"]["type"] == "object"
    assert first["tokens"] > 0


def test_results_summarize_each_task_and_the_shared_headline() -> None:
    runs = [
        _run("C", "frankfurter-001", 1, True, "1.0945"),
        _run("C", "frankfurter-001", 2, False, "1.09"),
        _run("A", "frankfurter-001", 1, True, "1.0945"),
        _run("B", "open_library-001", 1, True, "Roald Dahl"),
    ]
    out = export_demo.results(runs)
    assert out["by_api"]["shared"]["C"] == {
        "runs": 2, "passed": 1, "grounded": 1, "tool_calls": 1.0, "tokens": 1000,
    }  # fmt: skip
    assert "B" not in out["by_api"]["shared"]
    assert out["by_api"]["open_library"]["B"]["passed"] == 1
    task = next(t for t in out["tasks"] if t["id"] == "frankfurter-001")
    assert task["expected"] == 1.0945
    assert task["outcomes"]["C"]["answers"] == ["1.09", "1.0945"]
    assert set(task["outcomes"]) == {"A", "C"}
    assert len(out["tasks"]) == 100


def test_main_writes_the_bundle(tmp_path: Path) -> None:
    results = tmp_path / "run"
    results.mkdir()
    (results / "results.jsonl").write_text(
        _run("C", "frankfurter-001", 1, True, "1.0945").model_dump_json() + "\n", "utf-8"
    )
    (results / "run.json").write_text(json.dumps({"model": "m", "repeats": 1}), "utf-8")
    out = tmp_path / "demo.json"
    assert export_demo.main(["--results", str(results), "--out", str(out)]) == 0
    data = json.loads(out.read_text("utf-8"))
    assert data["run"]["model"] == "m"
    assert data["apis"]["open_library"]["tools"] is None
    assert len(data["apis"]["pokeapi"]["tools"]["C"]) == 52
    assert export_demo.main(["--results", str(tmp_path / "empty"), "--out", str(out)]) == 1


def test_the_committed_bundle_is_up_to_date(tmp_path: Path) -> None:
    out = tmp_path / "demo.json"
    assert export_demo.main(["--out", str(out)]) == 0
    committed = export_demo.OUT.read_text("utf-8")
    assert out.read_text("utf-8") == committed, "run: uv run python -m scripts.export_demo"


def test_committed_live_servers_are_up_to_date(tmp_path: Path) -> None:
    manifest = export_demo.live_servers(tmp_path)
    for path in sorted(tmp_path.iterdir()):
        committed = export_demo.LIVE / path.name
        assert committed.read_text("utf-8") == path.read_text("utf-8"), (
            f"{committed} is stale: run `uv run python -m scripts.export_demo`"
        )
    assert {p.name for p in export_demo.LIVE.iterdir()} >= {p.name for p in tmp_path.iterdir()}
    assert manifest["frankfurter"]["C"]

"""Agent eval results and metrics (ADR 0013), with no model or LangGraph code.

The runner (`scripts/agent_eval.py`) writes one `TaskRun` per task, condition
and repeat. This module classifies tool errors, and turns runs into the reported
metrics: success rate and grounded success rate (minimum and mean over repeats),
tool calls, tokens and invalid tool calls per task. Grounded success counts a
pass only when a literal answer appears in a tool result the agent saw (#57).
"""

import json
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from docs2mcp.evals.tasks import Verdict

Condition = Literal["A", "B", "C", "D"]
CONDITIONS: dict[Condition, str] = {
    "A": "OpenAPI direct",
    "B": "docs2mcp direct",
    "C": "docs2mcp task-shaped",
    "D": "FastMCP from OpenAPI",
}

SYSTEM_PROMPT = """\
You answer one question using the tools of a single web API.
Look the answer up with the tools; do not answer from memory.
When you have it, call submit_answer exactly once with only the final answer: \
a bare number, name, code, date, yes or no, or a comma-separated list, as the \
question asks. Leave out units, working and explanations.
If the tools cannot give the answer, submit your best answer anyway."""

SUBMIT_TOOL = {
    "name": "submit_answer",
    "description": "Submit the final answer to the question. Call this once, at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Only the answer, e.g. '1.0945', 'Westminster' or 'grass, poison'.",
            }
        },
        "required": ["answer"],
    },
}


class Outcome(StrEnum):
    OK = "ok"
    INVALID = "invalid"
    """Rejected because of the arguments: schema validation, or an HTTP 4xx from the API."""
    NOT_RECORDED = "not_recorded"
    """Replay mode met a request that was never recorded: a problem with the run, not the tools."""
    ERROR = "error"
    """Any other failure, such as an HTTP 5xx or a network error."""


def classify(is_error: bool, text: str) -> Outcome:
    """What a tool result means for the metrics."""
    if not is_error:
        return Outcome.OK
    lowered = text.lower()
    if "not recorded:" in lowered:
        return Outcome.NOT_RECORDED
    invalid = (
        "validation error",  # the MCP SDK rejected the arguments
        "missing required argument",  # FastMCP's own check (condition D)
        "returned http 4",  # an API 4xx, from a generated server
        "http error 4",  # an API 4xx, from FastMCP
        "unknown tool",
    )
    if any(marker in lowered for marker in invalid):
        return Outcome.INVALID
    return Outcome.ERROR


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCall(_Model):
    name: str
    arguments: dict[str, object]
    outcome: Outcome
    chars: int = Field(ge=0, description="Length of the tool result text.")


StopReason = Literal["answered", "no_answer", "step_limit", "error"]


class TaskRun(_Model):
    condition: Condition
    api: str
    task: str
    level: str
    repeat: int = Field(ge=1)
    model: str
    answer: str | None
    verdict: Verdict
    grounded: bool | None = None
    """Whether the answer appears in a tool result the agent saw; None for derived answers."""
    stop: StopReason
    error: str | None = None
    calls: list[ToolCall] = Field(default_factory=list[ToolCall])
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    seconds: float = 0.0

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.condition, self.api, self.task, self.repeat)

    @property
    def grounded_pass(self) -> bool:
        """Passed, and not shown to have come from somewhere other than the tools."""
        return self.verdict.passed and self.grounded is not False

    @property
    def tool_calls(self) -> int:
        return len(self.calls)

    @property
    def invalid_calls(self) -> int:
        return sum(1 for c in self.calls if c.outcome is Outcome.INVALID)


def load_runs(path: Path) -> list[TaskRun]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [TaskRun.model_validate_json(line) for line in lines if line.strip()]


def append_run(path: Path, run: TaskRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(run.model_dump_json() + "\n")


class Metrics(_Model):
    condition: Condition
    api: str
    tasks: int
    repeats: int
    success_min: float
    success_mean: float
    grounded_min: float
    grounded_mean: float
    tool_calls: float
    tokens: float
    input_tokens: float
    output_tokens: float
    invalid_rate: float
    not_recorded: int
    errors: int


def _metrics(condition: Condition, api: str, runs: Sequence[TaskRun]) -> Metrics:
    by_repeat: dict[int, list[TaskRun]] = defaultdict(list)
    for run in runs:
        by_repeat[run.repeat].append(run)
    rates = [sum(r.verdict.passed for r in rs) / len(rs) for rs in by_repeat.values()]
    grounded = [sum(r.grounded_pass for r in rs) / len(rs) for rs in by_repeat.values()]
    calls = sum(r.tool_calls for r in runs)
    return Metrics(
        condition=condition,
        api=api,
        tasks=len({r.task for r in runs}),
        repeats=len(by_repeat),
        success_min=min(rates),
        success_mean=statistics.fmean(rates),
        grounded_min=min(grounded),
        grounded_mean=statistics.fmean(grounded),
        tool_calls=calls / len(runs),
        tokens=sum(r.input_tokens + r.output_tokens for r in runs) / len(runs),
        input_tokens=sum(r.input_tokens for r in runs) / len(runs),
        output_tokens=sum(r.output_tokens for r in runs) / len(runs),
        invalid_rate=sum(r.invalid_calls for r in runs) / calls if calls else 0.0,
        not_recorded=sum(1 for r in runs for c in r.calls if c.outcome is Outcome.NOT_RECORDED),
        errors=sum(1 for r in runs if r.stop == "error"),
    )


def summarize(runs: Iterable[TaskRun]) -> list[Metrics]:
    """Metrics per condition and API, then per condition over all APIs ("all")."""
    groups: dict[tuple[Condition, str], list[TaskRun]] = defaultdict(list)
    for run in runs:
        groups[(run.condition, run.api)].append(run)
        groups[(run.condition, "all")].append(run)
    order = {c: i for i, c in enumerate(CONDITIONS)}
    keys = sorted(groups, key=lambda k: (k[1] == "all", k[1], order[k[0]]))
    return [_metrics(condition, api, groups[(condition, api)]) for condition, api in keys]


def format_table(metrics: Sequence[Metrics]) -> str:
    """A Markdown table of the metrics, one row per condition and API."""
    header = (
        "| API | Condition | Tasks x repeats | Success (min / mean) | Grounded success "
        "| Tool calls | Tokens | Invalid calls |\n|---|---|---|---|---|---|---|---|"
    )
    rows = [
        f"| {m.api} | {m.condition} ({CONDITIONS[m.condition]}) | {m.tasks} x {m.repeats} "
        f"| {m.success_min:.0%} / {m.success_mean:.0%} "
        f"| {m.grounded_min:.0%} / {m.grounded_mean:.0%} | {m.tool_calls:.1f} "
        f"| {m.tokens:,.0f} | {m.invalid_rate:.0%} |"
        for m in metrics
    ]
    return "\n".join([header, *rows])


def metrics_json(metrics: Sequence[Metrics]) -> str:
    return json.dumps([m.model_dump() for m in metrics], indent=2) + "\n"


# The report (#51)


def _row(label: str, runs: Sequence[TaskRun], price: tuple[float, float]) -> str:
    by_repeat: dict[int, list[TaskRun]] = defaultdict(list)
    for run in runs:
        by_repeat[run.repeat].append(run)
    rates = [sum(r.verdict.passed for r in rs) / len(rs) for rs in by_repeat.values()]
    grounded = statistics.fmean(
        sum(r.grounded_pass for r in rs) / len(rs) for rs in by_repeat.values()
    )
    tokens = statistics.fmean(r.input_tokens + r.output_tokens for r in runs)
    calls = sum(r.tool_calls for r in runs)
    invalid = sum(r.invalid_calls for r in runs) / calls if calls else 0.0
    cost = statistics.fmean(
        r.input_tokens * price[0] / 1e6 + r.output_tokens * price[1] / 1e6 for r in runs
    )
    return (
        f"| {label} | {len(runs)} | {min(rates):.1%} / {statistics.fmean(rates):.1%} "
        f"| {grounded:.1%} | {calls / len(runs):.2f} | {tokens:,.0f} | ${cost:.4f} "
        f"| {invalid:.0%} |"
    )


_HEADER = (
    "| {first} | Runs | Success (min / mean) | Grounded success | Tool calls | Tokens "
    "| Cost per task | Invalid calls |\n|---|---|---|---|---|---|---|---|"
)


def report(runs: Sequence[TaskRun], shared: Sequence[str], price: tuple[float, float]) -> str:
    """The results as Markdown: the headline on the APIs every condition covers, the
    task-shaped design against the direct one on every API, results by level, and
    every task that failed or was not grounded. price is (input, output) per million."""
    shared_runs = [r for r in runs if r.api in shared]
    conditions: list[Condition] = [c for c in CONDITIONS if any(r.condition == c for r in runs)]
    out = [
        f"## Every condition, on {', '.join(shared)}\n",
        _HEADER.format(first="Condition"),
        *(
            _row(f"{c} ({CONDITIONS[c]})", [r for r in shared_runs if r.condition == c], price)
            for c in conditions
            if any(r.condition == c for r in shared_runs)
        ),
        "\n## By level, on the same APIs\n",
        "| Level | " + " | ".join(conditions) + " |\n|---|" + "---|" * len(conditions),
    ]
    for level in ("single", "multi", "complex"):
        cells: list[str] = []
        for c in conditions:
            rows = [r for r in shared_runs if r.condition == c and r.level == level]
            cells.append(f"{sum(r.verdict.passed for r in rows) / len(rows):.0%}" if rows else "")
        out.append(f"| {level} | " + " | ".join(cells) + " |")
    apis = sorted({r.api for r in runs})
    out += ["\n## Direct (B) and task-shaped (C), on every API\n", _HEADER.format(first="API")]
    for api in [*apis, "all"]:
        for c in ("B", "C"):
            rows = [r for r in runs if r.condition == c and api in ("all", r.api)]
            if rows:
                out.append(_row(f"{api} {c}", rows, price))
    out += ["\n## Tasks that failed or were not grounded\n", "| Task | Runs |\n|---|---|"]
    misses: dict[str, list[str]] = defaultdict(list)
    for r in sorted(runs, key=lambda r: (r.task, r.condition, r.repeat)):
        if not r.grounded_pass:
            if r.verdict.passed:
                tag = "not grounded"
            else:
                tag = "wrong" if r.stop == "answered" else r.stop.replace("_", " ")
            answer = (r.answer or "").replace("|", "/")[:40]
            misses[r.task].append(f"{r.condition}{r.repeat} {tag}: `{answer}`")
    out += [f"| {task} | {'; '.join(items)} |" for task, items in sorted(misses.items())]
    return "\n".join(out) + "\n"

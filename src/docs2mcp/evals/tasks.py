"""Agent eval tasks and their deterministic scoring (ADR 0013).

Tasks live in TOML, one file per API (`evals/agent/tasks/<api>.toml`):

    api = "frankfurter"

    [[task]]
    id = "frankfurter-001"
    level = "single"
    question = "What was the EUR to USD exchange rate on 2024-01-15?"
    evidence = "GET https://api.frankfurter.dev/v2/rate/EUR/USD?date=2024-01-15"
    answer = { rule = "number", value = 1.0945, abs_tolerance = 0.0001 }

Every answer is checked by a rule, never by a model, so a score can be re-derived
from a transcript.
"""

import json
import math
import re
import tomllib
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Level = Literal["single", "multi", "complex"]

_NUMBER = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?")
_SPLIT = re.compile(r"[,;\n]")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Exact(_Model):
    rule: Literal["exact"]
    value: str = Field(min_length=1)
    accept: list[str] = Field(default_factory=list[str], description="Other answers that count.")
    case_sensitive: bool = False


class Number(_Model):
    rule: Literal["number"]
    value: float
    abs_tolerance: float = Field(default=0.0, ge=0)
    rel_tolerance: float = Field(default=0.0, ge=0)


class Contains(_Model):
    rule: Literal["contains"]
    value: list[str] = Field(min_length=1, description="Every string must appear.")
    case_sensitive: bool = False


class SetOf(_Model):
    rule: Literal["set"]
    value: list[str] = Field(min_length=1, description="Exactly these items, in any order.")
    case_sensitive: bool = False


Answer = Annotated[Exact | Number | Contains | SetOf, Field(discriminator="rule")]


class Task(_Model):
    id: str = Field(pattern=r"^[a-z0-9_]+-\d{3}$")
    level: Level
    question: str = Field(min_length=10)
    evidence: str = Field(min_length=1, description="How the expected answer was checked.")
    answer: Answer
    note: str | None = None


class TaskFile(_Model):
    api: str = Field(pattern=r"^[a-z0-9_]+$")
    task: list[Task] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids = [t.id for t in self.task]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            msg = f"task ids repeat: {duplicates}"
            raise ValueError(msg)
        wrong = [i for i in ids if not i.startswith(f"{self.api}-")]
        if wrong:
            msg = f"task ids must start with '{self.api}-': {wrong}"
            raise ValueError(msg)
        return self


def load_tasks(path: Path) -> TaskFile:
    with path.open("rb") as handle:
        return TaskFile.model_validate(tomllib.load(handle))


def level_counts(tasks: TaskFile) -> dict[str, int]:
    counts: dict[str, int] = {"single": 0, "multi": 0, "complex": 0}
    for task in tasks.task:
        counts[task.level] += 1
    return counts


def _normalize(text: str, *, case_sensitive: bool) -> str:
    text = " ".join(text.split())
    return text if case_sensitive else text.casefold()


def first_number(text: str) -> float | None:
    match = _NUMBER.search(text)
    if match is None:
        return None
    value = float(match.group().replace(",", ""))
    return value if math.isfinite(value) else None


class Verdict(_Model):
    passed: bool
    reason: str


def _exact(answer: Exact, given: str) -> Verdict:
    norm = _normalize(given, case_sensitive=answer.case_sensitive)
    allowed = {
        _normalize(a, case_sensitive=answer.case_sensitive) for a in [answer.value, *answer.accept]
    }
    ok = norm in allowed
    return Verdict(passed=ok, reason="matches" if ok else f"expected {answer.value!r}")


def _number(answer: Number, given: str) -> Verdict:
    found = first_number(given)
    if found is None:
        return Verdict(passed=False, reason="no number in the answer")
    limit = answer.abs_tolerance + answer.rel_tolerance * abs(answer.value)
    if abs(found - answer.value) <= limit:
        return Verdict(passed=True, reason="within tolerance")
    return Verdict(passed=False, reason=f"{found} is not within {limit} of {answer.value}")


def _contains(answer: Contains, given: str) -> Verdict:
    norm = _normalize(given, case_sensitive=answer.case_sensitive)
    wanted = [(v, _normalize(v, case_sensitive=answer.case_sensitive)) for v in answer.value]
    missing = [v for v, n in wanted if n not in norm]
    return Verdict(passed=not missing, reason=f"missing {missing}" if missing else "contains all")


def _set(answer: SetOf, given: str) -> Verdict:
    sensitive = answer.case_sensitive
    got = {_normalize(p, case_sensitive=sensitive) for p in _SPLIT.split(given) if p.strip()}
    want = {_normalize(v, case_sensitive=sensitive) for v in answer.value}
    if got == want:
        return Verdict(passed=True, reason="same set")
    return Verdict(
        passed=False, reason=f"missing {sorted(want - got)}, unexpected {sorted(got - want)}"
    )


def score(answer: Exact | Number | Contains | SetOf, given: str | None) -> Verdict:
    """Check one submitted answer against a task's rule."""
    if given is None or not given.strip():
        return Verdict(passed=False, reason="no answer submitted")
    match answer:
        case Exact():
            return _exact(answer, given)
        case Number():
            return _number(answer, given)
        case Contains():
            return _contains(answer, given)
        case SetOf():
            return _set(answer, given)


def json_schema() -> str:
    """The JSON Schema for a task file, as stable, pretty-printed JSON."""
    return json.dumps(TaskFile.model_json_schema(), indent=2, ensure_ascii=False) + "\n"

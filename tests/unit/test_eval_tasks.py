"""The agent eval task format and its deterministic scorer (ADR 0013)."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from docs2mcp.cli import main
from docs2mcp.evals.tasks import (
    Answer,
    Contains,
    Exact,
    Number,
    SetOf,
    first_number,
    grounded,
    json_schema,
    level_counts,
    load_tasks,
    score,
)

pytestmark = pytest.mark.unit

_ANSWER: TypeAdapter[Exact | Number | Contains | SetOf] = TypeAdapter(Answer)

_FILE = """\
api = "frankfurter"

[[task]]
id = "frankfurter-001"
level = "single"
question = "What was the EUR to USD rate on 2024-01-15?"
evidence = "GET /v2/rate/EUR/USD?date=2024-01-15"
answer = { rule = "number", value = 1.0945, abs_tolerance = 0.0001 }

[[task]]
id = "frankfurter-002"
level = "multi"
question = "Which of GBP and JPY rose against EUR in January 2024?"
evidence = "GET /v2/rates?base=EUR&from=2024-01-01&to=2024-01-31"
answer = { rule = "set", value = ["GBP"] }
note = "JPY fell."
"""


def _write(tmp_path: Path, text: str, name: str = "frankfurter.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# Loading


def test_load_tasks_reads_a_task_file(tmp_path: Path) -> None:
    tasks = load_tasks(_write(tmp_path, _FILE))
    assert tasks.api == "frankfurter"
    assert [t.id for t in tasks.task] == ["frankfurter-001", "frankfurter-002"]
    assert isinstance(tasks.task[0].answer, Number)
    assert tasks.task[1].note == "JPY fell."
    assert level_counts(tasks) == {"single": 1, "multi": 1, "complex": 0}


def test_repeated_ids_are_rejected(tmp_path: Path) -> None:
    text = _FILE.replace("frankfurter-002", "frankfurter-001")
    with pytest.raises(ValidationError, match="repeat"):
        load_tasks(_write(tmp_path, text))


def test_ids_must_carry_the_api_prefix(tmp_path: Path) -> None:
    text = _FILE.replace("frankfurter-002", "pokeapi-002")
    with pytest.raises(ValidationError, match="must start with 'frankfurter-'"):
        load_tasks(_write(tmp_path, text))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('level = "single"', 'level = "easy"'),
        ('rule = "number"', 'rule = "judge"'),
        ("abs_tolerance = 0.0001", "abs_tolerance = -1"),
        ('evidence = "GET /v2/rate/EUR/USD?date=2024-01-15"', 'evidence = ""'),
        ('note = "JPY fell."', 'notes = "JPY fell."'),
    ],
)
def test_invalid_tasks_are_rejected(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ValidationError):
        load_tasks(_write(tmp_path, _FILE.replace(old, new)))


def test_set_and_contains_need_at_least_one_value() -> None:
    with pytest.raises(ValidationError):
        _ANSWER.validate_python({"rule": "set", "value": []})
    with pytest.raises(ValidationError):
        _ANSWER.validate_python({"rule": "contains", "value": []})


# Scoring


def test_no_answer_fails_every_rule() -> None:
    rule = Exact(rule="exact", value="Pikachu")
    assert score(rule, None).reason == "no answer submitted"
    assert not score(rule, "   ").passed


@pytest.mark.parametrize(
    ("given", "passed"),
    [
        ("Pikachu", True),
        ("  pikachu ", True),
        ("PIKACHU", True),
        ("Raichu", False),
        ("Pikachu.", False),
    ],
)
def test_exact_normalizes_case_and_space(given: str, passed: bool) -> None:
    assert score(Exact(rule="exact", value="Pikachu"), given).passed is passed


def test_exact_accepts_alternatives_and_can_be_case_sensitive() -> None:
    rule = Exact(rule="exact", value="Westminster", accept=["City of Westminster"])
    assert score(rule, "city of   westminster").passed
    strict = Exact(rule="exact", value="SW1A", case_sensitive=True)
    assert score(strict, "SW1A").passed
    assert not score(strict, "sw1a").passed


@pytest.mark.parametrize(
    ("given", "passed"),
    [
        ("1.0945", True),
        ("The rate was 1.0946 USD per EUR.", True),
        ("1.0947", False),
        ("about one dollar", False),
    ],
)
def test_number_uses_the_first_number_and_absolute_tolerance(given: str, passed: bool) -> None:
    rule = Number(rule="number", value=1.0945, abs_tolerance=0.0001)
    assert score(rule, given).passed is passed


def test_number_relative_tolerance_and_thousands() -> None:
    rule = Number(rule="number", value=8_982_000, rel_tolerance=0.01)
    assert score(rule, "London has 8,950,000 people").passed
    assert not score(rule, "8,800,000").passed
    assert score(Number(rule="number", value=-3.5), "-3.5 °C").passed
    assert "not within" in score(Number(rule="number", value=2), "3").reason


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("35", 35.0),
        ("height 0.4 m", 0.4),
        ("1,234.5 and 7", 1234.5),
        ("-12 degrees", -12.0),
        ("6.02e23 atoms", 6.02e23),
        ("none here", None),
        ("1e999", None),
    ],
)
def test_first_number(text: str, number: float | None) -> None:
    assert first_number(text) == number


def test_contains_needs_every_string() -> None:
    rule = Contains(rule="contains", value=["static", "lightning rod"])
    assert score(rule, "Abilities: Static and Lightning   Rod").passed
    verdict = score(rule, "Static only")
    assert not verdict.passed
    assert "lightning rod" in verdict.reason


def test_set_ignores_order_and_separators_but_not_extras() -> None:
    rule = SetOf(rule="set", value=["Grass", "Poison"])
    assert score(rule, "poison, grass").passed
    assert score(rule, "Grass\nPoison;").passed
    verdict = score(rule, "Grass, Poison, Fire")
    assert not verdict.passed
    assert "unexpected ['fire']" in verdict.reason
    assert "missing ['poison']" in score(rule, "Grass").reason


# Schema and CLI


def test_json_schema_matches_the_committed_file() -> None:
    committed = Path("docs/tasks.schema.json").read_text(encoding="utf-8")
    assert json_schema() == committed
    assert json.loads(committed)["title"] == "TaskFile"


def test_check_tasks_reports_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path, _FILE)
    assert main(["check-tasks", str(path)]) == 0
    out = capsys.readouterr().out
    assert "frankfurter, 2 tasks (1 single, 1 multi, 0 complex)" in out
    assert "2 tasks in 1 valid files" in out


def test_check_tasks_fails_on_an_invalid_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = _write(tmp_path, _FILE)
    bad = _write(tmp_path, "api = 'x'\n[[task]\n", name="bad.toml")
    assert main(["check-tasks", str(good), str(bad)]) == 1
    assert "bad.toml: invalid" in capsys.readouterr().err


def test_schema_tasks_prints_the_task_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema", "--tasks"]) == 0
    assert '"TaskFile"' in capsys.readouterr().out


# Grounding (#57)


def test_tasks_default_to_literal_grounding(tmp_path: Path) -> None:
    tasks = load_tasks(_write(tmp_path, _FILE))
    assert {t.grounding for t in tasks.task} == {"literal"}
    derived = _FILE.replace('note = "JPY fell."', 'grounding = "derived"')
    assert load_tasks(_write(tmp_path, derived)).task[1].grounding == "derived"
    with pytest.raises(ValidationError):
        load_tasks(_write(tmp_path, _FILE.replace('note = "JPY fell."', 'grounding = "guess"')))


def test_exact_answers_are_grounded_when_a_result_contains_them() -> None:
    rule = Exact(rule="exact", value="Icelandic Króna")
    results = ['{"iso_code": "ISK", "name": "Icelandic Kr\\u00f3na"}']
    assert grounded(rule, "icelandic  KRÓNA", results)
    assert not grounded(rule, "Icelandic Krona", results)
    assert not grounded(rule, "Icelandic Króna", [])
    assert grounded(rule, "plain text", ["not json, but plain   text"])


def test_numbers_are_grounded_within_the_task_tolerance() -> None:
    results = ['{"rate": 1.0945, "date": "2024-01-15"}', '{"population": 3426354}']
    assert grounded(Number(rule="number", value=1.0945, abs_tolerance=0.0001), "1.0946", results)
    assert not grounded(Number(rule="number", value=1.0945), "1.0946", results)
    assert grounded(Number(rule="number", value=3426354), "3,426,354 people", results)
    assert not grounded(Number(rule="number", value=130), "130", results)
    assert not grounded(Number(rule="number", value=1), "about one", results)


def test_contains_and_set_answers_need_every_item_in_the_results() -> None:
    results = ['{"abilities": ["static", "lightning-rod"]}', '{"types": ["grass", "poison"]}']
    assert grounded(Contains(rule="contains", value=["static", "lightning-rod"]), "x", results)
    assert not grounded(Contains(rule="contains", value=["static", "levitate"]), "x", results)
    rule = SetOf(rule="set", value=["grass", "poison"])
    assert grounded(rule, "Grass, Poison", results)
    assert not grounded(rule, "grass, fire", results)
    assert not grounded(rule, " , ", results)


def test_every_derived_task_is_marked_and_literal_answers_are_not_derived() -> None:
    derived = {
        t.id
        for path in Path("evals/agent/tasks").glob("*.toml")
        for t in load_tasks(path).task
        if t.grounding == "derived"
    }
    assert len(derived) == 17
    assert {"pokeapi-008", "frankfurter-013", "postcodes_io-014", "open_meteo-004"} <= derived
    assert "pokeapi-007" not in derived

"""Per-tool approval and the approve command (ADR 0011, amended)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from docs2mcp.cli import main
from docs2mcp.design import ToolSet, UnknownToolError, direct_design, set_approval
from docs2mcp.spec import ApiSpec

SPECS = Path(__file__).parents[2] / "tests" / "fixtures" / "specs"


@pytest.fixture
def frankfurter() -> ToolSet:
    spec = ApiSpec.model_validate_json((SPECS / "frankfurter.json").read_text(encoding="utf-8"))
    return direct_design(spec)


def approved(toolset: ToolSet) -> list[str]:
    return [t.name for t in toolset.approved_tools]


def test_named_tools_are_approved_with_a_note(frankfurter: ToolSet) -> None:
    result = set_approval(frankfurter, names=["get_rates", "get_rate"], note="checked")

    assert approved(result.toolset) == ["get_rates", "get_rate"]
    assert result.changed == ["get_rates", "get_rate"]
    assert {t.note for t in result.toolset.approved_tools} == {"checked"}
    assert approved(frankfurter) == []  # the input is not changed


def test_every_tool_but_some(frankfurter: ToolSet) -> None:
    result = set_approval(frankfurter, every=True, except_=["get_rates_csv"])
    assert "get_rates_csv" not in approved(result.toolset)
    assert len(approved(result.toolset)) == len(frankfurter.tools) - 1


def test_revoking_and_repeating_report_only_real_changes(frankfurter: ToolSet) -> None:
    first = set_approval(frankfurter, names=["get_rates"]).toolset
    again = set_approval(first, names=["get_rates"])
    assert again.changed == []

    revoked = set_approval(first, names=["get_rates"], approve=False, note="wrong base")
    assert approved(revoked.toolset) == []
    assert revoked.toolset.tools[0].note == "wrong base"


def test_bad_requests_change_nothing(frankfurter: ToolSet) -> None:
    with pytest.raises(UnknownToolError, match=r"no tools named \['nope'\]"):
        set_approval(frankfurter, names=["get_rates", "nope"])
    with pytest.raises(UnknownToolError):
        set_approval(frankfurter, every=True, except_=["nope"])
    with pytest.raises(ValueError, match="name the tools"):
        set_approval(frankfurter)
    with pytest.raises(ValidationError):
        set_approval(frankfurter, names=["get_rates"], note="")


def test_approve_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tools = tmp_path / "tools.json"
    assert main(["design", str(SPECS / "frankfurter.json"), "--direct", "--out", str(tools)]) == 0
    capsys.readouterr()

    assert main(["approve", str(tools), "--all", "--except", "get_rates_csv"]) == 0
    assert "Approved 8 tools; 8 of 9 tools approved." in capsys.readouterr().out
    assert main(["approve", str(tools), "--tool", "get_rate,get_rates", "--revoke"]) == 0
    assert "Revoked 2 tools; 6 of 9 tools approved." in capsys.readouterr().out
    data = json.loads(tools.read_text())
    assert [t["approved"] for t in data["tools"]].count(True) == 6

    assert main(["approve", str(tools)]) == 2
    assert main(["approve", str(tools), "--all", "--tool", "get_rate"]) == 2
    assert main(["approve", str(tools), "--tool", "get_rate", "--except", "x"]) == 2
    assert main(["approve", str(tools), "--tool", "nope"]) == 1
    assert "no tools named ['nope']" in capsys.readouterr().err

    tools.write_text('{"api": "x"}')
    assert main(["approve", str(tools), "--all"]) == 1
    assert "is not a valid tools.json" in capsys.readouterr().err

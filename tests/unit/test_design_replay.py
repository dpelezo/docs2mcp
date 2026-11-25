"""Replay recorded tool designs on the committed specs and check the result.

Each folder in evals/design/replay/<id>/ holds Claude's recorded design replies
for tests/fixtures/specs/<id>.json and the tools.json they produced. This test
replays them offline and requires the same tools.json, so a change to
conversion, validation or repair that moves a design shows up in the pull
request without a live call.

A change to the design prompt or the wire schema changes the recording keys,
so replay fails with "record again": record live with
`docs2mcp design <spec> --record <folder>` and commit the new folder.
"""

import json
from pathlib import Path

import pytest

from docs2mcp.config import Settings
from docs2mcp.design import Designer, ReplayDesignModel, ToolSet, validate_toolset
from docs2mcp.spec import ApiSpec

ROOT = Path(__file__).parents[2]
SPECS = ROOT / "tests" / "fixtures" / "specs"
REPLAY = ROOT / "evals" / "design" / "replay"
FOLDERS = sorted(p for p in REPLAY.glob("*") if p.is_dir())


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda p: p.name)
def test_recorded_design_replays_to_the_committed_tools(folder: Path) -> None:
    spec = ApiSpec.model_validate_json((SPECS / f"{folder.name}.json").read_text(encoding="utf-8"))
    recordings = folder / "recordings"
    model = json.loads(next(recordings.glob("*.json")).read_text(encoding="utf-8"))["model"]
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    designer = Designer(
        ReplayDesignModel(recordings, model), max_retries=settings.design_max_retries
    )

    toolset, report = designer.run(spec)

    expected = ToolSet.model_validate_json((folder / "tools.json").read_text(encoding="utf-8"))
    assert validate_toolset(toolset, spec) == []
    assert report.repairs.added_direct == []
    assert toolset == expected, (
        f"{folder.name}: the replayed design changed. If that is intended, record again and "
        "commit the new recordings and tools.json in this pull request."
    )


def test_every_committed_spec_has_a_replayed_design() -> None:
    assert {p.name for p in FOLDERS} == {p.stem for p in SPECS.glob("*.json")}

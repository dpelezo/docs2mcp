"""Replay recorded extraction on the committed snapshots and check the score.

Each folder in evals/extraction/replay/<id>/ holds Claude's recorded replies
for that API's snapshot chunks, and the score they produced. This test ingests
the snapshots offline, replays the recordings, scores the spec and requires
the same score, so a change to ingest, merging or scoring that moves a result
shows up in the pull request without a live call.

When a change is meant to move the score, record again with
`uv run python scripts/record_replay.py <id>` and commit the new recordings and
score.json in the same pull request.
"""

import json
from pathlib import Path

import httpx
import pytest

from docs2mcp.config import Settings
from docs2mcp.evals import Reference, score, score_json
from docs2mcp.evals.snapshots import ingest_snapshots, load_api, page_filename, snapshot_client
from docs2mcp.extract import Extractor, ReplayModel
from docs2mcp.ingest import read_chunks

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "docs"
SOURCES = FIXTURES / "sources.toml"
REFERENCES = ROOT / "evals" / "extraction" / "reference"
REPLAY = ROOT / "evals" / "extraction" / "replay"
FOLDERS = sorted(p for p in REPLAY.glob("*") if p.is_dir())


def settings() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda p: p.name)
def test_recorded_extraction_replays_to_the_committed_score(folder: Path, tmp_path: Path) -> None:
    api = load_api(SOURCES, folder.name)
    ingest_snapshots(
        folder.name, tmp_path / "ingest", sources=SOURCES, fixtures=FIXTURES, settings=settings()
    )
    recordings = folder / "recordings"
    model = json.loads(next(recordings.glob("*.json")).read_text(encoding="utf-8"))["model"]
    extractor = Extractor(
        ReplayModel(recordings, model),
        batch_tokens=settings().extract_batch_tokens,
        max_retries=settings().extract_max_retries,
    )

    spec, _ = extractor.run(read_chunks(tmp_path / "ingest"), base_url=str(api["base_url"]))

    reference = Reference.model_validate_json((REFERENCES / f"{folder.name}.json").read_text())
    expected = json.loads((folder / "score.json").read_text(encoding="utf-8"))
    assert score_json(score(spec, reference)) == expected, (
        f"{folder.name}: the replayed score changed. If that is intended, record again with "
        f"scripts/record_replay.py {folder.name} and commit the new score in this pull request."
    )


def test_every_api_with_snapshots_can_be_replayed() -> None:
    assert {p.name for p in FOLDERS} == {"pokeapi", "frankfurter", "postcodes_io"}


def test_snapshot_client_serves_listed_pages_only() -> None:
    api = load_api(SOURCES, "frankfurter")
    with snapshot_client(api, FIXTURES) as client:
        page = client.get("https://frankfurter.dev/")
        other = client.get("https://frankfurter.dev/v1")
    assert page.status_code == 200
    assert (
        page.text
        == (FIXTURES / "frankfurter" / page_filename("https://frankfurter.dev/")).read_text()
    )
    assert other.status_code == 404


def test_apis_without_committed_snapshots_cannot_be_replayed() -> None:
    with pytest.raises(ValueError, match="no committed snapshots"):
        snapshot_client(load_api(SOURCES, "open_library"), FIXTURES)


def test_unknown_api_is_an_error() -> None:
    with pytest.raises(KeyError, match="not in"):
        load_api(SOURCES, "rest_countries")


def test_snapshot_ingest_ignores_a_live_page_in_the_shared_cache(tmp_path: Path) -> None:
    """Refreshing from the snapshot means a cached live copy can never leak in."""
    shared = settings().model_copy(update={"cache_dir": tmp_path / "shared-cache"})
    live = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/html"}, text="<main><h1>Live</h1></main>"
            )
        )
    )
    from docs2mcp.ingest import ingest  # noqa: PLC0415 - kept next to its only use

    ingest(["https://frankfurter.dev/"], tmp_path / "live", shared, max_depth=0, client=live)

    result = ingest_snapshots(
        "frankfurter", tmp_path / "snap", sources=SOURCES, fixtures=FIXTURES, settings=shared
    )
    assert "Live" not in "".join(c.markdown for c in result.chunks)
    assert len(result.chunks) > 1

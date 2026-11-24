"""End-to-end ingest over the committed documentation snapshots, with no network."""

import json
from pathlib import Path

import httpx
import pytest

from docs2mcp.config import Settings
from docs2mcp.ingest import IngestResult, ingest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "docs"
SNAPSHOTS = {
    "https://pokeapi.co/docs/v2": FIXTURES / "pokeapi" / "docs_v2.html",
    "https://frankfurter.dev/": FIXTURES / "frankfurter" / "index.html",
}


def serve_snapshots(request: httpx.Request) -> httpx.Response:
    path = SNAPSHOTS.get(str(request.url))
    if path is None:
        return httpx.Response(404, headers={"content-type": "text/html"}, text="not found")
    return httpx.Response(200, headers={"content-type": "text/html"}, text=path.read_text())


def run(seed: str, out: Path, cache: Path) -> IngestResult:
    settings = Settings(_env_file=None, cache_dir=cache, crawl_delay_seconds=0)  # pyright: ignore[reportCallIssue]
    client = httpx.Client(transport=httpx.MockTransport(serve_snapshots))
    return ingest([seed], out, settings, client=client)


@pytest.fixture(scope="module")
def pokeapi(tmp_path_factory: pytest.TempPathFactory) -> tuple[IngestResult, Path]:
    root = tmp_path_factory.mktemp("pokeapi")
    return run("https://pokeapi.co/docs/v2", root / "out", root / "cache"), root


def test_pokeapi_snapshot_becomes_labelled_chunks(pokeapi: tuple[IngestResult, Path]) -> None:
    result, _ = pokeapi
    assert len(result.pages) == 1
    assert len(result.chunks) > 100

    firmness = [c for c in result.chunks if c.anchor == "berry-firmnesses"]
    assert firmness, "the Berry Firmnesses section should be its own chunk"
    assert firmness[0].heading_path[-1].startswith("Berry Firmnesses")
    assert "berry-firmness" in firmness[0].markdown
    assert firmness[0].source_url == "https://pokeapi.co/docs/v2#berry-firmnesses"


def test_chunk_invariants_hold_on_real_docs(pokeapi: tuple[IngestResult, Path]) -> None:
    result, _ = pokeapi
    for chunk in result.chunks:
        assert chunk.url == "https://pokeapi.co/docs/v2"
        assert chunk.heading_path[0] == result.pages[0].title
        assert chunk.markdown.count("```") % 2 == 0, f"{chunk.id} cuts a code block"
        if chunk.tokens > 1500:
            assert chunk.markdown.lstrip().startswith(("```", "####")), (
                f"{chunk.id} is over the limit and is not a single code block"
            )


def test_output_files_are_written_and_identical_on_rerun(
    pokeapi: tuple[IngestResult, Path],
) -> None:
    result, root = pokeapi
    first = [(root / "out" / name).read_bytes() for name in ("pages.jsonl", "chunks.jsonl")]
    lines = first[1].decode().splitlines()
    assert len(lines) == len(result.chunks)
    assert json.loads(lines[0])["id"] == "p01-c001"

    run("https://pokeapi.co/docs/v2", root / "out2", root / "cache")
    second = [(root / "out2" / name).read_bytes() for name in ("pages.jsonl", "chunks.jsonl")]
    assert first == second


def test_frankfurter_snapshot_keeps_each_endpoint_section(tmp_path: Path) -> None:
    result = run("https://frankfurter.dev/", tmp_path / "out", tmp_path / "cache")
    time_series = next(c for c in result.chunks if "### Time Series" in c.markdown)
    # Small sibling sections share a chunk labelled with their parent, "Rates".
    assert time_series.heading_path[-1] in {"Time Series", "Rates"}
    assert time_series.anchor == "time-series"

    # Every documented section survives, alone or sharing a chunk with a short neighbour.
    text = "\n".join(c.markdown for c in result.chunks)
    for heading in ("## Rates", "## Rate", "## Currencies", "## Currency", "## Conversion"):
        assert f"{heading}\n" in text
    assert any("api.frankfurter.dev/v2/" in c.markdown for c in result.chunks)
    assert {s.reason for s in result.skipped} <= {"HTTP 404", "page limit reached"}

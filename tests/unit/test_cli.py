from datetime import UTC, datetime
from pathlib import Path

import pytest

from docs2mcp import __version__
from docs2mcp.cli import main
from docs2mcp.ingest import Chunk, IngestResult, Page, SkippedUrl


def test_version_flag_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"docs2mcp {__version__}"


def test_no_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: docs2mcp" in capsys.readouterr().out


def test_schema_command_prints_the_spec_json_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema"]) == 0
    assert '"title": "ApiSpec"' in capsys.readouterr().out


def test_ingest_command_prints_a_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    calls: list[tuple[list[str], Path, int | None, int | None, bool]] = []

    def fake_ingest(
        urls: list[str],
        out: Path,
        _settings: object,
        *,
        max_pages: int | None,
        max_depth: int | None,
        refresh: bool,
    ) -> IngestResult:
        calls.append((urls, out, max_pages, max_depth, refresh))
        page = Page(
            url=urls[0], final_url=urls[0], title="Docs", fetched_at=datetime.now(UTC), sha256="x"
        )
        chunk = Chunk(id="p01-c001", url=urls[0], heading_path=["Docs"], markdown="m", tokens=1200)
        skipped = [SkippedUrl(url="https://x.dev/old", reason="HTTP 404")]
        return IngestResult(pages=[page], chunks=[chunk], skipped=skipped)

    monkeypatch.setattr("docs2mcp.cli.ingest", fake_ingest)
    code = main(
        [
            "ingest",
            "https://x.dev/docs",
            "--out",
            str(tmp_path),
            "--max-pages",
            "5",
            "--depth",
            "0",
            "--refresh",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert calls == [(["https://x.dev/docs"], tmp_path, 5, 0, True)]
    assert "1 pages -> 1 chunks (~1,200 tokens)" in out
    assert "https://x.dev/old  (HTTP 404)" in out


def test_ingest_command_fails_when_nothing_was_fetched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def empty(*_args: object, **_kwargs: object) -> IngestResult:
        return IngestResult(pages=[], chunks=[], skipped=[])

    monkeypatch.setattr("docs2mcp.cli.ingest", empty)
    assert main(["ingest", "https://x.dev/docs", "--out", str(tmp_path)]) == 1

"""Live ingest against a real documentation site. Needs network; not run in unit CI.

Run with: just integration
"""

from pathlib import Path

from docs2mcp.config import Settings
from docs2mcp.ingest import ingest


def test_ingest_frankfurter_live(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, cache_dir=tmp_path / "cache")  # pyright: ignore[reportCallIssue]
    result = ingest(["https://frankfurter.dev/"], tmp_path / "out", settings, max_pages=3)
    assert result.pages, result.skipped
    # "Time Series" may share a chunk with a short sibling, labelled with their parent.
    time_series = next(c for c in result.chunks if "### Time Series" in c.markdown)
    assert time_series.heading_path[-1] in {"Time Series", "Rates"}
    assert time_series.anchor == "time-series"

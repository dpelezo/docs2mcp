"""Ingest an eval API's committed docs snapshots without the network.

The snapshots in `tests/fixtures/docs/<id>/` are served through an
`httpx.MockTransport`, so ingest sees exactly the committed HTML every time. This is
what makes recorded extraction replayable in CI: the chunks, and therefore the
recording keys, never depend on the live site or on a local page cache.
"""

import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from docs2mcp.config import Settings
from docs2mcp.ingest import IngestResult, ingest


def page_filename(url: str) -> str:
    """The snapshot file name for a docs URL: its path with `/` replaced by `_`."""
    path = urlparse(url).path.strip("/").replace("/", "_") or "index"
    return f"{path}.html"


def load_api(sources: Path, api_id: str) -> dict[str, Any]:
    apis: list[dict[str, Any]] = tomllib.loads(sources.read_text(encoding="utf-8"))["api"]
    for api in apis:
        if api["id"] == api_id:
            return api
    msg = f"{api_id!r} is not in {sources}"
    raise KeyError(msg)


def snapshot_client(api: dict[str, Any], fixtures: Path) -> httpx.Client:
    """An HTTP client that serves the API's committed snapshots, and 404 for anything else."""
    if not api.get("redistribute"):
        msg = f"{api['id']} has no committed snapshots (its docs licence does not allow it)"
        raise ValueError(msg)
    folder = fixtures / str(api["id"])
    pages = {url: folder / page_filename(url) for url in api["pages"]}

    def serve(request: httpx.Request) -> httpx.Response:
        path = pages.get(str(request.url))
        if path is None or not path.exists():
            return httpx.Response(
                404, headers={"content-type": "text/html"}, text="not in snapshots"
            )
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=path.read_text("utf-8")
        )

    return httpx.Client(transport=httpx.MockTransport(serve))


def ingest_snapshots(
    api_id: str, out_dir: Path, *, sources: Path, fixtures: Path, settings: Settings
) -> IngestResult:
    """Ingest exactly the API's listed pages from its snapshots (`--depth 0`).

    Pages are always taken from the snapshots (`refresh=True`) and cached under
    `out_dir`, never in the shared page cache, so a live page cached earlier on
    the same machine cannot leak in.
    """
    api = load_api(sources, api_id)
    isolated = settings.model_copy(
        update={"cache_dir": out_dir / ".snapshot-cache", "crawl_delay_seconds": 0}
    )
    with snapshot_client(api, fixtures) as client:
        return ingest(
            list(api["pages"]),
            out_dir,
            isolated,
            max_pages=len(api["pages"]),
            max_depth=0,
            refresh=True,
            client=client,
        )

"""Run ingest end to end and write pages.jsonl and chunks.jsonl."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from docs2mcp.config import Settings
from docs2mcp.ingest.cache import PageCache
from docs2mcp.ingest.chunk import chunk_page
from docs2mcp.ingest.convert import to_markdown
from docs2mcp.ingest.crawl import Crawler
from docs2mcp.ingest.models import Chunk, Page, SkippedUrl


@dataclass(frozen=True)
class IngestResult:
    pages: list[Page]
    chunks: list[Chunk]
    skipped: list[SkippedUrl]


def read_chunks(ingest_dir: Path) -> list[Chunk]:
    """Load chunks.jsonl written by `ingest`."""
    path = ingest_dir / "chunks.jsonl"
    if not path.exists():
        msg = f"{path} not found; run docs2mcp ingest first"
        raise FileNotFoundError(msg)
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Chunk.model_validate_json(line) for line in lines if line.strip()]


def read_pages(ingest_dir: Path) -> list[Page]:
    path = ingest_dir / "pages.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Page.model_validate_json(line) for line in lines if line.strip()]


def _write_jsonl(path: Path, records: Sequence[Page | Chunk]) -> None:
    lines = (
        json.dumps(json.loads(r.model_dump_json()), ensure_ascii=False, sort_keys=True)
        for r in records
    )
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def ingest(
    seed_urls: Sequence[str],
    out_dir: Path,
    settings: Settings,
    *,
    max_pages: int | None = None,
    max_depth: int | None = None,
    refresh: bool = False,
    client: httpx.Client | None = None,
) -> IngestResult:
    """Crawl the docs, convert each page to Markdown and chunk it.

    Writes `pages.jsonl` and `chunks.jsonl` to `out_dir`. Pass `client` in tests to
    serve pages without the network.
    """
    owns_client = client is None
    http = client or httpx.Client(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
    )
    try:
        crawler = Crawler(
            client=http,
            cache=PageCache(settings.cache_dir / "pages"),
            max_pages=max_pages or settings.ingest_max_pages,
            max_depth=max_depth,
            refresh=refresh,
            delay_seconds=settings.crawl_delay_seconds,
        )
        crawled = crawler.crawl(seed_urls)
    finally:
        if owns_client:
            http.close()

    pages: list[Page] = []
    chunks: list[Chunk] = []
    for number, fetched in enumerate(crawled.pages, start=1):
        pages.append(
            Page(
                url=fetched.url,
                final_url=fetched.final_url,
                title=fetched.title,
                fetched_at=fetched.fetched_at,
                sha256=fetched.sha256,
            )
        )
        converted = to_markdown(fetched.html, base_url=fetched.final_url)
        chunks.extend(
            chunk_page(
                converted.markdown,
                url=fetched.url,
                title=fetched.title,
                headings=converted.headings,
                id_prefix=f"p{number:02d}",
                max_tokens=settings.chunk_max_tokens,
                min_tokens=settings.chunk_min_tokens,
            )
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "pages.jsonl", pages)
    _write_jsonl(out_dir / "chunks.jsonl", chunks)
    return IngestResult(pages=pages, chunks=chunks, skipped=crawled.skipped)

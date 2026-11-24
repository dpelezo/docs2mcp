from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from docs2mcp.ingest.cache import PageCache
from docs2mcp.ingest.crawl import Crawler, in_scope, normalize_url

HOST = "https://docs.example.com"
HTML = "text/html; charset=utf-8"
FIXED_TIME = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def page(title: str, *links: str) -> str:
    anchors = "".join(f'<a href="{href}">link</a>' for href in links)
    head = f"<head><title>{title}</title></head>"
    return f"<html>{head}<body><main>{title}{anchors}</main></body></html>"


SITE: dict[str, tuple[int, str, str]] = {
    "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /docs/private\n"),
    "/docs": (
        200,
        HTML,
        page(
            "Docs home",
            "/docs/a",
            "/docs/b/",  # trailing slash
            "b/",  # relative to /docs, so it resolves to /b/: out of scope
            "/docs/a#section",  # same page as /docs/a
            "https://other.example.com/x",
            "/blog/post",
            "/docs/logo.png",
            "mailto:team@example.com",
            "/docs/private/secret",
            "/docs/missing",
            "/docs/data",
            "/docs/copy-of-a",
        ),
    ),
    "/docs/a": (200, HTML, page("Page A")),
    "/docs/copy-of-a": (200, HTML, page("Page A")),
    "/docs/b": (200, HTML, page("Page B", "/docs/c?utm=1")),
    "/docs/c": (200, HTML, page("Page C", "/docs")),
    "/docs/data": (200, "application/json", "{}"),
}


class Site:
    """A fake documentation site that records every request."""

    def __init__(self, routes: dict[str, tuple[int, str, str]]) -> None:
        self.routes = routes
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        status, content_type, body = self.routes.get(request.url.path, (404, HTML, "missing"))
        return httpx.Response(status, headers={"content-type": content_type}, text=body)


def make_crawler(
    site: Callable[[httpx.Request], httpx.Response], cache_dir: Path, **options: object
) -> Crawler:
    client = httpx.Client(transport=httpx.MockTransport(site), base_url=HOST)
    defaults: dict[str, object] = {"delay_seconds": 0, "now": lambda: FIXED_TIME}
    return Crawler(client=client, cache=PageCache(cache_dir), **(defaults | options))  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("href", "base", "expected"),
    [
        ("https://Docs.Example.com/api/", None, "https://docs.example.com/api"),
        ("/api/v1?x=1#top", "https://docs.example.com/guide", "https://docs.example.com/api/v1"),
        ("users", "https://docs.example.com/api/", "https://docs.example.com/api/users"),
        ("https://docs.example.com/", None, "https://docs.example.com/"),
        ("/static/app.js", "https://docs.example.com", None),
        ("/guide.PDF", "https://docs.example.com", None),
        ("mailto:a@b.c", "https://docs.example.com", None),
        ("javascript:void(0)", "https://docs.example.com", None),
    ],
)
def test_normalize_url(href: str, base: str | None, expected: str | None) -> None:
    assert normalize_url(href, base) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://docs.example.com/docs", True),
        ("https://docs.example.com/docs/a/b", True),
        ("https://docs.example.com/docs-old", False),
        ("https://docs.example.com/blog", False),
        ("https://other.example.com/docs", False),
    ],
)
def test_in_scope(url: str, expected: bool) -> None:
    assert in_scope(url, ["https://docs.example.com/docs"]) is expected


def test_crawl_follows_in_scope_links_in_page_order(tmp_path: Path) -> None:
    result = make_crawler(Site(SITE), tmp_path).crawl([f"{HOST}/docs"])

    assert [p.url for p in result.pages] == [
        f"{HOST}/docs",
        f"{HOST}/docs/a",
        f"{HOST}/docs/b",
        f"{HOST}/docs/c",
    ]
    assert [p.title for p in result.pages] == ["Docs home", "Page A", "Page B", "Page C"]
    assert {s.url: s.reason for s in result.skipped} == {
        f"{HOST}/docs/private/secret": "disallowed by robots.txt",
        f"{HOST}/docs/missing": "HTTP 404",
        f"{HOST}/docs/data": "not HTML (application/json)",
        f"{HOST}/docs/copy-of-a": "same content as an earlier page",
    }


def test_second_run_reads_the_cache_and_refresh_refetches(tmp_path: Path) -> None:
    make_crawler(Site(SITE), tmp_path).crawl([f"{HOST}/docs"])

    offline = Site(SITE)
    cached = make_crawler(offline, tmp_path).crawl([f"{HOST}/docs"])
    assert len(cached.pages) == 4
    assert all(p.fetched_at == FIXED_TIME for p in cached.pages)
    assert [path for path in offline.requests if path != "/robots.txt"] == [
        "/docs/missing",  # failures are not cached
        "/docs/data",
    ]

    online = Site(SITE)
    make_crawler(online, tmp_path, refresh=True).crawl([f"{HOST}/docs"])
    assert "/docs/a" in online.requests


def test_page_limit_stops_the_crawl_and_reports_what_was_left(tmp_path: Path) -> None:
    result = make_crawler(Site(SITE), tmp_path, max_pages=2).crawl([f"{HOST}/docs"])
    assert len(result.pages) == 2
    assert f"{HOST}/docs/b" in {s.url for s in result.skipped if s.reason == "page limit reached"}


def test_waits_between_live_fetches_but_not_before_the_first(tmp_path: Path) -> None:
    pauses: list[float] = []
    make_crawler(Site(SITE), tmp_path, delay_seconds=0.5, sleep=pauses.append).crawl(
        [f"{HOST}/docs"]
    )
    assert pauses
    assert set(pauses) == {0.5}
    assert len(pauses) == len(SITE) - 1  # every page except robots.txt and the first fetch


def test_missing_robots_txt_allows_everything(tmp_path: Path) -> None:
    routes = {k: v for k, v in SITE.items() if k != "/robots.txt"}
    result = make_crawler(Site(routes), tmp_path).crawl([f"{HOST}/docs"])
    assert f"{HOST}/docs/private/secret" in {
        s.url for s in result.skipped if s.reason == "HTTP 404"
    }


def test_network_errors_are_skipped_not_raised(tmp_path: Path) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/docs/a":
            msg = "connection refused"
            raise httpx.ConnectError(msg, request=request)
        return Site(SITE)(request)

    result = make_crawler(failing, tmp_path).crawl([f"{HOST}/docs"])
    assert {"url": f"{HOST}/docs/a", "reason": "request failed: ConnectError"} in [
        s.model_dump() for s in result.skipped
    ]


def test_unreachable_robots_txt_allows_everything(tmp_path: Path) -> None:
    def no_robots(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            msg = "timed out"
            raise httpx.ReadTimeout(msg, request=request)
        return Site(SITE)(request)

    result = make_crawler(no_robots, tmp_path).crawl([f"{HOST}/docs"])
    assert len(result.pages) == 4


def test_invalid_seed_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not an http"):
        make_crawler(Site(SITE), tmp_path).crawl(["ftp://docs.example.com"])

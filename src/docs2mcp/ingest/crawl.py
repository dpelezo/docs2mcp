"""Find and fetch the pages that make up a documentation site.

Rules:
- Start from one or more seed URLs and follow links breadth-first, in page order.
- Follow only links on a seed's host whose path is the seed path or below it. Pass
  several seeds when the docs live in sibling folders (Open Library's search, books
  and authors pages).
- Skip assets, non-HTTP links, URL fragments and query strings; respect robots.txt.
- Drop pages whose HTML is identical to one already fetched.
- Follow links at most `max_depth` hops from a seed (`None`: no limit; `0`: only the
  seeds themselves).
- Stop after `max_pages` pages.
"""

import hashlib
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from http import HTTPStatus
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from docs2mcp.ingest.cache import CachedPage, PageCache
from docs2mcp.ingest.models import SkippedUrl

ASSET_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".ico",
        ".css",
        ".js",
        ".mjs",
        ".map",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".mp4",
        ".mp3",
        ".woff",
        ".woff2",
        ".ttf",
        ".xml",
        ".rss",
        ".atom",
        ".txt",
    }
)


def normalize_url(href: str, base: str | None = None) -> str | None:
    """Absolute http(s) URL without fragment or query, lower-case host, no trailing slash.

    Returns None for links that are not web pages (mailto:, javascript:, assets).
    """
    url = urljoin(base, href.strip()) if base else href.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    path = parts.path or "/"
    if PurePosixPath(path).suffix.lower() in ASSET_SUFFIXES:
        return None
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def in_scope(url: str, seeds: Iterable[str]) -> bool:
    """True when `url` is on a seed's host, at or below that seed's path."""
    target = urlsplit(url)
    for seed in seeds:
        base = urlsplit(seed)
        if target.netloc != base.netloc:
            continue
        prefix = base.path.rstrip("/") + "/"
        if target.path == base.path or (target.path.rstrip("/") + "/").startswith(prefix):
            return True
    return False


class _LinkAndTitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def parse_links_and_title(html: str) -> tuple[list[str], str]:
    parser = _LinkAndTitleParser()
    parser.feed(html)
    parser.close()
    return parser.links, " ".join(parser.title.split())


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    fetched_at: datetime
    title: str
    html: str
    sha256: str


@dataclass(frozen=True)
class CrawlResult:
    pages: list[FetchedPage]
    skipped: list[SkippedUrl]


@dataclass
class Crawler:
    client: httpx.Client
    cache: PageCache
    max_pages: int = 20
    max_depth: int | None = None
    refresh: bool = False
    delay_seconds: float = 0.5
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if self.max_depth is not None and self.max_depth < 0:
            msg = f"max_depth must be 0 or more, got {self.max_depth}"
            raise ValueError(msg)
        self._robots: dict[str, RobotFileParser | None] = {}
        self._fetched_live = False

    def crawl(self, seed_urls: Iterable[str]) -> CrawlResult:
        seeds: list[str] = []
        for seed in seed_urls:
            normalized = normalize_url(seed)
            if normalized is None:
                msg = f"not an http(s) documentation URL: {seed}"
                raise ValueError(msg)
            seeds.append(normalized)

        queue: deque[tuple[str, int]] = deque((seed, 0) for seed in dict.fromkeys(seeds))
        queued = {url for url, _ in queue}
        beyond_depth: list[str] = []
        pages: list[FetchedPage] = []
        skipped: list[SkippedUrl] = []
        seen_hashes: set[str] = set()

        while queue and len(pages) < self.max_pages:
            url, depth = queue.popleft()
            if not self._allowed(url):
                skipped.append(SkippedUrl(url=url, reason="disallowed by robots.txt"))
                continue
            cached, reason = self._load(url)
            if cached is None:
                skipped.append(SkippedUrl(url=url, reason=reason))
                continue
            digest = hashlib.sha256(cached.html.encode()).hexdigest()
            if digest in seen_hashes:
                skipped.append(SkippedUrl(url=url, reason="same content as an earlier page"))
                continue
            seen_hashes.add(digest)

            links, title = parse_links_and_title(cached.html)
            pages.append(
                FetchedPage(
                    url=url,
                    final_url=cached.final_url,
                    fetched_at=cached.fetched_at,
                    title=title or url,
                    html=cached.html,
                    sha256=digest,
                )
            )
            for href in links:
                link = normalize_url(href, base=cached.final_url)
                if link and link not in queued and in_scope(link, seeds):
                    queued.add(link)
                    if self.max_depth is not None and depth >= self.max_depth:
                        beyond_depth.append(link)
                    else:
                        queue.append((link, depth + 1))

        skipped.extend(SkippedUrl(url=url, reason="page limit reached") for url, _ in queue)
        skipped.extend(SkippedUrl(url=url, reason="beyond the depth limit") for url in beyond_depth)
        return CrawlResult(pages=pages, skipped=skipped)

    def _load(self, url: str) -> tuple[CachedPage | None, str]:
        if not self.refresh and (cached := self.cache.get(url)) is not None:
            return cached, ""
        if self._fetched_live and self.delay_seconds:
            self.sleep(self.delay_seconds)
        self._fetched_live = True
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            return None, f"request failed: {exc.__class__.__name__}"
        if response.is_error:
            return None, f"HTTP {response.status_code}"
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            return None, f"not HTML ({content_type or 'no content type'})"
        page = CachedPage(
            url=url,
            final_url=str(response.url),
            fetched_at=self.now(),
            content_type=content_type,
            html=response.text,
        )
        self.cache.put(page)
        return page, ""

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            self._robots[host] = self._load_robots(host)
        robots = self._robots[host]
        user_agent = str(self.client.headers.get("user-agent", "*"))
        return robots is None or robots.can_fetch(user_agent, url)

    def _load_robots(self, host: str) -> RobotFileParser | None:
        """Parse robots.txt; a missing or unreachable file allows everything."""
        try:
            response = self.client.get(f"{host}/robots.txt")
        except httpx.HTTPError:
            return None
        if response.status_code != HTTPStatus.OK:
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

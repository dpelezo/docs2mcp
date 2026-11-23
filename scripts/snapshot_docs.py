"""Fetch, trim and save documentation snapshots for the eval APIs.

Usage (needs network access):

    uv run python scripts/snapshot_docs.py

Reads tests/fixtures/docs/sources.toml. Redistributable pages go to
tests/fixtures/docs/<id>/ and are recorded in manifest.json; the rest go to the
git-ignored .cache/docs/<id>/. Also checks each listed OpenAPI URL.
"""

import hashlib
import html
import json
import sys
import tomllib
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "docs"
CACHE = ROOT / ".cache" / "docs"
SOURCES = FIXTURES / "sources.toml"
MANIFEST = FIXTURES / "manifest.json"
USER_AGENT = "docs2mcp-snapshots (+https://github.com/dpelezo/docs2mcp)"
MAX_BYTES = 400_000  # a trimmed page larger than this is suspicious; fail rather than commit it

# Elements whose content never helps extraction.
# Buttons are copy/toggle controls in docs; forms are kept because some docs (Open-Meteo)
# list their parameters as form fields with labels.
DROP_WITH_CONTENT = {"script", "style", "noscript", "svg", "iframe", "template", "canvas", "button"}
DROP_TAG_ONLY = {"link", "meta", "img", "picture", "source", "input", "form"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
KEEP_ATTRS = {"href", "id", "class", "lang", "role", "aria-label"}


class _Trimmer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth or tag in DROP_WITH_CONTENT:
            if tag not in VOID:
                self._skip_depth += 1
            return
        if tag in DROP_TAG_ONLY:
            return
        kept = "".join(
            f' {name}="{html.escape(value or "", quote=True)}"'
            for name, value in attrs
            if name in KEEP_ATTRS
        )
        self.out.append(f"<{tag}{kept}>")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag not in VOID:
                self._skip_depth -= 1
            return
        if tag in DROP_TAG_ONLY or tag in VOID:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.out.append(html.escape(data, quote=False))


def trim_html(raw: str) -> str:
    """Drop scripts, styles, media, buttons, comments and noisy attributes.

    Text, headings, tables, code blocks and links are kept.
    """
    trimmer = _Trimmer()
    trimmer.feed(raw)
    trimmer.close()
    lines = (line.rstrip() for line in "".join(trimmer.out).splitlines())
    return "\n".join(line for line in lines if line.strip()) + "\n"


def page_filename(url: str) -> str:
    path = urlparse(url).path.strip("/").replace("/", "_") or "index"
    return f"{path}.html"


def fetch(client: httpx.Client, url: str) -> str:
    response = client.get(url)
    response.raise_for_status()
    if "html" not in response.headers.get("content-type", ""):
        msg = f"{url} returned {response.headers.get('content-type')!r}, not HTML"
        raise ValueError(msg)
    return response.text


def check_openapi(client: httpx.Client, url: str) -> str:
    if not url:
        return "not published"
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return f"unreachable: {exc.__class__.__name__}"
    return f"HTTP {response.status_code}"


def main() -> int:
    sources: list[dict[str, Any]] = tomllib.loads(SOURCES.read_text())["api"]
    retrieved = datetime.now(UTC).date().isoformat()
    manifest: list[dict[str, Any]] = []
    failures = 0

    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    ) as client:
        for api in sources:
            target = (FIXTURES if api["redistribute"] else CACHE) / api["id"]
            target.mkdir(parents=True, exist_ok=True)
            print(f"\n{api['name']} -> {target.relative_to(ROOT)}")
            for url in api["pages"]:
                try:
                    trimmed = trim_html(fetch(client, url))
                except (httpx.HTTPError, ValueError) as exc:
                    failures += 1
                    print(f"  FAILED {url}: {exc}")
                    continue
                data = trimmed.encode()
                if len(data) > MAX_BYTES:
                    failures += 1
                    print(f"  TOO LARGE {url}: {len(data):,} bytes after trimming")
                    continue
                path = target / page_filename(url)
                path.write_bytes(data)
                print(f"  saved {path.name} ({len(data):,} bytes)")
                if api["redistribute"]:
                    manifest.append(
                        {
                            "api": api["id"],
                            "url": url,
                            "path": str(path.relative_to(FIXTURES)),
                            "retrieved": retrieved,
                            "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
            print(f"  OpenAPI: {check_openapi(client, api['openapi'])}  ({api['openapi_note']})")

    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote {MANIFEST.relative_to(ROOT)} with {len(manifest)} committed snapshots.")
    if failures:
        print(f"{failures} page(s) failed; fix the URLs in sources.toml and run again.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

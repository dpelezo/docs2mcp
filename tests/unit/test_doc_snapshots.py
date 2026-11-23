"""The documentation fixtures: sources config, committed snapshots and the trimmer."""

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from scripts.snapshot_docs import page_filename, trim_html

FIXTURES = Path(__file__).parents[1] / "fixtures" / "docs"
EVAL_APIS = {"open_meteo", "pokeapi", "rest_countries", "open_library", "frankfurter"}


def load_sources() -> list[dict[str, Any]]:
    return tomllib.loads((FIXTURES / "sources.toml").read_text())["api"]


def load_manifest() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "manifest.json").read_text())


def test_sources_cover_the_five_eval_apis_once() -> None:
    ids = [api["id"] for api in load_sources()]
    assert sorted(ids) == sorted(EVAL_APIS)


@pytest.mark.parametrize("api", load_sources(), ids=lambda api: api["id"])
def test_each_source_is_complete(api: dict[str, Any]) -> None:
    assert api["pages"], "list at least one documentation page"
    assert all(url.startswith("https://") for url in api["pages"])
    assert isinstance(api["redistribute"], bool)
    assert api["licence"].strip(), "record the licence or terms"
    assert api["openapi"] == "" or api["openapi"].startswith("https://")
    assert api["openapi_note"].strip()


def test_every_redistributable_page_has_a_snapshot() -> None:
    expected = {
        (api["id"], url) for api in load_sources() if api["redistribute"] for url in api["pages"]
    }
    recorded = {(entry["api"], entry["url"]) for entry in load_manifest()}
    assert recorded == expected


@pytest.mark.parametrize("entry", load_manifest(), ids=lambda entry: entry["path"])
def test_snapshot_matches_the_manifest(entry: dict[str, Any]) -> None:
    data = (FIXTURES / entry["path"]).read_bytes()
    assert len(data) == entry["bytes"]
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert entry["path"] == f"{entry['api']}/{page_filename(entry['url'])}"


def test_only_redistributable_apis_are_committed() -> None:
    private = {api["id"] for api in load_sources() if not api["redistribute"]}
    committed = {path.parent.name for path in FIXTURES.glob("*/*.html")}
    assert not committed & private


def test_trim_html_keeps_text_and_structure_and_drops_noise() -> None:
    raw = """<!doctype html><html lang="en"><head>
      <meta charset="utf-8"><title>Docs</title><style>body{color:red}</style>
      <script>track()</script><link rel="stylesheet" href="x.css"></head>
      <body><!-- comment --><nav class="sidebar" style="x" data-x="1">Menu</nav>
      <h2 id="pokemon">GET /api/v2/pokemon/{id or name}</h2>
      <p>It&#39;s <b>fast</b> &amp; free.</p><svg><path d="M0"/></svg>
      <pre><code>curl https://pokeapi.co/api/v2/pokemon/ditto</code></pre>
      <img src="a.png"><button onclick="go()">Copy</button></body></html>"""
    out = trim_html(raw)

    assert '<h2 id="pokemon">GET /api/v2/pokemon/{id or name}</h2>' in out
    assert "<p>It's <b>fast</b> &amp; free.</p>" in out
    assert "<pre><code>curl https://pokeapi.co/api/v2/pokemon/ditto</code></pre>" in out
    assert '<nav class="sidebar">Menu</nav>' in out
    for noise in ("track()", "color:red", "comment", "<svg", "<img", "Copy", "data-x", "style="):
        assert noise not in out


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://pokeapi.co/docs/v2", "docs_v2.html"),
        ("https://frankfurter.dev/", "index.html"),
        ("https://openlibrary.org/dev/docs/api/search", "dev_docs_api_search.html"),
    ],
)
def test_page_filename(url: str, name: str) -> None:
    assert page_filename(url) == name

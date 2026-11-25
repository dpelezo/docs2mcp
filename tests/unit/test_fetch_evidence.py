"""scripts/fetch_evidence.py: parse evidence requests and save responses, offline."""

import json
from pathlib import Path

import httpx
import pytest

from scripts.fetch_evidence import Request, fetch_all, requests_in

pytestmark = pytest.mark.unit

_FILE = """\
api = "postcodes_io"

[[task]]
id = "postcodes_io-001"
level = "single"
question = "Which district is SW1A 1AA in?"
evidence = '''
Checked on the live API.
GET https://api.postcodes.io/postcodes/SW1A1AA
POST https://api.postcodes.io/postcodes {"postcodes": ["M1 1AE"]}
'''
answer = { rule = "exact", value = "Westminster" }

[[task]]
id = "postcodes_io-002"
level = "single"
question = "Is XX1 1XX a postcode at all?"
evidence = "GET https://api.postcodes.io/postcodes/XX11XX"
answer = { rule = "exact", value = "no" }
"""


def test_requests_in_reads_get_and_post_lines_and_skips_notes() -> None:
    evidence = """
    Checked on 2026-11-24.
    GET https://example.org/a?b=1
    POST https://example.org/c {"x": [1, 2]}
    get https://example.org/lowercase-is-a-note
    """
    assert requests_in(evidence) == [
        Request("GET", "https://example.org/a?b=1"),
        Request("POST", "https://example.org/c", {"x": [1, 2]}),
    ]
    assert requests_in("just a note") == []


def test_fetch_all_saves_each_response_and_counts_failures(tmp_path: Path) -> None:
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.content))
        if request.url.path.endswith("XX11XX"):
            return httpx.Response(404, json={"status": 404, "error": "Invalid postcode"})
        return httpx.Response(200, json={"status": 200, "result": {"ok": True}})

    tasks = tmp_path / "postcodes_io.toml"
    tasks.write_text(_FILE, encoding="utf-8")
    client = httpx.Client(transport=httpx.MockTransport(handler))

    failed = fetch_all([tasks], tmp_path / "evidence", client, pause=0)

    assert failed == 1
    assert [s[0] for s in seen] == ["GET", "POST", "GET"]
    assert json.loads(seen[1][2]) == {"postcodes": ["M1 1AE"]}
    folder = tmp_path / "evidence" / "postcodes_io"
    assert sorted(p.name for p in folder.iterdir()) == [
        "postcodes_io-001-1.json",
        "postcodes_io-001-2.json",
        "postcodes_io-002-1.json",
    ]
    saved = json.loads((folder / "postcodes_io-002-1.json").read_text())
    assert saved["status"] == 404
    assert saved["request"]["url"] == "https://api.postcodes.io/postcodes/XX11XX"


def test_fetch_all_records_a_network_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        message = "no route"
        raise httpx.ConnectError(message, request=request)

    tasks = tmp_path / "postcodes_io.toml"
    tasks.write_text(_FILE, encoding="utf-8")
    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert fetch_all([tasks], tmp_path, client, pause=0) == 3
    saved = json.loads((tmp_path / "postcodes_io" / "postcodes_io-001-1.json").read_text())
    assert saved["status"] == 0
    assert "no route" in saved["response"]["error"]

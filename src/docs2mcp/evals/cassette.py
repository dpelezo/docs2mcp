"""Record API responses once and replay them, so agent eval runs are reproducible (ADR 0013).

A cassette is a gzip-compressed JSON Lines file. Each line is one request and the
response it got. Requests match on method, URL with sorted query parameters,
and JSON body. Headers are not stored or matched, and query parameters that look
like credentials (a name containing "key", "token" or "secret") are blanked, so
no secret is ever written.

Three modes:
- `record`: every request goes to the API, and new requests are added.
- `replay`: every request is served from the cassette. A request that was never
  recorded fails with `NotRecordedError`, which the generated server reports as a
  tool error, and is counted in `misses`.
- `cache`: replay what is recorded and record what is not. Repeats of a run see
  the same data, while an agent that tries a new request still gets an answer.

Several conditions share one cassette per API, so the same request returns the
same data whichever server makes it.
"""

import contextlib
import gzip
import json
import re
import threading
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

_SECRET = re.compile(r"key|token|secret", re.IGNORECASE)
REDACTED = "REDACTED"


class Mode(StrEnum):
    RECORD = "record"
    REPLAY = "replay"
    CACHE = "cache"


class NotRecordedError(httpx.TransportError):
    """Replay mode met a request that is not in the cassette."""


def request_key(request: httpx.Request) -> str:
    """Method, URL with sorted and redacted query parameters, and canonical JSON body."""
    params = httpx.QueryParams(
        sorted(
            (name, REDACTED if _SECRET.search(name) else value)
            for name, value in request.url.params.multi_items()
        )
    )
    url = request.url.copy_with(query=None).copy_merge_params(params)
    body = request.content.decode("utf-8", errors="replace") if request.content else ""
    if body:
        with contextlib.suppress(ValueError):
            body = json.dumps(json.loads(body), sort_keys=True, ensure_ascii=False)
    return f"{request.method} {url}" + (f" {body}" if body else "")


class Cassette:
    """The recorded interactions in one file, safe to use from several threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            for entry in _read(path):
                self._entries.setdefault(str(entry["key"]), entry)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> httpx.Response | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        headers = {"content-type": str(entry["content_type"])} if entry["content_type"] else {}
        content = str(entry["text"]).encode("utf-8")
        return httpx.Response(int(entry["status"]), headers=headers, content=content)

    def add(self, key: str, response: httpx.Response) -> None:
        """Store a response unless the key is already recorded (the first answer wins)."""
        entry = {
            "key": key,
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "text": response.text,
        }
        with self._lock:
            if key in self._entries:
                return
            self._entries[key] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(self.path, "at", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


class CassetteTransport(httpx.BaseTransport):
    """An httpx transport that records to, or replays from, a cassette."""

    def __init__(
        self, cassette: Cassette, mode: Mode, live: httpx.BaseTransport | None = None
    ) -> None:
        self.cassette = cassette
        self.mode = mode
        self.live = live if live is not None else httpx.HTTPTransport()
        self.hits = 0
        self.misses: list[str] = []
        self.recorded = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key = request_key(request)
        if self.mode is not Mode.RECORD:
            stored = self.cassette.get(key)
            if stored is not None:
                self.hits += 1
                return stored
            if self.mode is Mode.REPLAY:
                self.misses.append(key)
                msg = f"not recorded: {key}"
                raise NotRecordedError(msg, request=request)
        response = self.live.handle_request(request)
        response.read()
        self.cassette.add(key, response)
        self.recorded += 1
        return httpx.Response(
            response.status_code,
            headers={"content-type": response.headers.get("content-type", "")},
            content=response.content,
        )

    def close(self) -> None:
        """Keep the live connection pool open.

        Generated servers open a new `httpx.Client` for each call and close it,
        which closes its transport. This one outlives them; `shutdown` closes it.
        """

    def shutdown(self) -> None:
        self.live.close()

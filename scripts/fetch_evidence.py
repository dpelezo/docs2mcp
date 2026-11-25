"""Fetch the API responses that back each agent eval task's expected answer.

    uv run python scripts/fetch_evidence.py evals/agent/tasks/*.toml --out evidence

Each line of a task's `evidence` that starts with GET or POST is a request:

    GET https://api.postcodes.io/postcodes/SW1A1AA
    POST https://api.postcodes.io/postcodes {"postcodes": ["SW1A 1AA", "M1 1AE"]}

Every response is saved as `<out>/<api>/<task id>-<n>.json`, so a reviewer can
check each expected answer against what the API returned. Other evidence lines
are notes and are skipped. `/evidence/` is git-ignored: responses from the
AGPL-licensed APIs must not be committed. This calls the live APIs.
"""

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from docs2mcp.evals.tasks import load_tasks

_REQUEST = re.compile(r"^(GET|POST)\s+(https?://\S+)(?:\s+(.+))?$")


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    body: Any = None


def requests_in(evidence: str) -> list[Request]:
    """The requests in an evidence string, one per GET or POST line."""
    found: list[Request] = []
    for line in evidence.splitlines():
        match = _REQUEST.match(line.strip())
        if match is None:
            continue
        method, url, body = match.groups()
        found.append(Request(method, url, json.loads(body) if body else None))
    return found


def fetch_all(files: list[Path], out: Path, client: httpx.Client, *, pause: float = 0.2) -> int:
    """Fetch every request in every task file. Returns the number of failed requests."""
    failed = 0
    for path in files:
        tasks = load_tasks(path)
        folder = out / tasks.api
        folder.mkdir(parents=True, exist_ok=True)
        for task in tasks.task:
            for n, request in enumerate(requests_in(task.evidence), start=1):
                try:
                    response = client.request(request.method, request.url, json=request.body)
                    body: Any = response.json()
                    status = response.status_code
                except (httpx.HTTPError, ValueError) as error:
                    body, status = {"error": str(error)}, 0
                record = {"request": vars(request), "status": status, "response": body}
                target = folder / f"{task.id}-{n}.json"
                target.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
                ok = 200 <= status < 300  # noqa: PLR2004 - HTTP success range
                failed += not ok
                print(f"{'ok ' if ok else 'ERR'} {status} {task.id}-{n} {request.url}")
                time.sleep(pause)
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the responses behind agent eval answers.")
    parser.add_argument("files", type=Path, nargs="+", help="task files (TOML)")
    parser.add_argument("--out", type=Path, default=Path("evidence"), help="output folder")
    args = parser.parse_args(argv)
    headers = {"User-Agent": "docs2mcp-eval (github.com/dpelezo/docs2mcp)"}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client:
        failed = fetch_all(args.files, args.out, client)
    print(f"{failed} failed requests" if failed else "all requests succeeded")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

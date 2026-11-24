"""Build extraction references from the eval APIs' own OpenAPI files.

Usage (needs network access):

    uv run python scripts/reference_from_openapi.py            # every API with an OpenAPI URL
    uv run python scripts/reference_from_openapi.py pokeapi    # just one

Reads tests/fixtures/docs/sources.toml and writes
evals/extraction/reference/<id>.json: every operation with its parameters,
paths relative to the API's base_url, and the file's version and sha256 so a
changed upstream spec is visible in review. References for APIs without an
OpenAPI file are written by hand in the same format.
"""

import hashlib
import json
import sys
import tomllib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import yaml

from docs2mcp.evals import Reference, ReferenceSource, endpoints_from_openapi

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "tests" / "fixtures" / "docs" / "sources.toml"
OUT = ROOT / "evals" / "extraction" / "reference"
USER_AGENT = "docs2mcp-references (+https://github.com/dpelezo/docs2mcp)"


def parse_openapi(raw: bytes) -> dict[str, Any]:
    """JSON or YAML (YAML is a superset of JSON, but JSON parses faster and stricter)."""
    text = raw.decode("utf-8")
    loaded: object = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
    if not isinstance(loaded, dict) or "paths" not in loaded:
        msg = "not an OpenAPI document (no 'paths')"
        raise ValueError(msg)
    return cast("dict[str, Any]", loaded)


def build_reference(api: dict[str, Any], raw: bytes, retrieved: date) -> Reference:
    doc = parse_openapi(raw)
    return Reference(
        api=str(api["id"]),
        base_url=str(api["base_url"]),
        source=ReferenceSource(
            kind="openapi",
            url=str(api["openapi"]),
            version=str(doc.get("info", {}).get("version", "")),
            sha256=hashlib.sha256(raw).hexdigest(),
            retrieved=retrieved,
            note=str(api.get("openapi_note", "")),
        ),
        endpoints=endpoints_from_openapi(doc, str(api["base_url"])),
    )


def main(argv: Sequence[str] | None = None) -> int:
    wanted = set(sys.argv[1:] if argv is None else argv)
    apis: list[dict[str, Any]] = tomllib.loads(SOURCES.read_text(encoding="utf-8"))["api"]
    chosen = [a for a in apis if a.get("openapi") and (not wanted or a["id"] in wanted)]
    unknown = wanted - {str(a["id"]) for a in chosen}
    if unknown:
        print(f"no OpenAPI URL for: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).date()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30) as c:
        for api in chosen:
            response = c.get(str(api["openapi"]))
            response.raise_for_status()
            reference = build_reference(api, response.content, today)
            path = OUT / f"{api['id']}.json"
            path.write_text(reference.model_dump_json(indent=2) + "\n", encoding="utf-8")
            print(f"{api['id']}: {len(reference.endpoints)} operations -> {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

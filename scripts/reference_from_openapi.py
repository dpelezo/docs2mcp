"""Build extraction references from the eval APIs' own OpenAPI files.

Usage (needs network access):

    uv run python scripts/reference_from_openapi.py            # every API with an OpenAPI URL
    uv run python scripts/reference_from_openapi.py pokeapi    # just one

Reads tests/fixtures/docs/sources.toml and writes
evals/extraction/reference/<id>.json: every operation with its parameters,
paths relative to the API's base_url, and the file's version and sha256 so a
changed upstream spec is visible in review. If
evals/extraction/additions/<id>.json exists, its routes and parameters (ones
the official file leaves out, each with evidence) are applied on top. References
for APIs without an OpenAPI file are written by hand in the same format.
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

from docs2mcp.evals import (
    Reference,
    ReferenceAdditions,
    ReferenceSource,
    apply_additions,
    endpoints_from_openapi,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "tests" / "fixtures" / "docs" / "sources.toml"
OUT = ROOT / "evals" / "extraction" / "reference"
ADDITIONS = ROOT / "evals" / "extraction" / "additions"
USER_AGENT = "docs2mcp-references (+https://github.com/dpelezo/docs2mcp)"


def parse_openapi(raw: bytes) -> dict[str, Any]:
    """JSON or YAML (YAML is a superset of JSON, but JSON parses faster and stricter)."""
    text = raw.decode("utf-8")
    loaded: object = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
    if not isinstance(loaded, dict) or "paths" not in loaded:
        msg = "not an OpenAPI document (no 'paths')"
        raise ValueError(msg)
    return cast("dict[str, Any]", loaded)


def build_reference(
    api: dict[str, Any], raw: bytes, retrieved: date, additions: ReferenceAdditions | None = None
) -> Reference:
    doc = parse_openapi(raw)
    endpoints = endpoints_from_openapi(doc, str(api["base_url"]))
    note = str(api.get("openapi_note", ""))
    if additions is not None:
        endpoints = apply_additions(endpoints, additions)
        note += (
            f" Plus {len(additions.routes)} routes and {len(additions.parameters)} parameters"
            f" added by hand (marked with a note): {additions.reason}"
        )
    return Reference(
        api=str(api["id"]),
        base_url=str(api["base_url"]),
        source=ReferenceSource(
            kind="openapi",
            url=str(api["openapi"]),
            version=str(doc.get("info", {}).get("version", "")),
            sha256=hashlib.sha256(raw).hexdigest(),
            retrieved=retrieved,
            note=note.strip(),
        ),
        endpoints=endpoints,
    )


def load_additions(api_id: str, folder: Path = ADDITIONS) -> ReferenceAdditions | None:
    path = folder / f"{api_id}.json"
    if not path.exists():
        return None
    additions = ReferenceAdditions.model_validate_json(path.read_text(encoding="utf-8"))
    if additions.api != api_id:
        msg = f"{path.name} is for {additions.api!r}, not {api_id!r}"
        raise ValueError(msg)
    return additions


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
            additions = load_additions(str(api["id"]))
            reference = build_reference(api, response.content, today, additions)
            path = OUT / f"{api['id']}.json"
            text = reference.model_dump_json(indent=2, exclude_none=True)
            path.write_text(text + "\n", encoding="utf-8")
            print(f"{api['id']}: {len(reference.endpoints)} operations -> {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

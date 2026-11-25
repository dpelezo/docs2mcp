"""Build the agent eval's condition A: direct tools from each API's own OpenAPI file.

    uv run python -m scripts.openapi_condition              # every API with an OpenAPI URL
    uv run python -m scripts.openapi_condition frankfurter

For each API in tests/fixtures/docs/sources.toml that has an `openapi` URL, this
fetches the file (network), converts it with `docs2mcp.openapi`, makes the
direct design (one tool per endpoint), approves every tool as other generators
would, and writes evals/agent/conditions/openapi/<id>/:

- spec.json: the converted spec;
- tools.json: the approved direct design;
- source.json: the file's URL, version, SHA-256, retrieval date and every part
  of it that could not become a parameter;
- openapi.json or openapi.yml: the original file, which condition D (FastMCP)
  is built from. Each folder also holds the source project's license.

Build the server with:

    uv run docs2mcp generate evals/agent/conditions/openapi/<id>/tools.json \\
        --spec evals/agent/conditions/openapi/<id>/spec.json --out servers/openapi/<id>
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

from docs2mcp.design.approve import set_approval
from docs2mcp.design.direct import direct_design
from docs2mcp.design.toolset import ToolSet
from docs2mcp.openapi import spec_from_openapi
from scripts.reference_from_openapi import parse_openapi

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "tests" / "fixtures" / "docs" / "sources.toml"
OUT = ROOT / "evals" / "agent" / "conditions" / "openapi"
USER_AGENT = "docs2mcp-evals (+https://github.com/dpelezo/docs2mcp)"
APPROVAL_NOTE = "Condition A: approved without review, as a direct OpenAPI generator would."


def build(api: dict[str, Any], raw: bytes, retrieved: date) -> tuple[str, str, str]:
    """spec.json, tools.json and source.json for one API, as text."""
    doc = parse_openapi(raw)
    conversion = spec_from_openapi(doc, name=str(api["name"]), base_url=str(api["base_url"]))
    design: ToolSet = direct_design(conversion.spec)
    approved = set_approval(design, names=[], every=True, approve=True, note=APPROVAL_NOTE)
    info = cast("dict[str, Any]", doc.get("info") or {})
    source = {
        "api": api["id"],
        "url": api["openapi"],
        "version": str(info.get("version", "")),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "retrieved": retrieved.isoformat(),
        "endpoints": len(conversion.spec.endpoints),
        "skipped": conversion.skipped,
    }
    return (
        conversion.spec.model_dump_json(indent=2, exclude_none=True) + "\n",
        approved.toolset.model_dump_json(indent=2, exclude_none=True) + "\n",
        json.dumps(source, indent=2, ensure_ascii=False) + "\n",
    )


def main(argv: Sequence[str] | None = None) -> int:
    wanted = set(argv if argv is not None else sys.argv[1:])
    with SOURCES.open("rb") as handle:
        apis = [a for a in tomllib.load(handle)["api"] if a.get("openapi")]
    if wanted:
        apis = [a for a in apis if a["id"] in wanted]
    if not apis:
        print("No API with an OpenAPI URL matches.", file=sys.stderr)
        return 1
    today = datetime.now(UTC).date()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        for api in apis:
            response = client.get(str(api["openapi"]), follow_redirects=True)
            response.raise_for_status()
            spec, tools, source = build(api, response.content, today)
            folder = OUT / str(api["id"])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "spec.json").write_text(spec, encoding="utf-8")
            (folder / "tools.json").write_text(tools, encoding="utf-8")
            (folder / "source.json").write_text(source, encoding="utf-8")
            suffix = Path(str(api["openapi"])).suffix or ".json"
            (folder / f"openapi{suffix}").write_bytes(response.content)
            skipped = json.loads(source)["skipped"]
            print(f"{api['id']}: {json.loads(source)['endpoints']} tools, {len(skipped)} skipped")
            for line in skipped:
                print(f"  skipped {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

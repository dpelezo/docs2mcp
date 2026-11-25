"""Build the agent eval's servers from evals/agent/conditions.toml (ADR 0013).

    uv run python -m scripts.build_eval_servers               # every API and condition
    uv run python -m scripts.build_eval_servers frankfurter   # one API

Writes servers/eval/<A|B|C>/<api>/ with `docs2mcp generate`, and
servers/eval/manifest.json: for each server, the SHA-256 of the spec and design
it came from and the tools it has, so a run can say exactly what it measured.
A condition whose input file is missing is skipped and reported, not guessed.
"""

import hashlib
import json
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from docs2mcp.design import ToolSet, direct_design, set_approval
from docs2mcp.generate import NothingToGenerateError, generate
from docs2mcp.spec import ApiSpec

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "evals" / "agent" / "conditions.toml"
OUT = ROOT / "servers" / "eval"
NOTES = {
    "B": "Condition B: approved as generated, without review.",
    "C": "Condition C: approved as generated, without review.",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def designs(
    api: dict[str, Any], root: Path
) -> tuple[dict[str, tuple[ToolSet, ApiSpec, dict[str, str]]], list[str]]:
    """The approved design and spec for each condition this API can have, and what is missing."""
    found: dict[str, tuple[ToolSet, ApiSpec, dict[str, str]]] = {}
    missing: list[str] = []
    if "openapi" in api:
        folder = root / str(api["openapi"])
        spec_path, tools_path = folder / "spec.json", folder / "tools.json"
        spec = ApiSpec.model_validate_json(spec_path.read_text("utf-8"))
        tools = ToolSet.model_validate_json(tools_path.read_text("utf-8"))
        found["A"] = (tools, spec, {"spec": _sha(spec_path), "design": _sha(tools_path)})
    spec_path = root / str(api["spec"])
    if not spec_path.exists():
        missing.append(f"B and C: {api['spec']} not found")
        return found, missing
    spec = ApiSpec.model_validate_json(spec_path.read_text("utf-8"))
    direct = set_approval(direct_design(spec), every=True, note=NOTES["B"]).toolset
    found["B"] = (direct, spec, {"spec": _sha(spec_path), "design": "direct"})
    task_path = root / str(api["task_design"])
    if not task_path.exists():
        missing.append(f"C: {api['task_design']} not found")
        return found, missing
    task = ToolSet.model_validate_json(task_path.read_text("utf-8"))
    approved = set_approval(task, every=True, note=NOTES["C"]).toolset
    found["C"] = (approved, spec, {"spec": _sha(spec_path), "design": _sha(task_path)})
    return found, missing


def main(argv: Sequence[str] | None = None, root: Path = ROOT, out: Path = OUT) -> int:
    wanted = set(argv if argv is not None else sys.argv[1:])
    with (root / "evals" / "agent" / "conditions.toml").open("rb") as handle:
        apis: dict[str, dict[str, Any]] = tomllib.load(handle)["api"]
    manifest_path = out / "manifest.json"
    manifest: dict[str, Any] = (
        json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {}
    )
    failed = 0
    for api_id, api in apis.items():
        if wanted and api_id not in wanted:
            continue
        found, missing = designs(api, root)
        for line in missing:
            print(f"{api_id}: skipped {line}")
        for condition, (toolset, spec, hashes) in found.items():
            folder = out / condition / api_id
            try:
                plan = generate(toolset, spec, folder)
            except NothingToGenerateError as error:
                print(f"{api_id} {condition}: {error}", file=sys.stderr)
                failed += 1
                continue
            skipped = [f"{s.name}: {s.reason}" for s in plan.skipped]
            manifest[f"{condition}/{api_id}"] = {
                **hashes,
                "tools": len(plan.tools),
                "skipped": skipped,
            }
            print(f"{api_id} {condition}: {len(plan.tools)} tools, {len(skipped)} skipped")
            for line in skipped:
                print(f"  skipped {line}")
    out.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

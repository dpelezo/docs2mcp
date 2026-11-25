"""Generate a runnable MCP server from a reviewed tools.json (ADR 0011)."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from docs2mcp.design import ToolSet
from docs2mcp.generate.plan import GenerationPlan, SkippedTool, plan_generation
from docs2mcp.generate.render import FILES, MAX_RESULT_CHARS, RenderedServer, render, write
from docs2mcp.spec import ApiSpec

__all__ = [
    "FILES",
    "MAX_RESULT_CHARS",
    "GenerationPlan",
    "NothingToGenerateError",
    "RenderedServer",
    "SkippedTool",
    "generate",
    "plan_generation",
    "render",
    "report_json",
    "write",
]


class NothingToGenerateError(ValueError):
    """No tool is both approved and valid."""


def generate(toolset: ToolSet, spec: ApiSpec, out: Path) -> GenerationPlan:
    """Write server.py, pyproject.toml, README.md and generation_report.json into `out`."""
    plan = plan_generation(toolset, spec)
    if not plan.tools:
        reasons = "; ".join(f"{s.name}: {s.reason}" for s in plan.skipped[:5])
        msg = f"no tool is both approved and valid, so there is nothing to generate ({reasons})"
        raise NothingToGenerateError(msg)
    write(render(plan), out)
    (out / "generation_report.json").write_text(report_json(plan), encoding="utf-8")
    return plan


def report_json(plan: GenerationPlan) -> str:
    data: dict[str, Any] = {
        "api": plan.toolset.api,
        "design": plan.toolset.design,
        "tools": [t.name for t in plan.tools],
        "skipped": [asdict(s) for s in plan.skipped],
        "excluded": [x.model_dump() for x in plan.excluded],
        "unreachable": plan.unreachable,
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

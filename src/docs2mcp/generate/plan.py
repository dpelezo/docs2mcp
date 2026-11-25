"""Decide what a generated server contains: approved, valid tools only (ADR 0011, amended).

Every other tool is skipped with its reason, and every endpoint that no generated
tool reaches is listed, so the gaps are visible in the report and the README.
"""

from dataclasses import dataclass, field

from docs2mcp.design import ExcludedEndpoint, Tool, ToolSet, tool_errors
from docs2mcp.spec import ApiSpec, Endpoint


@dataclass(frozen=True)
class SkippedTool:
    name: str
    reason: str
    endpoints: list[str]


@dataclass
class GenerationPlan:
    toolset: ToolSet
    spec: ApiSpec
    tools: list[Tool] = field(default_factory=list[Tool])
    skipped: list[SkippedTool] = field(default_factory=list[SkippedTool])
    unreachable: list[str] = field(default_factory=list[str])

    @property
    def excluded(self) -> list[ExcludedEndpoint]:
        return self.toolset.excluded

    @property
    def endpoints(self) -> dict[str, Endpoint]:
        return {e.id: e for e in self.spec.endpoints}


def plan_generation(toolset: ToolSet, spec: ApiSpec) -> GenerationPlan:
    result = GenerationPlan(toolset=toolset, spec=spec)
    known = {e.id for e in spec.endpoints}
    reached: dict[str, str] = {}
    for tool in toolset.tools:
        targets = [r.endpoint for r in tool.routes]
        reason = _skip_reason(tool, spec, known, reached)
        if reason is not None:
            result.skipped.append(SkippedTool(tool.name, reason, targets))
            continue
        result.tools.append(tool)
        reached.update(dict.fromkeys(targets, tool.name))

    excluded = {x.endpoint for x in toolset.excluded}
    covered = set(reached) | excluded
    result.unreachable = [e.id for e in spec.endpoints if e.id not in covered]
    return result


def _skip_reason(tool: Tool, spec: ApiSpec, known: set[str], reached: dict[str, str]) -> str | None:
    if not tool.approved:
        return f"not approved: {tool.note}" if tool.note else "not approved"
    problems = [
        f"route to unknown endpoint '{r.endpoint}'" for r in tool.routes if r.endpoint not in known
    ]
    problems += [
        f"endpoint '{r.endpoint}' is already reached by tool '{reached[r.endpoint]}'"
        for r in tool.routes
        if r.endpoint in reached
    ]
    problems += tool_errors(tool, spec)
    if not problems:
        return None
    more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
    return f"invalid: {problems[0]}{more}"

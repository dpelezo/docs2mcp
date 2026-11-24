"""Measure a tool design (#29): the numbers a person reads next to the review checklist.

The rules (approved 2026-11-24, before any design prompt was written):

- A design is measured against the spec it was designed from. `valid` is
  `validate_toolset` finding no problems; the problems are listed.
- `tools` and `routes` count what the design contains. `covered` counts spec
  endpoints that some route reaches, `excluded` those excluded with a reason.
- `tool_list_tokens` estimates what an agent loads before its first call: every
  tool's name, description and argument schema, as JSON in the shape of an MCP
  `tools/list` reply. It uses the same four-characters-per-token estimate as
  chunking, so it compares designs; it is not an exact count for any one model.
- `description_tokens` is the part of that written in prose: tool and argument
  descriptions.
- `parameters_exposed` / `parameters_total` (added 2026-11-24): every parameter
  of every routed endpoint, counted once per route, and how many of them a route
  fills from an argument or a constant. A parameter no route fills is one the
  agent cannot use; `unexposed` lists them as `endpoint.parameter`. Endpoints
  that are excluded, or not in the spec, are not counted.
- Fewer tools and fewer tokens are not better on their own. The week 3 eval
  decides whether a design helps an agent; these numbers say what it costs.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from docs2mcp.design import Tool, ToolSet, validate_toolset
from docs2mcp.ingest.chunk import estimate_tokens
from docs2mcp.spec import ApiSpec


@dataclass(frozen=True)
class ToolMetrics:
    name: str
    routes: int
    arguments: int
    tokens: int


@dataclass(frozen=True)
class DesignMetrics:
    api: str
    design: str
    valid: bool
    endpoints: int
    covered: int
    excluded: int
    tools: int
    routes: int
    tool_list_tokens: int
    description_tokens: int
    parameters_exposed: int
    parameters_total: int
    unexposed: list[str] = field(default_factory=list[str])
    problems: list[str] = field(default_factory=list[str])
    per_tool: list[ToolMetrics] = field(default_factory=list[ToolMetrics])

    @property
    def max_routes_per_tool(self) -> int:
        return max((t.routes for t in self.per_tool), default=0)


def input_schema(tool: Tool) -> dict[str, Any]:
    """The JSON Schema an MCP client sees for a tool's arguments."""
    properties: dict[str, Any] = {}
    for parameter in tool.parameters:
        prop: dict[str, Any] = {"type": parameter.type.value, "description": parameter.description}
        if parameter.enum is not None:
            prop["enum"] = parameter.enum
        if parameter.default is not None:
            prop["default"] = parameter.default
        if parameter.items_type is not None:
            prop["items"] = {"type": parameter.items_type.value}
        properties[parameter.name] = prop
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    required = [p.name for p in tool.parameters if p.required]
    if required:
        schema["required"] = required
    return schema


def listed_tool(tool: Tool) -> dict[str, Any]:
    """One entry of an MCP `tools/list` reply for this tool."""
    return {"name": tool.name, "description": tool.description, "inputSchema": input_schema(tool)}


def _tokens(value: object) -> int:
    return estimate_tokens(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _parameters(toolset: ToolSet, spec: ApiSpec) -> tuple[int, int, list[str]]:
    endpoints = {e.id: e for e in spec.endpoints}
    exposed, total, unexposed = 0, 0, list[str]()
    for tool in toolset.tools:
        for route in tool.routes:
            endpoint = endpoints.get(route.endpoint)
            if endpoint is None:
                continue
            filled = set(route.arguments.values()) | set(route.constants)
            for parameter in endpoint.parameters:
                total += 1
                if parameter.name in filled:
                    exposed += 1
                else:
                    unexposed.append(f"{endpoint.id}.{parameter.name}")
    return exposed, total, unexposed


def measure_design(toolset: ToolSet, spec: ApiSpec) -> DesignMetrics:
    known = {e.id for e in spec.endpoints}
    routed = {r.endpoint for t in toolset.tools for r in t.routes} & known
    excluded = {x.endpoint for x in toolset.excluded} & known
    problems = validate_toolset(toolset, spec)
    exposed, total, unexposed = _parameters(toolset, spec)
    per_tool = [
        ToolMetrics(
            name=t.name,
            routes=len(t.routes),
            arguments=len(t.parameters),
            tokens=_tokens(listed_tool(t)),
        )
        for t in toolset.tools
    ]
    prose = sum(
        estimate_tokens(t.description) + sum(estimate_tokens(p.description) for p in t.parameters)
        for t in toolset.tools
    )
    return DesignMetrics(
        api=toolset.api,
        design=toolset.design,
        valid=not problems,
        endpoints=len(known),
        covered=len(routed),
        excluded=len(excluded),
        tools=len(toolset.tools),
        routes=sum(len(t.routes) for t in toolset.tools),
        tool_list_tokens=_tokens([listed_tool(t) for t in toolset.tools]),
        description_tokens=prose,
        parameters_exposed=exposed,
        parameters_total=total,
        unexposed=unexposed,
        problems=problems,
        per_tool=per_tool,
    )


def metrics_json(metrics: DesignMetrics) -> dict[str, Any]:
    data = asdict(metrics)
    data["max_routes_per_tool"] = metrics.max_routes_per_tool
    return data


def format_metrics(metrics: DesignMetrics) -> str:
    status = "valid" if metrics.valid else f"INVALID ({len(metrics.problems)} problems)"
    lines = [
        f"{metrics.api} ({metrics.design} design): {status}",
        f"  endpoints: {metrics.covered} covered, {metrics.excluded} excluded, "
        f"of {metrics.endpoints}",
        f"  tools: {metrics.tools}, routes: {metrics.routes} "
        f"(at most {metrics.max_routes_per_tool} in one tool)",
        f"  parameters: {metrics.parameters_exposed} of {metrics.parameters_total} exposed",
        f"  tool list: ~{metrics.tool_list_tokens:,} tokens, "
        f"of which descriptions ~{metrics.description_tokens:,}",
    ]
    if metrics.unexposed:
        lines.append(f"  not exposed: {', '.join(metrics.unexposed)}")
    lines.extend(f"  - {problem}" for problem in metrics.problems)
    return "\n".join(lines)

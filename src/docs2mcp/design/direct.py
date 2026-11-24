"""The direct design: one tool per endpoint, with no LLM (ADR 0011).

It is the fallback when no task-shaped design is wanted, and the comparison the
week 3 eval runs against. Tool and argument names are made safe for generated
Python code; each route maps them back to the endpoint's real parameter names.
"""

import keyword
import re

from docs2mcp.design.toolset import (
    MAX_PARAMETER_DESCRIPTION,
    MAX_TOOL_DESCRIPTION,
    Route,
    Tool,
    ToolParameter,
    ToolSet,
)
from docs2mcp.spec import ApiSpec, Endpoint, Parameter

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def safe_name(raw: str, max_length: int = 64) -> str:
    """`wideSearch` -> `wide_search`, `X-Key` -> `x_key`, `from` -> `from_`, `2fa` -> `p_2fa`."""
    name = _NOT_WORD.sub("_", _CAMEL.sub("_", raw).lower()).strip("_") or "p"
    if not name[0].isalpha():
        name = f"p_{name}"
    name = name[:max_length].rstrip("_")
    if keyword.iskeyword(name) or keyword.issoftkeyword(name):
        name = f"{name[: max_length - 1]}_"
    return name


def shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _parameter(param: Parameter, name: str) -> ToolParameter:
    description = param.description or f"The {param.location} parameter '{param.name}'."
    return ToolParameter(
        name=name,
        type=param.type,
        required=param.required,
        description=shorten(description, MAX_PARAMETER_DESCRIPTION),
        default=param.default,
        enum=param.enum,
        items_type=param.items_type,
    )


def direct_tool(endpoint: Endpoint) -> Tool:
    names: dict[str, str] = {}
    taken: set[str] = set()
    parameters: list[ToolParameter] = []
    for param in endpoint.parameters:
        name = safe_name(param.name)
        if name in taken:
            name = safe_name(f"{param.location}_{param.name}")
        suffix = 2
        base = name
        while name in taken:
            name = f"{base[:60]}_{suffix}"
            suffix += 1
        taken.add(name)
        names[name] = param.name
        parameters.append(_parameter(param, name))

    text = (
        endpoint.summary
        if not endpoint.description
        else f"{endpoint.summary} {endpoint.description}"
    )
    return Tool(
        name=safe_name(endpoint.id),
        description=shorten(text, MAX_TOOL_DESCRIPTION),
        parameters=parameters,
        routes=[Route(endpoint=endpoint.id, arguments=names)],
    )


def direct_design(spec: ApiSpec) -> ToolSet:
    return ToolSet(
        api=spec.name,
        base_url=spec.base_url,
        auth=spec.auth,
        design="direct",
        tools=[direct_tool(endpoint) for endpoint in spec.endpoints],
    )

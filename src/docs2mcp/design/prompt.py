"""Prompts for the task-shaped design. Changing them changes results: re-run the design eval."""

import json
from collections.abc import Sequence
from typing import Any

from docs2mcp.spec import ApiSpec, Endpoint, Parameter

SYSTEM = """\
You design the tools an AI agent uses to call an HTTP API through an MCP server. You get the \
API's endpoints; you return a small set of task-shaped tools that cover them.

How a tool works:
- Each tool call makes exactly one API request. A tool has one or more routes; each route \
calls one endpoint.
- A call takes the first route whose "requires" arguments were all given. So order routes from \
most specific to least: a route requiring ["name"] comes before a route requiring nothing. Two \
routes in one tool never require the same set of arguments.
- A route maps tool arguments to the endpoint's parameters by their exact names in the spec. \
Code copies each argument's type, allowed values and default from the parameters it fills, so \
you only name and describe arguments.

Rules:
- Every endpoint id appears in exactly one route of one tool, or in "excluded" with a reason. \
Exclude only what an agent cannot use: a non-JSON response such as an image, or an exact \
duplicate of another route. When unsure, keep the endpoint.
- Group endpoints that do one task on one kind of thing: getting one item and listing items of \
the same resource belong in one tool; so do the same search by different keys. Do not merge \
unrelated resources to reduce the tool count; an agent must be able to tell tools apart.
- Every required endpoint parameter, path parameters included, must be filled on its route by \
an argument that the route requires or that is required for the whole tool, or by a constant.
- An argument is "required" only if every route of the tool fills it. Arguments that choose a \
route are optional and listed in that route's "requires".
- Each argument a tool declares is passed by at least one route. Arguments filling the same \
kind of value on different routes share one name (for example limit and offset on every list \
route).
- Names: snake_case, at most 64 characters, not Python keywords. Name tools after the task or \
resource, such as "pokemon" or "postcode_lookup", not after HTTP methods.
- Tool descriptions are for an agent choosing a tool: say what it returns, when to use it, and \
which arguments select which behaviour. At most 1000 characters. Argument descriptions say \
what to pass, with the format or an example where it is not obvious. At most 300 characters.
- Use only what the spec says. Never invent endpoints, parameters, limits, values or behaviour.
- Constants are for parameters that must always take one value on a route; usually there are \
none.
"""


def _parameter(param: Parameter) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": param.name,
        "in": param.location.value,
        "type": param.type.value,
    }
    if param.required:
        data["required"] = True
    if param.enum is not None:
        data["enum"] = param.enum
    if param.default is not None:
        data["default"] = param.default
    if param.description:
        data["description"] = param.description
    return data


def _endpoint(endpoint: Endpoint) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": endpoint.id,
        "method": endpoint.method.value,
        "path": endpoint.path,
        "summary": endpoint.summary,
    }
    if endpoint.description:
        data["description"] = endpoint.description
    if endpoint.parameters:
        data["parameters"] = [_parameter(p) for p in endpoint.parameters]
    return data


def spec_message(spec: ApiSpec) -> str:
    """The user message: the API and one compact JSON line per endpoint."""
    lines = [json.dumps(_endpoint(e), ensure_ascii=False) for e in spec.endpoints]
    return (
        f"Design the tools for {spec.name} ({spec.base_url}), {len(lines)} endpoints:\n\n"
        + "\n".join(lines)
    )


def feedback_message(errors: Sequence[str]) -> str:
    """Ask Claude to fix the problems validation found, keeping the rest of the design."""
    listed = "\n".join(f"- {error}" for error in errors)
    return (
        "The design has these problems:\n"
        f"{listed}\n\n"
        "Return the complete design again with these problems fixed and everything else "
        "unchanged."
    )

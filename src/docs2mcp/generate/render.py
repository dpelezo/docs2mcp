"""Render a generation plan into a runnable MCP server folder (ADR 0011).

The templates get plain data built here, and every value that lands in Python
code goes through `repr`, so descriptions and enum values cannot break the
generated module.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

from docs2mcp.design import Tool, ToolParameter
from docs2mcp.generate.plan import GenerationPlan
from docs2mcp.spec import AuthType, ParamType

MAX_RESULT_CHARS = 20_000
FILES = ("server.py", "pyproject.toml", "README.md")

_PYTHON_TYPES = {
    ParamType.STRING: "str",
    ParamType.INTEGER: "int",
    ParamType.NUMBER: "float",
    ParamType.BOOLEAN: "bool",
    ParamType.OBJECT: "dict[str, Any]",
}


@dataclass(frozen=True)
class RenderedServer:
    files: dict[str, str]


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def slug(text: str) -> str:
    """`PokéAPI` -> `pokeapi`, `Open-Meteo Geocoding API` -> `open-meteo-geocoding-api`."""
    return re.sub(r"[^a-z0-9]+", "-", _ascii(text).lower()).strip("-") or "api"


def python_type(parameter: ToolParameter) -> str:
    if parameter.enum is not None:
        inner = f"Literal[{', '.join(repr(v) for v in parameter.enum)}]"
    elif parameter.type is ParamType.ARRAY:
        item = _PYTHON_TYPES.get(parameter.items_type or ParamType.STRING, "Any")
        inner = f"list[{item}]"
    else:
        inner = _PYTHON_TYPES[parameter.type]
    if parameter.type is ParamType.ARRAY and parameter.enum is not None:
        inner = f"list[{inner}]"
    return inner if parameter.required else f"{inner} | None"


def _field(parameter: ToolParameter) -> str:
    parts = [f"description={parameter.description!r}"]
    if parameter.default is not None:
        parts.append(f"json_schema_extra={{'default': {parameter.default!r}}}")
    return f"Field({', '.join(parts)})"


def _tool_context(tool: Tool, plan: GenerationPlan) -> dict[str, Any]:
    endpoints = plan.endpoints
    routes: list[dict[str, Any]] = []
    for route in tool.routes:
        endpoint = endpoints[route.endpoint]
        location = {p.name: p.location.value for p in endpoint.parameters}
        routes.append(
            {
                "endpoint": endpoint.id,
                "method": endpoint.method.value,
                "path": endpoint.path,
                "requires": list(route.requires),
                "arguments": {arg: (name, location[name]) for arg, name in route.arguments.items()},
                "constants": [
                    (name, location[name], value) for name, value in route.constants.items()
                ],
            }
        )
    ordered = sorted(tool.parameters, key=lambda p: not p.required)  # required first: no defaults
    parameters = [
        {
            "name": p.name,
            "annotation": f"Annotated[{python_type(p)}, {_field(p)}]",
            "required": p.required,
        }
        for p in ordered
    ]
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": parameters,
        "routes": routes,
    }


def _auth_context(plan: GenerationPlan) -> dict[str, Any]:
    auth = plan.toolset.auth
    name = slug(plan.toolset.api).removesuffix("-api").replace("-", "_").upper()
    env = f"{name}_API_KEY"
    match auth.type:
        case AuthType.API_KEY:
            return {"kind": "api_key", "env": env, "location": auth.location, "name": auth.name}
        case AuthType.BEARER:
            return {"kind": "bearer", "env": env, "location": "header", "name": "Authorization"}
        case AuthType.NONE:
            return {"kind": "none", "env": env, "location": None, "name": None}
        case _:
            return {"kind": "unsupported", "env": env, "location": None, "name": str(auth.type)}


def render(plan: GenerationPlan) -> RenderedServer:
    environment = Environment(
        loader=PackageLoader("docs2mcp.generate", "templates"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,  # noqa: S701 - renders Python and Markdown, never HTML
    )
    environment.filters["py"] = repr
    environment.filters["json"] = lambda value: json.dumps(value, ensure_ascii=False)
    context = {
        "api": plan.toolset.api,
        "slug": slug(plan.toolset.api),
        "base_url": str(plan.toolset.base_url).rstrip("/"),
        "design": plan.toolset.design,
        "auth": _auth_context(plan),
        "tools": [_tool_context(t, plan) for t in plan.tools],
        "skipped": plan.skipped,
        "excluded": plan.excluded,
        "unreachable": plan.unreachable,
        "endpoints": plan.endpoints,
        "max_result_chars": MAX_RESULT_CHARS,
        "version": metadata.version("docs2mcp"),
    }
    files = {name: environment.get_template(f"{name}.j2").render(context) for name in FILES}
    return RenderedServer(files)


def write(server: RenderedServer, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, text in server.files.items():
        path = out / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written

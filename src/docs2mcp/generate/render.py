"""Render a generation plan into a runnable MCP server folder (ADR 0011).

The Python code is written here, in the layout Ruff's formatter produces (see
pycode.py), and every value goes through a literal writer, so descriptions and
enum values cannot break the generated module.
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
from docs2mcp.generate.pycode import block, literal, string
from docs2mcp.spec import AuthType, ParamType

MAX_RESULT_CHARS = 20_000
_PARSER_PREFIX = "    parser = argparse.ArgumentParser(description="
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


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def python_type(parameter: ToolParameter, alias: str | None = None) -> str:
    """The annotation for an argument; `alias` names its Literal type when it has an enum."""
    if parameter.enum is not None:
        inner = alias or f"Literal[{', '.join(literal(v) for v in parameter.enum)}]"
    elif parameter.type is ParamType.ARRAY:
        inner = _PYTHON_TYPES.get(parameter.items_type or ParamType.STRING, "Any")
    else:
        inner = _PYTHON_TYPES[parameter.type]
    if parameter.type is ParamType.ARRAY:
        inner = f"list[{inner}]"
    return inner if parameter.required else f"{inner} | None"


def _argument(parameter: ToolParameter, alias: str | None) -> str:
    """One parameter of a tool function, as Ruff would format it."""
    lines = [f"    {parameter.name}: {python_type(parameter, alias)} = Field("]
    if not parameter.required:
        lines.append("        default=None,")
    used = len("        description=")
    lines.append(f"        description={string(parameter.description, 2, used)},")
    if parameter.default is not None:
        lines.append(f'        json_schema_extra={{"default": {literal(parameter.default)}}},')
    lines.append("    ),")
    return "\n".join(lines)


def _tool_code(tool: Tool, aliases: dict[tuple[str, str], str]) -> str:
    head = [
        "@mcp.tool(",
        f"    name={literal(tool.name)},",
        f"    description={string(tool.description, 1, len('    description='))},",
        ")",
    ]
    if tool.parameters:
        params = [_argument(p, aliases.get((tool.name, p.name))) for p in tool.parameters]
        signature = [f"def {tool.name}(", *params, ") -> str:"]
        values = "\n".join(f"            {literal(p.name)}: {p.name}," for p in tool.parameters)
        body = [
            "    return call(",
            f"        {literal(tool.name)},",
            "        {",
            values,
            "        },",
            "    )",
        ]
    else:
        signature = [f"def {tool.name}() -> str:"]
        body = [f"    return call({literal(tool.name)}, {{}})"]
    return "\n".join([*head, *signature, *body])


def _aliases(tools: list[Tool]) -> tuple[dict[tuple[str, str], str], str]:
    """A named Literal type for every enum argument, and the code that defines them."""
    names: dict[tuple[str, str], str] = {}
    code: list[str] = []
    for tool in tools:
        for parameter in tool.parameters:
            if parameter.enum is None:
                continue
            name, n = f"{_camel(tool.name)}{_camel(parameter.name)}", 2
            while name in names.values():
                name, n = f"{_camel(tool.name)}{_camel(parameter.name)}{n}", n + 1
            names[(tool.name, parameter.name)] = name
            if len(parameter.enum) == 1:
                code.append(f"{name} = Literal[{literal(parameter.enum[0])}]")
            else:
                values = "\n".join(f"    {literal(v)}," for v in parameter.enum)
                code.append(f"{name} = Literal[\n{values}\n]")
    return names, "\n".join(code)


def _routes(tool: Tool, plan: GenerationPlan) -> list[dict[str, Any]]:
    endpoints = plan.endpoints
    routes: list[dict[str, Any]] = []
    for route in tool.routes:
        endpoint = endpoints[route.endpoint]
        where = {p.name: p.location.value for p in endpoint.parameters}
        routes.append(
            {
                "method": endpoint.method.value,
                "path": endpoint.path,
                "requires": list(route.requires),
                "arguments": {arg: (name, where[name]) for arg, name in route.arguments.items()},
                "constants": [
                    (name, where[name], value) for name, value in route.constants.items()
                ],
            }
        )
    return routes


def _summary(tool: Tool, plan: GenerationPlan) -> dict[str, Any]:
    endpoints = plan.endpoints
    return {
        "name": tool.name,
        "routes": [
            {"method": endpoints[r.endpoint].method.value, "path": endpoints[r.endpoint].path}
            for r in tool.routes
        ],
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


def _toml_string(text: str) -> str:
    """A TOML basic string: JSON's string escapes are valid TOML."""
    return json.dumps(text, ensure_ascii=False)


def render(plan: GenerationPlan) -> RenderedServer:
    environment = Environment(
        loader=PackageLoader("docs2mcp.generate", "templates"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,  # noqa: S701 - renders Python and Markdown, never HTML
    )
    environment.filters["py"] = literal
    environment.filters["toml"] = _toml_string
    base_url = str(plan.toolset.base_url).rstrip("/")
    aliases, alias_code = _aliases(plan.tools)
    routes = {t.name: _routes(t, plan) for t in plan.tools}
    context = {
        "api": plan.toolset.api,
        "doc_api": plan.toolset.api.replace('"""', "'''").replace("\\", "/"),
        "slug": slug(plan.toolset.api),
        "base_url": base_url,
        "design": plan.toolset.design,
        "auth": _auth_context(plan),
        "tools": [_summary(t, plan) for t in plan.tools],
        "tool_code": [_tool_code(t, aliases) for t in plan.tools],
        "aliases": alias_code,
        "routes": block(routes, 0),
        "instructions": string(
            f"Tools for the {plan.toolset.api} API at {base_url}. Each tool makes one request.",
            1,
            len("    instructions="),
        ),
        "cli_description": string(
            f"MCP server for {plan.toolset.api}",
            1,
            len("    parser = argparse.ArgumentParser(description="),
        ),
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

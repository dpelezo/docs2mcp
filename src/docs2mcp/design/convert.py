"""Turn Claude's wire design into a tool set, and repair what retries could not fix (ADR 0012).

`convert` does the mechanical work and reports every problem, so the designer can
send them back to Claude. `repair` runs only after the last retry: it keeps the
tools that are sound and gives every endpoint they leave uncovered a direct tool,
so a design is always valid and every repair is listed in the report.
"""

import json
from dataclasses import dataclass, field

from pydantic import JsonValue, ValidationError

from docs2mcp.design.direct import direct_tool, safe_name, shorten
from docs2mcp.design.toolset import (
    MAX_PARAMETER_DESCRIPTION,
    MAX_TOOL_DESCRIPTION,
    ExcludedEndpoint,
    Route,
    Tool,
    ToolParameter,
    ToolSet,
)
from docs2mcp.design.validate import tool_errors, validate_toolset
from docs2mcp.design.wire import WireArgument, WireDesign, WireRoute, WireTool
from docs2mcp.spec import ApiSpec, Endpoint, Parameter, ParamType


@dataclass
class Conversion:
    """What one reply turned into: the tools that could be built, and every problem."""

    tools: list[Tool] = field(default_factory=list[Tool])
    excluded: list[ExcludedEndpoint] = field(default_factory=list[ExcludedEndpoint])
    errors: list[str] = field(default_factory=list[str])
    notes: list[str] = field(default_factory=list[str])


@dataclass
class Repairs:
    dropped_tools: list[str] = field(default_factory=list[str])
    dropped_exclusions: list[str] = field(default_factory=list[str])
    added_direct: list[str] = field(default_factory=list[str])


def toolset_from(spec: ApiSpec, tools: list[Tool], excluded: list[ExcludedEndpoint]) -> ToolSet:
    return ToolSet(
        api=spec.name,
        base_url=spec.base_url,
        auth=spec.auth,
        design="task",
        tools=tools,
        excluded=excluded,
    )


def convert(wire: WireDesign, spec: ApiSpec) -> Conversion:
    result = Conversion()
    endpoints = {e.id: e for e in spec.endpoints}
    seen: set[str] = set()
    for wire_tool in wire.tools:
        tool = _tool(wire_tool, endpoints, result)
        if tool is None:
            continue
        if tool.name in seen:
            result.errors.append(f"tool '{tool.name}' is defined more than once")
            continue
        seen.add(tool.name)
        result.tools.append(tool)

    for exclusion in wire.excluded:
        try:
            result.excluded.append(
                ExcludedEndpoint(endpoint=exclusion.endpoint, reason=exclusion.reason)
            )
        except ValidationError as error:
            result.errors.extend(_messages(error, f"exclusion of '{exclusion.endpoint}'"))

    if not result.tools:
        result.errors.append("no tool could be built; every tool needs at least one route")
        return result
    toolset = toolset_from(spec, result.tools, result.excluded)
    result.errors.extend(e for e in validate_toolset(toolset, spec) if e not in result.errors)
    return result


def repair(conversion: Conversion, spec: ApiSpec) -> tuple[ToolSet, Repairs]:
    """Keep the sound tools, drop the rest, and give uncovered endpoints direct tools."""
    repairs = Repairs()
    known = {e.id for e in spec.endpoints}
    kept: list[Tool] = []
    routed: set[str] = set()
    for tool in conversion.tools:
        targets = [r.endpoint for r in tool.routes]
        problems = tool_errors(tool, spec)
        unknown = sorted(set(targets) - known)
        reused = sorted(set(targets) & routed)
        if problems or unknown or reused or len(set(targets)) != len(targets):
            why = problems[:1] or [
                f"routes to unknown endpoints {unknown}" if unknown else "reuses an endpoint"
            ]
            repairs.dropped_tools.append(f"{tool.name}: {why[0]}")
            continue
        kept.append(tool)
        routed.update(targets)

    excluded: list[ExcludedEndpoint] = []
    for exclusion in conversion.excluded:
        endpoint = exclusion.endpoint
        if (
            endpoint not in known
            or endpoint in routed
            or endpoint in {x.endpoint for x in excluded}
        ):
            repairs.dropped_exclusions.append(endpoint)
            continue
        excluded.append(exclusion)

    names = {t.name for t in kept}
    covered = routed | {x.endpoint for x in excluded}
    for endpoint in spec.endpoints:
        if endpoint.id in covered:
            continue
        tool = direct_tool(endpoint)
        name, suffix = tool.name, 2
        while name in names:
            name = f"{tool.name[:60]}_{suffix}"
            suffix += 1
        names.add(name)
        kept.append(tool.model_copy(update={"name": name}))
        repairs.added_direct.append(endpoint.id)

    toolset = toolset_from(spec, kept, excluded)
    errors = validate_toolset(toolset, spec)
    if errors:  # every kept tool passed tool_errors, so this would be a bug
        msg = "repaired design is still invalid: " + "; ".join(errors)
        raise RuntimeError(msg)
    return toolset, repairs


def _tool(wire: WireTool, endpoints: dict[str, Endpoint], result: Conversion) -> Tool | None:
    name = wire.name.strip()
    label = f"tool '{name}'"
    safe = safe_name(name)
    if safe != name:
        result.notes.append(f"{label}: renamed to '{safe}'")
        name, label = safe, f"tool '{safe}'"

    renames: dict[str, str] = {}
    for argument in wire.arguments:
        new = safe_name(argument.name.strip())
        if new != argument.name.strip():
            result.notes.append(f"{label}: argument '{argument.name}' renamed to '{new}'")
        renames[argument.name.strip()] = new

    built = [_route(r, renames, label, result) for r in wire.routes]
    routes = [r for r in built if r is not None]
    if len(routes) != len(built):
        return None
    parameters: list[ToolParameter] = []
    for argument in wire.arguments:
        try:
            parameters.append(_parameter(argument, renames, routes, endpoints, label, result))
        except ValidationError as error:
            result.errors.extend(_messages(error, f"{label}, argument '{argument.name}'"))
    if len(parameters) != len(wire.arguments):
        return None

    description = " ".join(wire.description.split())
    if len(description) > MAX_TOOL_DESCRIPTION:
        result.notes.append(f"{label}: description shortened to {MAX_TOOL_DESCRIPTION} characters")
        description = shorten(description, MAX_TOOL_DESCRIPTION)
    try:
        return Tool(name=name, description=description, parameters=parameters, routes=routes)
    except ValidationError as error:
        result.errors.extend(_messages(error, label))
        return None


def _route(
    wire: WireRoute, renames: dict[str, str], label: str, result: Conversion
) -> Route | None:
    where = f"{label}, route to '{wire.endpoint}'"
    problems: list[str] = []
    arguments: dict[str, str] = {}
    for mapping in wire.arguments:
        argument = renames.get(mapping.argument.strip(), mapping.argument.strip())
        if argument in arguments:
            problems.append(f"{where}: argument '{argument}' is mapped more than once")
        arguments[argument] = mapping.parameter.strip()
    constants: dict[str, JsonValue] = {}
    for constant in wire.constants:
        try:
            constants[constant.parameter.strip()] = json.loads(constant.value_json)
        except json.JSONDecodeError:
            problems.append(f"{where}: constant '{constant.parameter}' is not a JSON literal")
    if problems:
        result.errors.extend(problems)
        return None
    requires = [renames.get(r.strip(), r.strip()) for r in wire.requires]
    try:
        return Route(
            endpoint=wire.endpoint.strip(),
            requires=requires,
            arguments=arguments,
            constants=constants,
        )
    except ValidationError as error:
        result.errors.extend(_messages(error, where))
        return None


def _parameter(
    wire: WireArgument,
    renames: dict[str, str],
    routes: list[Route],
    endpoints: dict[str, Endpoint],
    label: str,
    result: Conversion,
) -> ToolParameter:
    """Type, enum, default and item type come from the endpoint parameters the argument fills.

    Raises ValidationError when the argument cannot be built, for example a keyword name.
    """
    name = renames[wire.name.strip()]
    targets: list[Parameter] = []
    for route in routes:
        endpoint = endpoints.get(route.endpoint)
        target = route.arguments.get(name)
        if endpoint is None or target is None:
            continue
        targets.extend(p for p in endpoint.parameters if p.name == target)

    type_ = _type(targets)
    enum = _enum(targets)
    default: JsonValue = None
    defaults = {json.dumps(p.default) for p in targets}
    if not wire.required and len(defaults) == 1 and targets[0].default is not None:
        candidate = targets[0].default
        if enum is None or candidate in enum:
            default = candidate
    items_type = next((p.items_type for p in targets if p.items_type is not None), None)

    description = " ".join(wire.description.split())
    if not description:
        description = (targets[0].description if targets else "") or f"The value of '{name}'."
        result.notes.append(f"{label}: argument '{name}' had no description; filled from the spec")
    if len(description) > MAX_PARAMETER_DESCRIPTION:
        result.notes.append(f"{label}: argument '{name}' description shortened")
        description = shorten(description, MAX_PARAMETER_DESCRIPTION)
    return ToolParameter(
        name=name,
        type=type_,
        required=wire.required,
        description=description,
        default=default,
        enum=enum,
        items_type=items_type if type_ is ParamType.ARRAY else None,
    )


def _type(targets: list[Parameter]) -> ParamType:
    types = {p.type for p in targets}
    if not types:
        return ParamType.STRING
    if types == {ParamType.INTEGER, ParamType.NUMBER}:
        return ParamType.INTEGER  # an integer argument may fill a number parameter
    return targets[0].type


def _enum(targets: list[Parameter]) -> list[str | int | float] | None:
    enums = [p.enum for p in targets if p.enum is not None]
    if not enums:
        return None
    common = [v for v in enums[0] if all(v in other for other in enums[1:])]
    return common or enums[0]


def _messages(error: ValidationError, prefix: str) -> list[str]:
    messages: list[str] = []
    for detail in error.errors():
        message = str(detail["msg"]).removeprefix("Value error, ")
        location = ".".join(str(part) for part in detail["loc"])
        messages.append(f"{prefix}: {location}: {message}" if location else f"{prefix}: {message}")
    return messages

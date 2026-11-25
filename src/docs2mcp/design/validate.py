"""Check a tool set against the API spec it was designed from (ADR 0011).

`validate_toolset` returns every problem it finds, not just the first, so a
person (or Claude, on a retry) sees them all at once. An empty list means the
tool set is safe to generate from.
"""

from docs2mcp.design.toolset import Route, Tool, ToolParameter, ToolSet
from docs2mcp.spec import ApiSpec, Endpoint, Parameter, ParamType
from docs2mcp.tracing import tracer


def validate_toolset(toolset: ToolSet, spec: ApiSpec) -> list[str]:
    with tracer.start_as_current_span("validate_toolset") as span:
        errors = _validate(toolset, spec)
        span.set_attribute("docs2mcp.problems", len(errors))
        return errors


def _validate(toolset: ToolSet, spec: ApiSpec) -> list[str]:
    errors: list[str] = []
    endpoints = {e.id: e for e in spec.endpoints}

    routed: dict[str, list[str]] = {}
    for tool in toolset.tools:
        for route in tool.routes:
            routed.setdefault(route.endpoint, []).append(tool.name)
    excluded = [x.endpoint for x in toolset.excluded]

    for endpoint_id in sorted(set(routed) | set(excluded)):
        if endpoint_id not in endpoints:
            errors.append(f"endpoint '{endpoint_id}' is not in the spec")
    for endpoint_id in endpoints:
        uses = len(routed.get(endpoint_id, [])) + excluded.count(endpoint_id)
        if uses == 0:
            errors.append(f"endpoint '{endpoint_id}' is neither in a route nor excluded")
        elif uses > 1:
            where = ", ".join(
                routed.get(endpoint_id, []) + ["excluded"] * excluded.count(endpoint_id)
            )
            errors.append(f"endpoint '{endpoint_id}' is used more than once ({where})")

    for tool in toolset.tools:
        errors.extend(_check_tool(tool, endpoints))
    return errors


def route_notes(toolset: ToolSet) -> list[str]:
    """Arguments a route would leave out: not errors, but worth a reviewer's look (#63).

    A route's argument is at risk when it is given along with just that route's
    required arguments and no route can take all of them. The generated server
    then picks the closest route and names the argument it left out.
    """
    notes: list[str] = []
    for tool in toolset.tools:
        for route in tool.routes:
            lost = [p.name for p in tool.parameters if _left_out(tool, route, p.name)]
            if lost:
                notes.append(
                    f"{tool.name}: with {', '.join(route.requires) or 'no arguments'} given, "
                    f"'{route.endpoint}' is chosen and cannot take {', '.join(lost)}"
                )
    return notes


def _left_out(tool: Tool, route: Route, name: str) -> bool:
    if name in route.arguments:
        return False
    given = {*route.requires, name}
    return not any(set(other.requires) <= given <= set(other.arguments) for other in tool.routes)


def tool_errors(tool: Tool, spec: ApiSpec) -> list[str]:
    """The problems inside one tool: its routes, types and arguments, not coverage."""
    return _check_tool(tool, {e.id: e for e in spec.endpoints})


def _check_tool(tool: Tool, endpoints: dict[str, Endpoint]) -> list[str]:
    errors: list[str] = []
    params = {p.name: p for p in tool.parameters}
    used: set[str] = set()

    for index, route in enumerate(tool.routes):
        used.update(route.arguments)
        endpoint = endpoints.get(route.endpoint)
        if endpoint is not None:
            errors.extend(_check_route(tool, route, endpoint, params))
        for earlier in tool.routes[:index]:
            if set(earlier.requires) <= set(route.requires):
                errors.append(
                    f"tool '{tool.name}': the route to '{route.endpoint}' can never be chosen, "
                    f"because the route to '{earlier.endpoint}' before it matches whenever it does"
                )
                break

    unused = sorted(set(params) - used)
    if unused:
        errors.append(f"tool '{tool.name}': arguments {unused} are not passed by any route")
    return errors


def _check_route(
    tool: Tool, route: Route, endpoint: Endpoint, params: dict[str, ToolParameter]
) -> list[str]:
    errors: list[str] = []
    where = f"tool '{tool.name}', route to '{endpoint.id}'"
    by_name = {p.name: p for p in endpoint.parameters}

    targets = list(route.arguments.values()) + list(route.constants) + list(route.omitted)
    for target in sorted({t for t in targets if targets.count(t) > 1}):
        errors.append(
            f"{where}: endpoint parameter '{target}' is in more than one of arguments, "
            "constants and omitted"
        )
    for target in sorted(set(targets) - set(by_name)):
        errors.append(f"{where}: '{endpoint.id}' has no parameter '{target}'")
    for name in sorted(set(route.omitted) & set(by_name)):
        if by_name[name].required:
            errors.append(f"{where}: required endpoint parameter '{name}' cannot be omitted")
    unhandled = [p.name for p in endpoint.parameters if p.name not in targets]
    if unhandled:
        errors.append(
            f"{where}: endpoint parameters {unhandled} are not handled; pass each from an "
            "argument, or list it in omitted with the reason the agent does not need it"
        )

    guaranteed = set(route.constants)
    for argument, target in route.arguments.items():
        if argument in route.requires or params[argument].required:
            guaranteed.add(target)
        if target in by_name:
            errors.extend(_check_types(where, params[argument], by_name[target]))
    errors.extend(_check_constants(where, route, by_name))

    missing = sorted(
        p.name
        for p in endpoint.parameters
        if p.required and p.name not in guaranteed and p.name not in route.omitted
    )
    if missing:
        errors.append(
            f"{where}: required endpoint parameters {missing} are not always filled; "
            "map them from arguments the route requires, from required arguments, or set constants"
        )
    return errors


def _check_constants(where: str, route: Route, by_name: dict[str, Parameter]) -> list[str]:
    errors: list[str] = []
    for name, value in route.constants.items():
        if name not in by_name:
            continue  # reported as an unknown parameter
        target = by_name[name]
        if not matches_type(value, target.type):
            errors.append(
                f"{where}: constant {name}={value!r} is not a JSON {target.type}, "
                f"which endpoint parameter '{name}' expects"
            )
        elif target.enum is not None and value not in target.enum:
            errors.append(f"{where}: constant {name}={value!r} is not in the endpoint's enum")
    return errors


def matches_type(value: object, type_: ParamType) -> bool:
    """Whether a JSON value has the type an endpoint parameter declares."""
    match type_:
        case ParamType.STRING:
            return isinstance(value, str)
        case ParamType.BOOLEAN:
            return isinstance(value, bool)
        case ParamType.INTEGER:
            return isinstance(value, int) and not isinstance(value, bool)
        case ParamType.NUMBER:
            return isinstance(value, int | float) and not isinstance(value, bool)
        case ParamType.ARRAY:
            return isinstance(value, list)
        case ParamType.OBJECT:
            return isinstance(value, dict)


def _check_types(where: str, argument: ToolParameter, target: Parameter) -> list[str]:
    errors: list[str] = []
    compatible = argument.type is target.type or (
        argument.type is ParamType.INTEGER and target.type is ParamType.NUMBER
    )
    if not compatible:
        errors.append(
            f"{where}: argument '{argument.name}' is {argument.type}, "
            f"but endpoint parameter '{target.name}' is {target.type}"
        )
    if target.enum is not None:
        if argument.enum is None:
            errors.append(
                f"{where}: endpoint parameter '{target.name}' only accepts {target.enum}; "
                f"give argument '{argument.name}' that enum"
            )
        elif extra := [v for v in argument.enum if v not in target.enum]:
            errors.append(
                f"{where}: argument '{argument.name}' allows {extra}, "
                f"which endpoint parameter '{target.name}' does not accept"
            )
    return errors

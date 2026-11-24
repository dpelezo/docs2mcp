"""Turn Claude's flat output into a strict ApiSpec.

Some fixes are safe to make in code, and each one is recorded in `notes` so the
report shows exactly what changed. Anything ambiguous is left for validation, and the
extractor sends those errors back to Claude instead of guessing.

Safe fixes:
- absolute URLs and the base URL's path prefix are stripped from paths;
- `:id` and `<id>` become `{id}`, and placeholder names become identifiers;
- a path placeholder with no matching parameter gets a required string parameter;
- a required parameter with a default becomes optional;
- a default outside its allowed values is dropped;
- duplicate endpoints are merged, and repeated operation names get a suffix.

One transformation is not a repair: a route Claude flags as working for every
resource (`GET /{endpoint}`) is expanded into one route per resource the docs
document (`GET /pokemon`, `GET /ability`, ...). See ADR 0006.
"""

import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from docs2mcp.extract.wire import WireAuth, WireEndpoint, WireParameter
from docs2mcp.ingest.models import Chunk
from docs2mcp.spec import (
    ApiSpec,
    AuthScheme,
    AuthType,
    Endpoint,
    Parameter,
    ParamLocation,
    ParamType,
    ResponseShape,
)

_COLON = re.compile(r"(?<=/):([A-Za-z_][A-Za-z0-9_]*)")
_ANGLE = re.compile(r"<([^<>/]+)>")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_NOT_IDENT = re.compile(r"[^a-z0-9_]+")


def identifier(text: str) -> str:
    """snake_case identifier: 'id or name' -> 'id_or_name', 'GetPokemon' -> 'getpokemon'."""
    ident = _NOT_IDENT.sub("_", text.strip().lower()).strip("_")
    return ident if ident and not ident[0].isdigit() else f"p_{ident}"


def normalize_base_url(url: str) -> str | None:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def choose_base_url(candidates: Iterable[str], override: str | None = None) -> str:
    """The override if given, otherwise the most frequent candidate (first seen on ties)."""
    if override:
        chosen = normalize_base_url(override)
        if chosen is None:
            msg = f"--base-url must be an http(s) URL, got {override!r}"
            raise ValueError(msg)
        return chosen
    counts = Counter(u for u in (normalize_base_url(c) for c in candidates) if u)
    if not counts:
        msg = "no base URL found in the documentation; pass one with --base-url"
        raise ValueError(msg)
    return counts.most_common(1)[0][0]


def normalize_path(path: str, base_url: str | None) -> str:
    raw = path.strip()
    if raw.startswith(("http://", "https://")):
        raw = urlsplit(raw).path
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    raw = _COLON.sub(r"{\1}", raw)
    raw = _ANGLE.sub(r"{\1}", raw)
    raw = _PLACEHOLDER.sub(lambda m: "{" + identifier(m.group(1)) + "}", raw)
    if not raw.startswith("/"):
        raw = "/" + raw
    raw = re.sub(r"/{2,}", "/", raw)
    base_path = urlsplit(base_url).path.rstrip("/") if base_url else ""
    if base_path and (raw == base_path or raw.startswith(base_path + "/")):
        raw = raw[len(base_path) :] or "/"
    return raw.rstrip("/") or "/"


def _parse_default(text: str) -> object:
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text.strip()


def _coerce_enum(values: Sequence[str], kind: ParamType) -> list[str | int | float] | None:
    if not values or kind in {ParamType.BOOLEAN, ParamType.ARRAY, ParamType.OBJECT}:
        return None
    result: list[str | int | float] = []
    for value in values:
        try:
            if kind is ParamType.INTEGER:
                result.append(int(value))
            elif kind is ParamType.NUMBER:
                result.append(float(value))
            else:
                result.append(value)
        except ValueError:
            result.append(value)
    return list(dict.fromkeys(result))


def to_parameter(wire: WireParameter, where: str, notes: list[str]) -> Parameter:
    location = ParamLocation(wire.location)
    kind = ParamType(wire.type)
    name = identifier(wire.name) if location is ParamLocation.PATH else wire.name.strip()
    default = _parse_default(wire.default_value)
    enum = _coerce_enum(wire.allowed_values, kind)
    required = wire.required or location is ParamLocation.PATH
    if location is ParamLocation.PATH and default is not None:
        notes.append(f"{where}: dropped default on path parameter '{name}'")
        default = None
    if required and default is not None:
        notes.append(f"{where}: '{name}' had a default, so it is optional, not required")
        required = False
    if enum is not None and default is not None and default not in enum:
        notes.append(f"{where}: dropped default {default!r} of '{name}', not in allowed values")
        default = None
    items_type = None if wire.items_type == "none" else ParamType(wire.items_type)
    return Parameter(
        name=name,
        location=location,
        type=kind,
        required=required,
        description=wire.description.strip(),
        default=default,  # pyright: ignore[reportArgumentType]
        enum=enum,
        items_type=items_type if kind is ParamType.ARRAY else None,
    )


def to_endpoint(
    wire: WireEndpoint, base_url: str | None, chunks: dict[str, Chunk], notes: list[str]
) -> Endpoint:
    """Convert one endpoint. Raises ValidationError when Claude must fix it."""
    path = normalize_path(wire.path, base_url)
    where = f"{wire.method} {path}"
    parameters: list[Parameter] = []
    seen: set[tuple[ParamLocation, str]] = set()
    for wire_param in wire.parameters:
        param = to_parameter(wire_param, where, notes)
        if (param.location, param.name) in seen:
            notes.append(f"{where}: dropped repeated {param.location} parameter '{param.name}'")
            continue
        seen.add((param.location, param.name))
        parameters.append(param)

    declared = {p.name for p in parameters if p.location is ParamLocation.PATH}
    for placeholder in _PLACEHOLDER.findall(path):
        if placeholder not in declared:
            notes.append(f"{where}: added missing path parameter '{placeholder}'")
            parameters.append(
                Parameter(
                    name=placeholder,
                    location=ParamLocation.PATH,
                    type=ParamType.STRING,
                    required=True,
                )
            )
            declared.add(placeholder)

    chunk = chunks.get(wire.source_chunk_id)
    return Endpoint(
        id=identifier(wire.operation or f"{wire.method} {path}"),
        method=wire.method,  # pyright: ignore[reportArgumentType]
        path=path,
        summary=wire.summary.strip(),
        description=wire.description.strip(),
        parameters=parameters,
        response=ResponseShape(key_fields=wire.response_key_fields)
        if wire.response_key_fields
        else None,
        source_url=chunk.source_url if chunk else None,  # pyright: ignore[reportArgumentType]
    )


def describe_error(wire: WireEndpoint, error: ValidationError) -> str:
    problems = "; ".join(e["msg"].removeprefix("Value error, ") for e in error.errors())
    return f"{wire.method} {wire.path} ({wire.operation}): {problems}"


def _route_key(endpoint: Endpoint) -> tuple[str, str]:
    return (endpoint.method, _PLACEHOLDER.sub("{}", endpoint.path))


_TEMPLATE = re.compile(r"^/\{([^{}/]+)\}(/.*)?$")


def expand_resource_templates(
    endpoints: Sequence[Endpoint], templates: Sequence[Endpoint], expanded: list[str]
) -> list[Endpoint]:
    """Replace each resource template with one route per resource.

    The resources are the first path segments of the other endpoints that have
    more than one segment (`pokemon` from `/pokemon/{id}`). The template's first
    placeholder becomes the resource, and its path parameter is dropped; every
    other parameter is kept. Routes that already exist are not added again. A
    template that cannot be expanded is kept as documented, with a note.
    """
    result = list(endpoints)
    known = {_route_key(e) for e in result}
    resources: list[str] = []
    for endpoint in endpoints:
        segments = endpoint.path.strip("/").split("/")
        if len(segments) > 1 and "{" not in segments[0] and segments[0] not in resources:
            resources.append(segments[0])

    for template in templates:
        where = f"{template.method} {template.path}"
        match = _TEMPLATE.match(template.path)
        if match is None or not resources:
            reason = (
                "its path does not start with a placeholder"
                if match is None
                else ("no other endpoint names a resource")
            )
            expanded.append(f"{where}: kept as documented; {reason}")
            if _route_key(template) not in known:
                known.add(_route_key(template))
                result.append(template)
            continue
        placeholder, rest = match.group(1), match.group(2) or ""
        params = [
            p
            for p in template.parameters
            if not (p.location is ParamLocation.PATH and p.name == placeholder)
        ]
        added = 0
        for resource in resources:
            data = template.model_dump(mode="json") | {
                "id": identifier(f"{template.id} {resource}"),
                "path": f"/{resource}{rest}",
                "summary": f"{template.summary.rstrip('.')} ({resource}).",
                "parameters": [p.model_dump(mode="json") for p in params],
            }
            endpoint = Endpoint.model_validate(data)
            if _route_key(endpoint) in known:
                continue
            known.add(_route_key(endpoint))
            result.append(endpoint)
            added += 1
        expanded.append(f"{where}: expanded into {added} routes, one per resource")
    return result


def merge_endpoints(
    endpoints: Iterable[Endpoint], notes: list[str], merges: list[str]
) -> list[Endpoint]:
    """Merge duplicates (same method and route) and make ids unique.

    Merges are expected when a route is documented in several chunks, so they
    go to `merges`, not to the repair `notes`.
    """
    by_route: dict[tuple[str, str], Endpoint] = {}
    for endpoint in endpoints:
        key = _route_key(endpoint)
        existing = by_route.get(key)
        if existing is None:
            by_route[key] = endpoint
            continue
        known = {(p.location, p.name) for p in existing.parameters}
        extra = [
            p
            for p in endpoint.parameters
            if p.location is not ParamLocation.PATH and (p.location, p.name) not in known
        ]
        description = max(existing.description, endpoint.description, key=len)
        by_route[key] = existing.model_copy(
            update={"parameters": [*existing.parameters, *extra], "description": description}
        )
        merges.append(f"{existing.method} {existing.path}: merged a duplicate from another chunk")

    unique: list[Endpoint] = []
    used: set[str] = set()
    for original in by_route.values():
        new_id, n = original.id, 2
        while new_id in used:
            new_id, n = f"{original.id}_{n}", n + 1
        endpoint = original
        if new_id != original.id:
            notes.append(f"{original.method} {original.path}: renamed id to '{new_id}'")
            endpoint = original.model_copy(update={"id": new_id})
        used.add(new_id)
        unique.append(endpoint)
    return unique


def choose_auth(candidates: Iterable[WireAuth], notes: list[str]) -> AuthScheme:
    """The most common non-'none' scheme, or none."""
    found = [a for a in candidates if a.type != "none"]
    if not found:
        return AuthScheme()
    top = Counter((a.type, a.location, a.name) for a in found).most_common(1)[0][0]
    auth = next(a for a in found if (a.type, a.location, a.name) == top)
    if auth.type == "api_key" and (auth.location == "none" or not auth.name):
        notes.append("auth: api_key without a header or query name; recorded as none")
        return AuthScheme()
    return AuthScheme(
        type=AuthType(auth.type),
        location=auth.location if auth.location != "none" else None,
        name=auth.name or None,
    )


def build_spec(
    *,
    name: str,
    base_url: str,
    docs_url: str | None,
    auth: AuthScheme,
    endpoints: Sequence[Endpoint],
) -> ApiSpec:
    return ApiSpec(
        name=name,
        base_url=base_url,  # pyright: ignore[reportArgumentType]
        docs_url=docs_url,  # pyright: ignore[reportArgumentType]
        auth=auth,
        endpoints=list(endpoints),
    )

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
- duplicate endpoints are merged, and repeated operation names get a suffix; a
  parameter stays required only if every merged copy requires it;
- a value the docs cite as `name=value` is added to that parameter's enum, and a
  numeric or boolean parameter described as comma-separated becomes an array.

Endpoints that Claude reports as served from a different base URL (another host,
or another API version) are out of scope for this spec and are dropped with a
note. See ADR 0007.

When Claude reports that an endpoint accepts the same parameters as another
(`same_parameters_as`), the other endpoint's query and header parameters are
copied onto it. See ADR 0008.

One transformation is not a repair: a route Claude flags as working for every
resource (`GET /{endpoint}`) is expanded into one route per resource the docs
document (`GET /pokemon`, `GET /ability`, ...). See ADR 0006.
"""

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
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


def _site(url: str) -> tuple[str, str] | None:
    """Host and path of an http(s) URL, ignoring scheme, `www.` and trailing slashes."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower().removeprefix("www.")
    return host, parts.path.rstrip("/")


def outside_base(served_from: str, path: str, base_url: str) -> bool:
    """Whether the endpoint's full URL is not under `base_url`.

    The full URL is `path` if it is absolute, otherwise `served_from` + `path`.
    An empty or unparsable `served_from` counts as the base URL itself.
    """
    base = _site(base_url)
    if base is None:
        return False
    raw = path.strip()
    if raw.startswith(("http://", "https://")):
        target = _site(raw)
    else:
        origin = _site(served_from) if served_from.strip() else None
        if origin is None:
            return False
        target = (origin[0], origin[1] + "/" + raw.lstrip("/"))
    if target is None:
        return False
    host, full_path = target
    base_host, base_path = base
    within = full_path == base_path or full_path.startswith(base_path + "/") or not base_path
    return host != base_host or not within


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


def route_key(endpoint: Endpoint) -> tuple[str, str]:
    """Method and path with placeholder names removed: what makes two routes the same."""
    return _route_key(endpoint)


def apply_shared_parameters(
    endpoints: Sequence[Endpoint],
    shares: Mapping[tuple[str, str], Sequence[str]],
    base_url: str,
    copied: list[str],
) -> list[Endpoint]:
    """Copy query and header parameters from the endpoints each one says it shares.

    `shares` maps an endpoint's route key to the paths the docs say it shares
    parameters with. A path is matched against the other endpoints by method and
    route, then by route alone. Parameters the endpoint already has are kept;
    path parameters are never copied. Sources are read as extracted, so a chain
    (A shares B, B shares C) copies one level.
    """
    by_route: dict[tuple[str, str], Endpoint] = {_route_key(e): e for e in endpoints}
    by_path: dict[str, Endpoint] = {}
    for endpoint in endpoints:
        by_path.setdefault(_route_key(endpoint)[1], endpoint)

    result: list[Endpoint] = []
    for endpoint in endpoints:
        key = _route_key(endpoint)
        where = f"{endpoint.method} {endpoint.path}"
        params = list(endpoint.parameters)
        have = {(p.location, p.name) for p in params}
        for ref in shares.get(key, ()):
            ref_path = _PLACEHOLDER.sub("{}", normalize_path(ref, base_url))
            source = by_route.get((endpoint.method, ref_path)) or by_path.get(ref_path)
            if source is None or _route_key(source) == key:
                copied.append(f"{where}: same parameters as {ref}, but no such endpoint was found")
                continue
            new = [
                p
                for p in source.parameters
                if p.location is not ParamLocation.PATH and (p.location, p.name) not in have
            ]
            have.update((p.location, p.name) for p in new)
            params.extend(new)
            names = ", ".join(p.name for p in new) or "nothing new"
            copied.append(f"{where}: copied {names} from {source.method} {source.path}")
        changed = len(params) != len(endpoint.parameters)
        result.append(endpoint.model_copy(update={"parameters": params}) if changed else endpoint)
    return result


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
    """Merge endpoints on the same method and route, and make ids unique.

    Merges are expected when a route is documented in several chunks, so they go to
    `merges`, not to the repair `notes`. One route can also serve several operations,
    such as `GET /postcodes?q=` and `GET /postcodes?lon=&lat=`. So a parameter stays
    required only if every merged copy requires it. When that relaxes a flag, the
    copies are different operations, so the other copies' summaries are added to the
    description; otherwise the longest description wins. Relaxed flags are repairs,
    so they go to `notes` (#36).
    """
    groups: dict[tuple[str, str], list[Endpoint]] = {}
    for endpoint in endpoints:
        groups.setdefault(_route_key(endpoint), []).append(endpoint)

    merged: list[Endpoint] = []
    for copies in groups.values():
        first = copies[0]
        if len(copies) > 1:
            first = _merge_copies(copies, notes)
            merges.extend(
                f"{first.method} {first.path}: merged a duplicate from another chunk"
                for _ in copies[1:]
            )
        merged.append(first)

    unique: list[Endpoint] = []
    used: set[str] = set()
    for original in merged:
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


def _merge_copies(copies: Sequence[Endpoint], notes: list[str]) -> Endpoint:
    first = copies[0]
    parameters: list[Parameter] = []
    seen: set[tuple[ParamLocation, str]] = set()
    for copy in copies:
        for param in copy.parameters:
            key = (param.location, param.name)
            if key in seen or (param.location is ParamLocation.PATH and copy is not first):
                continue
            seen.add(key)
            parameters.append(param)

    relaxed: list[str] = []
    for index, param in enumerate(parameters):
        if param.location is ParamLocation.PATH or not param.required:
            continue
        if not all(_requires(copy, param) for copy in copies):
            parameters[index] = param.model_copy(update={"required": False})
            relaxed.append(param.name)
    if relaxed:
        notes.append(
            f"{first.method} {first.path}: {relaxed} made optional, because not every "
            "operation merged on this route requires them"
        )
    description = (
        _merged_description(copies)
        if relaxed
        else max((copy.description for copy in copies), key=len)
    )
    return first.model_copy(update={"parameters": parameters, "description": description})


def _requires(endpoint: Endpoint, param: Parameter) -> bool:
    return any(
        p.location is param.location and p.name == param.name and p.required
        for p in endpoint.parameters
    )


def _merged_description(copies: Sequence[Endpoint]) -> str:
    """Every operation's text, for copies that are different operations on one route."""
    first = copies[0]
    parts = [first.description.strip()]
    for copy in copies[1:]:
        if copy.summary != first.summary:
            parts.append(f"Also: {copy.summary} {copy.description}".strip())
    return " ".join(part for part in parts if part)


def repair_cited_values(endpoints: Sequence[Endpoint], notes: list[str]) -> list[Endpoint]:
    """Make each endpoint's text agree with its parameter enums and types (#36).

    - A value the docs cite as `name=value` for a string enum parameter is added to
      its enum when missing (`jscmd=details` with an enum of viewapi and data).
    - A number, integer or boolean parameter described as taking comma-separated
      values becomes an array of that type, and loses a scalar default.
    """
    repaired: list[Endpoint] = []
    for endpoint in endpoints:
        text = " ".join(
            [endpoint.summary, endpoint.description, *(p.description for p in endpoint.parameters)]
        )
        parameters: list[Parameter] = []
        for param in endpoint.parameters:
            parameters.append(_repair_parameter(endpoint, param, text, notes))
        repaired.append(
            endpoint
            if parameters == endpoint.parameters
            else endpoint.model_copy(update={"parameters": parameters})
        )
    return repaired


_SCALARS = (ParamType.NUMBER, ParamType.INTEGER, ParamType.BOOLEAN)
_COMMA_SEPARATED = re.compile(r"comma[\s-]*separated", re.IGNORECASE)


def _repair_parameter(
    endpoint: Endpoint, param: Parameter, text: str, notes: list[str]
) -> Parameter:
    where = f"{endpoint.method} {endpoint.path}: parameter '{param.name}'"
    if param.enum is not None and param.type is ParamType.STRING:
        cited = re.findall(rf"(?<![\w-]){re.escape(param.name)}=([A-Za-z0-9_-]+)", text)
        missing = [v for v in dict.fromkeys(cited) if v not in param.enum]
        if missing:
            notes.append(f"{where}: added {missing} to its allowed values, as the docs cite them")
            param = param.model_copy(update={"enum": [*param.enum, *missing]})
    if param.type in _SCALARS and _COMMA_SEPARATED.search(param.description):
        notes.append(
            f"{where}: {param.type} described as comma-separated, so it is now an array of "
            f"{param.type}"
        )
        update: dict[str, object] = {"type": ParamType.ARRAY, "items_type": param.type}
        if param.default is not None and not isinstance(param.default, list):
            update["default"] = None
        if param.enum is not None:
            update["enum"] = None
        param = param.model_copy(update=update)
    return param


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

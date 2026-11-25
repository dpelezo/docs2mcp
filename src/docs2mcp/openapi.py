"""Convert an OpenAPI 3 document into an `ApiSpec`, with no LLM.

This is how the agent eval builds condition A (ADR 0013): the tools other
generators would make from the API's own OpenAPI file. It keeps what the file
says and adds nothing: names, types, enums, defaults, descriptions and JSON
request body fields. A route or parameter the file leaves out stays missing.

Two mechanical fixes keep the generated code valid: a schema with no `type`
takes the type of its default or bounds (else string), and a default that does
not fit its type is converted when that is unambiguous (`"false"` for a
boolean) and dropped otherwise. Only local `$ref`s are followed. Cookie
parameters, non-JSON request bodies and bodies with no listed fields are
skipped, and every skip is reported.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlparse

from docs2mcp.spec import (
    ApiSpec,
    Endpoint,
    HttpMethod,
    Parameter,
    ParamLocation,
    ParamType,
    ResponseShape,
)

_METHODS = {m.value.lower(): m for m in HttpMethod}
_LOCATIONS = {
    "path": ParamLocation.PATH,
    "query": ParamLocation.QUERY,
    "header": ParamLocation.HEADER,
}
_TYPES = {t.value: t for t in ParamType}
_NOT_WORD = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

type Node = Mapping[str, Any]


class OpenApiError(ValueError):
    """The document cannot be converted."""


@dataclass
class Conversion:
    spec: ApiSpec
    skipped: list[str] = field(default_factory=list[str])
    """Parts of the file that did not become parameters, for the run report."""


def _map(value: object) -> Node:
    return cast("Node", value) if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _resolve(node: object, doc: Node) -> Node:
    current = _map(node)
    seen: set[str] = set()
    while "$ref" in current:
        ref = str(current["$ref"])
        if not ref.startswith("#/") or ref in seen:
            msg = f"cannot resolve $ref {ref!r}"
            raise OpenApiError(msg)
        seen.add(ref)
        target: object = doc
        for raw_part in ref[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if part not in _map(target):
                msg = f"cannot resolve $ref {ref!r}"
                raise OpenApiError(msg)
            target = _map(target)[part]
        current = _map(target)
    return current


def _sample_type(value: object) -> str | None:
    """The JSON type of a sample value, for a schema that names none."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return None


def _schema_type(schema: Node, doc: Node) -> ParamType:
    raw: object = schema.get("type")
    if isinstance(raw, list):
        named = [str(t) for t in _list(schema["type"]) if t != "null"]
        raw = named[0] if named else None
    if raw is None:
        for key in ("anyOf", "oneOf", "allOf"):
            for option in _list(schema.get(key)):
                resolved = _resolve(option, doc)
                if resolved.get("type") not in (None, "null"):
                    return _schema_type(resolved, doc)
    if raw is None and "enum" in schema:
        values = _list(schema["enum"])
        raw = "integer" if values and all(isinstance(v, int) for v in values) else "string"
    if raw is None:
        raw = _sample_type(schema.get("default", schema.get("minimum", schema.get("maximum"))))
    return _TYPES.get(str(raw), ParamType.STRING)


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return {"true": True, "false": False}.get(str(value).lower())


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, str) and value.lstrip("-").isdigit() else None


def _as_float(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    try:
        return float(str(value))
    except ValueError:
        return None


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else str(value)


_COERCE: dict[ParamType, Callable[[object], object]] = {
    ParamType.BOOLEAN: _as_bool,
    ParamType.INTEGER: _as_int,
    ParamType.NUMBER: _as_float,
    ParamType.STRING: _as_str,
}


def _default(schema: Node, kind: ParamType, enum: list[str | int | float] | None) -> Any:
    value = schema.get("default")
    if value is None:
        return None
    coerce = _COERCE.get(kind)
    fitted = coerce(value) if coerce is not None else value
    return None if enum is not None and fitted not in enum else fitted


def _parameter(
    name: str,
    location: ParamLocation,
    schema_node: object,
    doc: Node,
    *,
    required: bool,
    description: str,
) -> Parameter:
    schema = _resolve(schema_node, doc)
    kind = _schema_type(schema, doc)
    items_type = None
    if kind is ParamType.ARRAY and schema.get("items"):
        items_type = _schema_type(_resolve(schema["items"], doc), doc)
    scalars: list[str | int | float] = [
        v
        for v in _list(schema.get("enum"))
        if isinstance(v, str | int | float) and not isinstance(v, bool)
    ]
    enum = scalars or None
    return Parameter(
        name=name,
        location=location,
        type=kind,
        required=required,
        description=_text(description or schema.get("description")),
        default=None if required else _default(schema, kind, enum),
        enum=enum,
        items_type=items_type,
    )


def _snake(text: str) -> str:
    name = _NOT_WORD.sub("_", _CAMEL.sub("_", text).lower()).strip("_")
    return name if name and name[0].isalpha() else f"op_{name}"


def _relative_path(raw: str, server_path: str, base_path: str) -> str:
    full = server_path + raw
    if not (full == base_path or full.startswith(base_path + "/")):
        msg = f"{full} is not under the base path {base_path!r}"
        raise OpenApiError(msg)
    return full[len(base_path) :].rstrip("/") or "/"


def _path_and_query(
    item: Node, operation: Node, doc: Node, route: str, skipped: list[str]
) -> list[Parameter]:
    merged: dict[tuple[ParamLocation, str], Parameter] = {}
    for entry in [*_list(item.get("parameters")), *_list(operation.get("parameters"))]:
        param = _resolve(entry, doc)
        location = _LOCATIONS.get(str(param.get("in")))
        name = str(param.get("name", ""))
        if location is None:
            skipped.append(f"{route}: {param.get('in')} parameter '{name}'")
            continue
        merged[(location, name)] = _parameter(
            name,
            location,
            param.get("schema"),
            doc,
            required=location is ParamLocation.PATH or bool(param.get("required", False)),
            description=str(param.get("description") or ""),
        )
    return list(merged.values())


def _body(operation: Node, doc: Node, route: str, skipped: list[str]) -> list[Parameter]:
    if "requestBody" not in operation:
        skipped.append(f"{route}: the file describes no request body")
        return []
    content = _map(_resolve(operation["requestBody"], doc).get("content"))
    if "application/json" not in content:
        skipped.append(f"{route}: request body is not application/json ({sorted(content)})")
        return []
    schema = _resolve(_map(content["application/json"]).get("schema"), doc)
    properties = _map(schema.get("properties"))
    if not properties:
        skipped.append(f"{route}: the JSON request body lists no fields")
        return []
    required = {str(n) for n in _list(schema.get("required"))}
    return [
        _parameter(n, ParamLocation.BODY, p, doc, required=n in required, description="")
        for n, p in properties.items()
    ]


def _endpoint(
    key: str, path: str, item: Node, operation: Node, doc: Node, skipped: list[str]
) -> Endpoint:
    method = _METHODS[key]
    route = f"{method} {path}"
    parameters = _path_and_query(item, operation, doc, route, skipped)
    if method not in {HttpMethod.GET, HttpMethod.DELETE}:
        parameters += _body(operation, doc, route, skipped)
    ok = _map(_map(operation.get("responses")).get("200"))
    return Endpoint(
        id=_snake(str(operation.get("operationId") or f"{key}_{path}")),
        method=method,
        path=path,
        summary=_text(operation.get("summary") or operation.get("operationId") or route),
        description=_text(operation.get("description")),
        parameters=parameters,
        response=ResponseShape(description=_text(ok.get("description"))),
    )


def spec_from_openapi(doc: Node, *, name: str, base_url: str) -> Conversion:
    """Every operation in `doc` as an endpoint, with paths relative to `base_url`."""
    if "paths" not in doc:
        msg = "not an OpenAPI document (no 'paths')"
        raise OpenApiError(msg)
    servers = _list(doc.get("servers")) or [{"url": "/"}]
    server_path = urlparse(str(_map(servers[0]).get("url", "/"))).path.rstrip("/")
    base_path = urlparse(base_url).path.rstrip("/")
    skipped: list[str] = []
    endpoints: list[Endpoint] = []
    ids: set[str] = set()
    for raw_path, raw_item in _map(doc["paths"]).items():
        item = _resolve(raw_item, doc)
        path = _relative_path(str(raw_path), server_path, base_path)
        for key, operation in item.items():
            if key not in _METHODS:
                continue
            endpoint = _endpoint(key, path, item, _map(operation), doc, skipped)
            unique, n = endpoint.id, 2
            while unique in ids:
                unique, n = f"{endpoint.id}_{n}", n + 1
            ids.add(unique)
            endpoints.append(endpoint.model_copy(update={"id": unique}))
    spec = ApiSpec.model_validate(
        {
            "name": name,
            "base_url": base_url,
            "description": _text(_map(doc.get("info")).get("description")),
            "endpoints": endpoints,
        }
    )
    return Conversion(spec=spec, skipped=skipped)

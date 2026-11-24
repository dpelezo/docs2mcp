"""The ground truth an extracted spec is scored against.

A reference lists an API's real endpoints and their parameters, nothing else.
It is built from the API's own OpenAPI file where one exists (see
`scripts/reference_from_openapi.py`), or written by hand from the docs. Where
an official file leaves out routes or parameters that the docs document and
the server serves, `evals/extraction/additions/<id>.json` adds them, each with
its evidence, and the reference marks them with a `note`.
"""

import re
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from docs2mcp.spec import HttpMethod, ParamLocation

_PLACEHOLDER = re.compile(r"\{[^}]*\}")
_METHODS = {m.value.lower(): m for m in HttpMethod}
_LOCATIONS = {
    "path": ParamLocation.PATH,
    "query": ParamLocation.QUERY,
    "header": ParamLocation.HEADER,
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def route_key(method: str, path: str) -> str:
    """`GET /providers/{}/rates` for `GET /providers/{key}/rates/`."""
    return f"{method} {_PLACEHOLDER.sub('{}', path.rstrip('/') or '/')}"


class ReferenceParameter(_Model):
    name: str = Field(min_length=1)
    location: ParamLocation
    required: bool
    note: str | None = Field(
        default=None, description="Only on parameters added by hand: why, and the evidence."
    )


class ReferenceEndpoint(_Model):
    method: HttpMethod
    path: str = Field(pattern=r"^/", description="Relative to the reference's base_url.")
    parameters: list[ReferenceParameter] = Field(default_factory=list[ReferenceParameter])
    note: str | None = Field(
        default=None, description="Only on endpoints added by hand: why, and the evidence."
    )


class ReferenceSource(_Model):
    kind: Literal["openapi", "manual"]
    url: str
    version: str = ""
    sha256: str = Field(default="", description="Of the OpenAPI file this was built from.")
    retrieved: date
    note: str = ""


class Reference(_Model):
    api: str
    base_url: str
    source: ReferenceSource
    endpoints: list[ReferenceEndpoint] = Field(min_length=1)


class ParameterAddition(_Model):
    method: HttpMethod
    path: str = Field(pattern=r"^/")
    parameter: ReferenceParameter


class ReferenceAdditions(_Model):
    """Routes and parameters an official OpenAPI file leaves out.

    Every entry needs a `note` with its evidence (the docs section and the
    server code). An addition that the official file already covers is an
    error, so a fixed upstream file makes the addition visible for removal.
    """

    api: str
    reason: str = Field(min_length=1)
    routes: list[ReferenceEndpoint] = Field(default_factory=list[ReferenceEndpoint])
    parameters: list[ParameterAddition] = Field(default_factory=list[ParameterAddition])


class OpenApiError(ValueError):
    """The OpenAPI document uses something this converter does not handle."""


class AdditionError(ValueError):
    """A hand addition is missing its evidence or duplicates the official file."""


def apply_additions(
    endpoints: Iterable[ReferenceEndpoint], additions: ReferenceAdditions
) -> list[ReferenceEndpoint]:
    """Return the official endpoints with the hand additions applied."""
    result = list(endpoints)
    index = {route_key(e.method, e.path): i for i, e in enumerate(result)}

    for route in additions.routes:
        key = route_key(route.method, route.path)
        if not route.note:
            msg = f"added route {key} needs a note with its evidence"
            raise AdditionError(msg)
        if key in index:
            msg = f"{key} is already in the official file; remove the addition"
            raise AdditionError(msg)
        index[key] = len(result)
        result.append(route)

    for addition in additions.parameters:
        key = route_key(addition.method, addition.path)
        param = addition.parameter
        where = f"{key} {param.location}:{param.name}"
        if not param.note:
            msg = f"added parameter {where} needs a note with its evidence"
            raise AdditionError(msg)
        if key not in index:
            msg = f"cannot add {where}: the route is not in the reference"
            raise AdditionError(msg)
        endpoint = result[index[key]]
        if any((p.location, p.name) == (param.location, param.name) for p in endpoint.parameters):
            msg = f"{where} is already in the official file; remove the addition"
            raise AdditionError(msg)
        result[index[key]] = endpoint.model_copy(
            update={"parameters": [*endpoint.parameters, param]}
        )
    return result


def endpoints_from_openapi(doc: Mapping[str, Any], base_url: str) -> list[ReferenceEndpoint]:
    """List the operations of an OpenAPI 3 document, with paths relative to `base_url`.

    Path-level parameters apply to every operation under the path; an operation
    parameter with the same location and name overrides one. Local `$ref`s to
    `#/components/parameters/...` are resolved. Cookie parameters and request
    bodies are left out (none of the eval APIs uses them).
    """
    servers: list[Mapping[str, Any]] = doc.get("servers") or [{"url": "/"}]
    server_path = urlparse(str(servers[0]["url"])).path.rstrip("/")
    base_path = urlparse(base_url).path.rstrip("/")
    components: Mapping[str, Any] = doc.get("components", {}).get("parameters", {})

    endpoints: list[ReferenceEndpoint] = []
    paths: Mapping[str, Mapping[str, Any]] = doc.get("paths", {})
    for raw_path, item in paths.items():
        full = server_path + raw_path
        if not (full == base_path or full.startswith(base_path + "/")):
            msg = f"{full} is not under the base path {base_path!r}"
            raise OpenApiError(msg)
        path = full[len(base_path) :].rstrip("/") or "/"
        shared = _parameters(item.get("parameters", []), components)
        for key, operation in item.items():
            method = _METHODS.get(key)
            if method is None:
                continue
            own = _parameters(operation.get("parameters", []), components)
            merged = {(p.location, p.name): p for p in [*shared, *own]}
            endpoints.append(
                ReferenceEndpoint(method=method, path=path, parameters=list(merged.values()))
            )
    return endpoints


def _parameters(
    raw: list[Mapping[str, Any]], components: Mapping[str, Any]
) -> list[ReferenceParameter]:
    params: list[ReferenceParameter] = []
    for entry in raw:
        param = entry
        if "$ref" in entry:
            ref = str(entry["$ref"])
            prefix = "#/components/parameters/"
            if not ref.startswith(prefix) or ref[len(prefix) :] not in components:
                msg = f"cannot resolve parameter $ref {ref!r}"
                raise OpenApiError(msg)
            param = components[ref[len(prefix) :]]
        location = _LOCATIONS.get(str(param["in"]))
        if location is None:
            continue
        required = location is ParamLocation.PATH or bool(param.get("required", False))
        params.append(
            ReferenceParameter(name=str(param["name"]), location=location, required=required)
        )
    return params

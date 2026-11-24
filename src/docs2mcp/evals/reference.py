"""The ground truth an extracted spec is scored against.

A reference lists an API's real endpoints and their parameters, nothing else.
It is built from the API's own OpenAPI file where one exists (see
`scripts/reference_from_openapi.py`), or written by hand from the docs.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from docs2mcp.spec import HttpMethod, ParamLocation

_METHODS = {m.value.lower(): m for m in HttpMethod}
_LOCATIONS = {
    "path": ParamLocation.PATH,
    "query": ParamLocation.QUERY,
    "header": ParamLocation.HEADER,
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReferenceParameter(_Model):
    name: str = Field(min_length=1)
    location: ParamLocation
    required: bool


class ReferenceEndpoint(_Model):
    method: HttpMethod
    path: str = Field(pattern=r"^/", description="Relative to the reference's base_url.")
    parameters: list[ReferenceParameter] = Field(default_factory=list[ReferenceParameter])


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


class OpenApiError(ValueError):
    """The OpenAPI document uses something this converter does not handle."""


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

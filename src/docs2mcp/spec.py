"""The typed API spec.

Extraction produces an `ApiSpec` from documentation, and every later step
(design, generation, evals) reads it. The models are strict on purpose: a spec
that passes validation is internally consistent, so later steps can trust it.
See docs/adr/0002-api-spec-design.md.
"""

import json
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, model_validator

SPEC_VERSION = "1"

_PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ParamLocation(StrEnum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    BODY = "body"


class ParamType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class Parameter(_Model):
    """One input to an endpoint."""

    name: str = Field(min_length=1)
    location: ParamLocation
    type: ParamType
    required: bool = False
    description: str = ""
    default: JsonValue = None
    enum: list[str | int | float] | None = Field(default=None, min_length=1)
    items_type: ParamType | None = Field(
        default=None, description="Element type when `type` is `array`."
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.required and self.default is not None:
            msg = f"parameter '{self.name}' is required, so it cannot have a default"
            raise ValueError(msg)
        if self.enum is not None and self.default is not None and self.default not in self.enum:
            msg = f"parameter '{self.name}' has default {self.default!r}, which is not in its enum"
            raise ValueError(msg)
        if self.items_type is not None and self.type is not ParamType.ARRAY:
            msg = f"parameter '{self.name}' sets items_type, but its type is not 'array'"
            raise ValueError(msg)
        return self


class AuthType(StrEnum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    BASIC = "basic"


class AuthScheme(_Model):
    """How requests authenticate. The eval APIs all use `none`."""

    type: AuthType = AuthType.NONE
    location: Literal["header", "query"] | None = Field(
        default=None, description="Where an API key is sent. Required for `api_key`."
    )
    name: str | None = Field(
        default=None, description="Header or query parameter name. Required for `api_key`."
    )

    @model_validator(mode="after")
    def _check_api_key_fields(self) -> Self:
        if self.type is AuthType.API_KEY and (self.location is None or not self.name):
            msg = "api_key auth needs both 'location' and 'name'"
            raise ValueError(msg)
        return self


class ResponseShape(_Model):
    """What a successful response looks like, as far as the docs say."""

    content_type: str = "application/json"
    description: str = ""
    key_fields: list[str] = Field(
        default_factory=list[str],
        description="Dotted paths to the fields a caller is most likely to need.",
    )
    example: JsonValue = None


class Endpoint(_Model):
    """One HTTP operation."""

    id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Stable snake_case identifier, unique within the spec.",
    )
    method: HttpMethod
    path: str = Field(
        pattern=r"^/", description="Path relative to base_url, e.g. /v1/forecast/{city}."
    )
    summary: str = Field(min_length=1)
    description: str = ""
    parameters: list[Parameter] = Field(default_factory=list[Parameter])
    response: ResponseShape | None = None
    source_url: HttpUrl | None = Field(
        default=None, description="Documentation page this endpoint was extracted from."
    )

    @property
    def path_template_params(self) -> set[str]:
        return set(_PATH_PARAM.findall(self.path))

    @model_validator(mode="after")
    def _check_parameters(self) -> Self:
        seen: set[tuple[ParamLocation, str]] = set()
        for param in self.parameters:
            key = (param.location, param.name)
            if key in seen:
                msg = (
                    f"endpoint '{self.id}' declares {param.location} parameter '{param.name}' twice"
                )
                raise ValueError(msg)
            seen.add(key)

        declared = {p.name for p in self.parameters if p.location is ParamLocation.PATH}
        in_template = self.path_template_params
        if missing := sorted(in_template - declared):
            msg = f"endpoint '{self.id}': path placeholders {missing} have no path parameter"
            raise ValueError(msg)
        if extra := sorted(declared - in_template):
            msg = f"endpoint '{self.id}': path parameters {extra} are not in the path template"
            raise ValueError(msg)
        for param in self.parameters:
            if param.location is ParamLocation.PATH and not param.required:
                msg = f"endpoint '{self.id}': path parameter '{param.name}' must be required"
                raise ValueError(msg)

        body = [p for p in self.parameters if p.location is ParamLocation.BODY]
        if body and self.method in {HttpMethod.GET, HttpMethod.DELETE}:
            msg = f"endpoint '{self.id}': {self.method} requests cannot have body parameters"
            raise ValueError(msg)
        return self


class ApiSpec(_Model):
    """Everything extraction learned about one API."""

    spec_version: Literal["1"] = SPEC_VERSION
    name: str = Field(min_length=1)
    base_url: HttpUrl
    description: str = ""
    docs_url: HttpUrl | None = None
    auth: AuthScheme = Field(default_factory=AuthScheme)
    endpoints: list[Endpoint] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_endpoints_unique(self) -> Self:
        ids: set[str] = set()
        routes: set[tuple[HttpMethod, str]] = set()
        for endpoint in self.endpoints:
            if endpoint.id in ids:
                msg = f"endpoint id '{endpoint.id}' is used more than once"
                raise ValueError(msg)
            ids.add(endpoint.id)
            route = (endpoint.method, _normalise_path(endpoint.path))
            if route in routes:
                msg = f"{endpoint.method} {endpoint.path} is declared more than once"
                raise ValueError(msg)
            routes.add(route)
        return self


def _normalise_path(path: str) -> str:
    """Treat /pets/{id} and /pets/{pet_id} as the same route."""
    return _PATH_PARAM.sub("{}", path.rstrip("/") or "/")


def json_schema() -> str:
    """The JSON Schema for ApiSpec, as stable, pretty-printed JSON."""
    schema = ApiSpec.model_json_schema(mode="validation")
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"

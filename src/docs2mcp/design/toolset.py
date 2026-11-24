"""The tool set: what a generated MCP server exposes, and how each tool calls the API.

`design` writes a `ToolSet` to `tools.json`, a person reviews it, and `generate`
renders a server from it once it is approved. Each tool has one or more routes; a
call takes the first route whose required arguments are all present, and makes
exactly one API request (ADR 0011).

The models here check what can be checked from the file alone. Checks against
the API spec (coverage, buildable requests, types) are in `validate.py`.
"""

import json
import keyword
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, model_validator

from docs2mcp.spec import AuthScheme, ParamType

TOOLSET_VERSION = "1"
MAX_TOOL_DESCRIPTION = 1000  # characters, about 250 tokens: sent on every agent turn
MAX_PARAMETER_DESCRIPTION = 300

_IDENTIFIER = r"^[a-z][a-z0-9_]*$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _not_keyword(name: str, what: str) -> None:
    if keyword.iskeyword(name) or keyword.issoftkeyword(name):
        msg = f"{what} '{name}' is a Python keyword; generated code needs another name"
        raise ValueError(msg)


class ToolParameter(_Model):
    """One argument of a tool, as the agent sees it."""

    name: str = Field(pattern=_IDENTIFIER, max_length=64)
    type: ParamType
    required: bool = False
    description: str = Field(min_length=1, max_length=MAX_PARAMETER_DESCRIPTION)
    default: JsonValue = None
    enum: list[str | int | float] | None = Field(default=None, min_length=1)
    items_type: ParamType | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _not_keyword(self.name, "parameter")
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


class Route(_Model):
    """One API request a tool can make.

    `requires` lists the tool arguments that select this route: a call takes the
    first route whose `requires` are all present. `arguments` maps tool argument
    names to the endpoint's parameter names. `constants` sets endpoint parameters
    to fixed values (for example `format: "json"`). `omitted` lists the optional
    endpoint parameters the route deliberately does not send, each with a reason
    (for example an alias of another parameter). Every endpoint parameter is in
    exactly one of `arguments`, `constants` and `omitted`.
    """

    endpoint: str = Field(pattern=_IDENTIFIER, description="An endpoint id from the API spec.")
    requires: list[str] = Field(default_factory=list[str])
    arguments: dict[str, str] = Field(default_factory=dict[str, str])
    constants: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    omitted: dict[str, Annotated[str, Field(min_length=1)]] = Field(
        default_factory=dict[str, str],
        description="Endpoint parameter -> why the route does not send it.",
    )


class Tool(_Model):
    name: str = Field(pattern=_IDENTIFIER, max_length=64)
    description: str = Field(min_length=1, max_length=MAX_TOOL_DESCRIPTION)
    parameters: list[ToolParameter] = Field(default_factory=list[ToolParameter])
    routes: list[Route] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        _not_keyword(self.name, "tool")
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            msg = f"tool '{self.name}' declares a parameter twice"
            raise ValueError(msg)
        known = set(names)
        for route in self.routes:
            unknown = sorted((set(route.requires) | set(route.arguments)) - known)
            if unknown:
                msg = (
                    f"tool '{self.name}': route to '{route.endpoint}' uses unknown arguments "
                    f"{unknown}"
                )
                raise ValueError(msg)
            if not set(route.requires) <= set(route.arguments):
                msg = (
                    f"tool '{self.name}': route to '{route.endpoint}' requires arguments "
                    "it does not pass to the endpoint"
                )
                raise ValueError(msg)
        return self


class ExcludedEndpoint(_Model):
    endpoint: str = Field(pattern=_IDENTIFIER)
    reason: str = Field(min_length=1)


class ToolSet(_Model):
    """Everything `generate` needs to render one MCP server."""

    toolset_version: Literal["1"] = TOOLSET_VERSION
    api: str = Field(min_length=1)
    base_url: HttpUrl
    auth: AuthScheme = Field(default_factory=AuthScheme)
    design: Literal["direct", "task"] = Field(
        description="direct: one tool per endpoint, no LLM. task: grouped by Claude."
    )
    tools: list[Tool] = Field(min_length=1)
    excluded: list[ExcludedEndpoint] = Field(default_factory=list[ExcludedEndpoint])
    approved: bool = Field(
        default=False, description="Set to true by a person after review; generate refuses false."
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            msg = "tool names must be unique"
            raise ValueError(msg)
        return self


def json_schema() -> str:
    """The JSON Schema for tools.json, as stable, pretty-printed JSON."""
    schema = ToolSet.model_json_schema(mode="validation")
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"

"""The output schema Claude fills in when it designs tools (ADR 0012).

Like extraction's wire schema (ADR 0004), it is flat: every field is required and
there are no unions, so structured outputs can compile it. Claude decides only what
needs judgment: the grouping, names, descriptions, which arguments select which
route, and exclusions. Code fills in argument types, enums, defaults and array item
types from the endpoint parameters each argument fills (see convert.py).
"""

from pydantic import BaseModel, ConfigDict, Field


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WireArgument(_Wire):
    name: str = Field(description="snake_case argument name the agent passes, e.g. name_or_id.")
    description: str = Field(
        description=(
            "What value to pass, written for an AI agent: format, units or examples where they "
            "are not obvious. At most 300 characters."
        )
    )
    required: bool = Field(
        description="true only if every route needs it; route-selecting arguments are false."
    )


class WireMapping(_Wire):
    argument: str = Field(description="A tool argument name.")
    parameter: str = Field(description="The endpoint parameter it fills, exactly as in the spec.")


class WireConstant(_Wire):
    parameter: str = Field(description="An endpoint parameter, exactly as in the spec.")
    value_json: str = Field(description='The fixed value as a JSON literal, e.g. "\\"json\\"".')


class WireRoute(_Wire):
    endpoint: str = Field(description="The spec endpoint id this route calls.")
    requires: list[str] = Field(
        description=(
            "Arguments that select this route: it is chosen when all of them are given. Routes "
            "are tried in order, so put the route with the most requirements first and end "
            "with the route that requires least."
        )
    )
    arguments: list[WireMapping] = Field(
        description="Every tool argument this route sends, and the endpoint parameter it fills."
    )
    constants: list[WireConstant] = Field(
        description="Endpoint parameters this route always sends with a fixed value; usually empty."
    )


class WireTool(_Wire):
    name: str = Field(description="snake_case tool name, unique, at most 64 characters.")
    description: str = Field(
        description=(
            "For an AI agent: what the tool does, when to use it, and which arguments choose "
            "which behaviour. At most 1000 characters."
        )
    )
    arguments: list[WireArgument]
    routes: list[WireRoute] = Field(description="At least one route.")


class WireExclusion(_Wire):
    endpoint: str = Field(description="The spec endpoint id left out.")
    reason: str = Field(description="Why an agent does not need it, in one short sentence.")


class WireDesign(_Wire):
    """A whole tool design for one API."""

    tools: list[WireTool]
    excluded: list[WireExclusion] = Field(
        description="Endpoints deliberately left out, each with a reason; usually empty."
    )

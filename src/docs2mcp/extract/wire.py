"""The output schema Claude fills in during extraction.

Structured outputs compile the schema into a grammar, with limits on optional fields
(24) and union types (16) per request. ApiSpec has many optional fields and a recursive
JSON value type, so Claude gets this flat schema instead: every field is required,
there are no unions, and "none" or empty values stand in for missing ones. Code then
converts it into a strict ApiSpec (see merge.py). docs/adr/0004-extraction.md explains why.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WireParameter(_Wire):
    name: str = Field(description="Exact parameter name as written in the docs.")
    location: Literal["path", "query", "header", "body"]
    type: Literal["string", "integer", "number", "boolean", "array", "object"]
    required: bool
    description: str = Field(description="One short sentence, or empty.")
    default_value: str = Field(
        description='Default as a JSON literal, e.g. "10", "\\"celsius\\"", "true"; empty if none.'
    )
    allowed_values: list[str] = Field(
        description="Only values the docs list explicitly; empty otherwise."
    )
    items_type: Literal["none", "string", "integer", "number", "boolean", "object"] = Field(
        description='Element type when type is "array", otherwise "none".'
    )


class WireEndpoint(_Wire):
    operation: str = Field(description="Short snake_case name, e.g. get_pokemon.")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(
        description="Path with {placeholders} named like the path parameters, e.g. /pokemon/{name}."
    )
    summary: str = Field(description="What the endpoint does, in one short sentence.")
    description: str = Field(description="Extra detail an agent needs, or empty. Keep it short.")
    parameters: list[WireParameter]
    response_key_fields: list[str] = Field(
        description="Dotted paths of the most useful response fields; empty if not documented."
    )
    source_chunk_id: str = Field(description="Id of the chunk that documents this endpoint.")
    served_from: str = Field(
        description=(
            "The base URL the docs show this endpoint being called on: scheme, host and any "
            "version path before the endpoint path, e.g. https://api.example.com/v2. Use the "
            "API's base URL when the docs give only a relative path."
        )
    )
    same_parameters_as: list[str] = Field(
        description=(
            "Paths of other endpoints whose parameters the docs say this endpoint also accepts, "
            "as written in the docs (e.g. ['/v2/rates'] for 'supports the same parameters as "
            "/v2/rates'). Empty when the docs say nothing like that."
        )
    )
    for_every_resource: bool = Field(
        description=(
            "true only if the docs say this one route works for every resource of the API, with "
            "the resource name as its first path segment, e.g. GET /{endpoint}/ returning a list "
            "for any endpoint. false for every ordinary endpoint, including one whose first "
            "segment is a value such as a date or an id."
        )
    )


class WireAuth(_Wire):
    type: Literal["none", "api_key", "bearer", "basic"]
    location: Literal["none", "header", "query"]
    name: str = Field(description="Header or query parameter name for api_key; empty otherwise.")


class WireBatch(_Wire):
    """Everything Claude found in one batch of chunks."""

    api_name: str = Field(description="Name of the API, or empty if these chunks do not say.")
    base_urls: list[str] = Field(
        description="Base URLs of the API that appear in these chunks, e.g. https://api.x.dev/v2."
    )
    auth: WireAuth
    endpoints: list[WireEndpoint] = Field(
        description="Every HTTP endpoint documented in these chunks; empty if there are none."
    )

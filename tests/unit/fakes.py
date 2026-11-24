"""Test doubles for extraction: a scripted model and helpers to build wire objects."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from docs2mcp.extract.model import ModelOutputError, ModelReply, Turn
from docs2mcp.extract.wire import WireAuth, WireBatch, WireEndpoint, WireParameter
from docs2mcp.ingest.models import Chunk

NO_AUTH = WireAuth(type="none", location="none", name="")


def param(name: str, location: str = "query", **overrides: Any) -> WireParameter:
    data: dict[str, Any] = {
        "name": name,
        "location": location,
        "type": "string",
        "required": location == "path",
        "description": "",
        "default_value": "",
        "allowed_values": list[str](),
        "items_type": "none",
    } | overrides
    return WireParameter.model_validate(data)


def endpoint(
    operation: str, path: str, *params: WireParameter, method: str = "GET", **overrides: Any
) -> WireEndpoint:
    data: dict[str, Any] = {
        "operation": operation,
        "method": method,
        "path": path,
        "summary": f"{operation.replace('_', ' ').capitalize()}.",
        "description": "",
        "parameters": [p.model_dump() for p in params],
        "response_key_fields": [],
        "source_chunk_id": "p01-c001",
    } | overrides
    return WireEndpoint.model_validate(data)


def batch(
    *endpoints: WireEndpoint,
    base_urls: Sequence[str] = ("https://api.example.dev/v2",),
    api_name: str = "Example API",
    auth: WireAuth = NO_AUTH,
) -> WireBatch:
    return WireBatch(
        api_name=api_name, base_urls=list(base_urls), auth=auth, endpoints=list(endpoints)
    )


def chunk(chunk_id: str, text: str = "docs", tokens: int = 100, anchor: str | None = None) -> Chunk:
    return Chunk(
        id=chunk_id,
        url="https://example.dev/docs",
        anchor=anchor,
        heading_path=["Example API", chunk_id],
        markdown=text,
        tokens=tokens,
    )


Script = WireBatch | ModelOutputError | Callable[[Sequence[Turn]], WireBatch]


@dataclass
class ScriptedModel:
    """Returns the scripted replies in order and records every request."""

    replies: list[Script]
    name: str = "fake-model"
    calls: list[list[Turn]] = field(default_factory=list[list[Turn]])

    def extract(self, system: str, turns: Sequence[Turn]) -> ModelReply:
        assert system
        self.calls.append(list(turns))
        if not self.replies:
            msg = "the scripted model ran out of replies"
            raise AssertionError(msg)
        reply = self.replies.pop(0)
        if isinstance(reply, ModelOutputError):
            raise reply
        result = reply if isinstance(reply, WireBatch) else reply(turns)
        return ModelReply(batch=result, input_tokens=1000, output_tokens=200)

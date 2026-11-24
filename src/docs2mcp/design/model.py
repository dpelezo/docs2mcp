"""Calling Claude for tool design, plus record and replay (as in extraction, ADR 0004).

The request keys and the recording format are the same as extraction's, so the
same tools inspect both. Live calls cache the system prompt and the spec message:
a retry resends both, so it reads them from the cache.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from docs2mcp.design.wire import WireDesign
from docs2mcp.extract.model import ModelOutputError, Turn, request_key

_TIMEOUT_SECONDS = 900.0  # a large design can take minutes; the SDK requires a timeout this long


@dataclass(frozen=True)
class DesignReply:
    design: WireDesign
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class DesignModel(Protocol):
    name: str

    def design(self, system: str, turns: Sequence[Turn]) -> DesignReply: ...


class AnthropicDesignModel:
    """Claude via the official SDK, with the reply constrained to WireDesign."""

    def __init__(self, client: Any, model: str, max_tokens: int) -> None:
        self._client = client
        self.name = model
        self._max_tokens = max_tokens

    def design(self, system: str, turns: Sequence[Turn]) -> DesignReply:
        messages: list[dict[str, Any]] = []
        for index, turn in enumerate(turns):
            block: dict[str, Any] = {"type": "text", "text": turn.content}
            if index == 0:  # the spec: identical on every retry
                block["cache_control"] = {"type": "ephemeral"}
            messages.append({"role": turn.role, "content": [block]})
        response = self._client.messages.parse(
            model=self.name,
            max_tokens=self._max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_format=WireDesign,
            timeout=_TIMEOUT_SECONDS,
        )
        if response.stop_reason in {"max_tokens", "refusal"}:
            raise ModelOutputError(response.stop_reason, f"stop_reason={response.stop_reason}")
        parsed = response.parsed_output
        if not isinstance(parsed, WireDesign):
            raise ModelOutputError("invalid", "reply did not match the schema")
        usage = response.usage
        read = usage.cache_read_input_tokens or 0
        written = usage.cache_creation_input_tokens or 0
        return DesignReply(
            design=parsed,
            input_tokens=usage.input_tokens + read + written,
            output_tokens=usage.output_tokens,
            cache_read_tokens=read,
            cache_write_tokens=written,
        )


class RecordingDesignModel:
    """Pass requests to a real model and save each reply under `directory`."""

    def __init__(self, inner: DesignModel, directory: Path) -> None:
        self._inner = inner
        self._directory = directory
        self.name = inner.name

    def design(self, system: str, turns: Sequence[Turn]) -> DesignReply:
        reply = self._inner.design(system, turns)
        self._directory.mkdir(parents=True, exist_ok=True)
        record = {
            "model": self.name,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "cache_read_tokens": reply.cache_read_tokens,
            "cache_write_tokens": reply.cache_write_tokens,
            "output": reply.design.model_dump(mode="json"),
        }
        path = self._directory / f"{request_key(self.name, system, turns)}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return reply


class ReplayDesignModel:
    """Serve replies saved by RecordingDesignModel; never calls the API."""

    def __init__(self, directory: Path, name: str) -> None:
        self._directory = directory
        self.name = name

    def design(self, system: str, turns: Sequence[Turn]) -> DesignReply:
        key = request_key(self.name, system, turns)
        path = self._directory / f"{key}.json"
        if not path.exists():
            msg = (
                f"no recorded reply {key} in {self._directory}. The prompt, spec or model "
                "changed since recording; record again with --record."
            )
            raise FileNotFoundError(msg)
        record = json.loads(path.read_text(encoding="utf-8"))
        return DesignReply(
            design=WireDesign.model_validate(record["output"]),
            input_tokens=record["input_tokens"],
            output_tokens=record["output_tokens"],
            cache_read_tokens=record.get("cache_read_tokens", 0),
            cache_write_tokens=record.get("cache_write_tokens", 0),
        )

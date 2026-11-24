"""Calling Claude for extraction, plus record and replay for offline tests.

`ExtractionModel` is the only interface the extractor uses, so tests can pass a fake
and CI never calls the API. `RecordingModel` saves real replies; `ReplayModel` serves
them back by a hash of the request.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from docs2mcp.extract.wire import WireBatch

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Turn:
    role: Role
    content: str


@dataclass(frozen=True)
class ModelReply:
    """A parsed reply and its token use.

    `input_tokens` is the total input, cached or not, so totals stay comparable
    whether or not caching is on. `cache_read_tokens` and `cache_write_tokens` say
    how much of it was read from or written to the prompt cache.
    """

    batch: WireBatch
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ModelOutputError(RuntimeError):
    """The model stopped without a complete, valid answer."""

    def __init__(self, reason: Literal["max_tokens", "refusal", "invalid"], detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


class ExtractionModel(Protocol):
    name: str

    def extract(self, system: str, turns: Sequence[Turn]) -> ModelReply: ...


def request_key(model: str, system: str, turns: Sequence[Turn]) -> str:
    payload = json.dumps(
        {"model": model, "system": system, "turns": [[t.role, t.content] for t in turns]},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


class AnthropicModel:
    """Claude via the official SDK, with the reply constrained to WireBatch."""

    def __init__(
        self, client: Any, model: str, max_tokens: int, *, cache_prompt: bool = True
    ) -> None:
        self._client = client
        self.name = model
        self._max_tokens = max_tokens
        self._cache_prompt = cache_prompt

    def extract(self, system: str, turns: Sequence[Turn]) -> ModelReply:
        # The system prompt is the same on every call, so it is the cache breakpoint
        # (ADR 0009). Each batch's docs differ, so caching them would only cost more.
        block: dict[str, Any] = {"type": "text", "text": system}
        if self._cache_prompt:
            block["cache_control"] = {"type": "ephemeral"}
        response = self._client.messages.parse(
            model=self.name,
            max_tokens=self._max_tokens,
            system=[block],
            messages=[{"role": t.role, "content": t.content} for t in turns],
            output_format=WireBatch,
        )
        if response.stop_reason in {"max_tokens", "refusal"}:
            raise ModelOutputError(response.stop_reason, f"stop_reason={response.stop_reason}")
        parsed = response.parsed_output
        if not isinstance(parsed, WireBatch):
            raise ModelOutputError("invalid", "reply did not match the schema")
        usage = response.usage
        read = usage.cache_read_input_tokens or 0
        written = usage.cache_creation_input_tokens or 0
        return ModelReply(
            batch=parsed,
            input_tokens=usage.input_tokens + read + written,
            output_tokens=usage.output_tokens,
            cache_read_tokens=read,
            cache_write_tokens=written,
        )


class RecordingModel:
    """Pass requests to a real model and save each reply under `directory`."""

    def __init__(self, inner: ExtractionModel, directory: Path) -> None:
        self._inner = inner
        self._directory = directory
        self.name = inner.name

    def extract(self, system: str, turns: Sequence[Turn]) -> ModelReply:
        reply = self._inner.extract(system, turns)
        self._directory.mkdir(parents=True, exist_ok=True)
        record = {
            "model": self.name,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "cache_read_tokens": reply.cache_read_tokens,
            "cache_write_tokens": reply.cache_write_tokens,
            "output": reply.batch.model_dump(mode="json"),
        }
        path = self._directory / f"{request_key(self.name, system, turns)}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return reply


class ReplayModel:
    """Serve replies saved by RecordingModel; never calls the API."""

    def __init__(self, directory: Path, name: str) -> None:
        self._directory = directory
        self.name = name

    def extract(self, system: str, turns: Sequence[Turn]) -> ModelReply:
        key = request_key(self.name, system, turns)
        path = self._directory / f"{key}.json"
        if not path.exists():
            msg = (
                f"no recorded reply {key} in {self._directory}. The prompt, chunks or model "
                "changed since recording; record again with --record."
            )
            raise FileNotFoundError(msg)
        record = json.loads(path.read_text(encoding="utf-8"))
        return ModelReply(
            batch=WireBatch.model_validate(record["output"]),
            input_tokens=record["input_tokens"],
            output_tokens=record["output_tokens"],
            cache_read_tokens=record.get("cache_read_tokens", 0),
            cache_write_tokens=record.get("cache_write_tokens", 0),
        )

import json
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from docs2mcp.extract import (
    AnthropicModel,
    Extractor,
    ModelOutputError,
    ModelReply,
    RecordingModel,
    ReplayModel,
    Turn,
)
from docs2mcp.extract.wire import WireBatch
from tests.unit.fakes import ScriptedModel, batch, chunk, endpoint

TURNS = [Turn("user", "Record every endpoint.")]


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


def fake_client(
    stop_reason: str = "end_turn",
    parsed: object = None,
    cache_read: int | None = None,
    cache_write: int | None = None,
) -> Any:
    usage = SimpleNamespace(
        input_tokens=1234,
        output_tokens=56,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )
    response = SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=batch(endpoint("get_a", "/a")) if parsed is None else parsed,
        usage=usage,
    )
    return SimpleNamespace(messages=FakeMessages(response))


def test_wire_schema_stays_within_structured_output_limits() -> None:
    """No optional fields or unions, so the grammar compiles (limits: 24 and 16)."""
    schema = anthropic.transform_schema(WireBatch)
    text = json.dumps(schema)
    assert '"anyOf"' not in text
    for definition in [schema, *schema.get("$defs", {}).values()]:
        assert set(definition.get("properties", {})) == set(definition.get("required", []))


def test_anthropic_model_sends_the_schema_and_returns_usage() -> None:
    client = fake_client()
    reply = AnthropicModel(client, "claude-haiku-5-5", max_tokens=16000).extract("sys", TURNS)

    sent = client.messages.kwargs
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["max_tokens"] == 16000
    assert sent["output_format"] is WireBatch
    assert sent["messages"] == [{"role": "user", "content": "Record every endpoint."}]
    assert "temperature" not in sent
    assert sent["system"] == [
        {"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}
    ]
    assert "cache_control" not in sent  # no automatic breakpoint on the changing docs
    assert (reply.input_tokens, reply.output_tokens) == (1234, 56)
    assert (reply.cache_read_tokens, reply.cache_write_tokens) == (0, 0)
    assert reply.batch.endpoints[0].operation == "get_a"


def test_input_tokens_include_cached_tokens_and_report_them_apart() -> None:
    client = fake_client(cache_read=5000, cache_write=600)
    reply = AnthropicModel(client, "m", max_tokens=100).extract("sys", TURNS)
    assert reply.input_tokens == 1234 + 5000 + 600
    assert (reply.cache_read_tokens, reply.cache_write_tokens) == (5000, 600)


def test_prompt_caching_can_be_turned_off() -> None:
    client = fake_client()
    AnthropicModel(client, "m", max_tokens=100, cache_prompt=False).extract("sys", TURNS)
    assert client.messages.kwargs["system"] == [{"type": "text", "text": "sys"}]


@pytest.mark.parametrize(
    ("stop_reason", "parsed", "reason"),
    [("max_tokens", None, "max_tokens"), ("refusal", None, "refusal"), ("end_turn", {}, "invalid")],
)
def test_anthropic_model_raises_on_incomplete_replies(
    stop_reason: str, parsed: object, reason: str
) -> None:
    model = AnthropicModel(fake_client(stop_reason, parsed), "m", max_tokens=100)
    with pytest.raises(ModelOutputError) as error:
        model.extract("sys", TURNS)
    assert error.value.reason == reason


def test_recorded_replies_replay_without_the_api(tmp_path: Path) -> None:
    live = ScriptedModel([batch(endpoint("get_a", "/a"))], name="claude-haiku-5-5")
    recorded = RecordingModel(live, tmp_path).extract("sys", TURNS)

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["model"] == "claude-haiku-5-5"

    replayed = ReplayModel(tmp_path, "claude-haiku-5-5").extract("sys", TURNS)
    assert replayed == recorded


def test_replay_fails_clearly_when_the_request_changed(tmp_path: Path) -> None:
    RecordingModel(ScriptedModel([batch()], name="m"), tmp_path).extract("sys", TURNS)
    with pytest.raises(FileNotFoundError, match="record again with --record"):
        ReplayModel(tmp_path, "m").extract("a different prompt", TURNS)
    with pytest.raises(FileNotFoundError):
        ReplayModel(tmp_path, "another-model").extract("sys", TURNS)


class CachedModel:
    """Replies like a live model whose system prompt was read from the cache."""

    name = "claude-haiku-5-5"

    def extract(self, system: str, turns: Sequence[Turn]) -> ModelReply:
        assert system
        assert turns
        return ModelReply(
            batch(endpoint("get_a", "/a")),
            input_tokens=7000,
            output_tokens=100,
            cache_read_tokens=5000,
            cache_write_tokens=0,
        )


def test_cache_tokens_are_recorded_replayed_and_reported(tmp_path: Path) -> None:
    recorded = RecordingModel(CachedModel(), tmp_path).extract("sys", TURNS)
    replayed = ReplayModel(tmp_path, "claude-haiku-5-5").extract("sys", TURNS)
    assert replayed == recorded
    assert replayed.cache_read_tokens == 5000

    _, report = Extractor(CachedModel()).run([chunk("p01-c001")])
    assert (report.input_tokens, report.cache_read_tokens, report.cache_write_tokens) == (
        7000,
        5000,
        0,
    )


def test_recordings_made_before_caching_replay_with_zero_cache_tokens(tmp_path: Path) -> None:
    RecordingModel(ScriptedModel([batch(endpoint("get_a", "/a"))], name="m"), tmp_path).extract(
        "sys", TURNS
    )
    path = next(tmp_path.glob("*.json"))
    record = json.loads(path.read_text())
    del record["cache_read_tokens"], record["cache_write_tokens"]
    path.write_text(json.dumps(record))

    replayed = ReplayModel(tmp_path, "m").extract("sys", TURNS)
    assert (replayed.cache_read_tokens, replayed.cache_write_tokens) == (0, 0)

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from docs2mcp.extract import AnthropicModel, ModelOutputError, RecordingModel, ReplayModel, Turn
from docs2mcp.extract.wire import WireBatch
from tests.unit.fakes import ScriptedModel, batch, endpoint

TURNS = [Turn("user", "Record every endpoint.")]


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


def fake_client(stop_reason: str = "end_turn", parsed: object = None) -> Any:
    response = SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=batch(endpoint("get_a", "/a")) if parsed is None else parsed,
        usage=SimpleNamespace(input_tokens=1234, output_tokens=56),
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
    assert (reply.input_tokens, reply.output_tokens) == (1234, 56)
    assert reply.batch.endpoints[0].operation == "get_a"


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

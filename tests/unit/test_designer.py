"""The task-shaped design: conversion, retries, repair, the model and the CLI (ADR 0012)."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from docs2mcp.cli import main
from docs2mcp.design import (
    AnthropicDesignModel,
    Designer,
    DesignReply,
    RecordingDesignModel,
    ReplayDesignModel,
    WireDesign,
    convert,
    direct_design,
    repair,
    validate_toolset,
)
from docs2mcp.design.prompt import SYSTEM, spec_message
from docs2mcp.design.toolset import MAX_PARAMETER_DESCRIPTION, MAX_TOOL_DESCRIPTION
from docs2mcp.extract.model import ModelOutputError, Turn
from docs2mcp.spec import ApiSpec, ParamType
from tests.unit.test_design import SPEC

ROOT = Path(__file__).parents[2]


def wire_route(
    endpoint: str,
    arguments: dict[str, str] | None = None,
    requires: Sequence[str] = (),
    constants: dict[str, str] | None = None,
    omitted: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "requires": list(requires),
        "arguments": [{"argument": a, "parameter": p} for a, p in (arguments or {}).items()],
        "constants": [{"parameter": p, "value_json": v} for p, v in (constants or {}).items()],
        "omitted": [{"parameter": p, "reason": r} for p, r in (omitted or {}).items()],
    }


def wire_arg(name: str, description: str = "", *, required: bool = False) -> dict[str, Any]:
    return {"name": name, "description": description or f"The {name}.", "required": required}


def wire_tool(
    name: str, routes: list[dict[str, Any]], *arguments: dict[str, Any], description: str = ""
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description or f"Use {name}.",
        "arguments": list(arguments),
        "routes": routes,
    }


POKEMON = wire_tool(
    "pokemon",
    [
        wire_route("get_pokemon", {"name": "id_or_name"}, ["name"]),
        wire_route("list_pokemon", {"limit": "limit", "offset": "offset"}),
    ],
    wire_arg("name", "A Pokémon name or id, e.g. pikachu or 25."),
    wire_arg("limit"),
    wire_arg("offset"),
)
SPRITE = wire_tool(
    "sprite",
    [wire_route("get_sprite", {"id": "id", "size": "size"})],
    wire_arg("id", required=True),
    wire_arg("size"),
)


def design(*tools: dict[str, Any], excluded: Sequence[tuple[str, str]] = ()) -> WireDesign:
    return WireDesign.model_validate(
        {
            "tools": list(tools),
            "excluded": [{"endpoint": e, "reason": r} for e, r in excluded],
        }
    )


@dataclass
class ScriptedDesigner:
    replies: list[WireDesign | ModelOutputError]
    name: str = "fake-model"
    calls: list[list[Turn]] = field(default_factory=list[list[Turn]])

    def design(self, system: str, turns: Sequence[Turn]) -> DesignReply:
        assert system == SYSTEM
        self.calls.append(list(turns))
        reply = self.replies.pop(0)
        if isinstance(reply, ModelOutputError):
            raise reply
        return DesignReply(design=reply, input_tokens=1000, output_tokens=200, cache_read_tokens=10)


# --- conversion ---------------------------------------------------------------------------


def test_a_good_design_converts_and_copies_types_from_the_spec() -> None:
    result = convert(design(POKEMON, SPRITE), SPEC)

    assert result.errors == []
    pokemon, sprite = result.tools
    by_name = {p.name: p for p in pokemon.parameters}
    assert by_name["name"].type is ParamType.STRING
    assert by_name["limit"].type is ParamType.NUMBER
    assert by_name["offset"].type is ParamType.INTEGER
    assert [r.requires for r in pokemon.routes] == [["name"], []]
    size = next(p for p in sprite.parameters if p.name == "size")
    assert size.enum == ["S", "M"]
    assert sprite.parameters[0].required


def test_names_are_made_safe_and_routes_follow_the_renames() -> None:
    tool = wire_tool(
        "Get-Pokemon",
        [wire_route("get_pokemon", {"idOrName": "id_or_name"}, ["idOrName"])],
        wire_arg("idOrName"),
    )
    result = convert(design(tool, excluded=[("list_pokemon", "x"), ("get_sprite", "x")]), SPEC)

    assert result.errors == []
    assert result.tools[0].name == "get_pokemon"
    assert result.tools[0].routes[0].requires == ["id_or_name"]
    assert "tool 'Get-Pokemon': renamed to 'get_pokemon'" in result.notes
    assert any("'idOrName' renamed to 'id_or_name'" in n for n in result.notes)


def test_defaults_are_copied_only_for_optional_arguments_with_one_default() -> None:
    spec = ApiSpec.model_validate(
        {
            "name": "Rates",
            "base_url": "https://api.example.dev",
            "endpoints": [
                {
                    "id": "latest",
                    "method": "GET",
                    "path": "/latest",
                    "summary": "Latest rates.",
                    "parameters": [
                        {"name": "base", "location": "query", "type": "string", "default": "EUR"},
                        {"name": "places", "location": "query", "type": "integer", "default": 4},
                    ],
                },
                {
                    "id": "on_date",
                    "method": "GET",
                    "path": "/{date}",
                    "summary": "Rates on a date.",
                    "parameters": [
                        {"name": "date", "location": "path", "type": "string", "required": True},
                        {"name": "base", "location": "query", "type": "string", "default": "USD"},
                        {"name": "places", "location": "query", "type": "integer", "default": 4},
                    ],
                },
            ],
        }
    )
    tool = wire_tool(
        "rates",
        [
            wire_route("on_date", {"date": "date", "base": "base", "places": "p"}, ["date"]),
            wire_route("latest", {"base": "base", "places": "places"}),
        ],
        wire_arg("date"),
        wire_arg("base"),
        wire_arg("places"),
    )
    tool["routes"][0]["arguments"][2]["parameter"] = "places"
    result = convert(design(tool), spec)

    assert result.errors == []
    by_name = {p.name: p for p in result.tools[0].parameters}
    assert by_name["base"].default is None  # EUR on one route, USD on the other
    assert by_name["places"].default == 4


def test_long_and_missing_descriptions_are_fixed_and_noted() -> None:
    tool = wire_tool(
        "sprite",
        [wire_route("get_sprite", {"id": "id"})],
        {"name": "id", "description": "  ", "required": True},
        description="x" * (MAX_TOOL_DESCRIPTION + 50),
    )
    long_arg = wire_arg("size", "y" * (MAX_PARAMETER_DESCRIPTION + 1))
    tool["arguments"].append(long_arg)
    tool["routes"][0]["arguments"].append({"argument": "size", "parameter": "size"})
    result = convert(design(POKEMON, tool), SPEC)

    assert result.errors == []
    sprite = result.tools[1]
    assert len(sprite.description) == MAX_TOOL_DESCRIPTION
    assert sprite.parameters[0].description == "The value of 'id'."
    assert len(sprite.parameters[1].description) == MAX_PARAMETER_DESCRIPTION
    assert len(result.notes) == 3


def test_model_messages_do_not_repeat_the_tool_name() -> None:
    twice = wire_tool(
        "sprite",
        [wire_route("get_sprite", {"id": "id"})],
        wire_arg("id", required=True),
        wire_arg("id"),
    )
    errors = convert(design(POKEMON, twice), SPEC).errors
    assert "tool 'sprite': tool 'sprite'" not in " ".join(errors)


@pytest.mark.parametrize(
    ("tools", "excluded", "message"),
    [
        ((POKEMON, POKEMON, SPRITE), (), "tool 'pokemon' is defined more than once"),
        ((POKEMON,), (("get_sprite", " "),), "exclusion of 'get_sprite'"),
        ((POKEMON,), (), "'get_sprite' is neither in a route nor excluded"),
        (
            (
                POKEMON,
                wire_tool(
                    "sprite",
                    [wire_route("get_sprite", {"id": "id"}, constants={"size": "not json"})],
                    wire_arg("id", required=True),
                ),
            ),
            (),
            "constant 'size' is not a JSON literal",
        ),
        (
            (
                POKEMON,
                wire_tool(
                    "sprite",
                    [wire_route("get_sprite", {"id": "id"})],
                    wire_arg("id", required=True),
                    wire_arg("id"),
                ),
            ),
            (),
            "declares a parameter twice",
        ),
        (
            (
                POKEMON,
                wire_tool(
                    "sprite",
                    [wire_route("get_sprite", {"id": "id"}), wire_route("Bad Id!")],
                    wire_arg("id", required=True),
                ),
            ),
            (),
            "route to 'Bad Id!': endpoint",
        ),
        (
            (POKEMON, wire_tool("sprite", [wire_route("get_sprite")], description=" ")),
            (),
            "tool 'sprite': description",
        ),
        ((), (), "no tool could be built"),
    ],
)
def test_conversion_reports_problems(
    tools: tuple[dict[str, Any], ...], excluded: tuple[tuple[str, str], ...], message: str
) -> None:
    errors = convert(design(*tools, excluded=excluded), SPEC).errors
    assert any(message in e for e in errors), errors


def test_an_argument_mapped_twice_is_reported() -> None:
    tool = wire_tool(
        "sprite", [wire_route("get_sprite", {"id": "id"})], wire_arg("id", required=True)
    )
    tool["routes"][0]["arguments"].append({"argument": "id", "parameter": "size"})
    errors = convert(design(POKEMON, tool), SPEC).errors
    assert any("argument 'id' is mapped more than once" in e for e in errors)


def test_a_keyword_argument_name_is_renamed_not_rejected() -> None:
    tool = wire_tool(
        "sprite",
        [wire_route("get_sprite", {"from": "id"}, omitted={"size": "one size is enough"})],
        wire_arg("from", required=True),
    )
    result = convert(design(POKEMON, tool), SPEC)
    assert result.errors == []
    assert result.tools[1].parameters[0].name == "from_"


# --- repair -------------------------------------------------------------------------------


def test_repair_keeps_sound_tools_and_adds_direct_tools_for_the_rest() -> None:
    broken = wire_tool("sprite", [wire_route("get_sprite", {"size": "size"})], wire_arg("size"))
    clash = wire_tool("get_sprite", [wire_route("no_such_endpoint")])
    reuse = wire_tool("more", [wire_route("list_pokemon")])
    conversion = convert(
        design(POKEMON, broken, clash, reuse, excluded=[("list_pokemon", "dup"), ("ghost", "x")]),
        SPEC,
    )
    assert conversion.errors

    toolset, repairs = repair(conversion, SPEC)

    assert validate_toolset(toolset, SPEC) == []
    assert [t.name for t in toolset.tools] == ["pokemon", "get_sprite"]
    assert [d.split(":")[0] for d in repairs.dropped_tools] == ["sprite", "get_sprite", "more"]
    assert repairs.dropped_exclusions == ["list_pokemon", "ghost"]
    assert repairs.added_direct == ["get_sprite"]


def test_omissions_pass_through_to_the_route() -> None:
    lister = wire_tool(
        "pokemon_list",
        [wire_route("list_pokemon", {"limit": "limit"}, omitted={"offset": "first page only"})],
        wire_arg("limit"),
    )
    getter = wire_tool(
        "pokemon",
        [wire_route("get_pokemon", {"name": "id_or_name"})],
        wire_arg("name", required=True),
    )
    result = convert(design(getter, lister, SPRITE), SPEC)

    assert result.errors == []
    assert result.tools[1].routes[0].omitted == {"offset": "first page only"}


def test_repair_adds_arguments_for_unhandled_optional_parameters() -> None:
    lister = wire_tool("pokemon", POKEMON["routes"][:1], wire_arg("name"))
    lister["routes"] = [
        wire_route("get_pokemon", {"name": "id_or_name"}, ["name"]),
        wire_route("list_pokemon"),
    ]
    conversion = convert(design(lister, SPRITE), SPEC)
    assert any("['limit', 'offset'] are not handled" in e for e in conversion.errors)

    toolset, repairs = repair(conversion, SPEC)

    assert validate_toolset(toolset, SPEC) == []
    assert [p.name for p in toolset.tools[0].parameters] == ["name", "limit", "offset"]
    assert toolset.tools[0].routes[1].arguments == {"limit": "limit", "offset": "offset"}
    assert repairs.added_arguments == [
        "pokemon.limit -> list_pokemon.limit",
        "pokemon.offset -> list_pokemon.offset",
    ]
    assert repairs.dropped_tools == []
    assert repairs.added_direct == []


def test_an_added_argument_gets_a_free_name() -> None:
    tool = wire_tool(
        "pokemon",
        [
            wire_route("get_pokemon", {"limit": "id_or_name"}, ["limit"]),
            wire_route("list_pokemon", omitted={"offset": "x"}),
        ],
        wire_arg("limit"),
    )
    toolset, repairs = repair(convert(design(tool, SPRITE), SPEC), SPEC)

    assert repairs.added_arguments == ["pokemon.limit_2 -> list_pokemon.limit"]
    assert validate_toolset(toolset, SPEC) == []


def test_a_direct_tool_never_takes_a_kept_tool_name() -> None:
    named_like_direct = {**POKEMON, "name": "get_sprite"}
    toolset, repairs = repair(convert(design(named_like_direct), SPEC), SPEC)

    assert [t.name for t in toolset.tools] == ["get_sprite", "get_sprite_2"]
    assert repairs.added_direct == ["get_sprite"]
    assert validate_toolset(toolset, SPEC) == []


# --- the designer -------------------------------------------------------------------------


def test_a_valid_first_reply_needs_one_call() -> None:
    model = ScriptedDesigner([design(POKEMON, SPRITE)])
    toolset, report = Designer(model).run(SPEC)

    assert toolset.design == "task"
    assert not toolset.approved
    assert [t.name for t in toolset.tools] == ["pokemon", "sprite"]
    assert (report.calls, report.input_tokens, report.output_tokens) == (1, 1000, 200)
    assert report.cache_read_tokens == 10
    assert report.cache_write_tokens == 0
    assert model.calls[0] == [Turn("user", spec_message(SPEC))]


def test_problems_are_fed_back_and_fixed() -> None:
    model = ScriptedDesigner([design(POKEMON), design(POKEMON, SPRITE)])
    toolset, report = Designer(model).run(SPEC)

    assert len(toolset.tools) == 2
    assert report.calls == 2
    assert report.attempts[0].problems
    assert report.attempts[1].problems == []
    feedback = model.calls[1][-1]
    assert feedback.role == "user"
    assert "'get_sprite' is neither in a route nor excluded" in feedback.content
    assert model.calls[1][1].role == "assistant"
    assert report.repairs.added_direct == []


def test_problems_left_after_the_last_retry_are_repaired() -> None:
    model = ScriptedDesigner([design(POKEMON)] * 2)
    toolset, report = Designer(model, max_retries=1).run(SPEC)

    assert report.calls == 2
    assert validate_toolset(toolset, SPEC) == []
    assert report.repairs.added_direct == ["get_sprite"]


def test_a_model_failure_is_not_hidden() -> None:
    model = ScriptedDesigner([ModelOutputError("max_tokens", "stop_reason=max_tokens")])
    with pytest.raises(ModelOutputError, match="max_tokens"):
        Designer(model).run(SPEC)


def test_spec_message_is_compact_and_complete() -> None:
    message = spec_message(SPEC)
    lines = message.splitlines()[2:]
    assert len(lines) == len(SPEC.endpoints)
    sprite = json.loads(lines[2])
    assert sprite["id"] == "get_sprite"
    assert sprite["parameters"][1] == {
        "name": "size",
        "in": "query",
        "type": "string",
        "enum": ["S", "M"],
    }
    assert "required" not in json.loads(lines[1])["parameters"][0]


# --- the model client ---------------------------------------------------------------------


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


def fake_client(stop_reason: str = "end_turn", parsed: object = None) -> Any:
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=900,
        cache_creation_input_tokens=None,
    )
    response = SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=design(POKEMON) if parsed is None else parsed,
        usage=usage,
    )
    return SimpleNamespace(messages=FakeMessages(response))


TURNS = [Turn("user", "spec"), Turn("assistant", "{}"), Turn("user", "fix it")]


def test_wire_schema_stays_within_structured_output_limits() -> None:
    schema = anthropic.transform_schema(WireDesign)
    assert '"anyOf"' not in json.dumps(schema)
    for definition in [schema, *schema.get("$defs", {}).values()]:
        assert set(definition.get("properties", {})) == set(definition.get("required", []))


def test_anthropic_design_model_caches_the_system_prompt_and_the_spec() -> None:
    client = fake_client()
    reply = AnthropicDesignModel(client, "claude-haiku-5-5", max_tokens=32000).design("sys", TURNS)

    sent = client.messages.kwargs
    assert sent["output_format"] is WireDesign
    assert sent["max_tokens"] == 32000
    assert sent["timeout"] > 600
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    cached = ["cache_control" in m["content"][0] for m in sent["messages"]]
    assert cached == [True, False, False]
    assert (reply.input_tokens, reply.output_tokens, reply.cache_read_tokens) == (1000, 50, 900)


@pytest.mark.parametrize(
    ("stop_reason", "parsed", "reason"),
    [
        ("max_tokens", None, "max_tokens"),
        ("refusal", None, "refusal"),
        ("end_turn", "x", "invalid"),
    ],
)
def test_anthropic_design_model_raises_on_incomplete_replies(
    stop_reason: str, parsed: object, reason: str
) -> None:
    model = AnthropicDesignModel(fake_client(stop_reason, parsed), "m", max_tokens=1000)
    with pytest.raises(ModelOutputError) as caught:
        model.design("sys", TURNS)
    assert caught.value.reason == reason


def test_record_then_replay(tmp_path: Path) -> None:
    inner = AnthropicDesignModel(fake_client(), "claude-haiku-5-5", max_tokens=1000)
    recorded = RecordingDesignModel(inner, tmp_path).design("sys", TURNS)

    replayed = ReplayDesignModel(tmp_path, "claude-haiku-5-5").design("sys", TURNS)
    assert replayed == recorded

    with pytest.raises(FileNotFoundError, match="record again"):
        ReplayDesignModel(tmp_path, "claude-haiku-5-5").design("sys", TURNS[:1])


# --- the command --------------------------------------------------------------------------


def test_design_command_replays_a_recorded_design(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(SPEC.model_dump_json())
    recordings = tmp_path / "recordings"
    recorder = RecordingDesignModel(ScriptedDesigner([design(POKEMON)] * 3), recordings)
    Designer(recorder).run(SPEC)

    out = tmp_path / "tools" / "tools.json"
    args = ["design", str(spec_path), "--out", str(out), "--replay", str(recordings)]
    assert main([*args, "--model", "fake-model"]) == 0
    printed = capsys.readouterr().out
    assert "PokéAPI: 2 tools for 3 endpoints (task design, 0 excluded)" in printed
    assert "3 model calls, 3,000 input and 600 output tokens (fake-model)" in printed
    assert "added 0 arguments, dropped 0 tools and added 1 direct tools" in printed

    report = json.loads((out.parent / "design_report.json").read_text())
    assert report["repairs"]["added_direct"] == ["get_sprite"]
    assert len(report["attempts"]) == 3
    assert json.loads(out.read_text())["design"] == "task"


@pytest.mark.parametrize(
    "path", sorted((ROOT / "tests/fixtures/specs").glob("*.json")), ids=lambda p: p.stem
)
def test_the_direct_design_survives_the_wire_round_trip(path: Path) -> None:
    """A design that only names and maps arguments gets every type, enum and default right."""
    spec = ApiSpec.model_validate_json(path.read_text(encoding="utf-8"))
    tools = [
        wire_tool(
            t.name,
            [wire_route(r.endpoint, r.arguments, r.requires) for r in t.routes],
            *(wire_arg(p.name, p.description, required=p.required) for p in t.parameters),
            description=t.description,
        )
        for t in direct_design(spec).tools
    ]
    result = convert(design(*tools), spec)

    assert result.errors == []
    assert result.notes == []
    assert [t.model_dump() for t in result.tools] == [
        t.model_dump() for t in direct_design(spec).tools
    ]

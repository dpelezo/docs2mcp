from collections.abc import Sequence

import pytest

from docs2mcp.extract import Extractor, ModelOutputError, Turn, make_batches
from docs2mcp.extract.prompt import SYSTEM, batch_message, feedback_message
from docs2mcp.extract.wire import WireBatch
from tests.unit.fakes import ScriptedModel, batch, chunk, endpoint, param

CHUNKS = [chunk(f"p01-c00{i}", f"Section {i}", tokens=3000) for i in range(1, 5)]


def test_make_batches_respects_the_token_budget_and_order() -> None:
    sizes = [chunk(f"c{i}", tokens=t) for i, t in enumerate([100, 200, 300, 5000, 50])]
    batches = make_batches(sizes, max_tokens=400)
    assert [[c.id for c in b] for b in batches] == [["c0", "c1"], ["c2"], ["c3"], ["c4"]]


def test_prompts_carry_chunk_ids_sources_and_errors() -> None:
    message = batch_message([chunk("p01-c007", "## Berries", anchor="berries")])
    assert '<chunk id="p01-c007" source="https://example.dev/docs#berries">' in message
    assert "<heading_path>Example API > p01-c007</heading_path>" in message
    assert "## Berries" in message
    assert "- GET /x (x): broken" in feedback_message(["GET /x (x): broken"])
    assert "Never invent endpoints" in SYSTEM


def test_extracts_merges_and_reports_across_batches() -> None:
    model = ScriptedModel(
        [
            batch(endpoint("get_rates", "/rates", param("base"))),
            batch(endpoint("get_rates", "/v2/rates", param("symbols")), base_urls=[]),
        ]
    )
    spec, report = Extractor(model, batch_tokens=6000).run(CHUNKS, docs_url="https://x.dev/docs")

    assert len(model.calls) == 2  # 4 chunks of 3,000 tokens -> 2 batches
    assert spec.name == "Example API"
    assert str(spec.base_url) == "https://api.example.dev/v2"
    assert [(e.id, e.path) for e in spec.endpoints] == [("get_rates", "/rates")]
    assert [p.name for p in spec.endpoints[0].parameters] == ["base", "symbols"]
    assert (report.calls, report.input_tokens, report.output_tokens) == (2, 2000, 400)
    assert report.merged == ["GET /rates: merged a duplicate from another chunk"]
    assert report.rejected == []


def test_invalid_endpoints_go_back_to_the_model_with_the_errors() -> None:
    broken = endpoint("get_pokemon", "/pokemon", param("name", "path"))
    fixed = endpoint("get_pokemon", "/pokemon/{name}", param("name", "path"))
    keep = endpoint("list_pokemon", "/pokemon")

    def check_feedback(turns: Sequence[Turn]) -> WireBatch:
        assert [t.role for t in turns] == ["user", "assistant", "user"]
        assert "path parameters ['name'] are not in the path template" in turns[2].content
        return batch(fixed, keep)

    model = ScriptedModel([batch(broken, keep), check_feedback])
    spec, report = Extractor(model).run(CHUNKS[:1])

    assert {e.id for e in spec.endpoints} == {"get_pokemon", "list_pokemon"}
    assert report.batches[0].calls == 2
    assert report.rejected == []


def test_endpoints_still_invalid_after_retries_are_rejected_and_reported() -> None:
    broken = endpoint("get_pokemon", "/pokemon", param("name", "path"))
    keep = endpoint("list_pokemon", "/pokemon")
    model = ScriptedModel([batch(broken, keep) for _ in range(3)])

    spec, report = Extractor(model, max_retries=2).run(CHUNKS[:1])

    assert [e.id for e in spec.endpoints] == ["list_pokemon"]
    assert len(model.calls) == 3
    assert report.rejected == [
        "GET /pokemon (get_pokemon): endpoint 'get_pokemon': path parameters ['name'] "
        "are not in the path template"
    ]


def test_a_cut_off_reply_splits_the_batch_in_half() -> None:
    model = ScriptedModel(
        [
            ModelOutputError("max_tokens", "stop_reason=max_tokens"),
            batch(endpoint("get_a", "/a")),
            batch(endpoint("get_b", "/b")),
        ]
    )
    spec, report = Extractor(model, batch_tokens=10_000).run(CHUNKS[:2])

    assert [e.id for e in spec.endpoints] == ["get_a", "get_b"]
    assert [b.chunk_ids for b in report.batches] == [["p01-c001"], ["p01-c002"]]


def test_refusals_and_single_chunk_cut_offs_are_reported_not_raised() -> None:
    model = ScriptedModel(
        [
            ModelOutputError("refusal", "stop_reason=refusal"),
            ModelOutputError("max_tokens", "stop_reason=max_tokens"),
            batch(endpoint("get_c", "/c")),
        ]
    )
    spec, report = Extractor(model, batch_tokens=3000).run(CHUNKS[:3])

    assert [e.id for e in spec.endpoints] == ["get_c"]
    assert [b.error for b in report.failed_batches] == [
        "refusal: stop_reason=refusal",
        "max_tokens: stop_reason=max_tokens",
    ]


def test_name_and_base_url_overrides() -> None:
    model = ScriptedModel([batch(endpoint("get_a", "/v9/a"), base_urls=[], api_name="")])
    spec, _ = Extractor(model).run(CHUNKS[:1], name="Mine", base_url="https://api.mine.dev/v9")
    assert (spec.name, str(spec.base_url), spec.endpoints[0].path) == (
        "Mine",
        "https://api.mine.dev/v9",
        "/a",
    )


def test_fails_clearly_without_chunks_or_endpoints() -> None:
    with pytest.raises(ValueError, match="run docs2mcp ingest first"):
        Extractor(ScriptedModel([])).run([])
    with pytest.raises(ValueError, match="no valid endpoints"):
        Extractor(ScriptedModel([batch()])).run(CHUNKS[:1])


def test_flagged_resource_templates_are_expanded_once_and_reported() -> None:
    template = endpoint(
        "list_resources",
        "/{endpoint}",
        param("endpoint", "path"),
        param("limit"),
        for_every_resource=True,
    )
    model = ScriptedModel(
        [
            batch(template, endpoint("get_pokemon", "/pokemon/{id}", param("id", "path"))),
            batch(template, endpoint("get_berry", "/berry/{id}", param("id", "path"))),
        ]
    )

    spec, report = Extractor(model, batch_tokens=6000).run(CHUNKS)

    assert sorted(e.path for e in spec.endpoints) == [
        "/berry",
        "/berry/{id}",
        "/pokemon",
        "/pokemon/{id}",
    ]
    assert report.expanded == ["GET /{endpoint}: expanded into 2 routes, one per resource"]

"""Tracing: off unless configured, and spans for fetches, model calls and validation (#6)."""

import base64
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from docs2mcp import tracing
from docs2mcp.cli import main
from docs2mcp.config import Settings
from docs2mcp.design import Designer
from docs2mcp.extract import Extractor, ModelOutputError
from tests.unit.fakes import ScriptedModel, batch, chunk, endpoint, param
from tests.unit.test_design import SPEC
from tests.unit.test_designer import POKEMON, SPRITE, ScriptedDesigner, design
from tests.unit.test_ingest_crawl import SITE, Site, make_crawler

EXPORTER = InMemorySpanExporter()


@pytest.fixture(autouse=True)
def spans() -> Iterator[InMemorySpanExporter]:
    """One SDK provider for the test process (OpenTelemetry allows setting it once)."""
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
        trace.set_tracer_provider(provider)
    EXPORTER.clear()
    tracing.set_content(False)
    yield EXPORTER
    tracing.set_content(False)


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def named(exporter: InMemorySpanExporter, name: str) -> list[ReadableSpan]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def span_id(span: ReadableSpan) -> int:
    assert span.context is not None
    return span.context.span_id


def attrs(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


# --- turning tracing on ----------------------------------------------------------------


def test_tracing_is_off_without_an_exporter() -> None:
    assert tracing.exporter_for(settings(), environ={}) is None
    assert tracing.setup_tracing(settings(trace_content=True), environ={}) is None
    assert tracing.content_enabled()


def test_langfuse_keys_select_its_otlp_endpoint() -> None:
    keys = settings(
        LANGFUSE_PUBLIC_KEY="pk-lf-1",
        LANGFUSE_SECRET_KEY="sk-lf-2",  # noqa: S106 - a fake key
        LANGFUSE_HOST="https://us.cloud.langfuse.com/",
    )
    assert tracing.langfuse_target(keys) == (
        "https://us.cloud.langfuse.com/api/public/otel/v1/traces",
        {"Authorization": "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-2").decode()},
    )
    assert isinstance(tracing.exporter_for(keys, environ={}), OTLPSpanExporter)


def test_a_public_key_alone_is_not_enough() -> None:
    assert tracing.exporter_for(settings(LANGFUSE_PUBLIC_KEY="pk"), environ={}) is None


def test_any_otlp_endpoint_works_too() -> None:
    environ = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"}
    assert isinstance(tracing.exporter_for(settings(), environ=environ), OTLPSpanExporter)


def test_setup_installs_a_provider_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[object] = []
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    provider = tracing.setup_tracing(
        settings(LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk"),  # noqa: S106 - fake
        environ={},
    )
    assert provider is not None
    assert installed == [provider]
    assert provider.resource.attributes["service.name"] == "docs2mcp"
    provider.shutdown()


def test_the_secret_key_never_appears_in_settings_output() -> None:
    keys = settings(LANGFUSE_SECRET_KEY="sk-lf-secret")  # noqa: S106 - a fake key
    assert "sk-lf-secret" not in repr(keys)


# --- spans -----------------------------------------------------------------------------


def extractor_run(reply: Any) -> None:
    model = ScriptedModel([reply])
    Extractor(model).run([chunk("p01-c001")], base_url="https://api.example.dev/v2")


def test_extraction_spans_carry_token_usage_but_no_content(
    spans: InMemorySpanExporter,
) -> None:
    extractor_run(batch(endpoint("get_a", "/a", param("q"))))

    (call,) = named(spans, "chat fake-model")
    expected = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": "fake-model",
        "gen_ai.usage.input_tokens": 1000,
        "gen_ai.usage.output_tokens": 200,
        "docs2mcp.step": "extract",
    }
    assert expected.items() <= attrs(call).items()
    assert "gen_ai.prompt" not in attrs(call)
    assert "gen_ai.completion" not in attrs(call)
    (validate,) = named(spans, "extract.validate")
    assert attrs(validate) == {"docs2mcp.attempt": 0, "docs2mcp.valid": 1, "docs2mcp.problems": 0}
    (run,) = named(spans, "extract")
    assert attrs(run)["docs2mcp.endpoints"] == 1
    assert named(spans, "extract.batch")
    assert named(spans, "extract.merge")
    batch_span = named(spans, "extract.batch")[0]
    assert call.parent is not None
    assert call.parent.span_id == span_id(batch_span)


def test_content_is_added_only_when_asked(spans: InMemorySpanExporter) -> None:
    tracing.set_content(True)
    extractor_run(batch(endpoint("get_a", "/a")))

    (call,) = named(spans, "chat fake-model")
    assert '"role": "system"' in attrs(call)["gen_ai.prompt"]
    assert '"get_a"' in attrs(call)["gen_ai.completion"]


def test_a_failed_model_call_is_an_error_span(spans: InMemorySpanExporter) -> None:
    with pytest.raises(ValueError, match="no valid endpoints"):
        extractor_run(ModelOutputError("refusal", "stop_reason=refusal"))

    (call,) = named(spans, "chat fake-model")
    assert call.status.status_code is StatusCode.ERROR
    assert any(event.name == "exception" for event in call.events)


def test_design_spans(spans: InMemorySpanExporter) -> None:
    Designer(ScriptedDesigner([design(POKEMON)] * 2), max_retries=1).run(SPEC)

    assert len(named(spans, "chat fake-model")) == 2
    problems = [attrs(s)["docs2mcp.problems"] for s in named(spans, "design.validate")]
    assert problems[0] > 0
    (repair,) = named(spans, "design.repair")
    assert attrs(repair)["docs2mcp.added_direct"] == 1
    (run,) = named(spans, "design")
    assert attrs(run)["docs2mcp.model_calls"] == 2
    assert attrs(run)["gen_ai.usage.output_tokens"] == 400


def test_a_valid_design_has_no_repair_span(spans: InMemorySpanExporter) -> None:
    Designer(ScriptedDesigner([design(POKEMON, SPRITE)])).run(SPEC)
    assert named(spans, "design.repair") == []


def test_fetch_spans(spans: InMemorySpanExporter, tmp_path: Path) -> None:
    make_crawler(Site(SITE), tmp_path, max_pages=20).crawl(["https://docs.example.com/docs"])
    again = make_crawler(Site(SITE), tmp_path, max_pages=20)  # the second time, from the cache
    again.crawl(["https://docs.example.com/docs"])

    fetches = named(spans, "fetch")
    by_url: dict[str, list[ReadableSpan]] = {}
    for span in fetches:
        by_url.setdefault(attrs(span)["url.full"], []).append(span)
    home = by_url["https://docs.example.com/docs"]
    assert [attrs(s)["docs2mcp.cache_hit"] for s in home] == [False, True]
    assert attrs(home[0])["http.response.status_code"] == 200
    missing = by_url["https://docs.example.com/docs/missing"][0]
    assert missing.status.status_code is StatusCode.ERROR
    assert missing.status.description == "HTTP 404"


def test_each_command_is_a_root_span(spans: InMemorySpanExporter, tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    spec = root / "tests" / "fixtures" / "specs" / "frankfurter.json"
    tools = tmp_path / "tools.json"
    assert main(["design", str(spec), "--direct", "--out", str(tools)]) == 0
    assert main(["check-tools", str(tools), "--spec", str(spec)]) == 0

    (command,) = named(spans, "docs2mcp check-tools")
    assert attrs(command)["process.exit.code"] == 0
    assert command.parent is None
    (validation,) = [
        s
        for s in named(spans, "validate_toolset")
        if s.parent is not None and s.parent.span_id == span_id(command)
    ]
    assert attrs(validation)["docs2mcp.problems"] == 0

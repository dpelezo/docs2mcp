"""OpenTelemetry tracing: spans for fetches, model calls and validation (#6).

Tracing is off unless an exporter is configured, and then the OpenTelemetry API
records nothing at almost no cost. Two ways to turn it on:

- Langfuse: set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (and LANGFUSE_HOST for
  a self-hosted or US instance). Spans go to its OTLP endpoint.
- Any OTLP backend: set OTEL_EXPORTER_OTLP_ENDPOINT (and the usual OTEL_* variables).

Model call spans follow the OpenTelemetry GenAI conventions (`gen_ai.*`), which
Langfuse shows as generations with token usage. Prompts and replies are left out
unless DOCS2MCP_TRACE_CONTENT is true, so docs text stays out of traces by default.
"""

import base64
import json
import os
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Span, Status, StatusCode

from docs2mcp import __version__
from docs2mcp.config import Settings

tracer = trace.get_tracer("docs2mcp", __version__)

_state = {"content": False}

AttributeValue = str | bool | int | float


def content_enabled() -> bool:
    return _state["content"]


def set_content(enabled: bool) -> None:
    _state["content"] = enabled


def langfuse_target(settings: Settings) -> tuple[str, dict[str, str]] | None:
    """Langfuse's OTLP endpoint and Basic auth header, when both keys are set."""
    if not settings.langfuse_public_key or settings.langfuse_secret_key is None:
        return None
    pair = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key.get_secret_value()}"
    token = base64.b64encode(pair.encode()).decode()
    endpoint = f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces"
    return endpoint, {"Authorization": f"Basic {token}"}


def exporter_for(
    settings: Settings, environ: Mapping[str, str] = os.environ
) -> SpanExporter | None:
    """The exporter the settings ask for, or None when tracing should stay off."""
    langfuse = langfuse_target(settings)
    if langfuse is not None:
        endpoint, headers = langfuse
        return OTLPSpanExporter(endpoint=endpoint, headers=headers)
    if environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or environ.get(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
    ):
        return OTLPSpanExporter()
    return None


def setup_tracing(
    settings: Settings, environ: Mapping[str, str] = os.environ
) -> TracerProvider | None:
    """Install a tracer provider when an exporter is configured; otherwise change nothing."""
    set_content(settings.trace_content)
    exporter = exporter_for(settings, environ)
    if exporter is None:
        return None
    provider = TracerProvider(
        resource=Resource.create({"service.name": "docs2mcp", "service.version": __version__})
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


def set_attributes(span: Span, attributes: Mapping[str, AttributeValue | None]) -> None:
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(key, value)


@contextmanager
def model_call(operation: str, model: str, system: str, turns: Any) -> Generator[Span]:
    """A GenAI span around one model call; the caller records usage with `record_usage`."""
    with tracer.start_as_current_span(f"chat {model}") as span:
        set_attributes(
            span,
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "anthropic",
                "gen_ai.request.model": model,
                "docs2mcp.step": operation,
            },
        )
        if content_enabled():
            messages = [{"role": "system", "content": system}]
            messages += [{"role": t.role, "content": t.content} for t in turns]
            span.set_attribute("gen_ai.prompt", json.dumps(messages, ensure_ascii=False))
        yield span


def record_usage(span: Span, reply: Any, output: Any = None) -> None:
    """Token usage from a ModelReply or DesignReply, and the reply itself when content is on."""
    set_attributes(
        span,
        {
            "gen_ai.usage.input_tokens": reply.input_tokens,
            "gen_ai.usage.output_tokens": reply.output_tokens,
            "gen_ai.usage.cache_read.input_tokens": reply.cache_read_tokens,
            "gen_ai.usage.cache_creation.input_tokens": reply.cache_write_tokens,
        },
    )
    if content_enabled() and output is not None:
        span.set_attribute("gen_ai.completion", output.model_dump_json())


def mark_failed(span: Span, description: str) -> None:
    """Mark a span failed without an exception, e.g. a fetch that returned HTTP 404."""
    span.set_status(Status(StatusCode.ERROR, description))

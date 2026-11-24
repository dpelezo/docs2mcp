"""Ingest the Frankfurter snapshot, then extract with a scripted model reply.

The reply is written the way Claude answers: some paths as full URLs, and /rates
reported from two chunks. The real model's replies are recorded in a later step.
"""

from argparse import Namespace
from pathlib import Path

import httpx
import pytest

from docs2mcp.cli import build_model, main
from docs2mcp.config import Settings
from docs2mcp.extract import AnthropicModel, Extractor, RecordingModel, ReplayModel
from docs2mcp.extract.wire import WireBatch
from docs2mcp.ingest import Chunk, ingest, read_chunks
from tests.unit.fakes import ScriptedModel, batch, endpoint, param

SNAPSHOT = Path(__file__).parents[1] / "fixtures" / "docs" / "frankfurter" / "index.html"
SEED = "https://frankfurter.dev/"
BASE = "https://api.frankfurter.dev/v2"


def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, cache_dir=tmp_path / "cache", crawl_delay_seconds=0)  # pyright: ignore[reportCallIssue]


@pytest.fixture
def ingest_dir(tmp_path: Path) -> Path:
    def serve(request: httpx.Request) -> httpx.Response:
        if str(request.url) == SEED:
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text=SNAPSHOT.read_text()
            )
        return httpx.Response(404, headers={"content-type": "text/html"}, text="")

    out = tmp_path / "ingest"
    client = httpx.Client(transport=httpx.MockTransport(serve))
    ingest([SEED], out, settings(tmp_path), client=client)
    return out


def chunk_with(chunks: list[Chunk], text: str) -> str:
    return next(c.id for c in chunks if text in c.markdown)


def frankfurter_reply(chunks: list[Chunk]) -> WireBatch:
    rates = chunk_with(chunks, "## Rates")
    return batch(
        endpoint(
            "get_rates",
            f"{BASE}/rates",
            param("base", description="Base currency, EUR by default."),
            param("quotes", description="Comma-separated currencies to return."),
            param("date", description="Rates for a past date (YYYY-MM-DD)."),
            param("from"),
            param("to"),
            param("group", allowed_values=["week", "month"]),
            source_chunk_id=rates,
        ),
        endpoint(
            "get_rates",
            "/rates",
            param("providers"),
            param("expand", allowed_values=["providers"]),
            source_chunk_id=chunk_with(chunks, "### Filtering by Provider"),
        ),
        endpoint("get_rates_csv", "/rates.csv", source_chunk_id=chunk_with(chunks, "### CSV")),
        endpoint(
            "get_rate",
            f"{BASE}/rate/{{base}}/{{quote}}",
            param("base", "path"),
            param("quote", "path"),
            source_chunk_id=chunk_with(chunks, "## Rate\n"),
        ),
        endpoint(
            "list_currencies",
            "/currencies",
            param("scope", allowed_values=["all"]),
            source_chunk_id=chunk_with(chunks, "## Currencies"),
        ),
        endpoint("get_currency", "/currency/{code}", param("code", "path")),
        endpoint("list_providers", "/providers"),
        endpoint("get_provider", "/providers/{id}", param("id", "path")),
        endpoint("get_provider_rates", "/providers/{id}/rates", param("date")),  # id left out
        endpoint(
            "get_provider_rate",
            "/providers/{id}/rate/{base}/{quote}",
            param("id", "path"),
            param("base", "path"),
            param("quote", "path"),
        ),
        api_name="Frankfurter",
        base_urls=[BASE, "https://api.frankfurter.dev"],
    )


def test_frankfurter_docs_become_a_valid_spec(ingest_dir: Path) -> None:
    chunks = read_chunks(ingest_dir)
    model = ScriptedModel([frankfurter_reply(chunks)])
    spec, report = Extractor(model).run(chunks, docs_url=SEED)

    assert len(model.calls) == 1  # 12 chunks, ~2,600 tokens: one batch
    assert (spec.name, str(spec.base_url)) == ("Frankfurter", BASE)
    assert [(e.method, e.path) for e in spec.endpoints] == [
        ("GET", "/rates"),
        ("GET", "/rates.csv"),
        ("GET", "/rate/{base}/{quote}"),
        ("GET", "/currencies"),
        ("GET", "/currency/{code}"),
        ("GET", "/providers"),
        ("GET", "/providers/{id}"),
        ("GET", "/providers/{id}/rates"),
        ("GET", "/providers/{id}/rate/{base}/{quote}"),
    ]
    rates = spec.endpoints[0]
    assert [p.name for p in rates.parameters] == [
        "base",
        "quotes",
        "date",
        "from",
        "to",
        "group",
        "providers",
        "expand",
    ]
    assert str(rates.source_url) == "https://frankfurter.dev/#rates"
    assert str(spec.endpoints[2].source_url) == "https://frankfurter.dev/#rate"
    assert sorted(report.repairs) == [
        "GET /providers/{id}/rates: added missing path parameter 'id'",
        "GET /rates: merged a duplicate from another chunk",
    ]


def test_extract_command_writes_the_spec_and_report_and_replays_offline(
    ingest_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("docs2mcp.cli.get_settings", lambda: settings(tmp_path))
    chunks = read_chunks(ingest_dir)
    recordings = tmp_path / "recordings"
    live = RecordingModel(
        ScriptedModel([frankfurter_reply(chunks)], name="claude-test"), recordings
    )

    def use_recording_model(_args: Namespace, _settings: Settings) -> RecordingModel:
        return live

    monkeypatch.setattr("docs2mcp.cli.build_model", use_recording_model)

    spec_path = tmp_path / "out" / "spec.json"
    assert main(["extract", str(ingest_dir), "--out", str(spec_path)]) == 0
    out = capsys.readouterr().out
    assert "Frankfurter: 9 endpoints at https://api.frankfurter.dev/v2" in out
    assert "1 model calls, 1,000 input and 200 output tokens (claude-test)" in out
    assert (tmp_path / "out" / "extraction_report.json").exists()
    first = spec_path.read_text()

    monkeypatch.undo()
    monkeypatch.setattr("docs2mcp.cli.get_settings", lambda: settings(tmp_path))
    replayed = tmp_path / "replayed" / "spec.json"
    args = ["extract", str(ingest_dir), "--out", str(replayed), "--replay", str(recordings)]
    assert main([*args, "--model", "claude-test"]) == 0
    assert replayed.read_text() == first


def test_build_model_chooses_replay_live_or_recording(tmp_path: Path) -> None:
    keyless = settings(tmp_path)
    args = Namespace(model=None, replay=tmp_path, record=None)
    assert isinstance(build_model(args, keyless), ReplayModel)

    live_args = Namespace(model="claude-sonnet-5-5", replay=None, record=None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        build_model(live_args, keyless)

    keyed = Settings(_env_file=None, ANTHROPIC_API_KEY="sk-ant-test")  # pyright: ignore[reportCallIssue]
    live = build_model(live_args, keyed)
    assert isinstance(live, AnthropicModel)
    assert live.name == "claude-sonnet-5-5"
    recording = build_model(Namespace(model=None, replay=None, record=tmp_path), keyed)
    assert isinstance(recording, RecordingModel)

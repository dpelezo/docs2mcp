"""Run extraction: batch the chunks, ask Claude, validate, retry with feedback, merge."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from docs2mcp.extract.merge import (
    apply_shared_parameters,
    build_spec,
    choose_auth,
    choose_base_url,
    describe_error,
    expand_resource_templates,
    merge_endpoints,
    outside_base,
    route_key,
    to_endpoint,
)
from docs2mcp.extract.model import ExtractionModel, ModelOutputError, Turn
from docs2mcp.extract.prompt import SYSTEM, batch_message, feedback_message
from docs2mcp.extract.wire import WireAuth, WireEndpoint
from docs2mcp.ingest.models import Chunk
from docs2mcp.spec import ApiSpec, Endpoint


@dataclass
class BatchLog:
    chunk_ids: list[str]
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    endpoints: int = 0
    rejected: list[str] = field(default_factory=list[str])
    error: str | None = None


@dataclass
class ExtractionReport:
    model: str
    batches: list[BatchLog] = field(default_factory=list[BatchLog])
    repairs: list[str] = field(default_factory=list[str])
    merged: list[str] = field(default_factory=list[str])
    expanded: list[str] = field(default_factory=list[str])
    out_of_scope: list[str] = field(default_factory=list[str])
    shared_parameters: list[str] = field(default_factory=list[str])

    @property
    def calls(self) -> int:
        return sum(b.calls for b in self.batches)

    @property
    def input_tokens(self) -> int:
        return sum(b.input_tokens for b in self.batches)

    @property
    def output_tokens(self) -> int:
        return sum(b.output_tokens for b in self.batches)

    @property
    def rejected(self) -> list[str]:
        return [r for b in self.batches for r in b.rejected]

    @property
    def failed_batches(self) -> list[BatchLog]:
        return [b for b in self.batches if b.error]


def make_batches(chunks: Sequence[Chunk], max_tokens: int) -> list[list[Chunk]]:
    """Group chunks in order without exceeding `max_tokens`; a big chunk gets its own batch."""
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    size = 0
    for chunk in chunks:
        if current and size + chunk.tokens > max_tokens:
            batches.append(current)
            current, size = [], 0
        current.append(chunk)
        size += chunk.tokens
    if current:
        batches.append(current)
    return batches


@dataclass
class _Accepted:
    endpoints: list[WireEndpoint] = field(default_factory=list[WireEndpoint])
    base_urls: list[str] = field(default_factory=list[str])
    auths: list[WireAuth] = field(default_factory=list[WireAuth])
    names: list[str] = field(default_factory=list[str])


class Extractor:
    def __init__(
        self, model: ExtractionModel, *, batch_tokens: int = 6000, max_retries: int = 2
    ) -> None:
        self.model = model
        self.batch_tokens = batch_tokens
        self.max_retries = max_retries

    def run(
        self,
        chunks: Sequence[Chunk],
        *,
        name: str | None = None,
        base_url: str | None = None,
        docs_url: str | None = None,
    ) -> tuple[ApiSpec, ExtractionReport]:
        if not chunks:
            msg = "no chunks to extract from; run docs2mcp ingest first"
            raise ValueError(msg)
        lookup = {chunk.id: chunk for chunk in chunks}
        report = ExtractionReport(model=self.model.name)
        accepted = _Accepted()
        for batch in make_batches(chunks, self.batch_tokens):
            self._run_batch(batch, lookup, report, accepted)

        chosen_base = choose_base_url(accepted.base_urls, base_url)
        notes: list[str] = []
        endpoints: list[Endpoint] = []
        templates: dict[tuple[str, str], Endpoint] = {}
        shares: dict[tuple[str, str], list[str]] = {}
        for wire in accepted.endpoints:
            if outside_base(wire.served_from, wire.path, chosen_base):
                report.out_of_scope.append(
                    f"{wire.method} {wire.path}: served from {wire.served_from}, not {chosen_base}"
                )
                continue
            try:
                endpoint = to_endpoint(wire, chosen_base, lookup, notes)
            except ValidationError as error:  # cannot happen for endpoints validated per batch
                report.batches[-1].rejected.append(describe_error(wire, error))
                continue
            if wire.for_every_resource:
                templates.setdefault((endpoint.method, endpoint.path), endpoint)
            else:
                endpoints.append(endpoint)
                if wire.same_parameters_as:
                    refs = shares.setdefault(route_key(endpoint), [])
                    refs.extend(r for r in wire.same_parameters_as if r not in refs)
        endpoints = expand_resource_templates(endpoints, list(templates.values()), report.expanded)
        merged = merge_endpoints(endpoints, notes, report.merged)
        merged = apply_shared_parameters(merged, shares, chosen_base, report.shared_parameters)
        report.repairs = notes
        if not merged:
            msg = "no valid endpoints were extracted; see the report"
            raise ValueError(msg)
        spec = build_spec(
            name=name or _most_common(accepted.names) or "API",
            base_url=chosen_base,
            docs_url=docs_url,
            auth=choose_auth(accepted.auths, notes),
            endpoints=merged,
        )
        return spec, report

    def _run_batch(
        self,
        batch: list[Chunk],
        lookup: dict[str, Chunk],
        report: ExtractionReport,
        accepted: _Accepted,
    ) -> None:
        log = BatchLog(chunk_ids=[c.id for c in batch])
        report.batches.append(log)
        turns = [Turn("user", batch_message(batch))]
        for attempt in range(self.max_retries + 1):
            try:
                reply = self.model.extract(SYSTEM, turns)
            except ModelOutputError as error:
                log.calls += 1
                if error.reason == "max_tokens" and len(batch) > 1:
                    report.batches.remove(log)
                    middle = len(batch) // 2
                    self._run_batch(batch[:middle], lookup, report, accepted)
                    self._run_batch(batch[middle:], lookup, report, accepted)
                else:
                    log.error = str(error)
                return
            log.calls += 1
            log.input_tokens += reply.input_tokens
            log.output_tokens += reply.output_tokens

            valid: list[WireEndpoint] = []
            errors: list[str] = []
            for wire in reply.batch.endpoints:
                try:
                    to_endpoint(wire, None, lookup, [])
                    valid.append(wire)
                except ValidationError as error:
                    errors.append(describe_error(wire, error))
            if not errors or attempt == self.max_retries:
                log.endpoints = len(valid)
                log.rejected = errors
                accepted.endpoints.extend(valid)
                accepted.base_urls.extend(reply.batch.base_urls)
                accepted.auths.append(reply.batch.auth)
                if reply.batch.api_name.strip():
                    accepted.names.append(reply.batch.api_name.strip())
                return
            turns = [
                *turns,
                Turn("assistant", reply.batch.model_dump_json()),
                Turn("user", feedback_message(errors)),
            ]


def _most_common(values: Sequence[str]) -> str | None:
    return max(set(values), key=values.count) if values else None

"""Run the task-shaped design: ask Claude, convert, validate, retry with feedback, repair."""

from dataclasses import dataclass, field

from docs2mcp.design.convert import Conversion, Repairs, convert, repair, toolset_from
from docs2mcp.design.model import DesignModel
from docs2mcp.design.prompt import SYSTEM, feedback_message, spec_message
from docs2mcp.design.toolset import ToolSet
from docs2mcp.extract.model import Turn
from docs2mcp.spec import ApiSpec
from docs2mcp.tracing import model_call, record_usage, set_attributes, tracer


@dataclass
class Attempt:
    """One model call: its token use and the problems its design had."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    problems: list[str] = field(default_factory=list[str])


@dataclass
class DesignReport:
    model: str
    attempts: list[Attempt] = field(default_factory=list[Attempt])
    notes: list[str] = field(default_factory=list[str])
    repairs: Repairs = field(default_factory=Repairs)

    @property
    def calls(self) -> int:
        return len(self.attempts)

    @property
    def input_tokens(self) -> int:
        return sum(a.input_tokens for a in self.attempts)

    @property
    def output_tokens(self) -> int:
        return sum(a.output_tokens for a in self.attempts)

    @property
    def cache_read_tokens(self) -> int:
        return sum(a.cache_read_tokens for a in self.attempts)

    @property
    def cache_write_tokens(self) -> int:
        return sum(a.cache_write_tokens for a in self.attempts)


class Designer:
    def __init__(self, model: DesignModel, *, max_retries: int = 2) -> None:
        self.model = model
        self.max_retries = max_retries

    def run(self, spec: ApiSpec) -> tuple[ToolSet, DesignReport]:
        with tracer.start_as_current_span("design") as span:
            span.set_attribute("docs2mcp.endpoints", len(spec.endpoints))
            toolset, report = self._run(spec)
            set_attributes(
                span,
                {
                    "docs2mcp.tools": len(toolset.tools),
                    "docs2mcp.model_calls": report.calls,
                    "docs2mcp.added_direct": len(report.repairs.added_direct),
                    "gen_ai.usage.input_tokens": report.input_tokens,
                    "gen_ai.usage.output_tokens": report.output_tokens,
                },
            )
            return toolset, report

    def _run(self, spec: ApiSpec) -> tuple[ToolSet, DesignReport]:
        report = DesignReport(model=self.model.name)
        turns = [Turn("user", spec_message(spec))]
        conversion = Conversion()
        for attempt in range(self.max_retries + 1):
            with model_call("design", self.model.name, SYSTEM, turns) as call:
                reply = self.model.design(SYSTEM, turns)
                record_usage(call, reply, reply.design)
            with tracer.start_as_current_span("design.validate") as check:
                conversion = convert(reply.design, spec)
                set_attributes(
                    check,
                    {"docs2mcp.attempt": attempt, "docs2mcp.problems": len(conversion.errors)},
                )
            report.attempts.append(
                Attempt(
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    cache_read_tokens=reply.cache_read_tokens,
                    cache_write_tokens=reply.cache_write_tokens,
                    problems=conversion.errors,
                )
            )
            if not conversion.errors or attempt == self.max_retries:
                break
            turns = [
                *turns,
                Turn("assistant", reply.design.model_dump_json()),
                Turn("user", feedback_message(conversion.errors)),
            ]

        report.notes = conversion.notes
        if not conversion.errors:
            return toolset_from(spec, conversion.tools, conversion.excluded), report
        with tracer.start_as_current_span("design.repair") as fix:
            toolset, report.repairs = repair(conversion, spec)
            set_attributes(
                fix,
                {
                    "docs2mcp.added_arguments": len(report.repairs.added_arguments),
                    "docs2mcp.dropped_tools": len(report.repairs.dropped_tools),
                    "docs2mcp.added_direct": len(report.repairs.added_direct),
                },
            )
        return toolset, report

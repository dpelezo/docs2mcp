"""Run the task-shaped design: ask Claude, convert, validate, retry with feedback, repair."""

from dataclasses import dataclass, field

from docs2mcp.design.convert import Conversion, Repairs, convert, repair, toolset_from
from docs2mcp.design.model import DesignModel
from docs2mcp.design.prompt import SYSTEM, feedback_message, spec_message
from docs2mcp.design.toolset import ToolSet
from docs2mcp.extract.model import Turn
from docs2mcp.spec import ApiSpec


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
        report = DesignReport(model=self.model.name)
        turns = [Turn("user", spec_message(spec))]
        conversion = Conversion()
        for attempt in range(self.max_retries + 1):
            reply = self.model.design(SYSTEM, turns)
            conversion = convert(reply.design, spec)
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
        toolset, report.repairs = repair(conversion, spec)
        return toolset, report

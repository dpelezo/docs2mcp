"""Approve or revoke tools in a tool set: the review step before `generate` (ADR 0011)."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from docs2mcp.design.toolset import Tool, ToolSet


class UnknownToolError(ValueError):
    """A tool named on the command line is not in the tool set."""


@dataclass
class ApprovalChange:
    toolset: ToolSet
    changed: list[str] = field(default_factory=list[str])


def set_approval(
    toolset: ToolSet,
    *,
    names: Sequence[str] = (),
    every: bool = False,
    except_: Sequence[str] = (),
    approve: bool = True,
    note: str | None = None,
) -> ApprovalChange:
    """Set `approved` on the named tools (or on every tool but `except_`).

    A note replaces the tool's note. Unknown names fail before anything changes.
    """
    known = {t.name for t in toolset.tools}
    unknown = sorted((set(names) | set(except_)) - known)
    if unknown:
        msg = f"no tools named {unknown}; the tools are {sorted(known)}"
        raise UnknownToolError(msg)
    if not every and not names:
        msg = "name the tools to change, or use every=True"
        raise ValueError(msg)

    targets = known - set(except_) if every else set(names)
    tools: list[Tool] = []
    changed: list[str] = []
    for tool in toolset.tools:
        if tool.name in targets:
            update: dict[str, object] = {"approved": approve}
            if note is not None:
                update["note"] = note
            new = Tool.model_validate(tool.model_dump() | update)
            if new != tool:
                changed.append(tool.name)
            tools.append(new)
        else:
            tools.append(tool)
    return ApprovalChange(toolset.model_copy(update={"tools": tools}), changed)

"""Split a page's Markdown into chunks on headings.

- Every chunk records its heading path (page title > section > subsection) and the
  anchor of its heading, so extraction knows what a table describes and every
  endpoint can link back to its source.
- A section larger than `max_tokens` is split between blocks (paragraphs, lists,
  tables, code). Code blocks are never cut; a table that is still too large is split
  between rows, repeating its header row.
- A section smaller than `min_tokens` (often a heading with no text of its own) is
  joined to the section after it, or to the one before it at the end of a page.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from docs2mcp.ingest.convert import Heading
from docs2mcp.ingest.models import Chunk

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_FENCE = re.compile(r"^(```|~~~)")
_MIN_TABLE_LINES = 3  # header, separator, at least one row


def estimate_tokens(text: str) -> int:
    """Rough token count: about four characters per token for English and Markdown."""
    return max(1, math.ceil(len(text) / 4))


@dataclass
class _Section:
    heading_path: list[str]
    anchor: str | None
    lines: list[str] = field(default_factory=list[str])

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip("\n")


_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def plain_text(markdown_heading: str) -> str:
    """Heading text without link markup, backticks or emphasis markers."""
    text = _LINK.sub(r"\1", markdown_heading)
    return " ".join(text.replace("`", "").replace("**", "").split())


class _HeadingMatcher:
    """Pair Markdown headings with the converter's heading list (which has the HTML ids).

    The lists are in the same order, so pairing is positional; text is checked so a
    heading the converter reports differently cannot shift every anchor after it.
    """

    def __init__(self, headings: Sequence[Heading]) -> None:
        self._headings = list(headings)
        self._cursor = 0

    def match(self, level: int, text: str) -> Heading | None:
        for index in range(self._cursor, len(self._headings)):
            heading = self._headings[index]
            if heading.level == level and heading.text == text:
                self._cursor = index + 1
                return heading
        return None


def _sections(markdown: str, title: str, headings: Sequence[Heading]) -> list[_Section]:
    matcher = _HeadingMatcher(headings)
    stack: list[tuple[int, str]] = []
    current = _Section(heading_path=[title], anchor=None)
    sections = [current]
    in_fence = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match is None:
            current.lines.append(line)
            continue
        level, text = len(match.group(1)), plain_text(match.group(2))
        heading = matcher.match(level, text)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        current = _Section(
            heading_path=[title, *(t for _, t in stack)],
            anchor=heading.anchor if heading else None,
            lines=[line],
        )
        sections.append(current)
    return [s for s in sections if s.text.strip()]


def _blocks(text: str) -> list[str]:
    """Split on blank lines, keeping fenced code blocks whole."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _is_table(block: str) -> bool:
    lines = block.splitlines()
    return len(lines) >= _MIN_TABLE_LINES and all(line.lstrip().startswith("|") for line in lines)


def _split_table(block: str, first_budget: int, max_tokens: int) -> list[str]:
    """Split a table between rows, repeating the header; the first part fits `first_budget`."""
    header, separator, *rows = block.splitlines()
    parts: list[str] = []
    current: list[str] = []
    budget = first_budget
    for row in rows:
        candidate = "\n".join([header, separator, *current, row])
        if estimate_tokens(candidate) > budget and (current or parts or budget < max_tokens):
            parts.append("\n".join([header, separator, *current]) if current else "")
            current, budget = [], max_tokens
        current.append(row)
    parts.append("\n".join([header, separator, *current]))
    return parts


def _split_section(text: str, max_tokens: int) -> list[str]:
    """Pack blocks into parts of at most `max_tokens`.

    Text before a long table stays with the table's first rows, because it usually
    says what the table describes.
    """
    if estimate_tokens(text) <= max_tokens:
        return [text]
    parts: list[str] = []
    current: list[str] = []

    def joined(blocks: list[str]) -> str:
        return "\n\n".join(blocks)

    for block in _blocks(text):
        if estimate_tokens(joined([*current, block])) <= max_tokens:
            current.append(block)
            continue
        if _is_table(block):
            room = max_tokens - (estimate_tokens(joined(current)) + 1 if current else 0)
            first, *rest = _split_table(block, room, max_tokens)
            if first:
                current.append(first)
            if rest:
                if current:
                    parts.append(joined(current))
                parts.extend(rest[:-1])
                current = [rest[-1]]
            continue
        if current:
            parts.append(joined(current))
        current = [block]  # an oversized code block stays whole
    if current:
        parts.append(joined(current))
    return parts


@dataclass
class _Draft:
    heading_path: list[str]
    anchor: str | None
    text: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def _combine(first: _Draft, second: _Draft) -> _Draft:
    """Join two neighbouring drafts and label the result honestly.

    A parent heading followed by its child keeps the child's path. Siblings get the
    path they share (e.g. both under "Rates"). The anchor points at where the text starts.
    """
    a, b = first.heading_path, second.heading_path
    if b[: len(a)] == a:
        path = b
    elif a[: len(b)] == b:
        path = a
    else:
        common = 0
        while common < min(len(a), len(b)) and a[common] == b[common]:
            common += 1
        path = a[:common]
    return _Draft(path, first.anchor or second.anchor, f"{first.text}\n\n{second.text}")


def _merge_small(drafts: list[_Draft], min_tokens: int, max_tokens: int) -> list[_Draft]:
    merged: list[_Draft] = []
    pending: _Draft | None = None
    for original in drafts:
        draft = original
        if pending is not None:
            combined = _combine(pending, draft)
            if combined.tokens <= max_tokens:
                draft = combined
            else:
                merged.append(pending)
            pending = None
        if draft.tokens < min_tokens:
            pending = draft
        else:
            merged.append(draft)
    if pending is not None:
        if merged and (combined := _combine(merged[-1], pending)).tokens <= max_tokens:
            merged[-1] = combined
        else:
            merged.append(pending)
    return merged


def chunk_page(
    markdown: str,
    *,
    url: str,
    title: str,
    headings: Sequence[Heading],
    id_prefix: str,
    max_tokens: int = 1500,
    min_tokens: int = 80,
) -> list[Chunk]:
    """Chunk one page. Output is deterministic for the same input."""
    drafts = [
        _Draft(section.heading_path, section.anchor, part)
        for section in _sections(markdown, title, headings)
        for part in _split_section(section.text, max_tokens)
    ]
    return [
        Chunk(
            id=f"{id_prefix}-c{index:03d}",
            url=url,
            anchor=draft.anchor,
            heading_path=draft.heading_path,
            markdown=draft.text,
            tokens=draft.tokens,
        )
        for index, draft in enumerate(_merge_small(drafts, min_tokens, max_tokens), start=1)
    ]

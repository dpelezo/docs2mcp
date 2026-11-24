"""Keep the documentation part of a page and convert it to Markdown."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser

import html_to_markdown as h2m

# Page furniture that can sit inside <main> as well (tables of contents, side menus,
# copy buttons). Forms are kept: Open-Meteo lists its parameters as labelled fields.
EXCLUDE_SELECTORS = [
    "nav",
    "aside",
    "header",
    "footer",
    "script",
    "style",
    "noscript",
    "button",
    "[role=navigation]",
]


# Heading permalinks that docs generators add, such as Docusaurus's
# `[​](https://site/page#quick-start "Direct link to Quick Start")`: a link whose text
# is empty, invisible or a symbol, pointing at a fragment.
_PERMALINK = re.compile(
    r'\[[\s\u200b\u200c\u200d\ufeff#¶§🔗]*\]\([^()\s]*#[^()\s]*(?:\s+"[^"]*")?\)'
)
_INVISIBLE = re.compile("[\u200b\u200c\u200d\ufeff]")


def strip_permalinks(markdown: str) -> str:
    """Remove heading permalinks and zero-width characters."""
    return _INVISIBLE.sub("", _PERMALINK.sub("", markdown))


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    anchor: str | None


@dataclass(frozen=True)
class Converted:
    markdown: str
    headings: list[Heading]


class _MainFinder(HTMLParser):
    """Find the character span of the first <main>, <article> or role="main" element."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self._line_starts = [0]
        for index, char in enumerate(html):
            if char == "\n":
                self._line_starts.append(index + 1)
        self.start: int | None = None
        self.end: int | None = None
        self._tag: str | None = None
        self._depth = 0

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.end is not None:
            return
        if self._tag is None:
            if tag in {"main", "article"} or dict(attrs).get("role") == "main":
                self._tag, self._depth, self.start = tag, 1, self._offset()
        elif tag == self._tag:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._tag is None or self.end is not None or tag != self._tag:
            return
        self._depth -= 1
        if self._depth == 0:
            self.end = self._offset() + len(f"</{tag}>")


def main_content(html: str) -> str:
    """The first <main>/<article>/role="main" element, or the whole page if there is none."""
    finder = _MainFinder(html)
    finder.feed(html)
    finder.close()
    if finder.start is None:
        return html
    return html[finder.start : finder.end] if finder.end is not None else html[finder.start :]


def to_markdown(html: str, base_url: str) -> Converted:
    """Convert a page's main content to Markdown, with absolute links and compact tables."""
    options = h2m.ConversionOptions(
        compact_tables=True,
        highlight_style=h2m.HighlightStyle.NONE,
        base_url=base_url,
        exclude_selectors=EXCLUDE_SELECTORS,
        skip_images=True,
    )
    result = h2m.convert(main_content(html), options)
    headings = [
        Heading(
            level=h.level, text=" ".join(_INVISIBLE.sub("", h.text).split()), anchor=h.id or None
        )
        for h in result.metadata.headers
    ]
    return Converted(markdown=strip_permalinks(result.content or ""), headings=headings)

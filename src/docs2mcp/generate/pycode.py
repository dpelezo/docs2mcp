"""Write Python source the way Ruff's formatter would, so generated servers pass `ruff format`.

Collections are written one item per line with a trailing comma, which the formatter
keeps as written (the "magic trailing comma"). A string too long for its line is
split at spaces into implicitly joined parts inside parentheses. Strings use double
quotes; every value goes through `json.dumps` or `repr`, so no text can escape its
literal.
"""

import json
from typing import Any

LINE_LENGTH = 100
_SPACE = 0x20  # code points below it, and DEL, are control characters
_DELETE = 0x7F
_INDENT = "    "


def literal(value: Any) -> str:
    """A short, single-line Python literal for a JSON value or tuple."""
    if value is None or isinstance(value, bool):
        return repr(value)
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return _string_literal(value)
    if isinstance(value, tuple):
        items: list[Any] = list(value)  # pyright: ignore[reportUnknownArgumentType]
        inner = ", ".join(literal(v) for v in items)
        return f"({inner},)" if len(items) == 1 else f"({inner})"
    if isinstance(value, list):
        return "[" + ", ".join(literal(v) for v in value) + "]"  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        pairs = (f"{literal(k)}: {literal(v)}" for k, v in value.items())  # pyright: ignore[reportUnknownVariableType]
        return "{" + ", ".join(pairs) + "}"
    msg = f"cannot write {type(value).__name__} as a literal"
    raise TypeError(msg)


def _string_literal(text: str) -> str:
    """Double quotes, unless the text has more double than single quotes (Ruff's preference)."""
    quote = "'" if text.count('"') > text.count("'") else '"'
    if quote == '"':
        return json.dumps(text, ensure_ascii=False)
    out: list[str] = []
    for char in text:
        if char in "\\'":
            out.append("\\" + char)
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char == "\r":
            out.append("\\r")
        elif ord(char) < _SPACE or ord(char) == _DELETE:
            out.append(f"\\x{ord(char):02x}")
        else:
            out.append(char)
    return "'" + "".join(out) + "'"


def block(value: Any, depth: int) -> str:
    """A literal at `depth` indents: lists and dicts one item per line, the rest inline."""
    pad = _INDENT * (depth + 1)
    end = _INDENT * depth
    if isinstance(value, list) and value:
        lines = [f"{pad}{block(v, depth + 1)}," for v in value]  # pyright: ignore[reportUnknownVariableType]
        return "[\n" + "\n".join(lines) + f"\n{end}]"
    if isinstance(value, dict) and value:
        lines = [f"{pad}{literal(k)}: {block(v, depth + 1)}," for k, v in value.items()]  # pyright: ignore[reportUnknownVariableType]
        return "{\n" + "\n".join(lines) + f"\n{end}}}"
    return literal(value)


def string(text: str, depth: int, used: int) -> str:
    """A string literal for a line that already has `used` characters before it.

    One literal when it fits (with the trailing comma); otherwise parts of at most
    the line length inside parentheses, each part ending at a space.
    """
    whole = literal(text)
    if used + len(whole) + 1 <= LINE_LENGTH:
        return whole
    pad = _INDENT * (depth + 1)
    room = LINE_LENGTH - len(pad) - 2  # the two quotes
    parts: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and len(literal(candidate + " ")) - 2 > room:
            parts.append(current + " ")
            current = word
        else:
            current = candidate
    parts.append(current)
    lines = "\n".join(f"{pad}{literal(part)}" for part in parts)
    return f"(\n{lines}\n{_INDENT * depth})"

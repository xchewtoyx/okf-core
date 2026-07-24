"""Shared markdown-it inline-token helpers used by ``index.py`` and ``logs.py``.

Both modules reconstitute a run of inline child tokens back to Markdown
source (preserving embedded links verbatim) and both need a token's 1-based
source line number for problem reporting. Factored here once so the two
call sites can't drift out of sync with each other.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def inline_token_source(child: object) -> str | None:
    """Return the Markdown source for a single inline child token, or None to skip."""
    t = getattr(child, "type", "")
    if t == "text":
        return getattr(child, "content", "")
    if t == "code_inline":
        return f"`{getattr(child, 'content', '')}`"
    if t in ("softbreak", "hardbreak"):
        return " "
    # Delimiter tokens (strong_open/close, em_open/close, s_open/close, etc.)
    markup = getattr(child, "markup", "")
    return markup if markup else None


def _escape_title(title: str) -> str:
    """Escape a link title for embedding in a double-quoted CommonMark title.

    Backslashes must be escaped first so a pre-existing ``\\"`` in the title
    isn't double-escaped by the subsequent quote pass.
    """
    return title.replace("\\", "\\\\").replace('"', '\\"')


def _render_link_destination(child: Any) -> str:
    """Render a ``link_open`` token's ``(href "title")`` destination span.

    The title is omitted entirely when absent, matching markdown-it's own
    collapse of an empty ``""`` title to "no title attribute" -- so this
    already doesn't distinguish (nor needs to) a missing title from an empty
    one. When present, it's re-emitted in double-quoted form with embedded
    backslashes and double quotes escaped so the rendered source round-trips
    back through a CommonMark parser to the same title text.
    """
    href = child.attrGet("href") or ""
    title = child.attrGet("title")
    if not title:
        return f"({href})"
    return f'({href} "{_escape_title(str(title))}")'


def render_linked_span(children: Sequence[Any]) -> str:
    """Render a run of inline child tokens back to Markdown source.

    Links are reconstituted verbatim as ``[text](href)`` or, when the link
    carries a title, ``[text](href "title")``; every other token passes
    through ``inline_token_source``.
    """
    parts: list[str] = []
    i = 0
    n = len(children)
    while i < n:
        child = children[i]
        if child.type == "link_open":
            destination = _render_link_destination(child)
            i += 1
            inner: list[str] = []
            while i < n and children[i].type != "link_close":
                content = inline_token_source(children[i])
                if content is not None:
                    inner.append(content)
                i += 1
            parts.append(f"[{''.join(inner)}]{destination}")
            i += 1  # consume the matching link_close
        else:
            content = inline_token_source(child)
            if content is not None:
                parts.append(content)
            i += 1
    return "".join(parts)


def token_line(token: object) -> int | None:
    """Return a token's 1-based source line number, or None if unavailable."""
    token_map = getattr(token, "map", None)
    if not token_map:
        return None
    return int(token_map[0]) + 1

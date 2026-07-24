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


def render_linked_span(children: Sequence[Any]) -> str:
    """Render a run of inline child tokens back to Markdown source.

    Links are reconstituted verbatim as ``[text](href)``; every other token
    passes through ``inline_token_source``.
    """
    parts: list[str] = []
    i = 0
    n = len(children)
    while i < n:
        child = children[i]
        if child.type == "link_open":
            href = child.attrGet("href") or ""
            i += 1
            inner: list[str] = []
            while i < n and children[i].type != "link_close":
                content = inline_token_source(children[i])
                if content is not None:
                    inner.append(content)
                i += 1
            parts.append(f"[{''.join(inner)}]({href})")
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

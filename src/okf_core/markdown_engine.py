"""Shared Markdown parse/render engine (ADR-0002 "Markdown side", #198, #199).

One canonical CommonMark(+GFM tables) parser and one canonical ``mdformat``
renderer, shared by every module that reads or writes Markdown-significant
content: ``patching.py`` (section/link editing, frontmatter-adjacent
stamping), ``logs.py`` (``log.md`` parsing/rendering), and ``index.py``
(``index.md`` parsing/rendering). Before #199, four modules each carried
their own escaping rules for Markdown-significant characters (``[``, ``]``,
``(``, ``)``, backslash) -- three implemented them by hand
(``index.py``'s ``_md_escape``, ``_markdown_inline.py``'s ``_escape_title``,
and a pair of logs.py guards that *rejected* unsafe input rather than
escaping it, because the old per-module reconstitution never re-escaped on
read). Centralizing parsing and rendering here means escaping/unescaping
exists in exactly one place (#199 AC1): ``mdformat``'s own renderer decides
when a character needs a backslash escape (or an alternate destination
form, e.g. angle-bracket-wrapping a link href) to survive a re-parse, and
markdown-it-py's own parser is the sole authority on what counts as "the
same content" when reading it back in. No other module reimplements either
half, so a caller can hand this module an arbitrary string as literal link
text, a link title, or a link destination and get back Markdown that
round-trips -- no representability pre-check required upstream (see
``logs.py``'s ``_build_move_entry``, which used to reject concept IDs
containing ``[``/``]`` and paths with unbalanced parens for exactly this
reason).
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdformat.plugins import PARSER_EXTENSIONS
from mdformat.renderer import MDRenderer

__all__ = [
    "MARKDOWN",
    "link_children",
    "parse_inline_children",
    "render_inline_children",
    "render_markdown_tokens",
    "render_span",
    "text_children",
    "token_line",
]


# CommonMark plus GFM tables (AC1) -- `.enable("table")` turns on
# markdown-it-py's own built-in (but commonmark-preset-disabled) table rule;
# no plugin package is needed for *parsing*. The renderer is `mdformat`'s
# own `MDRenderer`, together with `mdformat.plugins.PARSER_EXTENSIONS["tables"]`
# (published by the `mdformat-gfm` dependency) supplying the table renderer
# functions `MDRenderer` has no built-in support for. Both the `enable()`
# call and the plugin registry are public, documented markdown-it-py/mdformat
# API -- unlike the deleted inline/core parser-rule instrumentation this
# engine replaced in #198, nothing here reaches into an internals module.
MARKDOWN = MarkdownIt("commonmark")
MARKDOWN.enable("table")
_RENDERER = MDRenderer()
_RENDER_OPTIONS: Mapping[str, Any] = {
    "parser_extension": [PARSER_EXTENSIONS["tables"]],
    "mdformat": {"wrap": "keep"},
}


def render_markdown_tokens(
    tokens: Sequence[Token], env: MutableMapping[str, Any] | None = None
) -> str:
    """Render a (sub)tree of markdown-it-py tokens to canonical Markdown.

    ``tokens`` need not be the whole document -- any balanced open/close
    token sequence (a full document, one section's body, or a single bare
    ``inline`` token) renders standalone. ``env`` should be the ``dict`` an
    earlier ``MARKDOWN.parse(...)`` call on the same document populated
    (carrying its ``references``, for any reference-style link definitions
    elsewhere in the document to survive an edit untouched); a fresh
    mapping (the default) is fine for a document/fragment with no
    reference-style links.
    """
    return _RENDERER.render(tokens, _RENDER_OPTIONS, env if env is not None else {})


def parse_inline_children(text: str) -> list[Token]:
    """Parse ``text`` as an inline Markdown fragment; return its child tokens.

    ``text`` is treated as Markdown *source*, not literal content -- e.g.
    ``"**bold**"`` parses to a ``strong_open``/``text``/``strong_close``
    run, not a literal ``**bold**`` text token. Paired with ``render_span``
    to canonicalize an already-Markdown-shaped string (parse, then
    reconstitute) -- see ``logs.py``'s ``_canonicalize_log_entry_prose``.
    Returns an empty list for a fragment with no inline content.
    """
    tokens = MARKDOWN.parseInline(text, {})
    return list(tokens[0].children or []) if tokens else []


def render_inline_children(children: Sequence[Token]) -> str:
    """Render a run of inline child tokens back to canonical Markdown source.

    Any Markdown-significant character inside a ``text`` child (or a link's
    text/title) is escaped exactly when needed for the rendered fragment to
    parse back to the same content -- decided by ``mdformat``'s renderer,
    not by this function. Returns ``""`` for an empty ``children`` sequence,
    without invoking the renderer.
    """
    if not children:
        return ""
    inline = Token("inline", "", 0, children=list(children))
    rendered = render_markdown_tokens([inline])
    return rendered.removesuffix("\n")


def text_children(content: str) -> list[Token]:
    """One literal ``text`` child token wrapping ``content``.

    Unlike ``parse_inline_children``, ``content`` is treated as plain
    literal data, not Markdown source: any Markdown-significant character
    it contains is escaped on render (via ``render_inline_children``) so
    the rendered fragment parses back to ``content`` verbatim, rather than
    being interpreted as Markdown markup.
    """
    return [Token("text", "", 0, content=content)]


def link_children(href: str, text: str, title: str | None = None) -> list[Token]:
    """Build one link's inline child tokens: ``[text](href "title")``.

    ``text`` is literal link-text content (escaped on render exactly like
    ``text_children``, so it round-trips even if it contains ``[``, ``]``,
    or a backslash); ``href`` is passed through as the link's ``href``
    attribute unnormalized -- callers that want ``markdown-it``'s own href
    normalization (e.g. percent-encoding) should call ``MARKDOWN.normalizeLink``
    themselves first, matching what parsing an existing link already
    applies to its ``href``. ``title`` is omitted from the rendered link
    entirely when falsy (``None`` or ``""``).

    A title, unlike ``text``, is *not* escaped by ``render_inline_children``
    -- ``mdformat``'s link-title renderer only backslash-escapes a literal
    ``"`` (to protect the title's own delimiter), not a literal backslash,
    so a title containing ``\\`` needs one explicit pre-escape here before
    ``mdformat``'s own quote-escaping runs, the same two-step order the
    retired ``_markdown_inline._escape_title`` used ("backslashes must be
    escaped first so a pre-existing ``\\"`` in the title isn't
    double-escaped by the subsequent quote pass") -- this is the one place
    in the module where the shared renderer's own escaping isn't sufficient
    on its own and a small, explicit rule is still needed.
    """
    attrs: dict[str, Any] = {"href": href}
    if title:
        attrs["title"] = title.replace("\\", "\\\\")
    return [
        Token("link_open", "a", 1, attrs=attrs),
        *text_children(text),
        Token("link_close", "a", -1),
    ]


def _span_token_source(child: Token) -> str | None:
    """Return one already-parsed token's Markdown source, or None to skip it.

    ``child.content``/``child.markup`` already hold the parser's own
    *unescaped* semantic value (e.g. a source ``\\]`` unescapes to a
    ``text`` token whose ``.content`` is plain ``]``) -- returning them
    verbatim recovers that value, rather than re-deriving fresh, escaped
    Markdown for it (that is ``render_inline_children``'s job, for content
    that isn't already-parsed). Delimiter tokens (``strong_open``/``close``,
    ``em_open``/``close``, ``s_open``/``close``, ...) fall through to their
    own ``markup`` attribute.
    """
    token_type = getattr(child, "type", "")
    if token_type == "text":
        return getattr(child, "content", "")
    if token_type == "code_inline":
        return f"`{getattr(child, 'content', '')}`"
    if token_type in ("softbreak", "hardbreak"):
        return " "
    markup = getattr(child, "markup", "")
    return markup if markup else None


def render_span(children: Sequence[Token]) -> str:
    """Reconstitute a run of already-parsed inline children to Markdown source.

    The counterpart to ``render_inline_children`` for content that has
    already been parsed (not freshly constructed): plain content (text,
    code spans, line breaks, emphasis/strong delimiters) passes through
    ``_span_token_source`` unescaped, recovering the parser's own semantic
    value. An embedded link is reconstructed as ``[text](href)`` -- or
    ``[text](href "title")`` when it carries a title -- via this module's
    one link-building/escaping primitive (``link_children`` +
    ``render_inline_children``), so a link's own text/title *are* escaped
    exactly as needed to survive being freshly re-serialized here, the same
    escaping the write side uses; nested links are not supported (CommonMark
    itself forbids them). Used by ``index.py``'s entry-title/description
    extraction and ``logs.py``'s entry-prose extraction (``parse_index``/
    ``parse_log``) -- every reconstitution of already-parsed content in the
    codebase shares this one function rather than each caller re-deriving
    its own walk.
    """
    parts: list[str] = []
    i = 0
    n = len(children)
    while i < n:
        child = children[i]
        if child.type == "link_open":
            href_attr = child.attrGet("href")
            href = href_attr if isinstance(href_attr, str) else ""
            title_attr = child.attrGet("title")
            title = title_attr if isinstance(title_attr, str) else None
            i += 1
            inner: list[str] = []
            while i < n and children[i].type != "link_close":
                content = _span_token_source(children[i])
                if content is not None:
                    inner.append(content)
                i += 1
            parts.append(
                render_inline_children(link_children(href, "".join(inner), title))
            )
            i += 1  # consume the matching link_close
        else:
            content = _span_token_source(child)
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

"""Tests for the span-partition model of ``_entry_from_inline_token``.

Covers the standalone suffix renderer directly, and pins the malformed-entry
edge cases whose precedence is easy to break when reshaping the parser (#153).
The behavioural contract itself lives in ``test_index.py``; this file guards the
new decomposition and the subtleties that the span model has to reproduce.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st
from markdown_it import MarkdownIt
from markdown_it.token import Token

from okf_core._markdown_inline import _escape_title
from okf_core.index import IndexEntry, _render_suffix_span, parse_index
from okf_core.logs import _has_balanced_parens

_MD = MarkdownIt("commonmark")


def _inline_children(src: str) -> list:
    """Parse an inline fragment and return its child tokens."""
    children = _MD.parseInline(src)[0].children
    assert children is not None
    return children


# ---------------------------------------------------------------------------
# _render_suffix_span — the standalone renderer that reconstitutes suffix text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src,expected",
    [
        (" - hello world", " - hello world"),
        (" - use `foo`", " - use `foo`"),
        ("a\nb", "a b"),  # softbreak collapses to a space
        (" - see [x](y)", " - see [x](y)"),  # single inner link round-trips
        (" - [a](b) and [c](d)", " - [a](b) and [c](d)"),  # multiple inner links
        (" - *em* and **bold**", " - *em* and **bold**"),  # emphasis delimiters
        (
            ' - see [x](y "a title")',
            ' - see [x](y "a title")',
        ),  # inner link title round-trips
        (
            ' - see [x](y "has \\"quotes\\"")',
            ' - see [x](y "has \\"quotes\\"")',
        ),  # inner link title with embedded quote round-trips
    ],
)
def test_render_suffix_span_round_trips(src: str, expected: str) -> None:
    assert _render_suffix_span(_inline_children(src)) == expected


def test_render_suffix_span_empty() -> None:
    assert _render_suffix_span([]) == ""


def test_render_suffix_span_inner_link_empty_href() -> None:
    # A link with no destination still reconstitutes with empty parens.
    assert _render_suffix_span(_inline_children("see [x]()")) == "see [x]()"


# ---------------------------------------------------------------------------
# Hypothesis round-trip: parse -> render_linked_span -> re-parse must recover
# the same text/href/title. `_render_suffix_span` is a direct alias for
# `render_linked_span` (see index.py), so exercising it here also covers the
# shared renderer's other call site in logs.py's entry reconstruction.
# ---------------------------------------------------------------------------

# Ordinary text plus the markdown-significant characters that are safe to
# embed in link *text* unescaped: quotes and parens have no special meaning
# inside `[...]`. `[`, `]`, and `\` are deliberately excluded -- an unescaped
# bracket breaks the enclosing link's own delimiter matching (the same
# limitation `logs.py`'s `_require_representable_concept_id` guards against
# for concept IDs used as link text), and a bare backslash immediately
# followed by punctuation is reinterpreted as an escape sequence on re-parse
# since `render_linked_span` reinserts already-unescaped token content
# verbatim rather than re-escaping it.
_TEXT_ALPHABET = 'ab01 .-"()'
# hrefs and titles are embedded via helpers that escape appropriately for
# their position, so the full markdown-significant alphabet is safe here.
_HREF_ALPHABET = "ab01 .-[]()\\\"'"
_TITLE_ALPHABET = "ab01 .-[]()\\\"'"


def _escape_href_angle(href: str) -> str:
    """Escape a destination for CommonMark's ``<...>`` angle-bracket form.

    Unlike the unencoded ``(href)`` form ``render_linked_span`` itself emits,
    the angle-bracket form doesn't require balanced parens -- only ``<``,
    ``>``, and the escape character itself need escaping -- so a test fixture
    built with it can carry an href alphabet that includes unbalanced parens
    without corrupting the initial parse used to build the "before" tokens.
    """
    return href.replace("\\", "\\\\").replace("<", "\\<").replace(">", "\\>")


def _link_source(text: str, href: str, title: str | None) -> str:
    """Build inline Markdown source for a single link with the given parts."""
    destination = f"<{_escape_href_angle(href)}>"
    if title is None:
        return f"[{text}]({destination})"
    return f'[{text}]({destination} "{_escape_title(title)}")'


def _first_link(children: list[Token]) -> tuple[Token, str]:
    """Return the link_open token and joined inner text of a single-link span."""
    link_open = children[0]
    assert link_open.type == "link_open"
    assert children[-1].type == "link_close"
    inner_text = "".join(
        child.content for child in children[1:-1] if child.type == "text"
    )
    return link_open, inner_text


@given(
    text=st.text(alphabet=_TEXT_ALPHABET, max_size=12),
    href=st.text(alphabet=_HREF_ALPHABET, max_size=12),
    title=st.text(alphabet=_TITLE_ALPHABET, min_size=1, max_size=12),
)
@example(text="x", href="a", title='has "quotes" and \\ backslash')
def test_render_suffix_span_link_round_trips(text: str, href: str, title: str) -> None:
    """A titled link's text/href/title survive parse -> render -> re-parse.

    ``href`` is filtered to balanced parens: ``render_linked_span`` re-emits it
    verbatim in the unencoded ``(href)`` form, and an unbalanced paren there
    breaks the destination grammar on re-parse -- the same representability
    limit ``_require_representable_move_target`` guards against for
    ``_build_move_entry`` in logs.py. Reusing ``_has_balanced_parens`` keeps
    this filter in sync with that real guard instead of drifting from it.

    The explicit backslash-and-quote example exercises ``_escape_title``,
    which is what makes the title (unlike link text) safe to round-trip for
    arbitrary content: it re-escapes on render, whereas link text is
    reinserted unescaped -- hence the narrower ``_TEXT_ALPHABET`` above.

    The normalized href is also filtered to non-empty: CommonMark's unencoded
    destination grammar has no way to write "empty destination, then a
    title" (`( "title")` parses the quoted title itself as a literal,
    percent-encoded href rather than as a separate title) -- distinct from
    the balanced-parens limit above, but likewise a real gap in what
    `render_linked_span`'s destination form can express, not a bug this test
    should chase. The filter is applied to the *normalized* href (after the
    initial parse, which already runs markdown-it's own `normalizeLink`) since
    that -- not the raw generated ``href`` -- is what could collapse to empty
    (e.g. a lone space normalizes away to nothing).
    """
    assume(_has_balanced_parens(href))

    first = _inline_children(_link_source(text, href, title))
    link1, text1 = _first_link(first)
    assume(link1.attrGet("href") != "")

    second = _inline_children(_render_suffix_span(first))
    link2, text2 = _first_link(second)

    assert text2 == text1 == text
    assert link2.attrGet("href") == link1.attrGet("href")
    # The title round-trips to the same content regardless of the source
    # delimiter style used to write it (`_render_link_destination` always
    # canonicalizes to double-quoted form) -- here the source is already
    # double-quoted, so the recovered title matches the generated title
    # exactly rather than merely up to re-quoting.
    assert link2.attrGet("title") == link1.attrGet("title") == title


@given(
    text=st.text(alphabet=_TEXT_ALPHABET, max_size=12),
    href=st.text(alphabet=_HREF_ALPHABET, max_size=12),
)
def test_render_suffix_span_link_round_trips_without_title(
    text: str, href: str
) -> None:
    """A titleless link stays titleless across the same round trip.

    Guards against a title being spuriously introduced by the render step --
    `_render_link_destination` must keep treating "no title" as "no title",
    not e.g. an empty-string title that then renders as `""`.
    """
    assume(_has_balanced_parens(href))

    first = _inline_children(_link_source(text, href, title=None))
    link1, text1 = _first_link(first)
    assert link1.attrGet("title") is None

    second = _inline_children(_render_suffix_span(first))
    link2, text2 = _first_link(second)

    assert text2 == text1 == text
    assert link2.attrGet("href") == link1.attrGet("href")
    assert link2.attrGet("title") is None


# ---------------------------------------------------------------------------
# Precedence / edge cases the span model must reproduce
# ---------------------------------------------------------------------------


def test_empty_href_is_missing_target_not_missing_title() -> None:
    # href="" is falsy, so it fails the target check *before* the title check.
    parsed = parse_index("# S\n\n* [x]()\n")
    assert parsed.sections[0].entries == ()
    assert parsed.problems[0].message.endswith("missing link target")


def test_space_only_title_is_accepted() -> None:
    # A single whitespace child is renderable content, so the title is not empty.
    parsed = parse_index("# S\n\n* [ ](a.md)\n")
    assert parsed.sections[0].entries == (
        IndexEntry(title=" ", link="a.md", description=None),
    )
    assert parsed.problems == ()


def test_link_inside_description_is_captured() -> None:
    parsed = parse_index("# S\n\n* [A](a.md) - see [B](b.md)\n")
    assert parsed.sections[0].entries == (
        IndexEntry(title="A", link="a.md", description="see [B](b.md)"),
    )


def test_second_link_outside_description_is_rejected() -> None:
    parsed = parse_index("# S\n\n* [A](a.md) and [B](b.md)\n")
    assert parsed.sections[0].entries == ()
    assert parsed.problems[0].message.endswith(
        "additional links must be in a description"
    )

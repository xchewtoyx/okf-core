"""Tests for the span-partition model of ``_entry_from_inline_token`` (#153)
and the shared parse-side reconstitution primitive it (and ``logs.py``) now
share: ``markdown_engine.render_span`` (#199).

The behavioural contract for ``parse_index`` itself lives in
``test_index.py``; this file guards the decomposition and the subtleties the
span model has to reproduce, plus property-tests ``render_span``'s round-trip
directly -- it's the one place both ``index.py`` and ``logs.py`` reconstitute
already-parsed inline content back to Markdown source, so a bug here would
affect both modules identically.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st
from markdown_it import MarkdownIt
from markdown_it.token import Token

from okf_core.index import IndexEntry, parse_index
from okf_core.markdown_engine import render_span

_MD = MarkdownIt("commonmark")


def _inline_children(src: str) -> list:
    """Parse an inline fragment and return its child tokens."""
    children = _MD.parseInline(src)[0].children
    assert children is not None
    return children


# ---------------------------------------------------------------------------
# render_span -- the shared reconstitution primitive (markdown_engine.py)
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
        # #199: an inner link's own text is now escaped by the shared engine
        # on reconstitution (via link_children + render_inline_children),
        # rather than reinserted raw -- so a bracket inside link text, which
        # the pre-#199 render_linked_span could not round-trip, now does.
        (" - see [a\\]b](y)", " - see [a\\]b](y)"),
    ],
)
def test_render_span_round_trips(src: str, expected: str) -> None:
    assert render_span(_inline_children(src)) == expected


def test_render_span_empty() -> None:
    assert render_span([]) == ""


def test_render_span_inner_link_empty_href() -> None:
    # A link with no destination still reconstitutes as a link -- #199: the
    # shared engine renders an empty href via CommonMark's `<...>`
    # angle-bracket destination form rather than the pre-#199 bare `()`, so
    # this checks the recovered (empty) href semantically rather than one
    # specific rendered byte sequence.
    rendered = render_span(_inline_children("[x]()"))
    children = _inline_children(rendered)
    assert children[0].type == "link_open"
    assert children[0].attrGet("href") == ""


# ---------------------------------------------------------------------------
# Hypothesis round-trip: parse -> render_span -> re-parse must recover the
# same text/href/title. `render_span` is shared by index.py's suffix/title
# extraction and logs.py's entry-prose extraction -- exercising it here
# covers both call sites.
# ---------------------------------------------------------------------------

# Ordinary text plus the markdown-significant characters that are safe to
# embed in link *text* -- including `[`, `]`, and `\` (#199 AC1): unlike the
# pre-#199 `render_linked_span`, which reinserted already-unescaped token
# content verbatim, `render_span` reconstructs a link's own text via the
# shared engine's escaping (`link_children` + `render_inline_children`), so
# these round-trip correctly now instead of needing to be excluded.
_TEXT_ALPHABET = 'ab01 .-"()[]\\'
# hrefs and titles are embedded via helpers that escape appropriately for
# their position, so the full markdown-significant alphabet is safe here.
_HREF_ALPHABET = "ab01 .-[]()\\\"'"
_TITLE_ALPHABET = "ab01 .-[]()\\\"'"


def _escape_href_angle(href: str) -> str:
    """Escape a destination for CommonMark's ``<...>`` angle-bracket form.

    Unlike the unencoded ``(href)`` form, the angle-bracket form doesn't
    require balanced parens -- only ``<``, ``>``, and the escape character
    itself need escaping -- so a test fixture built with it can carry an
    href alphabet that includes unbalanced parens without corrupting the
    initial parse used to build the "before" tokens.
    """
    return href.replace("\\", "\\\\").replace("<", "\\<").replace(">", "\\>")


def _escape_title(title: str) -> str:
    """Escape a title for embedding in a double-quoted CommonMark title."""
    return title.replace("\\", "\\\\").replace('"', '\\"')


def _escape_link_text(text: str) -> str:
    """Escape text for embedding as literal CommonMark link text."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _link_source(text: str, href: str, title: str | None) -> str:
    """Build inline Markdown source for a single link with the given parts."""
    destination = f"<{_escape_href_angle(href)}>"
    escaped_text = _escape_link_text(text)
    if title is None:
        return f"[{escaped_text}]({destination})"
    return f'[{escaped_text}]({destination} "{_escape_title(title)}")'


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
@example(text="", href="", title="\\")
@example(text="x", href="a", title='has "quotes" and \\ backslash')
def test_render_span_link_round_trips(text: str, href: str, title: str) -> None:
    """A titled link's text/href/title survive parse -> render -> re-parse.

    Unlike the pre-#199 version of this test, ``href`` is not filtered to
    balanced parens: ``render_span`` reconstructs the link via
    ``link_children``/``render_inline_children``, which represents an
    unbalanced-paren href using CommonMark's ``<...>`` angle-bracket
    destination form rather than requiring the caller to pre-filter it.

    The title round-trips to the same content regardless of the source
    delimiter style used to write it (the shared engine always canonicalizes
    to double-quoted form) -- here the source is already double-quoted, so
    the recovered title matches the generated title exactly rather than
    merely up to re-quoting.

    A run of two or more consecutive spaces in ``text`` is excluded: the
    shared engine's canonical renderer collapses an internal whitespace run
    in literal text content to a single space when reconstructing the link
    (the same "canonical, not byte-identical" convergence ADR-0002 documents
    elsewhere) -- a deliberate normalization, not the identity function this
    test otherwise checks for every other character in the alphabet.
    """
    assume("  " not in text)
    first = _inline_children(_link_source(text, href, title))
    link1, text1 = _first_link(first)

    second = _inline_children(render_span(first))
    link2, text2 = _first_link(second)

    assert text2 == text1 == text
    assert link2.attrGet("href") == link1.attrGet("href")
    assert link2.attrGet("title") == link1.attrGet("title") == title


@given(
    text=st.text(alphabet=_TEXT_ALPHABET, max_size=12),
    href=st.text(alphabet=_HREF_ALPHABET, max_size=12),
)
def test_render_span_link_round_trips_without_title(text: str, href: str) -> None:
    """A titleless link stays titleless across the same round trip.

    Guards against a title being spuriously introduced by the render step.
    See ``test_render_span_link_round_trips`` for why a run of consecutive
    spaces in ``text`` is excluded.
    """
    assume("  " not in text)
    first = _inline_children(_link_source(text, href, title=None))
    link1, text1 = _first_link(first)
    assert link1.attrGet("title") is None

    second = _inline_children(render_span(first))
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

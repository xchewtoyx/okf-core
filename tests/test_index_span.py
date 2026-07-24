"""Tests for the span-partition model of ``_entry_from_inline_token``.

Covers the standalone suffix renderer directly, and pins the malformed-entry
edge cases whose precedence is easy to break when reshaping the parser (#153).
The behavioural contract itself lives in ``test_index.py``; this file guards the
new decomposition and the subtleties that the span model has to reproduce.
"""

from __future__ import annotations

import pytest
from markdown_it import MarkdownIt

from okf_core.index import IndexEntry, _render_suffix_span, parse_index

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

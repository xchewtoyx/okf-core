from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pytest

from okf_core import (
    FootnoteOccurrence,
    ValidationFinding,
    check_attribution_consistency,
    extract_footnote_occurrences,
)

# ---------------------------------------------------------------------------
# extract_footnote_occurrences
# ---------------------------------------------------------------------------


def test_extract_reference_occurrence_reports_label_and_line() -> None:
    body = "Intro line.\n\nA claim.[^ga4-schema]\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="ga4-schema", line=3, is_definition=False),
    )


def test_extract_definition_occurrence_is_distinguished_from_reference() -> None:
    body = "A claim.[^ga4-schema]\n\n[^ga4-schema]: GA4 BigQuery Export schema\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="ga4-schema", line=1, is_definition=False),
        FootnoteOccurrence(label="ga4-schema", line=3, is_definition=True),
    )


def test_extract_occurrence_advances_line_across_soft_breaks() -> None:
    body = "First line continues\nsecond line claim.[^label]\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="label", line=2, is_definition=False),
    )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("Inline code `[^codelabel]` is not a citation.\n", id="code-span"),
        pytest.param(
            "```\n[^fencedlabel]\n```\n",
            id="fenced-code-block",
        ),
        pytest.param(
            "    [^indentedlabel]\n",
            id="indented-code-block",
        ),
    ],
)
def test_extract_ignores_labels_inside_code(body: str) -> None:
    assert extract_footnote_occurrences(body) == ()


def test_extract_finds_occurrences_immediately_before_and_after_a_code_span() -> None:
    """Boundary check for the token-type-skip design (round 7): a
    ``[^label]`` sitting immediately before or after a code span -- i.e. in
    its own ``text`` child adjacent to a ``code_inline`` token -- must still
    be found. Only a label whose text lives *inside* the ``code_inline``
    token itself (like ``[^inside]`` here) is skipped, because
    ``code_inline`` is never a ``text``-type child."""
    body = "See.[^before]`middle`[^after] and `[^inside]` skip.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="before", line=1, is_definition=False),
        FootnoteOccurrence(label="after", line=1, is_definition=False),
    )


def test_extract_does_not_merge_adjacent_fenced_code_block_ranges() -> None:
    """Two separate fenced code blocks are each their own ``fence`` block
    token with no ``inline`` children at all -- a real reference between
    them lives in its own ``inline`` token and must still be found, not
    lost just because it happens to sit between two fences that are,
    individually, correctly invisible to the inline-only walk."""
    body = (
        "```\n"
        "[^first]\n"
        "```\n"
        "\n"
        "Real claim.[^real]\n"
        "\n"
        "```\n"
        "[^second]\n"
        "```\n"
    )
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="real", line=5, is_definition=False),
    )


def test_extract_finds_occurrence_after_code_span_preceded_by_unmatched_backtick_pair() -> (
    None
):
    """An unmatched ```` `` ```` (two backticks with no later closing pair of
    the same length) earlier in the paragraph is literal text, not a code
    delimiter -- CommonMark's own tokenizer resolves the real, later
    ``` `code` ``` span using single backticks and hands it back as one
    ``code_inline`` token. This design never re-derives backtick positions
    itself, so the stray unmatched pair (living inside a plain ``text``
    child) is simply prose to the regex scan, with no opportunity to be
    mis-anchored against."""
    body = "a`` `code`[^after] b\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="after", line=1, is_definition=False),
    )


def test_extract_ignores_label_inside_literal_triple_backtick_text_within_quad_fence() -> (
    None
):
    """A fenced block delimited with four backticks can contain literal
    lines that themselves look like a triple-backtick fence -- those inner
    lines are inert content, not a nested fence (CommonMark has no fence
    nesting). The whole outer fence is one ``fence`` block token with no
    ``inline`` children, so its content -- including text that merely looks
    like a nested fence -- is never visited by the inline-only walk at all,
    not confused by the fence-shaped text inside it."""
    body = "````\n```\n[^fake-label]\n```\n````\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_no_footnotes_returns_empty_tuple() -> None:
    assert extract_footnote_occurrences("Just plain prose, no citations.\n") == ()


def test_extract_finds_both_occurrences_when_definition_is_url_shaped() -> None:
    """A ``[^label]: https://...`` definition text is itself a valid link
    destination: markdown-it's ``reference`` block rule (round 1's original
    trigger shape) would otherwise consume the definition line as a genuine
    reference definition and rewrite the earlier ``[^label]`` into a
    resolved link span with no plain-text trace left for a token scan to
    find. This module disables the ``reference`` rule entirely (module
    docstring, "Round 7"), so both occurrences stay literal ``text``
    children and are found directly by the regex scan, independent of how
    markdown-it would otherwise have resolved the surrounding syntax."""
    body = "A claim.[^ga4-schema]\n\n[^ga4-schema]: https://example.com/schema\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="ga4-schema", line=1, is_definition=False),
        FootnoteOccurrence(label="ga4-schema", line=3, is_definition=True),
    )


def test_extract_finds_reference_immediately_followed_by_parenthetical() -> None:
    """A ``[^label]`` immediately followed by ``(...)`` -- a plausible
    inline-citation authoring pattern -- parses as a real ``[text](dest)``
    link via markdown-it's *inline* ``link`` rule (round 2's original
    trigger shape), independent of the (disabled) ``reference`` rule. The
    closed-set ``link_open``/``text``/``link_close`` reconstruction
    (``_bracket_reconstructed_label``) recovers the label from that link
    token triple directly, without needing to know it was ever a bracketed
    footnote in the source."""
    body = "A claim citing an appendix.[^ga4-schema](appendix-a)\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="ga4-schema", line=1, is_definition=False),
    )


def test_extract_ignores_link_with_emphasis_wrapped_caret_label() -> None:
    """``[*^ga4-schema*](appendix-a)`` -- a caret label wrapped in emphasis
    inside link text -- must not be reconstructed: the ``link_open``
    reconstruction only accepts a *direct* ``text`` child immediately
    followed by ``link_close`` (module docstring / ``_bracket_reconstructed_label``),
    and here the direct child is ``em_open``, not ``text`` (the caret text
    lives one level deeper, inside the ``em_open``/``em_close`` pair). A
    reconstruction that instead searched *any* descendant's content for a
    bare-label match would wrongly recognize this as citing
    ``ga4-schema``; requiring a direct child keeps the closed-set
    reconstruction from over-matching into arbitrarily nested link text."""
    body = "A claim.[*^ga4-schema*](appendix-a)\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_ignores_link_with_empty_text_near_end_of_children() -> None:
    """Defensive out-of-bounds guard in ``_bracket_reconstructed_label``:
    ``[](appendix-a)`` -- a link with empty text -- produces a ``link_open``
    immediately followed by ``link_close`` with no intervening ``text``
    child at all, and ``link_open`` is the second-to-last token overall.
    ``open_index + 2`` then indexes past the end of ``children``; the guard
    must decline (return ``None``) rather than raise an ``IndexError``."""
    body = "A claim.[](appendix-a)\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_ignores_link_text_split_by_soft_break_before_close() -> None:
    """Defensive guard in ``_bracket_reconstructed_label`` for a
    ``close_token.type != "link_close"`` triple: a soft line break inside
    the link text before the closing bracket (``"[^label\\n](url)"``)
    produces ``link_open``, ``text("^label")``, ``softbreak``,
    ``link_close`` -- four tokens, not the exact three-token
    ``link_open``/``text``/``link_close`` triple the reconstruction
    requires. The token immediately after the ``text`` child is
    ``softbreak``, not ``link_close``, so the guard must decline rather than
    guess at a label that was never cleanly bracketed."""
    body = "A claim.[^label\n](appendix-a)\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_ignores_image_style_caret_label_exact_match() -> None:
    """``![^label](url)`` -- an image-style caret label whose alt text is
    *exactly* ``^label`` -- is not recognized. Images are out of scope
    entirely (module docstring, "Images are out of scope"): an ``image``
    token is never inspected for footnote syntax, so this is inert, the
    same as any other prose, not a special-cased recognition."""
    body = "See the chart.\n\n![^chart-src](https://example.com/chart.png)\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_ignores_image_style_caret_label_non_trivial_alt_text() -> None:
    """Companion to the exact-match case above: non-trivial image alt text
    containing a label, e.g. ``![Diagram [^label]](url.png)``, is likewise
    not recognized -- not because it fails some content match, but because
    ``image`` tokens are never inspected at all. This is the same,
    uniformly-documented behavior as the exact-match case, not an
    inconsistency (this was the round 7 recurrence #7 finding: the old
    exact-match-only check made this shape silently invisible in a way that
    looked like a bug rather than a documented limitation)."""
    body = (
        "See the diagram.\n\n![Diagram [^chart-src]](https://example.com/chart.png)\n"
    )

    assert extract_footnote_occurrences(body) == ()


def test_extract_ignores_label_containing_nested_autolink() -> None:
    """Round 7 narrows the label charset to ``[A-Za-z0-9-]+``. A label
    containing an autolink (round 3's original trigger shape) is no longer
    slug-shaped, so ``[^lab<http://x.com>el]`` is not recognized as
    footnote syntax at all -- not an error, just inert, the same as any
    other prose containing stray brackets and an autolink."""
    body = "A claim.[^lab<http://x.com>el]\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_ignores_label_with_underscore() -> None:
    """Underscore is deliberately excluded from the label charset (module
    docstring, "Round 7") to sidestep CommonMark's intraword-emphasis
    flanking rules entirely. A label containing one is not slug-shaped and
    is not recognized -- inert, not an error."""
    body = "A claim.[^label_with_underscore]\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_ignores_label_with_internal_space() -> None:
    """Whitespace is not in the label charset. ``[^label with spaces]`` is
    not recognized as footnote syntax -- inert, not an error."""
    body = "A claim.[^label with spaces]\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_ignores_match_immediately_followed_by_label_charset_char() -> None:
    """A valid-shaped ``[^label]`` immediately followed by another
    label-charset character, with no boundary in between, is not
    recognized -- see the module docstring's boundary-lookahead rationale.
    This must not be silently truncated to citing ``label``; it must be
    fully inert."""
    body = "A claim.[^label]with-trailing\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_finds_valid_slug_label_immediately_followed_by_non_slug_char() -> None:
    """Control for the above: a valid slug label immediately followed by a
    character that is *not* in the label charset (here, a period) is
    recognized normally -- the boundary check only rejects a directly
    adjacent label-charset character, not any character at all."""
    body = "A claim.[^ga4-schema].\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="ga4-schema", line=1, is_definition=False),
    )


def test_extract_finds_valid_slug_label_inside_emphasis() -> None:
    """A valid slug label wrapped in emphasis is recognized normally --
    emphasis wraps the surrounding text without splitting it (module
    docstring, "Round 7": emphasis triggers only on ``*``/``_``, neither of
    which can appear in a slug label), so the label's own ``[^...]`` text
    survives as one intact ``text`` child."""
    body = "*[^ga4-schema]*\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="ga4-schema", line=1, is_definition=False),
    )


def test_extract_ignores_real_code_span_after_image_with_backtick_caption() -> None:
    """Round 4's original trigger shape (now moot, kept as a regression
    guard): an ``image`` token's alt text can itself contain a backtick pair
    (rendered into ``.content`` as a caption), historically confused with a
    following real code span. Since images are now out of scope entirely
    (module docstring, "Images are out of scope"), the ``image`` token
    contributes nothing regardless of what its alt text contains; the real
    ``[^hidden]`` still sits inside its own ``code_inline`` token (the
    `` `[^hidden]` `` span) and is correctly skipped by the token-type
    check, independent of the image."""
    body = "![diagram `caption`](img.png) See `[^hidden]` in the code.\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_ignores_code_span_after_image_with_plain_alt_text() -> None:
    """Control for the above: an image with no backticks in its alt text
    is unaffected either way -- the ``image`` token still contributes
    nothing, and the real code span after it is still skipped because it is
    a ``code_inline`` token, not because of anything about the preceding
    image."""
    body = "![plain diagram](img.png) See `[^hidden]` in the code.\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_finds_occurrence_after_code_span_preceded_by_html_comment_backtick() -> (
    None
):
    """Round 5's original trigger shape: an ``html_inline`` token (e.g. an
    HTML comment) can contain a literal backtick that has nothing to do with
    any code span. This design never re-derives a ``code_inline`` token's
    backtick position from raw text at all -- it trusts markdown_it's own
    tokenization, so the comment's stray backtick (opaque ``html_inline``
    content) and the real ``` `real` ``` code span (its own ``code_inline``
    token) can never be confused with one another; the footnote label
    between them lives in a plain ``text`` child and is found directly."""
    body = "Some text <!-- a ` stray backtick --> [^mylabel] and `real` code.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="mylabel", line=1, is_definition=False),
    )


def test_extract_finds_occurrence_after_code_span_preceded_by_autolink_backtick() -> (
    None
):
    """Round 5's other original trigger shape: an autolink's URI is legal
    CommonMark even when it contains a literal backtick (autolink content is
    not escape/entity-processed). This design has no backtick-delimiter
    search of its own to confuse -- it trusts markdown_it's own
    tokenization directly, so the autolink's internal token structure and
    the real ``` `real` ``` span's own, distinct ``code_inline`` token can
    never be mispaired with one another. The footnote label between them,
    a plain ``text`` child, is found directly by the regex scan."""
    body = "See <http://example.com/a`b> [^mylabel] and then `real` code.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="mylabel", line=1, is_definition=False),
    )


def test_extract_finds_occurrence_after_code_span_preceded_by_link_destination_backtick() -> (
    None
):
    """Round 5's third original trigger shape: a regular ``[text](url)``
    link's destination is not represented by any inline child token at all
    (only the visible link text is) -- a backtick inside the destination is
    genuinely invisible to this token-type-based scan, not merely
    unmatched. The real code span is still its own distinct ``code_inline``
    token, and the footnote label between them, a plain ``text`` child, is
    found directly by the regex scan."""
    body = "See [text](http://example.com/a`b) [^mylabel] and then `real` code.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="mylabel", line=1, is_definition=False),
    )


def test_extract_finds_occurrence_after_code_span_preceded_by_escaped_backtick() -> (
    None
):
    """Round 6's original trigger shape: a backslash-escaped backtick
    (``\\```) in ordinary prose is resolved by markdown_it into a literal
    backtick character inside a plain ``text`` token's ``.content`` -- the
    escape syntax itself leaves no trace once parsed. This design never
    inspects raw source text or backtick characters directly; the escaped
    backtick is just prose content of a ``text`` child (not matched by
    ``_FOOTNOTE_RE``), the real code span is still its own distinct
    ``code_inline`` token, and the footnote label between them is found
    directly."""
    body = "Some text \\` still prose [^mylabel] and `real` code.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="mylabel", line=1, is_definition=False),
    )


def test_extract_finds_both_spans_with_repeated_html_comments_between_them() -> None:
    """Generality check: the design must not depend on there being exactly
    one confusing construct in the paragraph. Two separate HTML comments,
    each with their own stray backtick, sit before two separate real code
    spans -- since token-type skipping treats every ``html_inline`` and
    every ``code_inline`` token independently by its own type, not by
    position or count, this holds regardless of how many such constructs
    appear."""
    body = "A <!-- ` --> [^one] `code1` B <!-- ` --> [^two] `code2` C\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="one", line=1, is_definition=False),
        FootnoteOccurrence(label="two", line=1, is_definition=False),
    )


def test_extract_ignores_label_containing_nested_code_span() -> None:
    """Round 3's other original trigger shape (a code span nested inside
    the label, `` [^lab`code`el] ``) is no longer slug-shaped under the
    round 7 charset restriction, so it is not recognized -- inert, not an
    error."""
    body = "A claim.[^lab`code`el]\n"

    assert extract_footnote_occurrences(body) == ()


# ---------------------------------------------------------------------------
# check_attribution_consistency
# ---------------------------------------------------------------------------


def test_clean_document_matching_label_and_source_reports_nothing() -> None:
    frontmatter = {"sources": [{"id": "ga4-schema", "resource": "https://example.com"}]}
    body = "A claim.[^ga4-schema]\n\n[^ga4-schema]: GA4 BigQuery Export schema\n"

    assert check_attribution_consistency(frontmatter, body) == ()


def test_no_footnotes_and_no_sources_reports_nothing() -> None:
    assert check_attribution_consistency({}, "Plain prose with no citations.\n") == ()


def test_clean_document_with_url_shaped_definition_reports_nothing() -> None:
    """Companion to ``test_extract_finds_both_occurrences_when_definition_is_url_shaped``
    at the ``check_attribution_consistency`` layer: a clean label/id match must
    not be misreported as an unreferenced-source warning just because the
    footnote definition text happens to be a bare URL."""
    frontmatter = {"sources": [{"id": "ga4-schema", "resource": "https://example.com"}]}
    body = "A claim.[^ga4-schema]\n\n[^ga4-schema]: https://example.com/schema\n"

    assert check_attribution_consistency(frontmatter, body) == ()


def test_clean_document_with_parenthetical_adjacent_reference_reports_nothing() -> None:
    """Companion to ``test_extract_finds_reference_immediately_followed_by_parenthetical``
    at the ``check_attribution_consistency`` layer: a clean label/id match must
    not be misreported as a dangling footnote (false positive) just because the
    reference happens to sit immediately before a parenthetical."""
    frontmatter = {"sources": [{"id": "ga4-schema", "resource": "https://example.com"}]}
    body = "A claim citing an appendix.[^ga4-schema](appendix-a)\n"

    assert check_attribution_consistency(frontmatter, body) == ()


def test_dangling_parenthetical_adjacent_reference_is_an_error_with_location() -> None:
    """A dangling footnote reference immediately followed by a parenthetical
    must still be reported as an error, not silently dropped as a false
    negative, per the inline ``link``-rule-consumption bug covered above."""
    frontmatter: dict[str, Any] = {}
    body = "A claim citing an appendix.[^missing-label](appendix-a)\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="error",
            message="Footnote label 'missing-label' has no matching sources[].id",
            field="missing-label",
            line=1,
        ),
    )


def test_source_id_only_cited_via_image_style_label_is_unreferenced_warning() -> None:
    """Companion to ``test_extract_ignores_image_style_caret_label_exact_match``
    at the ``check_attribution_consistency`` layer: since images are out of
    scope entirely, a ``sources[].id`` that is only ever "cited" via an
    image-style ``![^label](url)`` alt text is never actually joined to
    anything -- it surfaces as the advisory "unreferenced source" warning,
    not a clean match. This is the documented limitation (module docstring,
    "Images are out of scope"), not a bug."""
    frontmatter = {"sources": [{"id": "chart-src", "resource": "https://example.com"}]}
    body = "See the chart.\n\n![^chart-src](https://example.com/chart.png)\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="warning",
            message="sources[].id 'chart-src' is not referenced by any footnote",
            field="chart-src",
            line=None,
        ),
    )


def test_image_style_caret_label_with_no_matching_source_reports_nothing() -> None:
    """An image-style caret label with no matching ``sources[].id`` is *not*
    reported as a dangling-footnote error -- images are out of scope
    entirely (module docstring, "Images are out of scope"), so
    ``![^missing-chart](url)`` is never recognized as footnote syntax at
    all, the same as any other prose. This replaces the pre-scoping
    behavior where this shape was (inconsistently) recognized as a dangling
    reference."""
    frontmatter: dict[str, Any] = {}
    body = "See the chart.\n\n![^missing-chart](https://example.com/chart.png)\n"

    assert check_attribution_consistency(frontmatter, body) == ()


def test_clean_document_with_real_code_span_after_image_caption_reports_nothing() -> (
    None
):
    """Companion to
    ``test_extract_ignores_real_code_span_after_image_with_backtick_caption``
    at the ``check_attribution_consistency`` layer: with no ``sources`` at
    all, a false-positive dangling-footnote finding for the code-span-
    protected ``[^hidden]`` would be the observable symptom of a code-span
    exclusion regression; the preceding image (out of scope regardless of
    its alt text) must not affect that."""
    body = "![diagram `caption`](img.png) See `[^hidden]` in the code.\n"

    assert check_attribution_consistency({}, body) == ()


def test_clean_document_with_label_after_html_comment_backtick_reports_nothing() -> (
    None
):
    """Companion to
    ``test_extract_finds_occurrence_after_code_span_preceded_by_html_comment_backtick``
    at the ``check_attribution_consistency`` layer: a clean label/id match
    must not be misreported as dangling just because an HTML comment with a
    stray backtick precedes it."""
    frontmatter = {"sources": [{"id": "mylabel", "resource": "https://example.com"}]}
    body = "Some text <!-- a ` stray backtick --> [^mylabel] and `real` code.\n"

    assert check_attribution_consistency(frontmatter, body) == ()


def test_dangling_label_after_html_comment_backtick_is_an_error_with_location() -> None:
    """A dangling footnote label preceded by an HTML comment with a stray
    backtick must still be reported, not silently swallowed by a wrongly
    widened code-span exclusion range."""
    frontmatter: dict[str, Any] = {}
    body = "Some text <!-- a ` stray backtick --> [^missing] and `real` code.\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="error",
            message="Footnote label 'missing' has no matching sources[].id",
            field="missing",
            line=1,
        ),
    )


def test_declared_source_id_shaped_like_nested_autolink_label_is_unreferenced() -> None:
    """Companion to ``test_extract_ignores_label_containing_nested_autolink``
    at the ``check_attribution_consistency`` layer: since a
    ``[^lab<http://x.com>el]``-shaped construct is no longer recognized as
    footnote syntax at all (round 7), a ``sources[].id`` with that exact
    (non-slug-shaped) value can never be matched by any footnote reference
    under this check -- it always surfaces as the advisory
    "unreferenced source" warning, per the module docstring's "Narrowed
    contract" section. This is expected, not a bug."""
    frontmatter = {
        "sources": [{"id": "lab<http://x.com>el", "resource": "https://example.com"}]
    }
    body = "A claim.[^lab<http://x.com>el]\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="warning",
            message="sources[].id 'lab<http://x.com>el' is not referenced by any footnote",
            field="lab<http://x.com>el",
            line=None,
        ),
    )


def test_non_slug_shaped_label_construct_reports_nothing_with_no_sources() -> None:
    """A ``[^missing<http://x.com>label]``-shaped construct is invisible to
    this check under the round 7 charset restriction -- not an error, since
    it is not recognized as footnote syntax at all."""
    frontmatter: dict[str, Any] = {}
    body = "A claim.[^missing<http://x.com>label]\n"

    assert check_attribution_consistency(frontmatter, body) == ()


def test_declared_source_id_shaped_like_nested_code_span_label_is_unreferenced() -> (
    None
):
    """Companion to ``test_extract_ignores_label_containing_nested_code_span``
    at the ``check_attribution_consistency`` layer -- see the analogous
    autolink-label companion test above for the "always unreferenced"
    rationale."""
    frontmatter = {
        "sources": [{"id": "lab`code`el", "resource": "https://example.com"}]
    }
    body = "A claim.[^lab`code`el]\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="warning",
            message="sources[].id 'lab`code`el' is not referenced by any footnote",
            field="lab`code`el",
            line=None,
        ),
    )


def test_non_slug_shaped_code_span_label_construct_reports_nothing_with_no_sources() -> (
    None
):
    frontmatter: dict[str, Any] = {}
    body = "A claim.[^missing`code`label]\n"

    assert check_attribution_consistency(frontmatter, body) == ()


def test_dangling_footnote_with_url_shaped_definition_is_an_error_with_location() -> (
    None
):
    """A dangling footnote whose definition is a bare URL must still be
    reported as an error, not silently dropped, per the same
    reference-rule-consumption bug covered above."""
    frontmatter: dict[str, Any] = {}
    body = "A claim.[^missing]\n\n[^missing]: https://example.com/schema\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="error",
            message="Footnote label 'missing' has no matching sources[].id",
            field="missing",
            line=1,
        ),
        ValidationFinding(
            severity="error",
            message="Footnote label 'missing' has no matching sources[].id",
            field="missing",
            line=3,
        ),
    )


def test_dangling_footnote_reference_is_an_error_with_location() -> None:
    frontmatter: dict[str, Any] = {}
    body = "A claim.[^missing]\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="error",
            message="Footnote label 'missing' has no matching sources[].id",
            field="missing",
            line=1,
        ),
    )


def test_dangling_footnote_definition_is_an_error_with_location() -> None:
    frontmatter: dict[str, Any] = {}
    body = "[^missing]: Some definition text.\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="error",
            message="Footnote label 'missing' has no matching sources[].id",
            field="missing",
            line=1,
        ),
    )


def test_unreferenced_source_is_a_warning_without_a_line() -> None:
    frontmatter = {
        "sources": [{"id": "unused-source", "resource": "https://example.com"}]
    }
    body = "Prose with no footnotes at all.\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="warning",
            message="sources[].id 'unused-source' is not referenced by any footnote",
            field="unused-source",
            line=None,
        ),
    )


def test_mixed_document_reports_both_dangling_error_and_unreferenced_warning() -> None:
    frontmatter = {
        "sources": [
            {"id": "used-source", "resource": "https://example.com/a"},
            {"id": "unused-source", "resource": "https://example.com/b"},
        ]
    }
    body = "A claim.[^used-source] Another claim.[^dangling-label]\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="error",
            message="Footnote label 'dangling-label' has no matching sources[].id",
            field="dangling-label",
            line=1,
        ),
        ValidationFinding(
            severity="warning",
            message="sources[].id 'unused-source' is not referenced by any footnote",
            field="unused-source",
            line=None,
        ),
    )


def test_repeated_dangling_label_reports_one_finding_per_occurrence() -> None:
    frontmatter: dict[str, Any] = {}
    body = "First claim.[^missing] Second claim.[^missing]\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert len(findings) == 2
    assert all(f.severity == "error" and f.field == "missing" for f in findings)


@pytest.mark.parametrize(
    "sources",
    [
        pytest.param([{"resource": "https://example.com"}], id="entry-without-id"),
        pytest.param([{"id": "", "resource": "https://example.com"}], id="blank-id"),
        pytest.param("not-a-list", id="sources-not-a-list"),
        pytest.param(["not-a-dict"], id="entry-not-a-dict"),
    ],
)
def test_non_joinable_sources_are_silently_skipped(sources: object) -> None:
    frontmatter = {"sources": sources}

    assert check_attribution_consistency(frontmatter, "Plain prose.\n") == ()


def test_frozen_manifest_shaped_sources_are_joined_like_plain_dicts() -> None:
    """``ConceptManifestEntry.frontmatter`` freezes lists to tuples and dicts to
    ``MappingProxyType`` (see ``manifest._freeze_value``); the join must accept
    that shape, not just a plain ``list[dict]``, since that is what
    ``validate_bundle`` actually passes in."""
    frontmatter = MappingProxyType(
        {
            "sources": (
                MappingProxyType({"id": "src-a", "resource": "https://example.com"}),
            )
        }
    )
    body = "A claim.[^src-a]\n"

    assert check_attribution_consistency(frontmatter, body) == ()


def test_duplicate_source_ids_deduplicate_unreferenced_warning() -> None:
    frontmatter = {
        "sources": [
            {"id": "dup", "resource": "https://example.com/a"},
            {"id": "dup", "resource": "https://example.com/b"},
        ]
    }

    findings = check_attribution_consistency(frontmatter, "No citations here.\n")

    assert findings == (
        ValidationFinding(
            severity="warning",
            message="sources[].id 'dup' is not referenced by any footnote",
            field="dup",
            line=None,
        ),
    )

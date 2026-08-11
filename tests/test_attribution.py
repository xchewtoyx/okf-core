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
    """Boundary check for the new range-exclusion design: a ``[^label]``
    whose ``[`` sits at the exact char offset immediately before a code
    span's opening backtick, or immediately after its closing backtick,
    must not be treated as inside that span -- only a label whose ``[``
    falls strictly between the delimiters is excluded."""
    body = "See.[^before]`middle`[^after] and `[^inside]` skip.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="before", line=1, is_definition=False),
        FootnoteOccurrence(label="after", line=1, is_definition=False),
    )


def test_extract_does_not_merge_adjacent_fenced_code_block_ranges() -> None:
    """Two separate fenced code blocks must each exclude only their own
    line range -- a real reference sitting between them must still be
    found, not swallowed by a naive "first fence start to last fence end"
    range."""
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
    delimiter -- CommonMark still finds the real, later ``` `code` ``` span
    using single backticks. Locating that span's exact offsets must reject
    the false two-backtick candidate and keep scanning rather than
    mis-anchoring on it."""
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
    nesting). The whole outer fence's line range must still be excluded as
    one contiguous block, not confused by the fence-shaped text inside it."""
    body = "````\n```\n[^fake-label]\n```\n````\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_no_footnotes_returns_empty_tuple() -> None:
    assert extract_footnote_occurrences("Just plain prose, no citations.\n") == ()


def test_extract_finds_both_occurrences_when_definition_is_url_shaped() -> None:
    """A ``[^label]: https://...`` definition text is itself a valid link
    destination: markdown-it's ``reference`` block rule (round 1) consumes
    the definition line as a genuine reference definition and rewrites the
    earlier ``[^label]`` into a resolved link span -- but the raw-source
    scan never inspects tokens for extraction, so both occurrences are
    found regardless of how markdown-it resolves the surrounding syntax."""
    body = "A claim.[^ga4-schema]\n\n[^ga4-schema]: https://example.com/schema\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="ga4-schema", line=1, is_definition=False),
        FootnoteOccurrence(label="ga4-schema", line=3, is_definition=True),
    )


def test_extract_finds_reference_immediately_followed_by_parenthetical() -> None:
    """A ``[^label]`` immediately followed by ``(...)`` -- a plausible
    inline-citation authoring pattern -- parses as a real ``[text](dest)``
    link via markdown-it's *inline* ``link`` rule (round 2), independent of
    the ``reference`` rule. The raw-source scan finds the label directly in
    the source text without caring whether markdown-it resolved it as a
    link."""
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


def test_extract_finds_image_style_caret_label() -> None:
    """``![^label](url)`` -- an image-style caret label -- parses via
    markdown-it's *inline* ``image`` rule (round 2) into an ``image`` token
    that carries no ``text`` child at all. The raw-source scan doesn't
    depend on any inline child token existing for the label, so it is found
    directly."""
    body = "See the chart.\n\n![^chart-src](https://example.com/chart.png)\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="chart-src", line=3, is_definition=False),
    )


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
    """Round 4: an ``image`` token parents any ``code_inline`` in its alt
    text onto its own nested ``.children`` -- a list the top-level scan
    never walked. A code span search that only visited top-level children
    greedily matched the image's own (invisible-to-us) backtick pair
    instead of the real code span's delimiters, leaving the real span's
    content -- including a nested ``[^label]`` -- wrongly unexcluded. The
    full descendant walk must locate the image's caption backticks first,
    then the real code span, in raw-text order."""
    body = "![diagram `caption`](img.png) See `[^hidden]` in the code.\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_finds_label_after_image_with_plain_alt_text() -> None:
    """Control for the above: an image with no backticks in its alt text
    has no nested ``code_inline`` to confuse the matcher, so a real code
    span after it was already excluded correctly even before the fix --
    this guards against a regression that breaks the already-working
    simple case while fixing the nested case above."""
    body = "![plain diagram](img.png) See `[^hidden]` in the code.\n"

    assert extract_footnote_occurrences(body) == ()


def test_extract_finds_occurrence_after_code_span_preceded_by_html_comment_backtick() -> (
    None
):
    """Round 5 (researcher reproduction, HTML comment): an ``html_inline``
    token (e.g. an HTML comment) can contain a literal backtick that has
    nothing to do with any code span. The old cursor-based search treated
    that backtick as a delimiter candidate for the *next* ``code_inline``
    token, mispairing it with the real span's opening backtick and
    producing a wrong exclusion range that swallowed the footnote label in
    between. Validating a candidate pairing's content against the token's
    own known content (rather than accepting the first backtick-shaped
    match) rejects the false pairing and finds the real span instead."""
    body = "Some text <!-- a ` stray backtick --> [^mylabel] and `real` code.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="mylabel", line=1, is_definition=False),
    )


def test_extract_finds_occurrence_after_code_span_preceded_by_autolink_backtick() -> (
    None
):
    """Round 5 (researcher reproduction, autolink): an autolink's URI is
    legal CommonMark even when it contains a literal backtick (autolink
    content is not escape/entity-processed). That backtick is exposed as a
    plain ``text`` token's content, but the old cursor-based search still
    treated it as a delimiter candidate for the next real code span,
    mispairing and swallowing the footnote label between them."""
    body = "See <http://example.com/a`b> [^mylabel] and then `real` code.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="mylabel", line=1, is_definition=False),
    )


def test_extract_finds_occurrence_after_code_span_preceded_by_link_destination_backtick() -> (
    None
):
    """Round 5 (researcher reproduction, link destination): a regular
    ``[text](url)`` link's destination is not represented by any inline
    child token at all (only the label text is), so a backtick inside the
    destination is invisible to a token-type-based scan yet still present
    in the raw source the cursor walks. The old search mispaired it with
    the next real code span's opening backtick, swallowing the footnote
    label in between."""
    body = "See [text](http://example.com/a`b) [^mylabel] and then `real` code.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="mylabel", line=1, is_definition=False),
    )


def test_extract_finds_occurrence_after_code_span_preceded_by_escaped_backtick() -> (
    None
):
    """Round 6: a backslash-escaped backtick (``\\```) in ordinary prose is
    resolved by markdown_it into a literal backtick character inside a
    plain ``text`` token's ``.content`` -- the escape itself leaves no
    trace for a raw-text scan to see. This is not fixed by recognizing an
    escape syntax (the structural fix does not look for one); it is fixed
    the same way as the other three cases, by rejecting any candidate
    pairing whose content does not match the real span's."""
    body = "Some text \\` still prose [^mylabel] and `real` code.\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="mylabel", line=1, is_definition=False),
    )


def test_extract_finds_both_spans_with_repeated_html_comments_between_them() -> None:
    """Generality check: the fix must not depend on there being exactly one
    confusing construct in the paragraph. Two separate HTML comments, each
    with their own stray backtick, sit before two separate real code
    spans -- if the fix were a special case for "the first" comment rather
    than a general content-validated search, this would still mispair."""
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


def test_clean_document_with_image_style_label_reports_nothing() -> None:
    """Companion to ``test_extract_finds_image_style_caret_label`` at the
    ``check_attribution_consistency`` layer: a clean label/id match must not
    be misreported as a dangling footnote (false positive) just because the
    label is written in image-style ``![^label](url)`` form."""
    frontmatter = {"sources": [{"id": "chart-src", "resource": "https://example.com"}]}
    body = "See the chart.\n\n![^chart-src](https://example.com/chart.png)\n"

    assert check_attribution_consistency(frontmatter, body) == ()


def test_dangling_image_style_label_is_an_error_with_location() -> None:
    """A dangling image-style caret label must still be reported as an
    error, not silently dropped as a false negative, per the inline
    ``image``-rule-consumption bug covered above."""
    frontmatter: dict[str, Any] = {}
    body = "See the chart.\n\n![^missing-chart](https://example.com/chart.png)\n"

    findings = check_attribution_consistency(frontmatter, body)

    assert findings == (
        ValidationFinding(
            severity="error",
            message="Footnote label 'missing-chart' has no matching sources[].id",
            field="missing-chart",
            line=3,
        ),
    )


def test_clean_document_with_real_code_span_after_image_caption_reports_nothing() -> (
    None
):
    """Companion to
    ``test_extract_ignores_real_code_span_after_image_with_backtick_caption``
    at the ``check_attribution_consistency`` layer: with no ``sources`` at
    all, a false-positive dangling-footnote finding for the code-span-
    protected ``[^hidden]`` would be the observable symptom of the
    round-4 bug."""
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

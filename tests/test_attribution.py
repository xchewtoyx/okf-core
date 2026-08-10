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
    ],
)
def test_extract_ignores_labels_inside_code(body: str) -> None:
    assert extract_footnote_occurrences(body) == ()


def test_extract_no_footnotes_returns_empty_tuple() -> None:
    assert extract_footnote_occurrences("Just plain prose, no citations.\n") == ()


def test_extract_finds_both_occurrences_when_definition_is_url_shaped() -> None:
    """A ``[^label]: https://...`` definition text is itself a valid link
    destination. Without disabling markdown-it's ``reference`` block rule,
    that rule silently consumes the definition line before any ``inline``
    token is emitted for it, and rewrites the earlier ``[^label]`` reference
    into a ``link_open``/text/``link_close`` span -- so neither occurrence
    ever reaches the text-child regex scan. Both must still be found."""
    body = "A claim.[^ga4-schema]\n\n[^ga4-schema]: https://example.com/schema\n"
    occurrences = extract_footnote_occurrences(body)

    assert occurrences == (
        FootnoteOccurrence(label="ga4-schema", line=1, is_definition=False),
        FootnoteOccurrence(label="ga4-schema", line=3, is_definition=True),
    )


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

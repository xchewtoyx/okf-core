from __future__ import annotations

import pytest

from okf_core import (
    ConceptDocument,
    DocumentParseError,
    parse_concept_document,
    serialize_concept_document,
    validate_concept_document,
)


def test_round_trip_preserves_unknown_frontmatter() -> None:
    markdown = """---
type: concept
title: Known Field
custom_field: keep me
nested:
  owner: docs
---
# Body
"""

    parsed = parse_concept_document(markdown)
    serialized = serialize_concept_document(parsed)
    reparsed = parse_concept_document(serialized)

    assert reparsed.frontmatter == {
        "type": "concept",
        "title": "Known Field",
        "custom_field": "keep me",
        "nested": {"owner": "docs"},
    }
    assert reparsed.body == "# Body\n"


def test_document_with_only_type_and_body_is_valid() -> None:
    document = parse_concept_document("""---
type: concept
---
Only body content.
""")

    assert document.frontmatter == {"type": "concept"}
    assert document.body == "Only body content.\n"
    assert validate_concept_document(document) == ()


def test_unknown_type_and_unknown_fields_are_tolerated_by_base_validation() -> None:
    document = parse_concept_document("""---
type: Producer Defined Type
custom_field: keep me
---
Body.
""")

    assert validate_concept_document(document) == ()


def test_document_with_missing_frontmatter_keeps_body() -> None:
    markdown = "# Title\n\nBody without frontmatter.\n"

    document = parse_concept_document(markdown)

    assert document.frontmatter == {}
    assert document.body == markdown
    assert serialize_concept_document(document) == markdown


def test_empty_frontmatter_block_is_allowed() -> None:
    document = parse_concept_document("---\n---\nBody\n")

    assert document.frontmatter == {}
    assert document.body == "Body\n"


def test_parser_accepts_crlf_frontmatter_boundaries() -> None:
    document = parse_concept_document("---\r\ntype: concept\r\n---\r\nBody\r\n")

    assert document.frontmatter == {"type": "concept"}
    assert document.body == "Body\r\n"


def test_body_can_contain_frontmatter_delimiter_lines() -> None:
    body = "# Heading\n\n---\n\nThat delimiter belongs to the body.\n"

    document = parse_concept_document(f"---\ntype: concept\n---\n{body}")

    assert document.body == body


def test_invalid_yaml_frontmatter_raises_parse_error() -> None:
    markdown = """---
type: [unterminated
---
Body
"""

    with pytest.raises(DocumentParseError, match="Invalid YAML frontmatter"):
        parse_concept_document(markdown)


def test_unterminated_frontmatter_raises_parse_error() -> None:
    markdown = """---
type: concept
Body
"""

    with pytest.raises(DocumentParseError, match="Unterminated YAML frontmatter"):
        parse_concept_document(markdown)


@pytest.mark.parametrize(
    "frontmatter",
    [
        "- concept\n",
        "concept\n",
    ],
)
def test_non_mapping_frontmatter_raises_parse_error(frontmatter: str) -> None:
    markdown = f"---\n{frontmatter}---\nBody\n"

    with pytest.raises(DocumentParseError, match="YAML frontmatter must be a mapping"):
        parse_concept_document(markdown)


def test_non_string_frontmatter_key_raises_parse_error() -> None:
    markdown = """---
1: numeric key
---
Body
"""

    with pytest.raises(
        DocumentParseError, match="YAML frontmatter keys must be strings"
    ):
        parse_concept_document(markdown)


def test_body_content_is_preserved_after_frontmatter() -> None:
    body = "\n# Heading\n\n- item\n\nTrailing spaces  \n"
    markdown = f"---\ntype: concept\n---\n{body}"

    document = parse_concept_document(markdown)

    assert document.body == body


@pytest.mark.parametrize(
    ("document", "diagnostics"),
    [
        (
            ConceptDocument(frontmatter={}, body="Body"),
            ("Missing required frontmatter field: type",),
        ),
        (
            ConceptDocument(frontmatter={"type": ""}, body="Body"),
            ("Frontmatter field 'type' must be a non-empty string",),
        ),
        (
            ConceptDocument(frontmatter={"type": "   "}, body="Body"),
            ("Frontmatter field 'type' must be a non-empty string",),
        ),
        (
            ConceptDocument(frontmatter={"type": ["concept"]}, body="Body"),
            ("Frontmatter field 'type' must be a non-empty string",),
        ),
    ],
)
def test_validate_concept_document_reports_type_diagnostics(
    document: ConceptDocument,
    diagnostics: tuple[str, ...],
) -> None:
    assert validate_concept_document(document) == diagnostics


def test_serialize_concept_document_emits_frontmatter_and_body() -> None:
    document = ConceptDocument(
        frontmatter={"type": "concept", "unknown": "preserved"},
        body="# Heading\n",
    )

    assert serialize_concept_document(document) == (
        "---\ntype: concept\nunknown: preserved\n---\n# Heading\n"
    )


def test_concept_document_frontmatter_can_be_updated_before_serializing() -> None:
    document = parse_concept_document("---\ntype: concept\n---\nBody\n")

    document.frontmatter["status"] = "draft"

    assert serialize_concept_document(document) == (
        "---\ntype: concept\nstatus: draft\n---\nBody\n"
    )

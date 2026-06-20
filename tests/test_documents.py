from __future__ import annotations

import pytest

from okf_core import (
    ConceptDocument,
    DocumentParseError,
    ValidationFinding,
    parse_concept_document,
    serialize_concept_document,
    validate_concept_document,
    validate_concept_document_with_profile,
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
    ("document", "findings"),
    [
        (
            ConceptDocument(frontmatter={}, body="Body"),
            (
                ValidationFinding(
                    severity="error",
                    message="Missing required frontmatter field: type",
                    field="type",
                ),
            ),
        ),
        (
            ConceptDocument(frontmatter={"type": ""}, body="Body"),
            (
                ValidationFinding(
                    severity="error",
                    message="Frontmatter field 'type' must be a non-empty string",
                    field="type",
                ),
            ),
        ),
        (
            ConceptDocument(frontmatter={"type": "   "}, body="Body"),
            (
                ValidationFinding(
                    severity="error",
                    message="Frontmatter field 'type' must be a non-empty string",
                    field="type",
                ),
            ),
        ),
        (
            ConceptDocument(frontmatter={"type": ["concept"]}, body="Body"),
            (
                ValidationFinding(
                    severity="error",
                    message="Frontmatter field 'type' must be a non-empty string",
                    field="type",
                ),
            ),
        ),
    ],
)
def test_validate_concept_document_reports_type_diagnostics(
    document: ConceptDocument,
    findings: tuple[ValidationFinding, ...],
) -> None:
    assert validate_concept_document(document) == findings


def test_validate_concept_document_with_profile_required_fields() -> None:
    from okf_core import ProfileConfig

    profile = ProfileConfig(required_frontmatter=("title", "status"))
    doc = ConceptDocument(frontmatter={"type": "concept", "title": ""})

    findings = validate_concept_document_with_profile(doc, profile)

    assert len(findings) == 2
    assert findings[0] == ValidationFinding(
        severity="error",
        message="Required frontmatter field 'title' must be a non-empty string",
        field="title",
    )
    assert findings[1] == ValidationFinding(
        severity="error",
        message="Missing required frontmatter field: status",
        field="status",
    )


def test_validate_concept_document_with_profile_undocumented_fields() -> None:
    from okf_core import ProfileConfig

    profile = ProfileConfig(
        required_frontmatter=("title",), optional_frontmatter=("status",)
    )
    doc = ConceptDocument(
        frontmatter={
            "type": "concept",
            "title": "Topic",
            "status": "draft",
            "description": "A topic",
            "custom_field": "some value",
        }
    )

    findings = validate_concept_document_with_profile(doc, profile)

    assert findings == (
        ValidationFinding(
            severity="warning",
            message="Unknown frontmatter field: custom_field",
            field="custom_field",
        ),
    )


def test_validate_concept_document_with_profile_taxonomy_rules() -> None:
    from okf_core import ProfileConfig, TaxonomyConfig

    project_taxonomy = TaxonomyConfig(
        known_types=("concept", "decision"), allowed_types=("concept",)
    )
    profile_with_allowed = ProfileConfig(
        taxonomy=TaxonomyConfig(allowed_types=("decision",))
    )
    profile_with_known = ProfileConfig(
        taxonomy=TaxonomyConfig(known_types=("concept", "decision"))
    )

    # 1. Allowed type error
    doc_concept = ConceptDocument(frontmatter={"type": "concept"})
    findings = validate_concept_document_with_profile(
        doc_concept, profile_with_allowed, project_taxonomy
    )
    assert findings == (
        ValidationFinding(
            severity="error",
            message="Concept type 'concept' is not allowed by this profile",
            field="type",
        ),
    )

    # 2. Known type warning
    doc_unknown = ConceptDocument(frontmatter={"type": "proposal"})
    findings = validate_concept_document_with_profile(doc_unknown, profile_with_known)
    assert findings == (
        ValidationFinding(
            severity="warning",
            message="Concept type 'proposal' is not recognized as a known type",
            field="type",
        ),
    )

    # 3. Fallback to project taxonomy allowed_types
    profile_no_tax = ProfileConfig()
    doc_decision = ConceptDocument(frontmatter={"type": "decision"})
    findings_project_allowed = validate_concept_document_with_profile(
        doc_decision, profile_no_tax, project_taxonomy
    )
    assert findings_project_allowed == (
        ValidationFinding(
            severity="error",
            message="Concept type 'decision' is not allowed by this profile",
            field="type",
        ),
    )

    # 4. Fallback to project taxonomy known_types
    project_taxonomy_known = TaxonomyConfig(known_types=("concept", "decision"))
    findings_project_known = validate_concept_document_with_profile(
        doc_unknown, profile_no_tax, project_taxonomy_known
    )
    assert findings_project_known == (
        ValidationFinding(
            severity="warning",
            message="Concept type 'proposal' is not recognized as a known type",
            field="type",
        ),
    )


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

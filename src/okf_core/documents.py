"""Markdown concept document parsing and serialization."""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from okf_core.config import ProfileConfig, TaxonomyConfig

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


class DocumentParseError(Exception):
    """Raised when a concept document cannot be parsed."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects repeated explicit mapping keys."""

    def construct_mapping(
        self,
        node: yaml.Node,
        deep: bool = False,
    ) -> dict[Any, Any]:
        if isinstance(node, MappingNode):
            seen: set[Hashable] = set()
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                key = self.construct_object(key_node, deep=deep)
                if isinstance(key, Hashable) and key in seen:
                    raise ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"found duplicate key {key!r}",
                        key_node.start_mark,
                    )
                if isinstance(key, Hashable):
                    seen.add(key)
        return super().construct_mapping(node, deep=deep)


@dataclass(frozen=True)
class ValidationFinding:
    """A validation problem (error or warning) discovered in a concept document."""

    severity: str  # "error" | "warning"
    message: str
    field: str | None = None
    # 1-based source line the finding pertains to, when known (e.g. a
    # footnote occurrence's line in the body); None for findings with no
    # single source line (e.g. a frontmatter-only or whole-document finding).
    line: int | None = None


@dataclass
class ConceptDocument:
    """A parsed OKF concept document."""

    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""


def parse_concept_document(markdown: str) -> ConceptDocument:
    """Parse Markdown into frontmatter and body, rejecting duplicate YAML keys."""

    yaml_source, body = _split_frontmatter(markdown)
    if yaml_source is None:
        return ConceptDocument(frontmatter={}, body=body)

    frontmatter = _parse_frontmatter(yaml_source)
    return ConceptDocument(frontmatter=frontmatter, body=body)


def _split_frontmatter(markdown: str) -> tuple[str | None, str]:
    """Split *markdown* into its raw YAML frontmatter source and body.

    Returns ``(yaml_source, body)``. ``yaml_source`` is ``None`` when the
    document has no opening ``---`` delimiter line at all, in which case
    ``body`` is the entire document, unchanged. Otherwise ``yaml_source`` is
    the raw text between the opening and closing ``---`` delimiter lines
    (empty when the block itself is empty) and ``body`` is everything after
    the closing delimiter line.

    This is the single scan for the frontmatter delimiter block; both
    ``parse_concept_document`` and ``patching.py``'s frontmatter merge share
    it rather than each re-implementing the same line scan.
    """

    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None, markdown

    closing_index = _find_frontmatter_close(lines)
    if closing_index is None:
        raise DocumentParseError("Unterminated YAML frontmatter")

    yaml_source = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1 :])
    return yaml_source, body


def serialize_concept_document(document: ConceptDocument) -> str:
    """Serialize a concept document to Markdown."""

    if not document.frontmatter:
        return document.body

    yaml_source = yaml.safe_dump(
        document.frontmatter,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    return f"---\n{yaml_source}---\n{document.body}"


def validate_concept_document(
    document: ConceptDocument,
) -> tuple[ValidationFinding, ...]:
    """Return base OKF conformance findings for a parsed document."""

    concept_type = document.frontmatter.get("type")
    if concept_type is None:
        return (
            ValidationFinding(
                severity="error",
                message="Missing required frontmatter field: type",
                field="type",
            ),
        )
    if not isinstance(concept_type, str) or not concept_type.strip():
        return (
            ValidationFinding(
                severity="error",
                message="Frontmatter field 'type' must be a non-empty string",
                field="type",
            ),
        )
    return ()


def validate_concept_document_with_profile(
    document: ConceptDocument,
    profile: ProfileConfig,
    project_taxonomy: TaxonomyConfig | None = None,
    *,
    is_directory_meta: bool = False,
) -> tuple[ValidationFinding, ...]:
    """Validate a concept document against base OKF rules and a custom profile."""
    findings = list(validate_concept_document(document))
    concept_type = document.frontmatter.get("type")

    if isinstance(concept_type, str) and concept_type.strip():
        concept_type_str = concept_type.strip()
        is_system_type = is_directory_meta and concept_type_str.startswith("_")
        if not is_system_type:
            findings.extend(
                _validate_taxonomy(concept_type_str, profile, project_taxonomy)
            )

    if not is_directory_meta:
        findings.extend(_validate_required_fields(document, profile))
        findings.extend(_validate_no_undocumented_fields(document, profile))

    return tuple(findings)


def _validate_taxonomy(
    concept_type: str,
    profile: ProfileConfig,
    project_taxonomy: TaxonomyConfig | None,
) -> tuple[ValidationFinding, ...]:
    """Check a concept type against profile/project taxonomy allow- and known-type lists."""
    allowed_types: tuple[str, ...] = ()
    if profile.taxonomy.allowed_types:
        allowed_types = profile.taxonomy.allowed_types
    elif project_taxonomy is not None and project_taxonomy.allowed_types:
        allowed_types = project_taxonomy.allowed_types

    known_types: tuple[str, ...] = ()
    if profile.taxonomy.known_types:
        known_types = profile.taxonomy.known_types
    elif project_taxonomy is not None and project_taxonomy.known_types:
        known_types = project_taxonomy.known_types

    if allowed_types:
        if concept_type not in allowed_types:
            return (
                ValidationFinding(
                    severity="error",
                    message=f"Concept type '{concept_type}' is not allowed by this profile",
                    field="type",
                ),
            )
    elif known_types and concept_type not in known_types:
        return (
            ValidationFinding(
                severity="warning",
                message=f"Concept type '{concept_type}' is not recognized as a known type",
                field="type",
            ),
        )

    return ()


def _validate_required_fields(
    document: ConceptDocument,
    profile: ProfileConfig,
) -> tuple[ValidationFinding, ...]:
    """Check that profile-required frontmatter fields (except 'type') are present
    and non-empty when the value is a string."""
    findings: list[ValidationFinding] = []
    for field_name in profile.required_frontmatter:
        if field_name == "type":
            continue
        if (
            field_name not in document.frontmatter
            or document.frontmatter[field_name] is None
        ):
            findings.append(
                ValidationFinding(
                    severity="error",
                    message=f"Missing required frontmatter field: {field_name}",
                    field=field_name,
                )
            )
        elif (
            isinstance(document.frontmatter[field_name], str)
            and not document.frontmatter[field_name].strip()
        ):
            findings.append(
                ValidationFinding(
                    severity="error",
                    message=f"Required frontmatter field '{field_name}' must be a non-empty string",
                    field=field_name,
                )
            )
    return tuple(findings)


def _validate_no_undocumented_fields(
    document: ConceptDocument,
    profile: ProfileConfig,
) -> tuple[ValidationFinding, ...]:
    """Warn about frontmatter fields not in the standard set or the profile's
    required/optional field lists."""
    standard_fields = {
        "type",
        "title",
        "description",
        "resource",
        "tags",
        "timestamp",
    }
    defined_fields = standard_fields.union(profile.required_frontmatter).union(
        profile.optional_frontmatter
    )
    findings: list[ValidationFinding] = []
    for field_name in document.frontmatter:
        if field_name not in defined_fields:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    message=f"Unknown frontmatter field: {field_name}",
                    field=field_name,
                )
            )
    return tuple(findings)


def _find_frontmatter_close(lines: list[str]) -> int | None:
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            return index
    return None


def _parse_frontmatter(yaml_source: str) -> dict[str, Any]:
    try:
        loaded = yaml.load(yaml_source, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise DocumentParseError(f"Invalid YAML frontmatter: {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise DocumentParseError("YAML frontmatter must be a mapping")

    non_string_keys = [key for key in loaded if not isinstance(key, str)]
    if non_string_keys:
        raise DocumentParseError("YAML frontmatter keys must be strings")

    return loaded

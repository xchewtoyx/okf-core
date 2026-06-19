"""Markdown concept document parsing and serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


class DocumentParseError(Exception):
    """Raised when a concept document cannot be parsed."""


@dataclass
class ConceptDocument:
    """A parsed OKF concept document."""

    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""


def parse_concept_document(markdown: str) -> ConceptDocument:
    """Parse Markdown into YAML frontmatter and body content."""

    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return ConceptDocument(frontmatter={}, body=markdown)

    closing_index = _find_frontmatter_close(lines)
    if closing_index is None:
        raise DocumentParseError("Unterminated YAML frontmatter")

    yaml_source = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1 :])
    frontmatter = _parse_frontmatter(yaml_source)
    return ConceptDocument(frontmatter=frontmatter, body=body)


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


def validate_concept_document(document: ConceptDocument) -> tuple[str, ...]:
    """Return base OKF conformance diagnostics for a parsed document."""

    concept_type = document.frontmatter.get("type")
    if concept_type is None:
        return ("Missing required frontmatter field: type",)
    if not isinstance(concept_type, str) or not concept_type.strip():
        return ("Frontmatter field 'type' must be a non-empty string",)
    return ()


def _find_frontmatter_close(lines: list[str]) -> int | None:
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            return index
    return None


def _parse_frontmatter(yaml_source: str) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(yaml_source)
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

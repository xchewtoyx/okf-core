"""Attribution consistency check: footnote labels vs. ``sources[].id``.

OKF spec §5.1 attributes individual claims via a Markdown footnote whose
label joins to a ``sources[].id`` frontmatter entry, e.g.::

    The `events_` table is sharded daily.[^ga4-schema]

    [^ga4-schema]: GA4 BigQuery Export schema

This module hand-rolls a minimal ``[^label]`` (reference) / ``[^label]:``
(definition) scanner over a concept body's already-parsed inline tokens --
not a full footnote-content parser (footnotes are not a CommonMark
construct and OKF only needs the label, not rendered footnote content), and
not the ``mdit-py-plugins`` footnote plugin, which would be new-dependency
overhead for this narrow, well-scoped syntax. Scanning only ``text`` inline
children (mirroring ``_markdown_inline.py``'s ``code_inline`` exclusion)
keeps labels inside fenced/inline code or link destinations from being
mistaken for prose citations.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from markdown_it import MarkdownIt

from okf_core._markdown_inline import token_line
from okf_core.documents import ValidationFinding

_MARKDOWN = MarkdownIt("commonmark")

# A footnote label stops at the first "]" or whitespace; an immediately
# following ":" distinguishes a definition ("[^label]: text") from a
# reference ("[^label]" inline in prose).
_FOOTNOTE_RE = re.compile(r"\[\^([^\]\s]+)\](:)?")


@dataclass(frozen=True)
class FootnoteOccurrence:
    """One ``[^label]`` occurrence in a concept body.

    ``is_definition`` is True for a ``[^label]:`` definition line, False for
    a bare ``[^label]`` reference in prose.
    """

    label: str
    line: int | None
    is_definition: bool


def extract_footnote_occurrences(body: str) -> tuple[FootnoteOccurrence, ...]:
    """Extract every ``[^label]`` reference and ``[^label]:`` definition in *body*."""
    occurrences: list[FootnoteOccurrence] = []
    for token in _MARKDOWN.parse(body):
        if token.type != "inline" or token.children is None:
            continue
        occurrences.extend(_occurrences_in_inline(token))
    return tuple(occurrences)


def _occurrences_in_inline(token: Any) -> list[FootnoteOccurrence]:
    """Scan one inline token's ``text`` children for footnote-label matches.

    Line breaks within the run are ``softbreak``/``hardbreak`` child tokens
    (markdown-it never embeds a literal newline in a ``text`` child), so the
    running line number only needs to advance on those, not per-match.
    """
    occurrences: list[FootnoteOccurrence] = []
    line = token_line(token)
    for child in token.children:
        if child.type in ("softbreak", "hardbreak"):
            if line is not None:
                line += 1
            continue
        if child.type != "text":
            continue
        for match in _FOOTNOTE_RE.finditer(child.content):
            occurrences.append(
                FootnoteOccurrence(
                    label=match.group(1),
                    line=line,
                    is_definition=match.group(2) is not None,
                )
            )
    return occurrences


def _declared_source_ids(frontmatter: Mapping[str, Any]) -> tuple[str, ...]:
    """Return ``sources[].id`` values that are non-empty strings, dedup-first-seen.

    ``id`` is optional per spec §5.1 -- entries without one, or with a
    malformed ``sources`` list/entry, are simply not join candidates, not
    errors here; that permissiveness is validated elsewhere. ``frontmatter``
    may come straight from a freshly-parsed ``ConceptDocument`` (plain
    ``dict``/``list``) or from a scanned ``ConceptManifestEntry`` (frozen via
    ``MappingProxyType``/``tuple``), so both list-like and mapping-like
    variants are accepted rather than the concrete ``list``/``dict`` types.
    """
    sources = frontmatter.get("sources")
    if not isinstance(sources, (list, tuple)):
        return ()
    ids: list[str] = []
    for entry in sources:
        if not isinstance(entry, Mapping):
            continue
        source_id = entry.get("id")
        if isinstance(source_id, str) and source_id.strip():
            ids.append(source_id)
    return tuple(dict.fromkeys(ids))


def check_attribution_consistency(
    frontmatter: Mapping[str, Any], body: str
) -> tuple[ValidationFinding, ...]:
    """Join footnote labels in *body* against ``sources[].id`` in *frontmatter*.

    A footnote label (reference or definition) with no matching
    ``sources[].id`` is an ``"error"`` finding. A declared ``sources[].id``
    that no footnote (reference or definition) ever cites is a ``"warning"``
    finding -- an unreferenced source is legal, so this is advisory rather
    than an error. A document with neither footnotes nor sources reports
    nothing.
    """
    occurrences = extract_footnote_occurrences(body)
    source_ids = _declared_source_ids(frontmatter)
    if not occurrences and not source_ids:
        return ()

    referenced: set[str] = set()
    findings: list[ValidationFinding] = []
    for occurrence in occurrences:
        referenced.add(occurrence.label)
        if occurrence.label not in source_ids:
            findings.append(
                ValidationFinding(
                    severity="error",
                    message=(
                        f"Footnote label '{occurrence.label}' has no matching "
                        "sources[].id"
                    ),
                    field=occurrence.label,
                    line=occurrence.line,
                )
            )

    for source_id in source_ids:
        if source_id not in referenced:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    message=(
                        f"sources[].id '{source_id}' is not referenced by any footnote"
                    ),
                    field=source_id,
                )
            )

    return tuple(findings)

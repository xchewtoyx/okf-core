"""Structured concept listings for OKF bundles."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError
from okf_core.graph import BundleGraph
from okf_core.manifest import BundleManifest, ConceptManifestEntry, scan_bundle


@dataclass(frozen=True)
class ConceptListing:
    """A concept candidate for task-specific seed selection."""

    concept_id: str
    path: Path
    type: str
    title: str | None = None
    description: str | None = None
    fields: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    frontmatter: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    outbound_link_count: int | None = None
    inbound_link_count: int | None = None
    content: str | None = None


@dataclass(frozen=True)
class ListingProblem:
    """A non-fatal problem encountered while listing concepts."""

    path: Path
    kind: str
    message: str
    concept_id: str = ""


@dataclass(frozen=True)
class BundleListing:
    """A deterministic listing of concept candidates for one configured bundle."""

    bundle_name: str
    concepts: tuple[ConceptListing, ...] = ()
    problems: tuple[ListingProblem, ...] = ()


def list_concepts(
    bundle: BundleConfig,
    *,
    manifest: BundleManifest | None = None,
    graph: BundleGraph | None = None,
    with_content: bool = False,
) -> BundleListing:
    """List valid concept documents as deterministic seed candidates.

    Base OKF conformance stays permissive: unknown concept types and unknown
    producer-defined frontmatter fields are preserved.  Entries whose ``type``
    field is missing, non-string, or blank are reported as structured problems
    because OKF concept documents require a non-empty string ``type``.

    If ``with_content`` is True, the raw Markdown body of each valid concept
    is populated in the ``content`` field of the listing entries.
    """

    resolved_manifest = _resolve_manifest(bundle, manifest, graph)
    outbound_counts, inbound_counts = _graph_counts(graph)
    concepts: list[ConceptListing] = []
    problems: list[ListingProblem] = [
        ListingProblem(path=p.path, kind=p.kind, message=p.message)
        for p in resolved_manifest.problems
    ]

    for entry in resolved_manifest.concepts:
        type_value = entry.frontmatter.get("type")
        if not isinstance(type_value, str) or not type_value.strip():
            problems.append(
                ListingProblem(
                    concept_id=entry.concept_id,
                    path=entry.path,
                    kind="missing-type",
                    message=(
                        "'type' frontmatter must be a non-empty string, "
                        f"got {type_value!r}"
                    ),
                )
            )
            continue

        content_val = None
        if with_content:
            try:
                content_val = entry.body
            except (OSError, UnicodeDecodeError, DocumentParseError) as exc:
                problems.append(
                    ListingProblem(
                        concept_id=entry.concept_id,
                        path=entry.path,
                        kind=(
                            "read-error"
                            if isinstance(exc, OSError)
                            else (
                                "decode-error"
                                if isinstance(exc, UnicodeDecodeError)
                                else "parse-error"
                            )
                        ),
                        message=str(exc),
                    )
                )
                continue

        concepts.append(
            ConceptListing(
                concept_id=entry.concept_id,
                path=entry.path,
                type=type_value.strip(),
                title=_frontmatter_inline(entry.frontmatter, "title"),
                description=_frontmatter_inline(entry.frontmatter, "description"),
                fields=_selected_fields(entry, bundle.listing_fields),
                frontmatter=entry.frontmatter,
                outbound_link_count=(
                    outbound_counts.get(entry.concept_id) if graph is not None else None
                ),
                inbound_link_count=(
                    inbound_counts.get(entry.concept_id) if graph is not None else None
                ),
                content=content_val,
            )
        )

    problems.extend(_graph_listing_problems(graph, problems))

    return BundleListing(
        bundle_name=resolved_manifest.bundle_name,
        concepts=tuple(sorted(concepts, key=lambda c: (c.concept_id, str(c.path)))),
        problems=tuple(
            sorted(problems, key=lambda p: (str(p.path), p.kind, p.concept_id))
        ),
    )


def _resolve_manifest(
    bundle: BundleConfig,
    manifest: BundleManifest | None,
    graph: BundleGraph | None,
) -> BundleManifest:
    if manifest is not None:
        return manifest
    if graph is not None:
        return BundleManifest(bundle_name=graph.bundle_name, concepts=graph.concepts)
    return scan_bundle(bundle)


def _graph_counts(
    graph: BundleGraph | None,
) -> tuple[dict[str, int], dict[str, int]]:
    outbound: dict[str, int] = {}
    inbound: dict[str, int] = {}
    if graph is None:
        return outbound, inbound

    for concept in graph.concepts:
        outbound.setdefault(concept.concept_id, 0)
        inbound.setdefault(concept.concept_id, 0)
    for link in graph.links:
        outbound[link.source_concept_id] = outbound.get(link.source_concept_id, 0) + 1
        if link.target_concept_id is not None:
            inbound[link.target_concept_id] = inbound.get(link.target_concept_id, 0) + 1
    return outbound, inbound


def _graph_listing_problems(
    graph: BundleGraph | None,
    existing: list[ListingProblem],
) -> tuple[ListingProblem, ...]:
    if graph is None:
        return ()
    seen = {(p.path, p.kind, p.message) for p in existing}
    problems: list[ListingProblem] = []
    for problem in graph.problems:
        key = (problem.path, problem.kind, problem.message)
        if key in seen:
            continue
        problems.append(
            ListingProblem(
                concept_id=problem.concept_id,
                path=problem.path,
                kind=problem.kind,
                message=problem.message,
            )
        )
    return tuple(problems)


def _selected_fields(
    entry: ConceptManifestEntry,
    field_names: tuple[str, ...],
) -> Mapping[str, Any]:
    return MappingProxyType(
        {
            name: entry.frontmatter[name]
            for name in field_names
            if name in entry.frontmatter
        }
    )


def _frontmatter_inline(
    frontmatter: Mapping[str, Any],
    field_name: str,
) -> str | None:
    raw = frontmatter.get(field_name)
    if raw is None:
        return None
    normalized = _normalize_inline(str(raw))
    return normalized or None


def _normalize_inline(s: str) -> str:
    return re.sub(r"[\r\n]+", " ", s).strip()

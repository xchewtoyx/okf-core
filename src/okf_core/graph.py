"""Markdown link extraction and graph traversal for OKF bundles."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError, parse_concept_document
from okf_core.manifest import BundleManifest, ConceptManifestEntry, scan_bundle
from okf_core.paths import (
    ConceptPathError,
    is_reserved_concept_path,
    path_to_concept_id,
)


@dataclass(frozen=True)
class MarkdownLink:
    """A Markdown link extracted from a concept body."""

    text: str
    target: str


@dataclass(frozen=True)
class ConceptLink:
    """A resolved or broken directed link from one concept document."""

    source_concept_id: str
    source_path: Path
    text: str
    target: str
    target_path: Path
    target_concept_id: str | None = None


@dataclass(frozen=True)
class GraphProblem:
    """A non-fatal problem found while building a graph."""

    concept_id: str
    path: Path
    kind: str
    message: str


@dataclass(frozen=True)
class BundleGraph:
    """A deterministic directed graph for one configured OKF bundle."""

    bundle_name: str
    concepts: tuple[ConceptManifestEntry, ...] = ()
    links: tuple[ConceptLink, ...] = ()
    broken_links: tuple[ConceptLink, ...] = ()
    problems: tuple[GraphProblem, ...] = ()


def extract_markdown_links(markdown: str) -> tuple[MarkdownLink, ...]:
    """Extract standard non-image Markdown links from a Markdown string."""

    parser = MarkdownIt("commonmark")
    tokens = parser.parse(markdown)
    links: list[MarkdownLink] = []

    for token in tokens:
        if token.type != "inline" or token.children is None:
            continue
        children = token.children
        for index, child in enumerate(children):
            if child.type != "link_open":
                continue
            target = child.attrGet("href")
            if target is None:
                continue
            links.append(
                MarkdownLink(
                    text=_collect_link_text(children[index + 1 :]),
                    target=target,
                )
            )

    return tuple(links)


def build_bundle_graph(
    bundle: BundleConfig,
    manifest: BundleManifest | None = None,
) -> BundleGraph:
    """Build a deterministic concept-link graph from a configured bundle."""

    resolved_manifest = manifest if manifest is not None else scan_bundle(bundle)
    concept_ids = {entry.concept_id for entry in resolved_manifest.concepts}
    resolved_links: list[ConceptLink] = []
    broken_links: list[ConceptLink] = []
    problems: list[GraphProblem] = [
        GraphProblem(
            concept_id="",
            path=problem.path,
            kind=problem.kind,
            message=problem.message,
        )
        for problem in resolved_manifest.problems
    ]

    for entry in resolved_manifest.concepts:
        try:
            document = parse_concept_document(entry.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, DocumentParseError) as exc:
            problems.append(
                GraphProblem(
                    concept_id=entry.concept_id,
                    path=entry.path,
                    kind="read-error",
                    message=str(exc),
                )
            )
            continue

        for markdown_link in extract_markdown_links(document.body):
            link = _resolve_concept_link(bundle, entry, markdown_link)
            if link is None:
                continue
            if link.target_concept_id in concept_ids:
                resolved_links.append(link)
            else:
                broken_links.append(link)

    return BundleGraph(
        bundle_name=resolved_manifest.bundle_name,
        concepts=resolved_manifest.concepts,
        links=tuple(sorted(resolved_links, key=_link_sort_key)),
        broken_links=tuple(sorted(broken_links, key=_link_sort_key)),
        problems=tuple(
            sorted(problems, key=lambda problem: (str(problem.path), problem.kind))
        ),
    )


def links_from(graph: BundleGraph, concept_id: str) -> tuple[ConceptLink, ...]:
    """Return resolved outbound links from ``concept_id``."""

    return tuple(link for link in graph.links if link.source_concept_id == concept_id)


def backlinks_to(graph: BundleGraph, concept_id: str) -> tuple[ConceptLink, ...]:
    """Return resolved inbound links to ``concept_id``."""

    return tuple(link for link in graph.links if link.target_concept_id == concept_id)


def neighborhood(
    graph: BundleGraph,
    concept_id: str,
    depth: int = 1,
) -> tuple[str, ...]:
    """Return concept IDs reachable from ``concept_id`` within ``depth`` hops."""

    if depth < 0:
        raise ValueError("depth must be greater than or equal to 0")

    adjacency: dict[str, set[str]] = {}
    for link in graph.links:
        if link.target_concept_id is None:
            continue
        adjacency.setdefault(link.source_concept_id, set()).add(link.target_concept_id)
        adjacency.setdefault(link.target_concept_id, set()).add(link.source_concept_id)

    seen = {concept_id}
    queue: deque[tuple[str, int]] = deque([(concept_id, 0)])
    while queue:
        current, current_depth = queue.popleft()
        if current_depth == depth:
            continue
        for next_id in sorted(adjacency.get(current, ())):
            if next_id in seen:
                continue
            seen.add(next_id)
            queue.append((next_id, current_depth + 1))

    return tuple(sorted(seen))


def _collect_link_text(children: list[object]) -> str:
    parts: list[str] = []
    for child in children:
        child_type = getattr(child, "type", None)
        if child_type == "link_close":
            break
        if child_type in {"text", "code_inline"}:
            parts.append(getattr(child, "content", ""))
    return "".join(parts)


def _resolve_concept_link(
    bundle: BundleConfig,
    source: ConceptManifestEntry,
    markdown_link: MarkdownLink,
) -> ConceptLink | None:
    target = markdown_link.target.strip()
    if not target:
        return None

    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    if not parsed.path.endswith(".md"):
        return None

    if parsed.path.startswith("/"):
        target_path = (bundle.bundle_root / parsed.path.lstrip("/")).resolve(
            strict=False
        )
    else:
        target_path = (source.path.parent / parsed.path).resolve(strict=False)

    if _is_within_bundle_root(target_path, bundle) and is_reserved_concept_path(
        target_path, bundle
    ):
        return None

    try:
        target_concept_id = path_to_concept_id(target_path, bundle)
    except ConceptPathError:
        target_concept_id = None

    return ConceptLink(
        source_concept_id=source.concept_id,
        source_path=source.path,
        text=markdown_link.text,
        target=markdown_link.target,
        target_path=target_path,
        target_concept_id=target_concept_id,
    )


def _link_sort_key(link: ConceptLink) -> tuple[str, str, str, str]:
    return (
        link.source_concept_id,
        link.target_concept_id or "",
        str(link.target_path),
        link.target,
    )


def _is_within_bundle_root(path: Path, bundle: BundleConfig) -> bool:
    try:
        path.relative_to(bundle.bundle_root.resolve(strict=False))
    except ValueError:
        return False
    return True

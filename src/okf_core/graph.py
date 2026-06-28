"""Markdown link extraction and graph traversal for OKF bundles."""

from __future__ import annotations

import sqlite3
from collections import deque
from collections.abc import Sequence
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
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

_MARKDOWN = MarkdownIt("commonmark")


def compute_pagerank(
    nodes: set[str],
    edges: list[tuple[str, str]],
    d: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Compute PageRank centrality scores for a directed graph."""
    if not nodes:
        return {}

    n = len(nodes)
    sorted_nodes = sorted(nodes)
    pr = {node: 1.0 / n for node in sorted_nodes}

    out_links: dict[str, list[str]] = {node: [] for node in sorted_nodes}
    in_links: dict[str, list[str]] = {node: [] for node in sorted_nodes}

    for src, dst in edges:
        if src in nodes and dst in nodes:
            out_links[src].append(dst)
            in_links[dst].append(src)

    for node in sorted_nodes:
        out_links[node].sort()
        in_links[node].sort()

    sinks = [node for node in sorted_nodes if not out_links[node]]

    for _ in range(max_iter):
        next_pr = {}
        sink_sum = sum(pr[sink] for sink in sinks)

        for node in sorted_nodes:
            rank_sum = sum(
                pr[neighbor] / len(out_links[neighbor]) for neighbor in in_links[node]
            )
            rank_sum += sink_sum / n
            next_pr[node] = (1.0 - d) / n + d * rank_sum

        err = sum(abs(next_pr[node] - pr[node]) for node in sorted_nodes)
        pr = next_pr
        if err < tol:
            break

    return pr


@dataclass(frozen=True)
class MarkdownLink:
    """A Markdown link extracted from a concept body."""

    text: str
    target: str
    title: str | None = None


@dataclass(frozen=True)
class ConceptLink:
    """A resolved or broken directed link from one concept document."""

    source_concept_id: str
    source_path: Path
    text: str
    target: str
    target_path: Path
    target_concept_id: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class GraphProblem:
    """A non-fatal problem found while building a graph."""

    concept_id: str
    path: Path
    kind: str
    message: str


@dataclass(frozen=True)
class LinkSuggestion:
    """A candidate link: concept title mentioned in body without a Markdown link."""

    source_concept_id: str
    source_path: Path
    target_concept_id: str
    target_path: Path
    matched_text: str


@dataclass(frozen=True)
class UnlinkedMentionsResult:
    """Result of :func:`find_unlinked_mentions`."""

    suggestions: tuple[LinkSuggestion, ...]
    problems: tuple[GraphProblem, ...]


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

    tokens = _MARKDOWN.parse(markdown)
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
            title_raw = child.attrGet("title")
            links.append(
                MarkdownLink(
                    text=_collect_link_text(children[index + 1 :]),
                    target=cast(str, target),
                    title=str(title_raw) if title_raw else None,
                )
            )

    return tuple(links)


def build_bundle_graph(
    bundle: BundleConfig,
    manifest: BundleManifest | None = None,
) -> BundleGraph:
    """Build a deterministic concept-link graph from a configured bundle."""
    root = bundle.bundle_root.resolve(strict=False)
    if not root.is_dir():
        return BundleGraph(bundle_name=bundle.name)

    from okf_core.hooks import get_hook_manager

    pm = get_hook_manager(bundle)

    try:
        pm.hook.okf_start_graph(bundle=bundle)
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
            pm.hook.okf_enter_resolve_links(entry=entry, bundle=bundle)
            entry_links = pm.hook.okf_fetch_resolve_links(entry=entry, bundle=bundle)
            problem = None
            if entry_links is None:
                try:
                    markdown = entry.content
                except OSError as exc:
                    problem = _graph_problem(entry, "read-error", exc)
                except UnicodeDecodeError as exc:
                    problem = _graph_problem(entry, "decode-error", exc)

                if problem is None:
                    try:
                        document = parse_concept_document(markdown)
                    except DocumentParseError as exc:
                        problem = _graph_problem(entry, "parse-error", exc)

                if problem is not None:
                    problems.append(problem)
                    entry_links = None
                else:
                    resolved_extracted: list[ConceptLink] = []
                    for markdown_link in extract_markdown_links(document.body):
                        link = _resolve_concept_link(bundle, entry, markdown_link)
                        if link is not None:
                            resolved_extracted.append(link)
                    entry_links = resolved_extracted

            if entry_links is not None:
                for link in entry_links:
                    if link.target_concept_id in concept_ids:
                        resolved_links.append(link)
                    else:
                        broken_links.append(link)

            pm.hook.okf_exit_resolve_links(
                entry=entry,
                links=entry_links,
                problem=problem,
                bundle=bundle,
            )

        graph = BundleGraph(
            bundle_name=resolved_manifest.bundle_name,
            concepts=resolved_manifest.concepts,
            links=tuple(sorted(resolved_links, key=_link_sort_key)),
            broken_links=tuple(sorted(broken_links, key=_link_sort_key)),
            problems=tuple(
                sorted(problems, key=lambda problem: (str(problem.path), problem.kind))
            ),
        )
        pm.hook.okf_end_graph(bundle=bundle, graph=graph)
        return graph
    except Exception:
        pm.hook.okf_abort_graph(bundle=bundle)
        raise


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
    if concept_id not in {concept.concept_id for concept in graph.concepts}:
        raise ValueError(f"Concept {concept_id!r} is not in graph")

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


def find_unlinked_mentions(
    bundle: BundleConfig,
    *,
    refresh: bool = True,
) -> UnlinkedMentionsResult:
    """Return concept titles mentioned in other concepts' bodies without a Markdown link.

    Searches the body text only; matches in titles or frontmatter fields are not
    reported.  Requires ``bundle.okf_cache_dir`` to be configured; raises
    ``SearchConfigError`` otherwise.  Pass ``refresh=False`` to skip FTS index
    refresh and query the existing cache directly.  Regardless of ``refresh``,
    concept files are read from disk to compute already-linked pairs, so
    read/decode/parse errors may appear in ``problems`` in either mode.

    Non-fatal failures (unreadable or unparseable concepts) are collected in
    ``UnlinkedMentionsResult.problems`` rather than raised or silently dropped.
    """
    from okf_core.listing import list_concepts
    from okf_core.search import (
        SearchConfigError,
        _build_fts_query,
        _ensure_search_schema,
        _refresh_search_index,
    )

    if bundle.okf_cache_dir is None:
        raise SearchConfigError(
            "okf_cache_dir is not configured; enable bundle-level caching to use find_unlinked_mentions"
        )

    bundle.okf_cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = bundle.okf_cache_dir / "okf-cache.db"

    problems: list[GraphProblem] = []

    with sqlite3.connect(db_path) as conn:
        _ensure_search_schema(conn)

        if refresh:
            resolved_manifest = scan_bundle(bundle)
            listing = list_concepts(
                bundle, manifest=resolved_manifest, with_content=True
            )
            _refresh_search_index(conn, bundle, listing)
            for lp in listing.problems:
                problems.append(
                    GraphProblem(
                        concept_id=lp.concept_id,
                        path=lp.path,
                        kind=lp.kind,
                        message=lp.message,
                    )
                )

        rows = conn.execute(
            "SELECT concept_id, path, title FROM concept_fts"
        ).fetchall()

    all_concepts = {
        concept_id: (bundle.bundle_root / rel_path, title or "")
        for concept_id, rel_path, title in rows
    }

    # Build set of already-linked (source, target) pairs by parsing each concept
    # body (same scope as build_bundle_graph — frontmatter links do not count).
    linked_pairs: set[tuple[str, str]] = set()
    for source_id, (source_path, _) in all_concepts.items():
        try:
            content = source_path.read_text(encoding="utf-8")
            doc = parse_concept_document(content)
        except OSError as exc:
            problems.append(
                GraphProblem(
                    concept_id=source_id,
                    path=source_path,
                    kind="read-error",
                    message=str(exc),
                )
            )
            continue
        except UnicodeDecodeError as exc:
            problems.append(
                GraphProblem(
                    concept_id=source_id,
                    path=source_path,
                    kind="decode-error",
                    message=str(exc),
                )
            )
            continue
        except DocumentParseError as exc:
            problems.append(
                GraphProblem(
                    concept_id=source_id,
                    path=source_path,
                    kind="parse-error",
                    message=str(exc),
                )
            )
            continue
        for md_link in extract_markdown_links(doc.body):
            link = _resolve_concept_link(
                bundle,
                _MinimalEntry(source_id, source_path),
                md_link,
            )
            if link is not None and link.target_concept_id is not None:
                linked_pairs.add((source_id, link.target_concept_id))

    # For each target concept, query FTS body column for its title in other concepts
    seen_pairs: set[tuple[str, str]] = set()
    suggestions: list[LinkSuggestion] = []

    with sqlite3.connect(db_path) as conn:
        for target_id, (target_path, title) in sorted(all_concepts.items()):
            if not title:
                continue
            fts_query = _build_fts_query(title)
            if fts_query is None:
                continue

            # Scope to the body column so title/description/fields matches are excluded.
            # Parentheses ensure the entire expression (including AND terms) is restricted
            # to body when fts_query contains boolean operators.
            body_query = f"body : ({fts_query})"
            hits = conn.execute(
                """
                SELECT
                    concept_id,
                    path,
                    snippet(concept_fts, -1, '[', ']', '...', 16) AS snippet
                FROM concept_fts
                WHERE concept_fts MATCH ? AND concept_id != ?
                ORDER BY concept_id
                """,
                (body_query, target_id),
            ).fetchall()

            for source_id, rel_path, snippet in hits:
                pair = (source_id, target_id)
                if pair in seen_pairs or pair in linked_pairs:
                    continue
                seen_pairs.add(pair)
                suggestions.append(
                    LinkSuggestion(
                        source_concept_id=source_id,
                        source_path=bundle.bundle_root / rel_path,
                        target_concept_id=target_id,
                        target_path=target_path,
                        matched_text=snippet or title,
                    )
                )

    return UnlinkedMentionsResult(
        suggestions=tuple(
            sorted(
                suggestions, key=lambda s: (s.source_concept_id, s.target_concept_id)
            )
        ),
        problems=tuple(
            sorted(problems, key=lambda p: (str(p.path), p.kind, p.concept_id))
        ),
    )


@dataclass
class _MinimalEntry:
    concept_id: str
    path: Path


def _collect_link_text(children: Sequence[Any]) -> str:
    parts: list[str] = []
    for child in children:
        child_type = getattr(child, "type", None)
        if child_type == "link_close":
            break
        if child_type in {"text", "code_inline"}:
            parts.append(getattr(child, "content", ""))
    return "".join(parts)


def _graph_problem(
    entry: ConceptManifestEntry,
    kind: str,
    exc: Exception,
) -> GraphProblem:
    return GraphProblem(
        concept_id=entry.concept_id,
        path=entry.path,
        kind=kind,
        message=str(exc),
    )


def _resolve_concept_link(
    bundle: BundleConfig,
    source: ConceptManifestEntry | _MinimalEntry,
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

    try:
        target_concept_id = path_to_concept_id(target_path, bundle)
    except ConceptPathError:
        if _is_ignored_reserved_path(target_path, bundle):
            return None
        target_concept_id = None

    return ConceptLink(
        source_concept_id=source.concept_id,
        source_path=source.path,
        text=markdown_link.text,
        target=markdown_link.target,
        target_path=target_path,
        target_concept_id=target_concept_id,
        title=markdown_link.title,
    )


_LINK_SORT_FIELDS: tuple[str, ...] = tuple(
    dict.fromkeys(
        ("source_concept_id", "target_path", "target_concept_id", "target")
        + tuple(f.name for f in dataclasses.fields(ConceptLink))
    )
)


def _link_sort_key(link: ConceptLink) -> tuple[str, ...]:
    return tuple(
        "" if (v := getattr(link, name)) is None else str(v)
        for name in _LINK_SORT_FIELDS
    )


def _is_ignored_reserved_path(path: Path, bundle: BundleConfig) -> bool:
    if not is_reserved_concept_path(path, bundle):
        return False
    try:
        path.relative_to(bundle.bundle_root.resolve(strict=False))
    except ValueError:
        return False
    return True

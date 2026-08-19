"""Markdown link extraction and graph traversal for OKF bundles."""

from __future__ import annotations

import dataclasses
import os
import sqlite3
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError, parse_concept_document
from okf_core.manifest import BundleManifest, ConceptManifestEntry, scan_bundle
from okf_core.markdown_engine import link_children, render_inline_children
from okf_core.patching import (
    DocumentChangePlan,
    apply_document_change,
    plan_markdown_section_append,
)
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


# GraphProblem.kind values that mean a file's links could not be extracted at
# all -- as opposed to e.g. "stable-id-missing", a validation annotation on
# an entry whose body (and links) were still fully parsed. Callers that need
# a complete view of the link graph (e.g. concept moves, graph repair) treat
# any of these as a reason the graph can't be trusted as complete.
SCAN_FAILURE_KINDS = frozenset(
    {"path-error", "read-error", "decode-error", "parse-error"}
)


@dataclass(frozen=True)
class LinkSuggestion:
    """A candidate link: concept title mentioned in body without a Markdown link."""

    source_concept_id: str
    source_path: Path
    target_concept_id: str
    target_path: Path
    matched_text: str
    target_title: str


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


def _resolve_entry_links(
    bundle: BundleConfig,
    entry: ConceptManifestEntry,
) -> tuple[list[ConceptLink] | None, GraphProblem | None]:
    """Read, parse, and extract links for one concept entry.

    Returns ``(links, None)`` on success or ``(None, problem)`` if the entry
    could not be read or parsed.
    """
    try:
        markdown = entry.content
    except OSError as exc:
        return None, _graph_problem(entry, "read-error", exc)
    except UnicodeDecodeError as exc:
        return None, _graph_problem(entry, "decode-error", exc)

    try:
        document = parse_concept_document(markdown)
    except DocumentParseError as exc:
        return None, _graph_problem(entry, "parse-error", exc)

    resolved_extracted: list[ConceptLink] = []
    for markdown_link in extract_markdown_links(document.body):
        link = _resolve_concept_link(bundle, entry, markdown_link)
        if link is not None:
            resolved_extracted.append(link)
    return resolved_extracted, None


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
                entry_links, problem = _resolve_entry_links(bundle, entry)
                if problem is not None:
                    problems.append(problem)
                    entry_links = None

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

    Searches visible body prose only; matches in titles, frontmatter fields, code
    blocks, inline code, image destinations, or Markdown link destinations are
    not reported.  Requires ``bundle.okf_cache_dir`` to be configured; raises
    ``SearchConfigError`` otherwise.  Pass ``refresh=False`` to skip persistent
    FTS index refresh and query the existing cache directly.  Regardless of
    ``refresh``, concept files are read from disk to compute already-linked pairs
    and eligible prose, so read/decode/parse errors may appear in ``problems`` in
    either mode.

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

    with sqlite3.connect(db_path, timeout=30.0) as conn:
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
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

    linked_pairs, prose_bodies, collect_problems = _collect_linked_pairs_and_prose(
        bundle, all_concepts
    )
    problems.extend(collect_problems)

    # For each target concept, query FTS body column for its title in other concepts
    seen_pairs: set[tuple[str, str]] = set()
    suggestions: list[LinkSuggestion] = []

    with sqlite3.connect(db_path, timeout=30.0) as conn:
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("""
            CREATE VIRTUAL TABLE temp.unlinked_mentions_fts USING fts5(
                concept_id UNINDEXED,
                path UNINDEXED,
                body,
                tokenize = 'unicode61'
            );
            """)
        conn.executemany(
            """
            INSERT INTO unlinked_mentions_fts (concept_id, path, body)
            VALUES (?, ?, ?)
            """,
            [
                (source_id, rel_path, prose)
                for source_id, (rel_path, prose) in sorted(prose_bodies.items())
            ],
        )
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
                    snippet(unlinked_mentions_fts, -1, '[', ']', '...', 16) AS snippet
                FROM unlinked_mentions_fts
                WHERE unlinked_mentions_fts MATCH ? AND concept_id != ?
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
                        target_title=title,
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


def _collect_linked_pairs_and_prose(
    bundle: BundleConfig,
    all_concepts: dict[str, tuple[Path, str]],
) -> tuple[set[tuple[str, str]], dict[str, tuple[str, str]], list[GraphProblem]]:
    """Build the set of already-linked (source, target) pairs and per-concept prose.

    Parses each concept body (same scope as build_bundle_graph — frontmatter
    links do not count) to determine which (source, target) pairs are already
    linked and to extract the visible prose used for the unlinked-mentions FTS
    search.
    """
    linked_pairs: set[tuple[str, str]] = set()
    prose_bodies: dict[str, tuple[str, str]] = {}
    problems: list[GraphProblem] = []
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
        prose_bodies[source_id] = (
            source_path.relative_to(bundle.bundle_root).as_posix(),
            _extract_markdown_prose(doc.body),
        )
        for md_link in extract_markdown_links(doc.body):
            link = _resolve_concept_link(
                bundle,
                _MinimalEntry(source_id, source_path),
                md_link,
            )
            if link is not None and link.target_concept_id is not None:
                linked_pairs.add((source_id, link.target_concept_id))

    return linked_pairs, prose_bodies, problems


def _extract_markdown_prose(markdown: str) -> str:
    """Return visible Markdown prose without code or destination metadata."""
    parts: list[str] = []
    for token in _MARKDOWN.parse(markdown):
        if token.type != "inline" or token.children is None:
            continue
        for child in token.children:
            if child.type == "text":
                parts.append(child.content)
            elif child.type in {"code_inline", "image"}:
                parts.append(" ")
            elif child.type in {"softbreak", "hardbreak"}:
                parts.append("\n")
        parts.append("\n")
    return "".join(parts)


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
    # Markdown-it emits percent-encoded hrefs for paths with spaces/special
    # characters (e.g. "old%20file.md"); decode before matching against the
    # literal on-disk path, or such links are wrongly treated as broken.
    # Decoded per-segment (not as one string) so an encoded separator like
    # "%2F" can't be conflated with a real "/" path boundary: if decoding a
    # single segment produces its own "/", that segment can't correspond to
    # an actual filesystem path component, so the link is unresolvable.
    segments = [unquote(segment) for segment in parsed.path.split("/")]
    if any("/" in segment for segment in segments):
        return None
    path = "/".join(segments)
    if not path.endswith(".md"):
        return None

    try:
        if path.startswith("/"):
            target_path = (bundle.bundle_root / path.lstrip("/")).resolve(strict=False)
        else:
            target_path = (source.path.parent / path).resolve(strict=False)
    except ValueError:
        # A decoded href can contain characters invalid in a filesystem path
        # (e.g. "%00" decodes to an embedded NUL), which raises ValueError
        # from Path construction/resolve rather than yielding a normal
        # broken link -- treat it the same as any other unresolvable link.
        return None

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


# ---------------------------------------------------------------------------
# Applying link suggestions (issue #61): writing selected `find_unlinked_
# mentions` results back into the source concept's Markdown body as inline
# links under a conventional heading.
# ---------------------------------------------------------------------------

#: Default heading `find_unlinked_mentions` suggestions are appended under,
#: an H2 per the issue's own illustrative example.
DEFAULT_LINK_SUGGESTION_HEADING = "See also"
DEFAULT_LINK_SUGGESTION_HEADING_LEVEL = 2


def link_suggestion_href(suggestion: LinkSuggestion) -> str:
    """Compute the Markdown link href for `suggestion`, relative to its source.

    Analogous to the relative-target branch of
    `patching.link_target_for_new_location`: a POSIX-separator path from
    `suggestion.source_path`'s own directory to `suggestion.target_path`,
    percent-encoded (leaving '/' as a literal separator) so a target
    containing a space or other special character comes out in the same
    normalized form markdown-it itself emits for such hrefs. Unlike
    `link_target_for_new_location`, there is no existing link destination
    whose absolute-vs-relative style must be preserved -- a link suggestion
    is always written relative to its source concept's directory.
    """

    relative = os.path.relpath(suggestion.target_path, suggestion.source_path.parent)
    posix_path = PurePosixPath(relative.replace("\\", "/")).as_posix()
    return quote(posix_path, safe="/")


def _link_suggestion_line(suggestion: LinkSuggestion) -> str:
    """Render `suggestion` as one Markdown bullet: `- [title](href)`.

    `suggestion.target_title` is arbitrary, human-authored text embedded as
    literal Markdown link *text* -- built via the shared `markdown_engine`
    engine's own `link_children`/`render_inline_children` primitives (#199),
    the canonical, sole-sanctioned place for Markdown escaping, rather than
    a bespoke escaping helper: `link_children` wraps `target_title` as a
    literal text child of a fresh `[text](href)` link, and
    `render_inline_children` renders it back through `mdformat`'s renderer,
    which escapes `[`, `]`, and backslash exactly when needed for the
    result to round-trip on reparse.
    """

    href = link_suggestion_href(suggestion)
    link = render_inline_children(link_children(href, suggestion.target_title))
    return f"- {link}\n"


@dataclass(frozen=True)
class SuggestionSelection:
    """The result of matching caller-requested `(source, target)` concept ID
    pairs against a set of discovered `LinkSuggestion`s."""

    selected: tuple[LinkSuggestion, ...]
    unmatched_pairs: tuple[tuple[str, str], ...]


def select_link_suggestions(
    suggestions: Sequence[LinkSuggestion],
    pairs: Sequence[tuple[str, str]] | None,
) -> SuggestionSelection:
    """Filter `suggestions` down to caller-selected `(source, target)` pairs.

    `pairs` is a sequence of `(source_concept_id, target_concept_id)` tuples
    identifying which discovered suggestions to keep; a pair with no
    matching suggestion is reported in `unmatched_pairs` rather than silently
    dropped (AGENTS.md "surface problems explicitly"), so a caller can
    reject a typo'd or already-applied selector instead of quietly applying
    fewer suggestions than requested. A duplicate pair in `pairs` is matched
    once. `pairs=None` means "no selection requested" -- every suggestion is
    selected, matching `find_unlinked_mentions`' own discovery order.
    """

    if pairs is None:
        return SuggestionSelection(selected=tuple(suggestions), unmatched_pairs=())

    by_pair: dict[tuple[str, str], LinkSuggestion] = {
        (s.source_concept_id, s.target_concept_id): s for s in suggestions
    }
    selected: list[LinkSuggestion] = []
    unmatched: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pair in pairs:
        if pair in seen:
            continue
        seen.add(pair)
        match = by_pair.get(pair)
        if match is None:
            unmatched.append(pair)
        else:
            selected.append(match)

    return SuggestionSelection(
        selected=tuple(selected), unmatched_pairs=tuple(unmatched)
    )


@dataclass(frozen=True)
class LinkSuggestionApplyPreparation:
    """Read-only outputs of planning a link-suggestion apply; never writes.

    `section_plans` groups suggestions by source file: every selected
    suggestion targeting the same source concept is folded into a single
    `DocumentChangePlan` for that file's heading section (one new section
    body per file, not one write per suggestion).
    """

    section_plans: Mapping[Path, DocumentChangePlan]
    applied_suggestions: tuple[LinkSuggestion, ...]


@dataclass(frozen=True)
class LinkSuggestionApplyResult:
    """The result of applying selected link suggestions."""

    updated_files: tuple[Path, ...]
    applied_suggestions: tuple[LinkSuggestion, ...]


def _suggestion_sort_key(suggestion: LinkSuggestion) -> tuple[str, str]:
    return (suggestion.source_concept_id, suggestion.target_concept_id)


def plan_link_suggestions_apply(
    bundle: BundleConfig,
    suggestions: Sequence[LinkSuggestion],
    *,
    heading: str = DEFAULT_LINK_SUGGESTION_HEADING,
    level: int = DEFAULT_LINK_SUGGESTION_HEADING_LEVEL,
) -> LinkSuggestionApplyPreparation:
    """Read-only planning for writing `suggestions` into their source files.

    Suggestions are grouped by `source_path`; each source file gets one
    `plan_markdown_section_append` call appending every one of its selected
    suggestions, in `(source_concept_id, target_concept_id)` order, under
    `heading` at `level` (default `## See also`). `plan_markdown_section_
    append` itself skips a suggestion whose target already has a link
    somewhere in the section, so re-planning (or re-applying) an
    already-written suggestion is a no-op rather than a duplicate bullet.
    Never writes anything -- safe to call repeatedly, e.g. for a dry-run
    preview.
    """

    by_file: dict[Path, list[LinkSuggestion]] = {}
    for suggestion in suggestions:
        by_file.setdefault(suggestion.source_path, []).append(suggestion)

    section_plans: dict[Path, DocumentChangePlan] = {}
    for path, file_suggestions in sorted(by_file.items(), key=lambda kv: str(kv[0])):
        lines = [
            _link_suggestion_line(s)
            for s in sorted(file_suggestions, key=_suggestion_sort_key)
        ]
        section_plans[path] = plan_markdown_section_append(
            bundle, path, heading, lines, level=level
        )

    return LinkSuggestionApplyPreparation(
        section_plans=section_plans,
        applied_suggestions=tuple(sorted(suggestions, key=_suggestion_sort_key)),
    )


def apply_link_suggestions(
    bundle: BundleConfig,
    suggestions: Sequence[LinkSuggestion],
    *,
    heading: str = DEFAULT_LINK_SUGGESTION_HEADING,
    level: int = DEFAULT_LINK_SUGGESTION_HEADING_LEVEL,
) -> LinkSuggestionApplyResult:
    """Apply selected link suggestions: plan, then write each file's plan.

    Idempotent: re-running with the same (or an overlapping) suggestion set
    only re-touches files whose plan is not already a no-op -- see
    `plan_link_suggestions_apply`.
    """

    prep = plan_link_suggestions_apply(
        bundle, suggestions, heading=heading, level=level
    )
    updated: list[Path] = []
    for path, plan in prep.section_plans.items():
        result = apply_document_change(bundle, plan)
        if result.changed:
            updated.append(path)

    return LinkSuggestionApplyResult(
        updated_files=tuple(sorted(updated)),
        applied_suggestions=prep.applied_suggestions,
    )

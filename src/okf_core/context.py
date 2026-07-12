"""Deterministic seed-based context pack assembly for OKF bundles."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from okf_core.config import BundleConfig
from okf_core.graph import BundleGraph, ConceptLink, build_bundle_graph
from okf_core.manifest import ConceptManifestEntry


@dataclass(frozen=True)
class ContextEntry:
    """A concept document entry in a context pack."""

    concept_id: str
    path: Path
    title: str | None
    content: str
    selection_reason: Literal["seed", "outbound-link", "backlink"]
    graph_distance: int
    char_count: int


@dataclass(frozen=True)
class ContextPackProblem:
    """A non-fatal problem encountered during context pack assembly."""

    kind: str  # "unknown-seed", "read-error"
    message: str
    concept_id: str = ""
    path: Path | None = None


@dataclass(frozen=True)
class ContextPack:
    """A deterministic seed-based context pack for one configured OKF bundle."""

    bundle_name: str
    seeds: tuple[str, ...]
    entries: tuple[ContextEntry, ...]
    omitted_concept_ids: tuple[str, ...]
    problems: tuple[ContextPackProblem, ...]


def build_context_pack(
    bundle: BundleConfig,
    seed_concept_ids: Sequence[str],
    *,
    graph: BundleGraph | None = None,
    depth: int = 1,
    direction: Literal["outbound", "inbound", "both"] = "both",
    budget_chars: int | None = None,
) -> ContextPack:
    """Build a deterministic context pack from seed concept IDs.

    Seeds appear first in the returned entries, in the order they were
    provided.  Graph-expanded concepts follow, ordered by distance then
    concept ID.  Budget trimming is a stable prefix: entries are added in
    order until adding the next would exceed ``budget_chars``, at which point
    all remaining concepts (including any that would individually fit) are
    omitted.  Character count is used as a proxy for token count.

    Problems are returned for unknown seeds and file-read errors.  Budget
    omissions and read errors are reported via ``omitted_concept_ids``;
    read errors are also reported in ``problems``.

    Concept content comes from ``ConceptManifestEntry.content`` so scanned
    entries reuse the manifest's raw Markdown snapshot instead of rereading.
    """
    if depth < 0:
        raise ValueError("depth must be greater than or equal to 0")
    if budget_chars is not None and budget_chars < 0:
        raise ValueError("budget_chars must be non-negative")
    if direction not in {"outbound", "inbound", "both"}:
        raise ValueError("direction must be 'outbound', 'inbound', or 'both'")

    resolved_graph = graph if graph is not None else build_bundle_graph(bundle)
    concept_index = {c.concept_id: c for c in resolved_graph.concepts}

    valid_seeds, seed_problems = _resolve_seeds(seed_concept_ids, concept_index)
    problems: list[ContextPackProblem] = list(seed_problems)

    outbound_adj, inbound_adj = _build_adjacency_maps(resolved_graph.links)
    discovered, seed_order = _expand_from_seeds(
        valid_seeds, outbound_adj, inbound_adj, depth, direction
    )

    ordered_ids = sorted(
        discovered.keys(), key=lambda cid: _sort_key(cid, discovered, seed_order)
    )

    entries, omitted, read_error_problems = _assemble_entries(
        ordered_ids, discovered, concept_index, budget_chars
    )
    problems.extend(read_error_problems)

    return ContextPack(
        bundle_name=resolved_graph.bundle_name,
        seeds=tuple(valid_seeds),
        entries=tuple(entries),
        omitted_concept_ids=tuple(omitted),
        problems=tuple(problems),
    )


def _resolve_seeds(
    seed_concept_ids: Sequence[str],
    concept_index: dict[str, ConceptManifestEntry],
) -> tuple[list[str], list[ContextPackProblem]]:
    """Deduplicate seed IDs, splitting them into valid seeds (present in
    concept_index, in first-occurrence order) and unknown-seed problems."""
    seen_seeds: set[str] = set()
    valid_seeds: list[str] = []
    problems: list[ContextPackProblem] = []
    for seed_id in seed_concept_ids:
        if seed_id in seen_seeds:
            continue
        seen_seeds.add(seed_id)
        if seed_id not in concept_index:
            problems.append(
                ContextPackProblem(
                    kind="unknown-seed",
                    message=f"Seed concept {seed_id!r} is not in the bundle",
                    concept_id=seed_id,
                )
            )
        else:
            valid_seeds.append(seed_id)
    return valid_seeds, problems


def _build_adjacency_maps(
    links: Sequence[ConceptLink],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build outbound and inbound adjacency maps from bundle graph links,
    skipping links whose target_concept_id is unresolved."""
    outbound_adj: dict[str, list[str]] = {}
    inbound_adj: dict[str, list[str]] = {}
    for link in links:
        if link.target_concept_id is not None:
            outbound_adj.setdefault(link.source_concept_id, []).append(
                link.target_concept_id
            )
            inbound_adj.setdefault(link.target_concept_id, []).append(
                link.source_concept_id
            )
    return outbound_adj, inbound_adj


def _expand_from_seeds(
    valid_seeds: list[str],
    outbound_adj: dict[str, list[str]],
    inbound_adj: dict[str, list[str]],
    depth: int,
    direction: Literal["outbound", "inbound", "both"],
) -> tuple[
    dict[str, tuple[int, Literal["seed", "outbound-link", "backlink"]]],
    dict[str, int],
]:
    """Run BFS from valid_seeds up to `depth` hops per `direction`, returning the
    discovered map (concept_id -> (distance, selection_reason)) and the seed_order
    map (seed concept_id -> input position) used for stable sorting."""
    discovered: dict[str, tuple[int, Literal["seed", "outbound-link", "backlink"]]] = {}
    seed_order: dict[str, int] = {}
    for i, seed_id in enumerate(valid_seeds):
        if seed_id not in discovered:
            discovered[seed_id] = (0, "seed")
            seed_order[seed_id] = i

    frontier: list[str] = list(valid_seeds)
    for d in range(1, depth + 1):
        next_frontier: list[str] = []
        for current_id in frontier:
            if direction != "inbound":
                for nid in outbound_adj.get(current_id, []):
                    if nid not in discovered:
                        discovered[nid] = (d, "outbound-link")
                        next_frontier.append(nid)
            if direction != "outbound":
                for nid in inbound_adj.get(current_id, []):
                    if nid not in discovered:
                        discovered[nid] = (d, "backlink")
                        next_frontier.append(nid)
        frontier = sorted(next_frontier)

    return discovered, seed_order


def _assemble_entries(
    ordered_ids: list[str],
    discovered: dict[str, tuple[int, Literal["seed", "outbound-link", "backlink"]]],
    concept_index: dict[str, ConceptManifestEntry],
    budget_chars: int | None,
) -> tuple[list[ContextEntry], list[str], list[ContextPackProblem]]:
    """Build ContextEntry objects for ordered_ids under a stable-prefix budget
    cutoff: once a concept would push total_chars over budget_chars, that concept
    and all subsequent concepts are omitted (no knapsack repacking). Concepts whose
    content raises OSError/UnicodeDecodeError are also omitted and reported as
    read-error problems. Content is read via ConceptManifestEntry.content (never a
    fresh Path read) to preserve scan-time caching."""
    entries: list[ContextEntry] = []
    omitted: list[str] = []
    problems: list[ContextPackProblem] = []
    total_chars = 0
    budget_exhausted = False

    for concept_id in ordered_ids:
        distance, reason = discovered[concept_id]
        entry_meta = concept_index[concept_id]

        if budget_exhausted:
            omitted.append(concept_id)
            continue

        try:
            content = entry_meta.content
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(
                ContextPackProblem(
                    kind="read-error",
                    message=str(exc),
                    concept_id=concept_id,
                    path=entry_meta.path,
                )
            )
            omitted.append(concept_id)
            continue

        char_count = len(content)

        if budget_chars is not None and total_chars + char_count > budget_chars:
            omitted.append(concept_id)
            budget_exhausted = True
            continue

        entries.append(
            ContextEntry(
                concept_id=concept_id,
                path=entry_meta.path,
                title=_extract_title(entry_meta),
                content=content,
                selection_reason=reason,
                graph_distance=distance,
                char_count=char_count,
            )
        )
        total_chars += char_count

    return entries, omitted, problems


def _sort_key(
    concept_id: str,
    discovered: dict[str, tuple[int, Literal["seed", "outbound-link", "backlink"]]],
    seed_order: dict[str, int],
) -> tuple[int, int, str]:
    distance, reason = discovered[concept_id]
    if reason == "seed":
        # Seeds come first (group 0), ordered by input position
        return (0, seed_order[concept_id], "")
    # Graph-expanded come after (group = distance >= 1), then stable by concept_id
    return (distance, 0, concept_id)


def _extract_title(entry: ConceptManifestEntry) -> str | None:
    raw = entry.frontmatter.get("title")
    if raw is None:
        return None
    title = re.sub(r"[\r\n]+", " ", str(raw)).strip()
    return title if title else None

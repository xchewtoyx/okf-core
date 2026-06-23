"""Deterministic seed-based context pack assembly for OKF bundles."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from okf_core.config import BundleConfig
from okf_core.graph import BundleGraph, build_bundle_graph
from okf_core.manifest import ConceptManifestEntry


@dataclass(frozen=True)
class ContextEntry:
    """A concept document entry in a context pack."""

    concept_id: str
    path: Path
    title: str | None
    content: str
    selection_reason: str  # "seed", "outbound-link", "backlink"
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
    concept ID.  Budget trimming is deterministic but approximate: character
    count is used as a proxy for token count.

    Problems are returned for unknown seeds and file-read errors.  Budget
    omissions are reported via ``omitted_concept_ids``, not ``problems``.
    """
    if depth < 0:
        raise ValueError("depth must be greater than or equal to 0")

    resolved_graph = graph if graph is not None else build_bundle_graph(bundle)
    concept_index = {c.concept_id: c for c in resolved_graph.concepts}

    problems: list[ContextPackProblem] = []

    seen_seeds: set[str] = set()
    valid_seeds: list[str] = []
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

    # BFS traversal from seeds; track (distance, selection_reason) per concept
    discovered: dict[str, tuple[int, str]] = {}
    seed_order: dict[str, int] = {}
    for i, seed_id in enumerate(valid_seeds):
        if seed_id not in discovered:
            discovered[seed_id] = (0, "seed")
            seed_order[seed_id] = i

    frontier: list[str] = list(valid_seeds)
    for d in range(1, depth + 1):
        next_frontier: list[str] = []
        for current_id in frontier:
            for neighbor_id, reason in _neighbors(resolved_graph, current_id, direction):
                if neighbor_id not in discovered:
                    discovered[neighbor_id] = (d, reason)
                    next_frontier.append(neighbor_id)
        frontier = sorted(set(next_frontier))

    ordered_ids = sorted(discovered.keys(), key=lambda cid: _sort_key(cid, discovered, seed_order))

    entries: list[ContextEntry] = []
    omitted: list[str] = []
    total_chars = 0

    for concept_id in ordered_ids:
        distance, reason = discovered[concept_id]
        entry_meta = concept_index[concept_id]

        try:
            content = entry_meta.path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(
                ContextPackProblem(
                    kind="read-error",
                    message=str(exc),
                    concept_id=concept_id,
                    path=entry_meta.path,
                )
            )
            continue

        char_count = len(content)

        if budget_chars is not None and total_chars + char_count > budget_chars:
            omitted.append(concept_id)
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

    return ContextPack(
        bundle_name=resolved_graph.bundle_name,
        seeds=tuple(valid_seeds),
        entries=tuple(entries),
        omitted_concept_ids=tuple(omitted),
        problems=tuple(problems),
    )


def _sort_key(
    concept_id: str,
    discovered: dict[str, tuple[int, str]],
    seed_order: dict[str, int],
) -> tuple[int, int, str]:
    distance, reason = discovered[concept_id]
    if reason == "seed":
        # Seeds come first (group 0), ordered by input position
        return (0, seed_order[concept_id], "")
    # Graph-expanded come after (group = distance >= 1), then stable by concept_id
    return (distance, 0, concept_id)


def _neighbors(
    graph: BundleGraph,
    concept_id: str,
    direction: Literal["outbound", "inbound", "both"],
) -> list[tuple[str, str]]:
    """Return (neighbor_concept_id, selection_reason) pairs for graph expansion."""
    result: list[tuple[str, str]] = []
    for link in graph.links:
        if direction in ("outbound", "both") and link.source_concept_id == concept_id:
            if link.target_concept_id is not None:
                result.append((link.target_concept_id, "outbound-link"))
        if direction in ("inbound", "both") and link.target_concept_id == concept_id:
            result.append((link.source_concept_id, "backlink"))
    return result


def _extract_title(entry: ConceptManifestEntry) -> str | None:
    raw = entry.frontmatter.get("title")
    if raw is None:
        return None
    title = str(raw).strip()
    return title if title else None

"""In-process normalized graph model for later OKF graph-report stages.

Policy (also on :class:`NormalizedBundleGraph`):

- Source of truth is the in-process ``BundleGraph`` + ``BundleListing`` pair —
  the same facts ``okf graph`` and ``okf list-concepts --with-graph-counts``
  emit. This module does not shell out to the ``okf`` binary and does not
  re-parse concept bodies, so pure fragment targets are not recovered.
- The unique directed edge key is ``(source_id, target_id)``. Repeated
  ``ConceptLink`` instances collapse to one edge; ``instance_count`` and a
  deterministic ``texts`` tuple are retained.
- Self-links (``source_id == target_id``) go on ``self_links``, not
  ``edges``.
- Paths on the model are bundle-relative POSIX
  (``Path.relative_to(bundle_root).as_posix()``). A path that escapes the
  root raises :class:`GraphModelError` rather than emitting an absolute path.
- An edge is added only when it is a resolved ``graph.links`` instance whose
  both endpoints are in the listing node set. Resolved links with an unlisted
  endpoint go on ``excluded_links`` with reason ``unlisted-endpoint``.
- Listing omitting missing-``type`` concepts that still appear on
  ``graph.concepts`` is expected, not disagreement: those IDs stay in
  ``problems``, not as report nodes.
- Non-fatal OKF problems are carried onto the model (union of graph + listing
  problems, deterministic sort, relative paths) and do not fail construction.
- Cross-payload disagreement and wrong input types raise
  :class:`GraphModelError`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from okf_core.config import BundleConfig
from okf_core.graph import BundleGraph, ConceptLink, GraphProblem, build_bundle_graph
from okf_core.listing import BundleListing, ListingProblem, list_concepts
from okf_core.manifest import BundleManifest, scan_bundle

_UNLISTED_ENDPOINT = "unlisted-endpoint"


class GraphModelError(Exception):
    """Raised when a bundle graph cannot be normalized fail-closed."""


@dataclass(frozen=True)
class GraphModelNode:
    """One listed concept, with verbatim OKF metrics and unique-edge degrees."""

    concept_id: str
    path: str
    type: str
    title: str | None
    description: str | None
    inbound_link_count: int
    outbound_link_count: int
    pagerank: float
    is_orphan: bool
    unique_inbound_degree: int
    unique_outbound_degree: int


@dataclass(frozen=True)
class GraphModelEdge:
    """One unique directed edge between two distinct listed concepts."""

    source_id: str
    target_id: str
    instance_count: int
    texts: tuple[str, ...]


@dataclass(frozen=True)
class GraphModelSelfLink:
    """Resolved self-links for one listed concept, excluded from unique edges."""

    concept_id: str
    instance_count: int
    texts: tuple[str, ...]


@dataclass(frozen=True)
class GraphModelBrokenLink:
    """A broken internal concept link copied from ``BundleGraph.broken_links``."""

    source_id: str
    source_path: str
    text: str
    target: str
    target_path: str
    target_concept_id: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class GraphModelExcludedLink:
    """A resolved link omitted from unique edges (unlisted endpoint)."""

    source_id: str
    target_id: str | None
    text: str
    reason: str
    source_path: str
    target_path: str


@dataclass(frozen=True)
class GraphModelProblem:
    """A non-fatal OKF graph or listing problem carried onto the model."""

    concept_id: str
    path: str
    kind: str
    message: str


@dataclass(frozen=True)
class NormalizedBundleGraph:
    """Portable unique-edge view of one bundle's in-process OKF graph.

    Source of truth is the in-process ``BundleGraph`` + ``BundleListing``
    pair — the same facts ``okf graph`` and
    ``okf list-concepts --with-graph-counts`` emit. Repeated directed
    ``ConceptLink`` instances collapse under the unique key
    ``(source_id, target_id)``; ``instance_count`` and deterministic ``texts``
    are retained. Self-links go on ``self_links``, not ``edges``. Pure
    fragment targets are not recovered by re-parsing bodies. Paths are
    bundle-relative POSIX; a path that escapes the root raises
    :class:`GraphModelError` rather than becoming absolute. An edge is added
    only when it is a resolved ``graph.links`` instance with both endpoints
    in the listing node set. Listing omitting missing-``type`` concepts that
    still appear on ``graph.concepts`` is expected: those IDs stay in
    ``problems``, not as report nodes, and their resolved links go on
    ``excluded_links`` with reason ``unlisted-endpoint``. Each node stores
    both the verbatim OKF inbound/outbound/PageRank/orphan fields and the
    unique-edge degrees (which exclude self-links and unlisted-endpoint
    links). Non-fatal OKF problems do not fail construction.
    """

    bundle_name: str
    nodes: tuple[GraphModelNode, ...] = ()
    edges: tuple[GraphModelEdge, ...] = ()
    self_links: tuple[GraphModelSelfLink, ...] = ()
    broken_links: tuple[GraphModelBrokenLink, ...] = ()
    excluded_links: tuple[GraphModelExcludedLink, ...] = ()
    problems: tuple[GraphModelProblem, ...] = ()

    def to_portable_dict(self) -> dict[str, Any]:
        """Return a JSON-ready snapshot of this model.

        Includes only model fields: no analysis, no timestamps, and no
        absolute paths.
        """

        return _to_portable(asdict(self))


def normalize_bundle_graph(
    graph: BundleGraph,
    listing: BundleListing,
    *,
    bundle_root: Path,
) -> NormalizedBundleGraph:
    """Normalize in-process graph + listing facts into one portable model.

    Raises :class:`GraphModelError` on type errors, escaped paths, or
    cross-payload disagreement. Non-fatal OKF problems are carried onto the
    result and do not fail construction.
    """

    typed_graph, typed_listing = _require_bundle_graph_and_listing(graph, listing)
    root = _require_bundle_root(bundle_root)
    _require_matching_bundle_names(typed_graph, typed_listing)
    node_ids = _validated_listing_nodes(typed_graph, typed_listing)
    _require_link_count_agreement(typed_graph, typed_listing)
    _require_orphan_agreement(typed_listing)
    edges, self_links, excluded = _collect_unique_edges(typed_graph, node_ids, root)
    return NormalizedBundleGraph(
        bundle_name=typed_graph.bundle_name,
        nodes=_nodes_from_listing(typed_listing, edges, root),
        edges=edges,
        self_links=self_links,
        broken_links=_broken_links_from_graph(typed_graph, root),
        excluded_links=excluded,
        problems=_union_problems(typed_graph, typed_listing, root),
    )


def acquire_normalized_graph(
    bundle: BundleConfig,
    *,
    manifest: BundleManifest | None = None,
) -> NormalizedBundleGraph:
    """Scan once, build graph + listing, and normalize them.

    Shares a single ``scan_bundle`` result across ``build_bundle_graph`` and
    ``list_concepts`` so the bundle is not scanned twice. Pass an existing
    ``manifest`` to skip the scan entirely.
    """

    if not isinstance(bundle, BundleConfig):
        raise GraphModelError("bundle must be a BundleConfig")
    resolved = manifest if manifest is not None else scan_bundle(bundle)
    graph = build_bundle_graph(bundle, manifest=resolved)
    listing = list_concepts(bundle, manifest=resolved, graph=graph)
    return normalize_bundle_graph(graph, listing, bundle_root=bundle.bundle_root)


def _require_bundle_graph_and_listing(
    graph: object,
    listing: object,
) -> tuple[BundleGraph, BundleListing]:
    """Reject mapping / wrong-type payloads (the in-process malformed-JSON stand-in)."""

    if isinstance(graph, Mapping) or not isinstance(graph, BundleGraph):
        raise GraphModelError(
            "graph must be a BundleGraph; mapping payloads are not a source of truth"
        )
    if isinstance(listing, Mapping) or not isinstance(listing, BundleListing):
        raise GraphModelError(
            "listing must be a BundleListing; mapping payloads are not a source of truth"
        )
    return graph, listing


def _require_bundle_root(bundle_root: object) -> Path:
    if not isinstance(bundle_root, Path):
        raise GraphModelError("bundle_root must be a pathlib.Path")
    return bundle_root.expanduser().resolve(strict=False)


def _require_matching_bundle_names(graph: BundleGraph, listing: BundleListing) -> None:
    if listing.bundle_name != graph.bundle_name:
        raise GraphModelError(
            f"listing.bundle_name {listing.bundle_name!r} does not match "
            f"graph.bundle_name {graph.bundle_name!r}"
        )


def _validated_listing_nodes(
    graph: BundleGraph,
    listing: BundleListing,
) -> frozenset[str]:
    graph_ids = {entry.concept_id for entry in graph.concepts}
    node_ids: list[str] = []
    for row in listing.concepts:
        if row.concept_id not in graph_ids:
            raise GraphModelError(
                f"listing concept_id {row.concept_id!r} is absent from graph.concepts"
            )
        if (
            row.inbound_link_count is None
            or row.outbound_link_count is None
            or row.pagerank is None
        ):
            raise GraphModelError(
                f"listing concept {row.concept_id!r} is missing required graph "
                "counts (inbound_link_count, outbound_link_count, pagerank)"
            )
        node_ids.append(row.concept_id)
    return frozenset(node_ids)


def _require_link_count_agreement(graph: BundleGraph, listing: BundleListing) -> None:
    outbound, inbound = _recount_resolved_links(graph)
    for row in listing.concepts:
        actual_out = outbound.get(row.concept_id, 0)
        actual_in = inbound.get(row.concept_id, 0)
        if actual_out != row.outbound_link_count or actual_in != row.inbound_link_count:
            raise GraphModelError(
                f"listing link counts for {row.concept_id!r} disagree with "
                f"resolved graph.links (listing outbound="
                f"{row.outbound_link_count} inbound={row.inbound_link_count}; "
                f"graph outbound={actual_out} inbound={actual_in})"
            )


def _recount_resolved_links(
    graph: BundleGraph,
) -> tuple[dict[str, int], dict[str, int]]:
    outbound: dict[str, int] = {}
    inbound: dict[str, int] = {}
    for link in graph.links:
        outbound[link.source_concept_id] = outbound.get(link.source_concept_id, 0) + 1
        if link.target_concept_id is not None:
            inbound[link.target_concept_id] = inbound.get(link.target_concept_id, 0) + 1
    return outbound, inbound


def _require_orphan_agreement(listing: BundleListing) -> None:
    expected = {
        row.concept_id
        for row in listing.concepts
        if row.inbound_link_count == 0 and row.outbound_link_count == 0
    }
    actual = set(listing.orphans)
    if actual != expected:
        raise GraphModelError(
            "listing.orphans disagrees with listed concepts that have "
            f"inbound==0 and outbound==0 (listing={sorted(actual)}; "
            f"expected={sorted(expected)})"
        )


def _collect_unique_edges(
    graph: BundleGraph,
    node_ids: frozenset[str],
    bundle_root: Path,
) -> tuple[
    tuple[GraphModelEdge, ...],
    tuple[GraphModelSelfLink, ...],
    tuple[GraphModelExcludedLink, ...],
]:
    edge_texts: dict[tuple[str, str], list[str]] = {}
    self_texts: dict[str, list[str]] = {}
    excluded: list[GraphModelExcludedLink] = []
    for link in graph.links:
        edge_key, self_key, excluded_link, _problem = _classify_resolved_link(
            link, node_ids, bundle_root
        )
        if edge_key is not None:
            edge_texts.setdefault(edge_key, []).append(link.text)
        if self_key is not None:
            self_texts.setdefault(self_key, []).append(link.text)
        if excluded_link is not None:
            excluded.append(excluded_link)
    edges = tuple(
        GraphModelEdge(
            source_id=source,
            target_id=target,
            instance_count=len(texts),
            texts=tuple(texts),
        )
        for (source, target), texts in sorted(edge_texts.items())
    )
    self_links = tuple(
        GraphModelSelfLink(
            concept_id=concept_id,
            instance_count=len(texts),
            texts=tuple(texts),
        )
        for concept_id, texts in sorted(self_texts.items())
    )
    excluded_links = tuple(
        sorted(
            excluded,
            key=lambda item: (item.source_id, item.target_id or "", item.text),
        )
    )
    return edges, self_links, excluded_links


def _classify_resolved_link(
    link: ConceptLink,
    node_ids: frozenset[str],
    bundle_root: Path,
) -> tuple[
    tuple[str, str] | None,
    str | None,
    GraphModelExcludedLink | None,
    GraphModelProblem | None,
]:
    """Classify one resolved link; the collector only appends the result."""

    source = link.source_concept_id
    target = link.target_concept_id
    if target is None or source not in node_ids or target not in node_ids:
        excluded = GraphModelExcludedLink(
            source_id=source,
            target_id=target,
            text=link.text,
            reason=_UNLISTED_ENDPOINT,
            source_path=_relative_posix(link.source_path, bundle_root),
            target_path=_relative_posix(link.target_path, bundle_root),
        )
        return None, None, excluded, None
    if source == target:
        return None, source, None, None
    return (source, target), None, None, None


def _nodes_from_listing(
    listing: BundleListing,
    edges: tuple[GraphModelEdge, ...],
    bundle_root: Path,
) -> tuple[GraphModelNode, ...]:
    unique_in, unique_out = _unique_degrees(listing, edges)
    orphans = set(listing.orphans)
    nodes = [
        GraphModelNode(
            concept_id=row.concept_id,
            path=_relative_posix(row.path, bundle_root),
            type=row.type,
            title=row.title,
            description=row.description,
            inbound_link_count=_require_int(row.inbound_link_count),
            outbound_link_count=_require_int(row.outbound_link_count),
            pagerank=_require_float(row.pagerank),
            is_orphan=row.concept_id in orphans,
            unique_inbound_degree=unique_in[row.concept_id],
            unique_outbound_degree=unique_out[row.concept_id],
        )
        for row in listing.concepts
    ]
    nodes.sort(key=lambda node: node.concept_id)
    return tuple(nodes)


def _unique_degrees(
    listing: BundleListing,
    edges: tuple[GraphModelEdge, ...],
) -> tuple[dict[str, int], dict[str, int]]:
    inbound = {row.concept_id: 0 for row in listing.concepts}
    outbound = {row.concept_id: 0 for row in listing.concepts}
    for edge in edges:
        outbound[edge.source_id] = outbound.get(edge.source_id, 0) + 1
        inbound[edge.target_id] = inbound.get(edge.target_id, 0) + 1
    return inbound, outbound


def _broken_links_from_graph(
    graph: BundleGraph,
    bundle_root: Path,
) -> tuple[GraphModelBrokenLink, ...]:
    broken = [
        GraphModelBrokenLink(
            source_id=link.source_concept_id,
            source_path=_relative_posix(link.source_path, bundle_root),
            text=link.text,
            target=link.target,
            target_path=_relative_posix(link.target_path, bundle_root),
            target_concept_id=link.target_concept_id,
            title=link.title,
        )
        for link in graph.broken_links
    ]
    broken.sort(key=lambda item: (item.source_id, item.target, item.text))
    return tuple(broken)


def _union_problems(
    graph: BundleGraph,
    listing: BundleListing,
    bundle_root: Path,
) -> tuple[GraphModelProblem, ...]:
    seen: set[tuple[str, str, str, str]] = set()
    problems: list[GraphModelProblem] = []
    raw_problems: tuple[GraphProblem | ListingProblem, ...] = (
        *graph.problems,
        *listing.problems,
    )
    for raw in raw_problems:
        problem = _model_problem(raw, bundle_root)
        key = (problem.concept_id, problem.path, problem.kind, problem.message)
        if key in seen:
            continue
        seen.add(key)
        problems.append(problem)
    problems.sort(
        key=lambda item: (item.path, item.kind, item.concept_id, item.message)
    )
    return tuple(problems)


def _model_problem(
    raw: GraphProblem | ListingProblem,
    bundle_root: Path,
) -> GraphModelProblem:
    return GraphModelProblem(
        concept_id=raw.concept_id,
        path=_relative_posix(raw.path, bundle_root),
        kind=raw.kind,
        message=raw.message,
    )


def _relative_posix(path: Path, bundle_root: Path) -> str:
    candidate = path if path.is_absolute() else bundle_root / path
    try:
        return candidate.resolve(strict=False).relative_to(bundle_root).as_posix()
    except ValueError as exc:
        raise GraphModelError(f"path {path} escapes bundle root {bundle_root}") from exc


def _require_int(value: int | None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise GraphModelError(f"expected int graph count, got {value!r}")
    return value


def _require_float(value: float | None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GraphModelError(f"expected float pagerank, got {value!r}")
    return float(value)


def _to_portable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _to_portable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_portable(item) for item in value]
    return value

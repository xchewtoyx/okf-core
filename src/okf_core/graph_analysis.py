"""Deterministic structural analysis of a ``NormalizedBundleGraph``.

Policy (also on :func:`analyze_normalized_graph`):

- Source of truth is the already-normalized unique-edge model. This module
  does not reconstruct the graph, does not call NetworkX, and does not
  recompute OKF PageRank.
- The undirected projection used for weakly connected components and
  articulation points is the unique directed edge set with each ``{u, v}``
  collapsed once. Self-links and ``excluded_links`` stay off that
  projection.
- Density is ``|E| / (n * (n - 1))`` for ``n >= 2``, else ``0.0``, where
  ``E`` is the unique directed edge set.
- Reciprocal-edge ratio is ``|{ (u, v) in E : (v, u) in E }| / |E|``, else
  ``0.0``.
- Mean and median in- and out-degree are ``statistics.mean`` /
  ``statistics.median`` over every listed node's unique-edge degrees
  (``unique_inbound_degree`` / ``unique_outbound_degree``). An empty model
  yields ``0.0`` for all four. Even ``n`` uses ``statistics.median``.
- Rankings use verbatim OKF ``pagerank`` and OKF ``inbound_link_count``
  (not unique degree), ordered ``(-value, concept_id)``.
- Diagnostic lists are OKF signals, not defects: ``is_orphan``,
  ``inbound_link_count == 0``, and ``outbound_link_count == 0``.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from okf_core.graph_model import GraphModelEdge, GraphModelNode, NormalizedBundleGraph


class GraphAnalysisError(Exception):
    """Raised when a graph analysis cannot be produced fail-closed."""


@dataclass(frozen=True)
class GraphOverview:
    """Bundle-level counts and unique-edge degree statistics.

    ``link_instance_count`` is ``sum(edge.instance_count)`` over unique
    directed edges (self-links are not edges). ``density`` is
    ``|E| / (n * (n - 1))`` for ``n >= 2`` else ``0.0``. Reciprocal-edge
    ratio is the fraction of directed pairs that have a reverse edge, or
    ``0.0`` when ``E`` is empty. Mean/median in- and out-degree are taken
    over unique-edge degrees of every listed node; all four are ``0.0``
    when ``n == 0``.
    """

    concept_count: int
    link_instance_count: int
    unique_directed_edge_count: int
    density: float
    reciprocal_edge_ratio: float
    mean_in_degree: float
    median_in_degree: float
    mean_out_degree: float
    median_out_degree: float


@dataclass(frozen=True)
class WeaklyConnectedComponents:
    """Weakly connected components on the undirected unique-edge projection.

    Isolates are size-1 components. Components are ordered
    ``(-size, min(member_id))``; the first is the largest and is omitted
    from ``other_memberships``. Each membership tuple is slug-asc. An
    empty model has ``count=0`` and empty tuples.
    """

    count: int
    sizes: tuple[int, ...]
    other_memberships: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ArticulationPoint:
    """A cut vertex on the undirected unique-edge projection.

    ``regions`` are the pieces of this node's weakly connected component
    after its removal, each membership slug-asc, ordered
    ``(-len, min(slug))``. Isolated, size-2, and pure-cycle components
    contribute no articulation points.
    """

    concept_id: str
    regions: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class RankedConcept:
    """One concept in a centrality ranking, ordered ``(-value, concept_id)``."""

    concept_id: str
    value: float


@dataclass(frozen=True)
class GraphDiagnostics:
    """OKF diagnostic signals (not defects), each ordered slug-asc.

    ``orphans`` uses verbatim ``is_orphan``. ``zero_inbound`` /
    ``zero_outbound`` use OKF ``inbound_link_count == 0`` /
    ``outbound_link_count == 0``, which can disagree with unique-edge
    degrees when the only links are self-links.
    """

    orphans: tuple[str, ...]
    zero_inbound: tuple[str, ...]
    zero_outbound: tuple[str, ...]


@dataclass(frozen=True)
class BundleGraphAnalysis:
    """Portable structural snapshot of one ``NormalizedBundleGraph``.

    Rankings are ``top_by_pagerank`` (OKF PageRank) and
    ``top_by_inbound`` (OKF inbound link count). Analysis is not stored
    on the model; :meth:`to_portable_dict` carries no timestamps and no
    absolute paths.
    """

    bundle_name: str
    overview: GraphOverview
    components: WeaklyConnectedComponents
    articulation_points: tuple[ArticulationPoint, ...]
    top_by_pagerank: tuple[RankedConcept, ...]
    top_by_inbound: tuple[RankedConcept, ...]
    diagnostics: GraphDiagnostics

    def to_portable_dict(self) -> dict[str, Any]:
        """Return a JSON-ready snapshot of this analysis.

        Includes only analysis fields: no timestamps and no absolute paths.
        """

        return _to_portable(asdict(self))


def analyze_normalized_graph(
    model: NormalizedBundleGraph,
    *,
    top_n: int = 10,
) -> BundleGraphAnalysis:
    """Compute a deterministic structural snapshot of ``model``.

    The undirected projection used for weakly connected components and
    articulation points collapses each unique directed pair ``{u, v}``
    once; self-links and ``excluded_links`` stay off that projection.
    Density is ``|E| / (n * (n - 1))`` for ``n >= 2`` else ``0.0``.
    Reciprocal-edge ratio is the fraction of directed pairs that have a
    reverse, or ``0.0`` when ``E`` is empty. Mean/median degrees use
    unique-edge degrees of every listed node (``0.0`` when ``n == 0``;
    even ``n`` uses ``statistics.median``). Weakly connected components
    are Union-Find over that projection, isolates included, ordered
    ``(-size, min(member_id))``. Articulation points are linear-time
    Tarjan/Hopcroft; DFS roots are visited slug-asc. Rankings use OKF
    ``pagerank`` and OKF ``inbound_link_count``, take
    ``min(top_n, n)``, and break ties slug-asc. Diagnostic lists are
    OKF signals (orphan / zero inbound / zero outbound), not defects.

    Raises :class:`GraphAnalysisError` if ``model`` is not a
    :class:`NormalizedBundleGraph`.
    """

    typed = _require_normalized_model(model)
    adjacency = _undirected_adjacency(typed)
    top_by_pagerank, top_by_inbound = _centrality_rankings(typed, top_n)
    return BundleGraphAnalysis(
        bundle_name=typed.bundle_name,
        overview=_overview_metrics(typed),
        components=_weakly_connected_components(typed, adjacency),
        articulation_points=_articulation_points(typed, adjacency),
        top_by_pagerank=top_by_pagerank,
        top_by_inbound=top_by_inbound,
        diagnostics=_diagnostic_lists(typed),
    )


def _require_normalized_model(model: object) -> NormalizedBundleGraph:
    if not isinstance(model, NormalizedBundleGraph):
        raise GraphAnalysisError("model must be a NormalizedBundleGraph")
    return model


def _overview_metrics(model: NormalizedBundleGraph) -> GraphOverview:
    nodes = model.nodes
    edges = model.edges
    n = len(nodes)
    unique_e = len(edges)
    mean_in, median_in = _degree_moments([node.unique_inbound_degree for node in nodes])
    mean_out, median_out = _degree_moments(
        [node.unique_outbound_degree for node in nodes]
    )
    return GraphOverview(
        concept_count=n,
        link_instance_count=sum(edge.instance_count for edge in edges),
        unique_directed_edge_count=unique_e,
        density=_directed_density(n, unique_e),
        reciprocal_edge_ratio=_reciprocal_edge_ratio(edges),
        mean_in_degree=mean_in,
        median_in_degree=median_in,
        mean_out_degree=mean_out,
        median_out_degree=median_out,
    )


def _directed_density(n: int, unique_e: int) -> float:
    if n < 2:
        return 0.0
    return unique_e / (n * (n - 1))


def _reciprocal_edge_ratio(edges: tuple[GraphModelEdge, ...]) -> float:
    if not edges:
        return 0.0
    directed = {(edge.source_id, edge.target_id) for edge in edges}
    reciprocal = sum(1 for source, target in directed if (target, source) in directed)
    return reciprocal / len(directed)


def _degree_moments(values: list[int]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    return float(statistics.mean(values)), float(statistics.median(values))


def _weakly_connected_components(
    model: NormalizedBundleGraph,
    adjacency: Mapping[str, tuple[str, ...]],
) -> WeaklyConnectedComponents:
    groups = _component_groups(
        tuple(node.concept_id for node in model.nodes), adjacency
    )
    if not groups:
        return WeaklyConnectedComponents(count=0, sizes=(), other_memberships=())
    return WeaklyConnectedComponents(
        count=len(groups),
        sizes=tuple(len(group) for group in groups),
        other_memberships=groups[1:],
    )


def _articulation_points(
    model: NormalizedBundleGraph,
    adjacency: Mapping[str, tuple[str, ...]],
) -> tuple[ArticulationPoint, ...]:
    groups = _component_groups(
        tuple(node.concept_id for node in model.nodes), adjacency
    )
    wcc_of = {member: group for group in groups for member in group}
    points: list[ArticulationPoint] = []
    for concept_id in _articulation_ids(adjacency):
        point, _problem = _articulation_point_for(
            concept_id, wcc_of[concept_id], adjacency
        )
        points.append(point)
    return tuple(points)


def _articulation_point_for(
    concept_id: str,
    wcc: tuple[str, ...],
    adjacency: Mapping[str, tuple[str, ...]],
) -> tuple[ArticulationPoint, None]:
    return (
        ArticulationPoint(
            concept_id=concept_id,
            regions=_regions_after_removal(concept_id, wcc, adjacency),
        ),
        None,
    )


def _centrality_rankings(
    model: NormalizedBundleGraph,
    top_n: int,
) -> tuple[tuple[RankedConcept, ...], tuple[RankedConcept, ...]]:
    take = min(top_n, len(model.nodes))
    return (
        _rank_concepts(model.nodes, lambda node: node.pagerank, take),
        _rank_concepts(model.nodes, lambda node: float(node.inbound_link_count), take),
    )


def _rank_concepts(
    nodes: tuple[GraphModelNode, ...],
    value_of: Callable[[GraphModelNode], float],
    take: int,
) -> tuple[RankedConcept, ...]:
    ordered = sorted(nodes, key=lambda node: (-value_of(node), node.concept_id))
    return tuple(
        RankedConcept(concept_id=node.concept_id, value=value_of(node))
        for node in ordered[:take]
    )


def _diagnostic_lists(model: NormalizedBundleGraph) -> GraphDiagnostics:
    orphans: list[str] = []
    zero_inbound: list[str] = []
    zero_outbound: list[str] = []
    for node in model.nodes:
        orphan, inbound, outbound = _diagnostic_flags(node)
        if orphan:
            orphans.append(node.concept_id)
        if inbound:
            zero_inbound.append(node.concept_id)
        if outbound:
            zero_outbound.append(node.concept_id)
    orphans.sort()
    zero_inbound.sort()
    zero_outbound.sort()
    return GraphDiagnostics(
        orphans=tuple(orphans),
        zero_inbound=tuple(zero_inbound),
        zero_outbound=tuple(zero_outbound),
    )


def _diagnostic_flags(node: GraphModelNode) -> tuple[bool, bool, bool]:
    return (
        node.is_orphan,
        node.inbound_link_count == 0,
        node.outbound_link_count == 0,
    )


def _undirected_adjacency(
    model: NormalizedBundleGraph,
) -> dict[str, tuple[str, ...]]:
    neighbors: dict[str, set[str]] = {node.concept_id: set() for node in model.nodes}
    known = frozenset(neighbors)
    for edge in model.edges:
        pair, _problem = _undirected_pair(edge, known)
        if pair is None:
            continue
        left, right = pair
        neighbors[left].add(right)
        neighbors[right].add(left)
    return {node_id: tuple(sorted(adj)) for node_id, adj in neighbors.items()}


def _undirected_pair(
    edge: GraphModelEdge,
    known: frozenset[str],
) -> tuple[tuple[str, str] | None, None]:
    source, target = edge.source_id, edge.target_id
    if source == target or source not in known or target not in known:
        return None, None
    return (source, target), None


def _component_groups(
    node_ids: tuple[str, ...],
    adjacency: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    if not node_ids:
        return ()
    forest = _UnionFind(node_ids)
    for node_id, neighbors in adjacency.items():
        for neighbor in neighbors:
            if node_id < neighbor:
                forest.union(node_id, neighbor)
    buckets: dict[str, list[str]] = {}
    for node_id in node_ids:
        buckets.setdefault(forest.find(node_id), []).append(node_id)
    groups = [tuple(sorted(members)) for members in buckets.values()]
    groups.sort(key=lambda members: (-len(members), members[0]))
    return tuple(groups)


class _UnionFind:
    """Disjoint-set forest for the undirected unique-edge projection."""

    def __init__(self, node_ids: Sequence[str]) -> None:
        self._parent = {node_id: node_id for node_id in node_ids}
        self._rank = dict.fromkeys(node_ids, 0)

    def find(self, node_id: str) -> str:
        while self._parent[node_id] != node_id:
            self._parent[node_id] = self._parent[self._parent[node_id]]
            node_id = self._parent[node_id]
        return node_id

    def union(self, left: str, right: str) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left == root_right:
            return
        if self._rank[root_left] < self._rank[root_right]:
            self._parent[root_left] = root_right
            return
        if self._rank[root_left] > self._rank[root_right]:
            self._parent[root_right] = root_left
            return
        self._parent[root_right] = root_left
        self._rank[root_left] += 1


def _articulation_ids(adjacency: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    state = _TarjanState(adjacency)
    for root in sorted(adjacency):
        if root not in state.disc:
            _tarjan_visit(state, root, parent=None)
    return tuple(sorted(state.articulations))


class _TarjanState:
    """Mutable discovery state for one Tarjan/Hopcroft walk."""

    def __init__(self, adjacency: Mapping[str, tuple[str, ...]]) -> None:
        self.adjacency = adjacency
        self.disc: dict[str, int] = {}
        self.low: dict[str, int] = {}
        self.articulations: set[str] = set()
        self.clock = 0


def _tarjan_visit(state: _TarjanState, node: str, parent: str | None) -> None:
    state.clock += 1
    state.disc[node] = state.low[node] = state.clock
    children = 0
    for neighbor in state.adjacency[node]:
        if neighbor not in state.disc:
            children += 1
            _tarjan_visit(state, neighbor, node)
            state.low[node] = min(state.low[node], state.low[neighbor])
            if parent is not None and state.low[neighbor] >= state.disc[node]:
                state.articulations.add(node)
            continue
        if neighbor != parent:
            state.low[node] = min(state.low[node], state.disc[neighbor])
    if parent is None and children >= 2:
        state.articulations.add(node)


def _regions_after_removal(
    removed: str,
    wcc: tuple[str, ...],
    adjacency: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    remaining = [member for member in wcc if member != removed]
    seen: set[str] = set()
    wcc_set = set(wcc)
    regions: list[tuple[str, ...]] = []
    for start in remaining:
        region, _problem = _collect_region(start, removed, wcc_set, adjacency, seen)
        if region is not None:
            regions.append(region)
    regions.sort(key=lambda region: (-len(region), region[0]))
    return tuple(regions)


def _collect_region(
    start: str,
    removed: str,
    wcc_set: set[str],
    adjacency: Mapping[str, tuple[str, ...]],
    seen: set[str],
) -> tuple[tuple[str, ...] | None, None]:
    if start in seen:
        return None, None
    piece: list[str] = []
    stack = [start]
    seen.add(start)
    while stack:
        node = stack.pop()
        piece.append(node)
        for neighbor in adjacency[node]:
            if neighbor == removed or neighbor not in wcc_set or neighbor in seen:
                continue
            seen.add(neighbor)
            stack.append(neighbor)
    return tuple(sorted(piece)), None


def _to_portable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _to_portable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_portable(item) for item in value]
    return value

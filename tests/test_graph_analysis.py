"""Tests for deterministic structural analysis of a normalized graph."""

from __future__ import annotations

import json

import pytest

from okf_core import (
    ArticulationPoint,
    BundleGraphAnalysis,
    GraphAnalysisError,
    GraphModelEdge,
    GraphModelExcludedLink,
    GraphModelNode,
    GraphModelSelfLink,
    NormalizedBundleGraph,
    RankedConcept,
    WeaklyConnectedComponents,
    analyze_normalized_graph,
)


def _model(
    *nodes: GraphModelNode,
    edges: tuple[GraphModelEdge, ...] = (),
    name: str = "docs",
    self_links: tuple[GraphModelSelfLink, ...] = (),
    excluded_links: tuple[GraphModelExcludedLink, ...] = (),
) -> NormalizedBundleGraph:
    return NormalizedBundleGraph(
        bundle_name=name,
        nodes=tuple(sorted(nodes, key=lambda node: node.concept_id)),
        edges=tuple(sorted(edges, key=lambda edge: (edge.source_id, edge.target_id))),
        self_links=self_links,
        excluded_links=excluded_links,
    )


def _node(
    concept_id: str,
    *,
    inbound: int = 0,
    outbound: int = 0,
    pagerank: float = 0.0,
    is_orphan: bool | None = None,
    unique_in: int | None = None,
    unique_out: int | None = None,
) -> GraphModelNode:
    orphan = inbound == 0 and outbound == 0 if is_orphan is None else is_orphan
    return GraphModelNode(
        concept_id=concept_id,
        path=f"{concept_id}.md",
        type="note",
        title=concept_id,
        description=None,
        inbound_link_count=inbound,
        outbound_link_count=outbound,
        pagerank=pagerank,
        is_orphan=orphan,
        unique_inbound_degree=inbound if unique_in is None else unique_in,
        unique_outbound_degree=outbound if unique_out is None else unique_out,
    )


def _edge(source: str, target: str, count: int = 1) -> GraphModelEdge:
    return GraphModelEdge(
        source_id=source,
        target_id=target,
        instance_count=count,
        texts=tuple(f"t{i}" for i in range(count)),
    )


def _path_abc() -> NormalizedBundleGraph:
    return _model(
        _node("a", outbound=1, unique_out=1, pagerank=0.1),
        _node("b", inbound=1, outbound=1, unique_in=1, unique_out=1, pagerank=0.8),
        _node("c", inbound=1, unique_in=1, pagerank=0.1),
        edges=(_edge("a", "b"), _edge("b", "c")),
    )


def _two_cliques_sharing_m() -> NormalizedBundleGraph:
    return _model(
        _node("a", inbound=2, outbound=2, unique_in=2, unique_out=2, pagerank=0.1),
        _node("b", inbound=2, outbound=2, unique_in=2, unique_out=2, pagerank=0.1),
        _node("m", inbound=4, outbound=4, unique_in=4, unique_out=4, pagerank=0.5),
        _node("x", inbound=2, outbound=2, unique_in=2, unique_out=2, pagerank=0.15),
        _node("y", inbound=2, outbound=2, unique_in=2, unique_out=2, pagerank=0.15),
        edges=(
            _edge("a", "b"),
            _edge("b", "a"),
            _edge("a", "m"),
            _edge("m", "a"),
            _edge("b", "m"),
            _edge("m", "b"),
            _edge("m", "x"),
            _edge("x", "m"),
            _edge("m", "y"),
            _edge("y", "m"),
            _edge("x", "y"),
            _edge("y", "x"),
        ),
    )


def test_empty_bundle_overview_is_zeroed() -> None:
    analysis = analyze_normalized_graph(_model())

    overview = analysis.overview
    assert analysis.bundle_name == "docs"
    assert overview.concept_count == 0
    assert overview.link_instance_count == 0
    assert overview.unique_directed_edge_count == 0
    assert overview.density == 0.0
    assert overview.reciprocal_edge_ratio == 0.0
    assert overview.mean_in_degree == 0.0
    assert overview.median_in_degree == 0.0
    assert overview.mean_out_degree == 0.0
    assert overview.median_out_degree == 0.0


def test_empty_bundle_has_no_components_rankings_or_diagnostics() -> None:
    analysis = analyze_normalized_graph(_model())

    assert analysis.components == WeaklyConnectedComponents(
        count=0, sizes=(), other_memberships=()
    )
    assert analysis.articulation_points == ()
    assert analysis.top_by_pagerank == ()
    assert analysis.top_by_inbound == ()
    assert analysis.diagnostics.orphans == ()
    assert analysis.diagnostics.zero_inbound == ()
    assert analysis.diagnostics.zero_outbound == ()


def test_single_isolate_density_and_degrees_are_zero() -> None:
    analysis = analyze_normalized_graph(_model(_node("solo")))

    overview = analysis.overview
    assert overview.concept_count == 1
    assert overview.density == 0.0
    assert overview.reciprocal_edge_ratio == 0.0
    assert overview.mean_in_degree == 0.0
    assert overview.median_in_degree == 0.0
    assert overview.mean_out_degree == 0.0
    assert overview.median_out_degree == 0.0


def test_single_isolate_is_largest_component_without_other_memberships() -> None:
    analysis = analyze_normalized_graph(_model(_node("solo")))

    assert analysis.components.count == 1
    assert analysis.components.sizes == (1,)
    assert analysis.components.other_memberships == ()
    assert analysis.articulation_points == ()
    assert analysis.diagnostics.orphans == ("solo",)
    assert analysis.diagnostics.zero_inbound == ("solo",)
    assert analysis.diagnostics.zero_outbound == ("solo",)


def test_two_isolates_other_membership_is_the_higher_slug() -> None:
    analysis = analyze_normalized_graph(_model(_node("a"), _node("z")))

    assert analysis.components.count == 2
    assert analysis.components.sizes == (1, 1)
    assert analysis.components.other_memberships == (("z",),)
    assert analysis.articulation_points == ()


@pytest.mark.parametrize(
    ("edges", "expected_density", "expected_ratio"),
    [
        ((_edge("a", "b"), _edge("b", "a")), 1.0, 1.0),
        ((_edge("a", "b"),), 0.5, 0.0),
    ],
    ids=["reciprocal", "one-way"],
)
def test_two_node_density_and_reciprocal_ratio(
    edges: tuple[GraphModelEdge, ...],
    expected_density: float,
    expected_ratio: float,
) -> None:
    unique_out = {"a": 0, "b": 0}
    unique_in = {"a": 0, "b": 0}
    for edge in edges:
        unique_out[edge.source_id] += 1
        unique_in[edge.target_id] += 1
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=unique_out["a"], unique_out=unique_out["a"]),
            _node("b", inbound=unique_in["b"], unique_in=unique_in["b"]),
            edges=edges,
        )
    )

    assert analysis.overview.unique_directed_edge_count == len(edges)
    assert analysis.overview.density == expected_density
    assert analysis.overview.reciprocal_edge_ratio == expected_ratio


def test_one_way_pair_even_n_median_is_midpoint() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=1, unique_out=1),
            _node("b", inbound=1, unique_in=1),
            edges=(_edge("a", "b"),),
        )
    )

    assert analysis.overview.mean_in_degree == 0.5
    assert analysis.overview.median_in_degree == 0.5
    assert analysis.overview.mean_out_degree == 0.5
    assert analysis.overview.median_out_degree == 0.5


def test_repeated_instances_sum_into_link_instance_count_not_unique_edges() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=3, unique_out=1),
            _node("b", inbound=3, unique_in=1),
            edges=(_edge("a", "b", count=3),),
        )
    )

    assert analysis.overview.link_instance_count == 3
    assert analysis.overview.unique_directed_edge_count == 1


def test_multi_component_omits_largest_membership() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=1, unique_out=1),
            _node("b", inbound=1, outbound=1, unique_in=1, unique_out=1),
            _node("c", inbound=1, unique_in=1),
            _node("d", outbound=1, unique_out=1),
            _node("e", inbound=1, unique_in=1),
            _node("f"),
            edges=(_edge("a", "b"), _edge("b", "c"), _edge("d", "e")),
        )
    )

    assert analysis.components.count == 3
    assert analysis.components.sizes == (3, 2, 1)
    assert analysis.components.other_memberships == (("d", "e"), ("f",))


def test_largest_component_tie_breaks_by_min_member_id() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=1, unique_out=1),
            _node("z", inbound=1, unique_in=1),
            _node("m", outbound=1, unique_out=1),
            _node("n", inbound=1, unique_in=1),
            edges=(_edge("a", "z"), _edge("m", "n")),
        )
    )

    assert analysis.components.sizes == (2, 2)
    assert analysis.components.other_memberships == (("m", "n"),)


def test_bridge_path_middle_node_is_articulation_point() -> None:
    analysis = analyze_normalized_graph(_path_abc())

    assert analysis.articulation_points == (
        ArticulationPoint(concept_id="b", regions=(("a",), ("c",))),
    )


def test_triangle_has_no_articulation_points() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", inbound=1, outbound=1, unique_in=1, unique_out=1),
            _node("b", inbound=1, outbound=1, unique_in=1, unique_out=1),
            _node("c", inbound=1, outbound=1, unique_in=1, unique_out=1),
            edges=(_edge("a", "b"), _edge("b", "c"), _edge("c", "a")),
        )
    )

    assert analysis.articulation_points == ()


def test_two_cliques_sharing_one_vertex_articulates_the_shared_node() -> None:
    analysis = analyze_normalized_graph(_two_cliques_sharing_m())

    assert analysis.articulation_points == (
        ArticulationPoint(concept_id="m", regions=(("a", "b"), ("x", "y"))),
    )


def test_star_center_is_root_articulation_point() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=2, unique_out=2),
            _node("b", inbound=1, unique_in=1),
            _node("c", inbound=1, unique_in=1),
            edges=(_edge("a", "b"), _edge("a", "c")),
        )
    )

    assert analysis.articulation_points == (
        ArticulationPoint(concept_id="a", regions=(("b",), ("c",))),
    )


def test_shared_higher_slug_hub_is_one_component_and_an_articulation_point() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=1, unique_out=1),
            _node("m", outbound=1, unique_out=1),
            _node("z", inbound=2, unique_in=2),
            edges=(_edge("a", "z"), _edge("m", "z")),
        )
    )

    assert analysis.components.count == 1
    assert analysis.components.sizes == (3,)
    assert analysis.articulation_points == (
        ArticulationPoint(concept_id="z", regions=(("a",), ("m",))),
    )


def test_size_two_component_has_no_articulation_point() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=1, unique_out=1),
            _node("b", inbound=1, unique_in=1),
            edges=(_edge("a", "b"),),
        )
    )

    assert analysis.articulation_points == ()


@pytest.mark.parametrize(
    "payload",
    [None, {}, [], "bundle", object()],
    ids=["none", "mapping", "list", "string", "object"],
)
def test_wrong_type_raises_graph_analysis_error(payload: object) -> None:
    with pytest.raises(GraphAnalysisError, match="NormalizedBundleGraph"):
        analyze_normalized_graph(payload)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "top_n",
    [-1, True, False, 1.5, "10", None],
    ids=["negative", "true", "false", "float", "string", "none"],
)
def test_invalid_top_n_raises_graph_analysis_error(top_n: object) -> None:
    with pytest.raises(GraphAnalysisError, match="top_n"):
        analyze_normalized_graph(_model(_node("a")), top_n=top_n)  # type: ignore[arg-type]


def test_equal_pagerank_tie_breaks_slug_asc() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("b", pagerank=0.4),
            _node("a", pagerank=0.4),
            _node("c", pagerank=0.2),
        )
    )

    assert analysis.top_by_pagerank == (
        RankedConcept(concept_id="a", value=0.4),
        RankedConcept(concept_id="b", value=0.4),
        RankedConcept(concept_id="c", value=0.2),
    )


def test_equal_inbound_tie_breaks_slug_asc() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("b", inbound=3, unique_in=1),
            _node("a", inbound=3, unique_in=0),
            _node("c", inbound=1, unique_in=2),
        )
    )

    assert analysis.top_by_inbound == (
        RankedConcept(concept_id="a", value=3.0),
        RankedConcept(concept_id="b", value=3.0),
        RankedConcept(concept_id="c", value=1.0),
    )


def test_inbound_ranking_uses_okf_count_not_unique_degree() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("heavy", inbound=5, unique_in=1, pagerank=0.1),
            _node("unique", inbound=2, unique_in=4, pagerank=0.9),
        )
    )

    assert [row.concept_id for row in analysis.top_by_inbound] == ["heavy", "unique"]
    assert [row.concept_id for row in analysis.top_by_pagerank] == ["unique", "heavy"]


@pytest.mark.parametrize("top_n", [1, 2])
def test_top_n_cut_is_stable_for_tied_pagerank(top_n: int) -> None:
    model = _model(
        _node("b", pagerank=0.5),
        _node("a", pagerank=0.5),
        _node("c", pagerank=0.5),
    )

    analysis = analyze_normalized_graph(model, top_n=top_n)

    assert [row.concept_id for row in analysis.top_by_pagerank] == ["a", "b", "c"][
        :top_n
    ]


def test_top_n_cannot_exceed_node_count() -> None:
    analysis = analyze_normalized_graph(_model(_node("a"), _node("b")), top_n=10)

    assert len(analysis.top_by_pagerank) == 2
    assert len(analysis.top_by_inbound) == 2


def test_self_link_only_node_is_not_orphan_or_zero_degree() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node(
                "loop",
                inbound=2,
                outbound=2,
                unique_in=0,
                unique_out=0,
                is_orphan=False,
                pagerank=1.0,
            ),
            self_links=(
                GraphModelSelfLink(
                    concept_id="loop", instance_count=2, texts=("a", "b")
                ),
            ),
        )
    )

    assert analysis.overview.unique_directed_edge_count == 0
    assert analysis.overview.mean_in_degree == 0.0
    assert analysis.overview.mean_out_degree == 0.0
    assert analysis.diagnostics.orphans == ()
    assert analysis.diagnostics.zero_inbound == ()
    assert analysis.diagnostics.zero_outbound == ()
    assert analysis.components.sizes == (1,)
    assert analysis.articulation_points == ()


def test_self_links_and_excluded_links_stay_off_undirected_projection() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=1, unique_out=0, is_orphan=False),
            _node("z"),
            self_links=(
                GraphModelSelfLink(concept_id="a", instance_count=1, texts=("self",)),
            ),
            excluded_links=(
                GraphModelExcludedLink(
                    source_id="a",
                    target_id="ghost",
                    text="x",
                    reason="unlisted-endpoint",
                    source_path="a.md",
                    target_path="ghost.md",
                ),
            ),
        )
    )

    assert analysis.components.count == 2
    assert analysis.components.sizes == (1, 1)
    assert analysis.components.other_memberships == (("z",),)
    assert analysis.articulation_points == ()


def test_self_loop_on_edges_does_not_join_the_undirected_projection() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("solo", outbound=1, inbound=1, unique_in=0, unique_out=0),
            edges=(_edge("solo", "solo"),),
        )
    )

    assert analysis.overview.unique_directed_edge_count == 1
    assert analysis.components.sizes == (1,)
    assert analysis.articulation_points == ()


def test_unlisted_edge_endpoint_stays_off_the_undirected_projection() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("a", outbound=1, unique_out=0),
            _node("b"),
            edges=(_edge("a", "ghost"),),
        )
    )

    assert analysis.components.count == 2
    assert analysis.components.sizes == (1, 1)


def test_diagnostics_follow_okf_orphan_and_zero_counts() -> None:
    analysis = analyze_normalized_graph(
        _model(
            _node("orphan"),
            _node("sink", inbound=2, unique_in=1),
            _node("source", outbound=2, unique_out=1),
            _node("both", inbound=1, outbound=1, unique_in=1, unique_out=1),
            edges=(_edge("source", "sink"), _edge("both", "both")),
        )
    )

    assert analysis.diagnostics.orphans == ("orphan",)
    assert analysis.diagnostics.zero_inbound == ("orphan", "source")
    assert analysis.diagnostics.zero_outbound == ("orphan", "sink")


def test_repeated_analysis_portable_dict_is_byte_identical() -> None:
    model = _two_cliques_sharing_m()
    first = analyze_normalized_graph(model, top_n=3)
    second = analyze_normalized_graph(model, top_n=3)

    dumped = json.dumps(first.to_portable_dict(), sort_keys=True, separators=(",", ":"))
    assert dumped == json.dumps(
        second.to_portable_dict(), sort_keys=True, separators=(",", ":")
    )
    assert isinstance(first, BundleGraphAnalysis)
    assert "timestamp" not in dumped
    assert "/tmp/" not in dumped
    assert first.to_portable_dict()["bundle_name"] == "docs"

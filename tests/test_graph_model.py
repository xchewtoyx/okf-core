"""Tests for in-process bundle graph normalization."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from okf_core import (
    BundleConfig,
    BundleGraph,
    BundleListing,
    ConceptLink,
    ConceptListing,
    ConceptManifestEntry,
    GraphModelError,
    GraphProblem,
    ListingProblem,
    NormalizedBundleGraph,
    acquire_normalized_graph,
    normalize_bundle_graph,
    scan_bundle,
)
from okf_core.graph_model import GraphModelExcludedLink


def test_healthy_connected_graph_matches_verbatim_listing_and_graph(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    graph, listing = _connected_fixtures(root)

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    by_id = {node.concept_id: node for node in model.nodes}
    assert set(by_id) == {"a", "b", "c", "d"}
    assert by_id["a"].outbound_link_count == 1
    assert by_id["a"].inbound_link_count == 0
    assert by_id["b"].outbound_link_count == 1
    assert by_id["b"].inbound_link_count == 1
    assert by_id["c"].outbound_link_count == 0
    assert by_id["c"].inbound_link_count == 1
    assert by_id["d"].outbound_link_count == 0
    assert by_id["d"].inbound_link_count == 0
    assert by_id["d"].is_orphan is True
    assert {node.concept_id for node in model.nodes if node.is_orphan} == {"d"}
    assert by_id["a"].pagerank == 0.1
    assert by_id["b"].pagerank == 0.4
    assert {(edge.source_id, edge.target_id) for edge in model.edges} == {
        ("a", "b"),
        ("b", "c"),
    }
    assert all(edge.instance_count == 1 for edge in model.edges)
    assert model.broken_links[0].target == "missing.md"
    assert model.self_links == ()
    assert model.excluded_links == ()
    assert model.bundle_name == "docs"


def test_healthy_unique_edge_degrees_match_distinct_non_self_pairs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    graph, listing = _connected_fixtures(root)

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    by_id = {node.concept_id: node for node in model.nodes}
    assert by_id["a"].unique_outbound_degree == 1
    assert by_id["a"].unique_inbound_degree == 0
    assert by_id["b"].unique_outbound_degree == 1
    assert by_id["b"].unique_inbound_degree == 1
    assert by_id["c"].unique_outbound_degree == 0
    assert by_id["c"].unique_inbound_degree == 1
    assert by_id["d"].unique_outbound_degree == 0
    assert by_id["d"].unique_inbound_degree == 0


def test_repeated_directed_instances_collapse_to_one_edge(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    texts = ("first", "second", "third")
    links = tuple(_link(root, source="a", target="b", text=text) for text in texts)
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"), _entry(root, "b")),
        links=links,
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(
            _listed(root, "a", outbound=3, inbound=0, pagerank=0.5),
            _listed(root, "b", outbound=0, inbound=3, pagerank=0.5),
        ),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert len(model.edges) == 1
    assert model.edges[0].source_id == "a"
    assert model.edges[0].target_id == "b"
    assert model.edges[0].instance_count == 3
    assert model.edges[0].texts == texts
    by_id = {node.concept_id: node for node in model.nodes}
    assert by_id["a"].outbound_link_count == 3
    assert by_id["a"].unique_outbound_degree == 1


def test_self_links_are_tracked_and_excluded_from_unique_edges(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"), _entry(root, "b")),
        links=(
            _link(root, source="a", target="a", text="self"),
            _link(root, source="a", target="a", text="again"),
            _link(root, source="a", target="b", text="out"),
        ),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(
            _listed(root, "a", outbound=3, inbound=2, pagerank=0.6),
            _listed(root, "b", outbound=0, inbound=1, pagerank=0.4),
        ),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert [(edge.source_id, edge.target_id) for edge in model.edges] == [("a", "b")]
    assert len(model.self_links) == 1
    assert model.self_links[0].concept_id == "a"
    assert model.self_links[0].instance_count == 2
    assert model.self_links[0].texts == ("self", "again")
    by_id = {node.concept_id: node for node in model.nodes}
    assert by_id["a"].outbound_link_count == 3
    assert by_id["a"].inbound_link_count == 2
    assert by_id["a"].unique_outbound_degree == 1
    assert by_id["a"].unique_inbound_degree == 0


def test_broken_links_are_copied_and_never_become_edges(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    broken = _link(
        root,
        source="a",
        target="gone",
        text="missing",
        target_exists=False,
        target_concept_id=None,
    )
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"),),
        broken_links=(broken,),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(_listed(root, "a", outbound=0, inbound=0, pagerank=1.0),),
        orphans=("a",),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert model.edges == ()
    assert len(model.broken_links) == 1
    assert model.broken_links[0].source_id == "a"
    assert model.broken_links[0].target == "gone.md"
    assert model.broken_links[0].text == "missing"
    assert model.broken_links[0].source_path == "a.md"
    assert model.broken_links[0].target_path == "gone.md"


def test_empty_bundle_yields_empty_model(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(bundle_name="docs")
    listing = BundleListing(bundle_name="docs")

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert model == NormalizedBundleGraph(bundle_name="docs")
    assert model.to_portable_dict() == {
        "bundle_name": "docs",
        "nodes": [],
        "edges": [],
        "self_links": [],
        "broken_links": [],
        "excluded_links": [],
        "problems": [],
    }


def test_listing_id_missing_from_graph_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(bundle_name="docs", concepts=(_entry(root, "a"),))
    listing = BundleListing(
        bundle_name="docs",
        concepts=(
            _listed(root, "a", outbound=0, inbound=0, pagerank=1.0),
            _listed(root, "ghost", outbound=0, inbound=0, pagerank=0.0),
        ),
        orphans=("a", "ghost"),
    )

    with pytest.raises(GraphModelError, match="absent from graph.concepts"):
        normalize_bundle_graph(graph, listing, bundle_root=root)


@pytest.mark.parametrize(
    ("outbound", "inbound"),
    [(5, 0), (0, 2), (1, 1)],
)
def test_listing_count_mismatch_fails_closed(
    tmp_path: Path,
    outbound: int,
    inbound: int,
) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"), _entry(root, "b")),
        links=(_link(root, source="a", target="b", text="one"),),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(
            _listed(root, "a", outbound=outbound, inbound=inbound, pagerank=0.5),
            _listed(root, "b", outbound=0, inbound=1, pagerank=0.5),
        ),
    )

    with pytest.raises(GraphModelError, match="disagree with resolved graph.links"):
        normalize_bundle_graph(graph, listing, bundle_root=root)


@pytest.mark.parametrize(
    "field",
    ["inbound_link_count", "outbound_link_count", "pagerank"],
)
def test_none_graph_count_field_fails_closed(tmp_path: Path, field: str) -> None:
    root = tmp_path / "docs"
    kwargs = {"outbound": 0, "inbound": 0, "pagerank": 1.0}
    if field == "inbound_link_count":
        kwargs["inbound"] = None
    elif field == "outbound_link_count":
        kwargs["outbound"] = None
    else:
        kwargs["pagerank"] = None
    graph = BundleGraph(bundle_name="docs", concepts=(_entry(root, "a"),))
    listing = BundleListing(
        bundle_name="docs",
        concepts=(_listed(root, "a", **kwargs),),
        orphans=("a",) if field != "inbound_link_count" else (),
    )

    with pytest.raises(GraphModelError, match="missing required graph counts"):
        normalize_bundle_graph(graph, listing, bundle_root=root)


def test_absolute_paths_become_bundle_relative_posix(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "topics/a"), _entry(root, "topics/b")),
        links=(_link(root, source="topics/a", target="topics/b", text="rel"),),
        problems=(
            GraphProblem(
                concept_id="topics/a",
                path=root / "topics" / "a.md",
                kind="stable-id-missing",
                message="no id",
            ),
        ),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(
            _listed(root, "topics/a", outbound=1, inbound=0, pagerank=0.5),
            _listed(root, "topics/b", outbound=0, inbound=1, pagerank=0.5),
        ),
        problems=(
            ListingProblem(
                path=root / "topics" / "a.md",
                kind="stable-id-missing",
                message="no id",
                concept_id="topics/a",
            ),
        ),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert {node.path for node in model.nodes} == {"topics/a.md", "topics/b.md"}
    assert all(not Path(node.path).is_absolute() for node in model.nodes)
    assert model.problems[0].path == "topics/a.md"
    portable = model.to_portable_dict()
    assert portable["nodes"][0]["path"] == "topics/a.md"
    assert not any(
        isinstance(value, str) and value.startswith("/")
        for value in _walk_strings(portable)
    )


def test_escaped_root_path_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    outside = tmp_path / "outside.md"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(
            ConceptManifestEntry(
                concept_id="a",
                path=outside,
                bundle_root=root,
                mtime_ns=0,
                size=0,
                sha256="",
                frontmatter=MappingProxyType({"type": "concept"}),
            ),
        ),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(
            ConceptListing(
                concept_id="a",
                path=outside,
                type="concept",
                outbound_link_count=0,
                inbound_link_count=0,
                pagerank=1.0,
            ),
        ),
        orphans=("a",),
    )

    with pytest.raises(GraphModelError, match="escapes bundle root"):
        normalize_bundle_graph(graph, listing, bundle_root=root)


def test_missing_type_graph_only_id_does_not_fail_and_excludes_its_links(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"), _entry(root, "untyped")),
        links=(
            _link(root, source="a", target="untyped", text="to-untyped"),
            _link(root, source="untyped", target="a", text="from-untyped"),
        ),
        problems=(
            GraphProblem(
                concept_id="untyped",
                path=root / "untyped.md",
                kind="read-error",
                message="ignored for construction",
            ),
        ),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(_listed(root, "a", outbound=1, inbound=1, pagerank=1.0),),
        problems=(
            ListingProblem(
                path=root / "untyped.md",
                kind="missing-type",
                message="'type' frontmatter must be a non-empty string, got None",
                concept_id="untyped",
            ),
        ),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert [node.concept_id for node in model.nodes] == ["a"]
    assert model.edges == ()
    assert {link.reason for link in model.excluded_links} == {"unlisted-endpoint"}
    assert {
        (link.source_id, link.target_id, link.text) for link in model.excluded_links
    } == {("a", "untyped", "to-untyped"), ("untyped", "a", "from-untyped")}
    assert any(problem.kind == "missing-type" for problem in model.problems)
    assert any(problem.kind == "read-error" for problem in model.problems)
    by_id = {node.concept_id: node for node in model.nodes}
    assert by_id["a"].outbound_link_count == 1
    assert by_id["a"].inbound_link_count == 1
    assert by_id["a"].unique_outbound_degree == 0
    assert by_id["a"].unique_inbound_degree == 0


def test_missing_type_id_is_not_invented_as_a_report_node(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"), _entry(root, "untyped")),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(_listed(root, "a", outbound=0, inbound=0, pagerank=1.0),),
        orphans=("a",),
        problems=(
            ListingProblem(
                path=root / "untyped.md",
                kind="missing-type",
                message="missing type",
                concept_id="untyped",
            ),
        ),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert [node.concept_id for node in model.nodes] == ["a"]
    assert "untyped" not in {node.concept_id for node in model.nodes}


@pytest.mark.parametrize(
    ("graph", "listing"),
    [
        ({}, None),
        ({"concepts": []}, {"concepts": []}),
        ({"bundle_name": "docs"}, {"bundle_name": "docs"}),
    ],
)
def test_mapping_payloads_fail_closed(graph: object, listing: object) -> None:
    with pytest.raises(GraphModelError, match="mapping payloads"):
        normalize_bundle_graph(graph, listing, bundle_root=Path("docs"))  # type: ignore[arg-type]


def test_wrong_bundle_root_type_fails_closed() -> None:
    graph = BundleGraph(bundle_name="docs")
    listing = BundleListing(bundle_name="docs")

    with pytest.raises(GraphModelError, match="bundle_root must be a pathlib.Path"):
        normalize_bundle_graph(graph, listing, bundle_root="docs")  # type: ignore[arg-type]


def test_bundle_name_mismatch_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(bundle_name="docs")
    listing = BundleListing(bundle_name="other")

    with pytest.raises(GraphModelError, match="does not match"):
        normalize_bundle_graph(graph, listing, bundle_root=root)


def test_orphan_set_mismatch_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(bundle_name="docs", concepts=(_entry(root, "a"),))
    listing = BundleListing(
        bundle_name="docs",
        concepts=(_listed(root, "a", outbound=0, inbound=0, pagerank=1.0),),
        orphans=(),
    )

    with pytest.raises(GraphModelError, match="listing.orphans disagrees"):
        normalize_bundle_graph(graph, listing, bundle_root=root)


def test_orphan_with_extra_id_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"), _entry(root, "b")),
        links=(_link(root, source="a", target="b", text="edge"),),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(
            _listed(root, "a", outbound=1, inbound=0, pagerank=0.5),
            _listed(root, "b", outbound=0, inbound=1, pagerank=0.5),
        ),
        orphans=("b",),
    )

    with pytest.raises(GraphModelError, match="listing.orphans disagrees"):
        normalize_bundle_graph(graph, listing, bundle_root=root)


def test_normalize_does_not_reparse_concept_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "docs"
    graph, listing = _connected_fixtures(root)

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("normalize must not re-parse concept bodies")

    monkeypatch.setattr(Path, "read_text", _boom)
    monkeypatch.setattr(Path, "read_bytes", _boom)

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert model.edges
    assert model.nodes


def test_broken_link_is_not_promoted_when_same_ids_appear_resolved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"),),
        links=(),
        broken_links=(
            _link(
                root,
                source="a",
                target="b",
                text="broken",
                target_exists=False,
                target_concept_id=None,
            ),
        ),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(_listed(root, "a", outbound=0, inbound=0, pagerank=1.0),),
        orphans=("a",),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert model.edges == ()
    assert model.excluded_links == ()
    assert model.broken_links[0].text == "broken"


def test_nonfatal_problems_do_not_fail_construction(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"),),
        problems=(
            GraphProblem(
                concept_id="a",
                path=root / "a.md",
                kind="stable-id-missing",
                message="no stable id",
            ),
        ),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(_listed(root, "a", outbound=0, inbound=0, pagerank=1.0),),
        orphans=("a",),
        problems=(
            ListingProblem(
                path=root / "a.md",
                kind="stable-id-missing",
                message="no stable id",
                concept_id="a",
            ),
        ),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert len(model.problems) == 1
    assert model.problems[0].kind == "stable-id-missing"
    assert model.problems[0].path == "a.md"


def test_resolved_link_with_none_target_id_is_excluded_not_an_edge(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"),),
        links=(
            _link(
                root,
                source="a",
                target="frag",
                text="hash",
                target_concept_id=None,
            ),
        ),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(_listed(root, "a", outbound=1, inbound=0, pagerank=1.0),),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert model.edges == ()
    assert model.excluded_links[0].reason == "unlisted-endpoint"
    assert model.excluded_links[0].target_id is None


def test_portable_dict_has_no_analysis_or_timestamps(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph, listing = _connected_fixtures(root)

    portable = normalize_bundle_graph(
        graph, listing, bundle_root=root
    ).to_portable_dict()

    assert set(portable) == {
        "bundle_name",
        "nodes",
        "edges",
        "self_links",
        "broken_links",
        "excluded_links",
        "problems",
    }
    assert "components" not in portable
    assert "generated_at" not in portable
    assert "timestamp" not in portable


def test_acquire_normalized_graph_matches_normalize_of_shared_scan(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="See [B](b.md).\n")
    _write_concept(root / "b.md")
    bundle = _bundle(root)
    manifest = scan_bundle(bundle)

    acquired = acquire_normalized_graph(bundle, manifest=manifest)
    from okf_core.graph import build_bundle_graph
    from okf_core.listing import list_concepts

    graph = build_bundle_graph(bundle, manifest=manifest)
    listing = list_concepts(bundle, manifest=manifest, graph=graph)
    expected = normalize_bundle_graph(graph, listing, bundle_root=bundle.bundle_root)

    assert acquired == expected
    assert [node.concept_id for node in acquired.nodes] == ["a", "b"]
    assert [(edge.source_id, edge.target_id) for edge in acquired.edges] == [("a", "b")]


def test_acquire_shares_one_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md")
    bundle = _bundle(root)
    calls = {"n": 0}
    real = scan_bundle

    def _counting(target: BundleConfig) -> object:
        calls["n"] += 1
        return real(target)

    monkeypatch.setattr("okf_core.graph_model.scan_bundle", _counting)
    monkeypatch.setattr("okf_core.graph.scan_bundle", _counting)
    monkeypatch.setattr("okf_core.listing.scan_bundle", _counting)
    monkeypatch.setattr("okf_core.manifest.scan_bundle", _counting)

    acquire_normalized_graph(bundle)

    assert calls["n"] == 1


def test_acquire_with_manifest_does_not_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md")
    bundle = _bundle(root)
    manifest = scan_bundle(bundle)

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("scan_bundle should not run when manifest is passed")

    monkeypatch.setattr("okf_core.graph_model.scan_bundle", _boom)
    monkeypatch.setattr("okf_core.graph.scan_bundle", _boom)
    monkeypatch.setattr("okf_core.listing.scan_bundle", _boom)

    model = acquire_normalized_graph(bundle, manifest=manifest)

    assert [node.concept_id for node in model.nodes] == ["a"]


def test_acquire_empty_missing_root_is_empty(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    model = acquire_normalized_graph(_bundle(root))

    assert model == NormalizedBundleGraph(bundle_name="docs")


def test_acquire_rejects_wrong_bundle_type() -> None:
    with pytest.raises(GraphModelError, match="bundle must be a BundleConfig"):
        acquire_normalized_graph({})  # type: ignore[arg-type]


def test_wrong_count_value_types_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"), _entry(root, "b")),
        links=(_link(root, source="a", target="b", text="one"),),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(
            ConceptListing(
                concept_id="a",
                path=root / "a.md",
                type="concept",
                outbound_link_count=True,  # type: ignore[arg-type]
                inbound_link_count=0,
                pagerank=0.5,
            ),
            _listed(root, "b", outbound=0, inbound=1, pagerank=0.5),
        ),
    )

    with pytest.raises(GraphModelError, match="expected int graph count"):
        normalize_bundle_graph(graph, listing, bundle_root=root)


def test_excluded_link_type_is_unlisted_endpoint(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(_entry(root, "a"), _entry(root, "hidden")),
        links=(_link(root, source="a", target="hidden", text="x"),),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(_listed(root, "a", outbound=1, inbound=0, pagerank=1.0),),
    )

    model = normalize_bundle_graph(graph, listing, bundle_root=root)

    assert isinstance(model.excluded_links[0], GraphModelExcludedLink)
    assert model.excluded_links[0].source_path == "a.md"


def _connected_fixtures(root: Path) -> tuple[BundleGraph, BundleListing]:
    graph = BundleGraph(
        bundle_name="docs",
        concepts=(
            _entry(root, "a"),
            _entry(root, "b"),
            _entry(root, "c"),
            _entry(root, "d"),
        ),
        links=(
            _link(root, source="a", target="b", text="to-b"),
            _link(root, source="b", target="c", text="to-c"),
        ),
        broken_links=(
            _link(
                root,
                source="a",
                target="missing",
                text="gone",
                target_exists=False,
                target_concept_id=None,
            ),
        ),
    )
    listing = BundleListing(
        bundle_name="docs",
        concepts=(
            _listed(root, "a", outbound=1, inbound=0, pagerank=0.1),
            _listed(root, "b", outbound=1, inbound=1, pagerank=0.4),
            _listed(root, "c", outbound=0, inbound=1, pagerank=0.4),
            _listed(root, "d", outbound=0, inbound=0, pagerank=0.1),
        ),
        orphans=("d",),
    )
    return graph, listing


def _entry(root: Path, concept_id: str) -> ConceptManifestEntry:
    path = root.joinpath(*f"{concept_id}.md".split("/"))
    return ConceptManifestEntry(
        concept_id=concept_id,
        path=path,
        bundle_root=root,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType({"type": "concept", "title": concept_id}),
    )


def _listed(
    root: Path,
    concept_id: str,
    *,
    outbound: int | None,
    inbound: int | None,
    pagerank: float | None,
) -> ConceptListing:
    return ConceptListing(
        concept_id=concept_id,
        path=root.joinpath(*f"{concept_id}.md".split("/")),
        type="concept",
        title=concept_id,
        outbound_link_count=outbound,
        inbound_link_count=inbound,
        pagerank=pagerank,
    )


def _link(
    root: Path,
    *,
    source: str,
    target: str,
    text: str,
    target_exists: bool = True,
    target_concept_id: str | None | object = ...,
) -> ConceptLink:
    target_id: str | None
    if target_concept_id is ...:
        target_id = target if target_exists else None
    else:
        target_id = target_concept_id  # type: ignore[assignment]
    return ConceptLink(
        source_concept_id=source,
        source_path=root.joinpath(*f"{source}.md".split("/")),
        text=text,
        target=f"{target}.md",
        target_path=root.joinpath(*f"{target}.md".split("/")),
        target_concept_id=target_id,
    )


def _bundle(root: Path) -> BundleConfig:
    return BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
    )


def _write_concept(path: Path, body: str = "Body\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: concept\ntitle: {path.stem.title()}\n---\n{body}",
        encoding="utf-8",
        newline="\n",
    )


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_walk_strings(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_walk_strings(item))
        return found
    return []

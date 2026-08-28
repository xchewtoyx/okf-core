"""Tests for per-bundle GRAPH_REPORT.md and graph.json renderers."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from okf_core import (
    BundleGraphAnalysis,
    GraphModelBrokenLink,
    GraphModelEdge,
    GraphModelNode,
    GraphModelProblem,
    GraphReportError,
    GraphReportProvenance,
    NormalizedBundleGraph,
    analyze_normalized_graph,
    graph_report_payload,
    render_graph_json,
    render_graph_report,
    write_bundle_graph_artifacts,
)
from okf_core.markdown_engine import MARKDOWN

_GOLDENS = Path(__file__).resolve().parent / "goldens" / "graph_report"
_FROZEN_PROVENANCE = GraphReportProvenance(
    generated_at="2026-01-15T12:00:00Z",
    okf_version="0.2",
    git_revision="deadbeefcafebabe",
    source_commands=(),
)
_CANONICAL_HEADINGS = (
    "## Provenance",
    "## Graph overview",
    "## Graph health",
    "## High-centrality concepts",
    "## Bridge concepts",
    "## Suggested inspections",
    "## Communities",
)


def _model(
    *nodes: GraphModelNode,
    edges: tuple[GraphModelEdge, ...] = (),
    name: str = "docs",
    broken_links: tuple[GraphModelBrokenLink, ...] = (),
    problems: tuple[GraphModelProblem, ...] = (),
) -> NormalizedBundleGraph:
    return NormalizedBundleGraph(
        bundle_name=name,
        nodes=tuple(sorted(nodes, key=lambda node: node.concept_id)),
        edges=tuple(sorted(edges, key=lambda edge: (edge.source_id, edge.target_id))),
        broken_links=tuple(broken_links),
        problems=tuple(problems),
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
    title: str | None = None,
) -> GraphModelNode:
    orphan = inbound == 0 and outbound == 0 if is_orphan is None else is_orphan
    return GraphModelNode(
        concept_id=concept_id,
        path=f"{concept_id}.md",
        type="note",
        title=concept_id if title is None else title,
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


def _broken(
    source_id: str,
    target: str,
    *,
    target_path: str | None = None,
    text: str = "missing",
) -> GraphModelBrokenLink:
    return GraphModelBrokenLink(
        source_id=source_id,
        source_path=f"{source_id}.md",
        text=text,
        target=target,
        target_path=target if target_path is None else target_path,
    )


def _healthy_model() -> NormalizedBundleGraph:
    return _model(
        _node("a", inbound=2, outbound=2, unique_in=2, unique_out=2, pagerank=0.1),
        _node("b", inbound=2, outbound=2, unique_in=2, unique_out=2, pagerank=0.1),
        _node(
            "m",
            inbound=4,
            outbound=4,
            unique_in=4,
            unique_out=4,
            pagerank=0.5,
            title="Shared hub",
        ),
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
        name="healthy",
    )


def _fragmented_model() -> NormalizedBundleGraph:
    untitled = replace(_node("untitled"), title=None)
    return _model(
        _node("a", outbound=1, unique_out=1, pagerank=0.1),
        _node("b", inbound=1, outbound=1, unique_in=1, unique_out=1, pagerank=0.2),
        _node("c", inbound=1, outbound=1, unique_in=1, unique_out=1, pagerank=0.3),
        _node("d", outbound=1, unique_out=1, pagerank=0.05),
        _node("e", inbound=1, unique_in=1, pagerank=0.05),
        _node("h", inbound=1, unique_in=1, pagerank=0.9, title="Hub sink"),
        untitled,
        edges=(
            _edge("a", "b"),
            _edge("b", "c"),
            _edge("c", "h"),
            _edge("d", "e"),
        ),
        name="fragmented",
        broken_links=(
            _broken("a", "../outside.md"),
            _broken("a", "/root-rel.md", target_path="/root-rel.md"),
        ),
        problems=(
            GraphModelProblem(
                concept_id="a",
                path="a.md",
                kind="parse-error",
                message="frontmatter is not a mapping",
            ),
        ),
    )


def _empty_model() -> NormalizedBundleGraph:
    return _model(name="empty")


def _strip_provenance(markdown: str) -> str:
    start = markdown.find("## Provenance")
    if start == -1:
        return markdown
    rest = markdown[start:]
    next_heading = rest.find("\n## ", 1)
    if next_heading == -1:
        return markdown[:start]
    return markdown[:start] + rest[next_heading + 1 :]


def _render_pair(
    model: NormalizedBundleGraph,
    *,
    provenance: GraphReportProvenance = _FROZEN_PROVENANCE,
) -> tuple[str, str]:
    analysis = analyze_normalized_graph(model)
    return (
        render_graph_report(model, analysis, provenance=provenance),
        render_graph_json(model, analysis),
    )


def _inspection_section(report: str) -> str:
    start = report.find("## Suggested inspections")
    end = report.find("## Communities")
    return report[start:end]


@pytest.mark.parametrize(
    "name",
    ["healthy", "fragmented", "empty"],
)
def test_golden_graph_report_markdown(name: str) -> None:
    model = {
        "healthy": _healthy_model,
        "fragmented": _fragmented_model,
        "empty": _empty_model,
    }[name]()
    report, _json = _render_pair(model)
    expected = (_GOLDENS / name / "GRAPH_REPORT.md").read_text(encoding="utf-8")
    assert report == expected


@pytest.mark.parametrize(
    "name",
    ["healthy", "fragmented", "empty"],
)
def test_golden_graph_json(name: str) -> None:
    model = {
        "healthy": _healthy_model,
        "fragmented": _fragmented_model,
        "empty": _empty_model,
    }[name]()
    _report, payload = _render_pair(model)
    expected = (_GOLDENS / name / "graph.json").read_text(encoding="utf-8")
    assert payload == expected


def test_report_body_is_byte_stable_across_volatile_provenance() -> None:
    model = _fragmented_model()
    first, _ = _render_pair(
        model,
        provenance=GraphReportProvenance(
            generated_at="2026-01-01T00:00:00Z",
            okf_version="0.2",
            git_revision="aaa",
            source_commands=(".venv/bin/okf graph --bundle fragmented",),
        ),
    )
    second, _ = _render_pair(
        model,
        provenance=GraphReportProvenance(
            generated_at="2026-08-28T12:34:56Z",
            okf_version="0.2",
            git_revision="bbb",
            source_commands=(".venv/bin/okf graph --bundle fragmented",),
        ),
    )

    assert first != second
    assert _strip_provenance(first) == _strip_provenance(second)


def test_graph_json_omits_provenance_and_writer_paths(tmp_path: Path) -> None:
    model = _fragmented_model()
    analysis = analyze_normalized_graph(model)
    payload = render_graph_json(model, analysis)

    assert str(tmp_path) not in payload
    parsed = json.loads(payload)
    assert set(parsed) == {"schema_version", "normalized_graph", "analysis"}
    assert parsed["schema_version"] == 1
    dumped = json.dumps(parsed)
    assert "generated_at" not in dumped
    assert "output_dir" not in dumped
    assert "bundle_root" not in dumped
    assert "provenance" not in dumped


def test_graph_json_keeps_portable_parent_and_root_relative_targets() -> None:
    model = _fragmented_model()
    payload = render_graph_json(model, analyze_normalized_graph(model))

    assert "../outside.md" in payload
    assert "/root-rel.md" in payload


def test_writer_does_not_embed_output_paths_in_graph_json(tmp_path: Path) -> None:
    model = _fragmented_model()
    analysis = analyze_normalized_graph(model)
    payload = render_graph_json(model, analysis)
    paths = write_bundle_graph_artifacts(
        tmp_path,
        "fragmented",
        report_markdown=render_graph_report(
            model, analysis, provenance=_FROZEN_PROVENANCE
        ),
        graph_json=payload,
    )

    written = paths.graph_json_path.read_text(encoding="utf-8")
    assert written == payload
    assert str(tmp_path) not in written
    assert "../outside.md" in written
    assert "/root-rel.md" in written


@pytest.mark.parametrize(
    "payload",
    [None, {}, [], "bundle", object()],
    ids=["none", "mapping", "list", "string", "object"],
)
def test_wrong_model_type_raises_graph_report_error(payload: object) -> None:
    analysis = analyze_normalized_graph(_empty_model())
    with pytest.raises(GraphReportError, match="NormalizedBundleGraph"):
        render_graph_report(payload, analysis, provenance=_FROZEN_PROVENANCE)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [None, {}, [], "analysis", object()],
    ids=["none", "mapping", "list", "string", "object"],
)
def test_wrong_analysis_type_raises_graph_report_error(payload: object) -> None:
    with pytest.raises(GraphReportError, match="BundleGraphAnalysis"):
        render_graph_report(_empty_model(), payload, provenance=_FROZEN_PROVENANCE)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [None, {}, "provenance", object()],
    ids=["none", "mapping", "string", "object"],
)
def test_wrong_provenance_type_raises_graph_report_error(payload: object) -> None:
    model = _empty_model()
    with pytest.raises(GraphReportError, match="GraphReportProvenance"):
        render_graph_report(
            model, analyze_normalized_graph(model), provenance=payload  # type: ignore[arg-type]
        )


def test_bundle_name_mismatch_raises_graph_report_error() -> None:
    model = _healthy_model()
    analysis = analyze_normalized_graph(_empty_model())
    assert isinstance(analysis, BundleGraphAnalysis)
    with pytest.raises(GraphReportError, match="bundle_name"):
        render_graph_report(model, analysis, provenance=_FROZEN_PROVENANCE)


def test_payload_rejects_bundle_name_mismatch() -> None:
    with pytest.raises(GraphReportError, match="bundle_name"):
        graph_report_payload(_healthy_model(), analyze_normalized_graph(_empty_model()))


@pytest.mark.parametrize(
    "slug",
    ["", "foo/bar", "foo\\bar"],
    ids=["empty", "slash", "backslash"],
)
def test_invalid_slug_raises_graph_report_error(tmp_path: Path, slug: str) -> None:
    with pytest.raises(GraphReportError, match="bundle_slug"):
        write_bundle_graph_artifacts(
            tmp_path, slug, report_markdown="#\n", graph_json="{}\n"
        )


@pytest.mark.parametrize(
    "output_dir",
    ["tmp", None, 1],
    ids=["string", "none", "int"],
)
def test_non_path_output_dir_raises_graph_report_error(output_dir: object) -> None:
    with pytest.raises(GraphReportError, match="output_dir"):
        write_bundle_graph_artifacts(
            output_dir,  # type: ignore[arg-type]
            "docs",
            report_markdown="#\n",
            graph_json="{}\n",
        )


def test_writer_lands_both_files_under_output_slug(tmp_path: Path) -> None:
    paths = write_bundle_graph_artifacts(
        tmp_path / "out",
        "docs",
        report_markdown="# Graph report\n",
        graph_json='{"schema_version": 1}\n',
    )

    assert paths.report_path == tmp_path / "out" / "docs" / "GRAPH_REPORT.md"
    assert paths.graph_json_path == tmp_path / "out" / "docs" / "graph.json"
    assert paths.report_path.read_text(encoding="utf-8") == "# Graph report\n"
    assert (
        paths.graph_json_path.read_text(encoding="utf-8") == '{"schema_version": 1}\n'
    )


def test_empty_bundle_emits_every_canonical_heading() -> None:
    report, _ = _render_pair(_empty_model())

    positions = [report.index(heading) for heading in _CANONICAL_HEADINGS]
    assert positions == sorted(positions)
    assert "None observed." in report
    assert _inspection_section(report).count("```sh") == 0


@pytest.mark.parametrize(
    ("kind", "present"),
    [
        ("articulation", True),
        ("articulation", False),
        ("pagerank_zero_out", True),
        ("pagerank_zero_out", False),
        ("orphans", True),
        ("orphans", False),
        ("broken", True),
        ("broken", False),
    ],
)
def test_inspection_condition_present_vs_absent(kind: str, present: bool) -> None:
    model = _inspection_model(kind, present=present)
    report, _ = _render_pair(model)
    section = _inspection_section(report)
    needles = {
        "articulation": ("### Sole component bridge", "--concept b --depth 2"),
        "pagerank_zero_out": (
            "### High-PageRank concept with no outbound links",
            "--seed sink",
        ),
        "orphans": ("### Orphans (1)", "--seed lone"),
        "broken": ("### Broken link", "--concept src --depth 2"),
    }[kind]
    for needle in needles:
        assert (needle in section) is present


def test_two_articulation_points_use_articulation_point_label() -> None:
    report, _ = _render_pair(_fragmented_model())
    section = _inspection_section(report)
    assert "### Articulation point" in section
    assert "### Sole component bridge" not in section
    assert "--concept b --depth 2" in section


def test_none_title_falls_back_to_concept_id() -> None:
    report, _ = _render_pair(_fragmented_model())
    assert "- untitled" in report
    assert "None (untitled)" not in report


def test_markdown_significant_title_is_literal_inline_not_a_link() -> None:
    model = _model(
        _node("n", title="see [x](y.md)", pagerank=1.0),
        name="docs",
    )
    report, _ = _render_pair(model)
    tokens = MARKDOWN.parse(report)
    assert not any(token.type == "link_open" for token in tokens)


def test_empty_source_commands_use_default_okf_pair() -> None:
    report, _ = _render_pair(_healthy_model())
    assert ".venv/bin/okf graph --bundle healthy" in report
    assert ".venv/bin/okf list-concepts --bundle healthy --with-graph-counts" in report


def test_explicit_source_commands_replace_defaults() -> None:
    model = _empty_model()
    report = render_graph_report(
        model,
        analyze_normalized_graph(model),
        provenance=GraphReportProvenance(
            generated_at="2026-01-15T12:00:00Z",
            okf_version="0.2",
            source_commands=("custom-graph", "custom-list"),
        ),
    )
    assert "custom-graph" in report
    assert "custom-list" in report
    assert ".venv/bin/okf graph --bundle empty" not in report


def test_git_revision_omitted_when_none() -> None:
    model = _empty_model()
    report = render_graph_report(
        model,
        analyze_normalized_graph(model),
        provenance=GraphReportProvenance(
            generated_at="2026-01-15T12:00:00Z",
            okf_version="0.2",
            git_revision=None,
        ),
    )
    assert "Git revision" not in report


def test_markdown_floats_use_six_digits() -> None:
    report, _ = _render_pair(_healthy_model())
    assert "0.500000" in report
    assert "Density:" in report


def test_broken_link_source_absent_from_nodes_uses_concept_id() -> None:
    model = _model(
        _node("a"),
        broken_links=(_broken("ghost", "gone.md"),),
    )
    report, _ = _render_pair(model)
    assert "Inspect the neighborhood of ghost." in report


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"generated_at": True, "okf_version": "0.2"}, "generated_at"),
        ({"generated_at": "2026-01-15T12:00:00Z", "okf_version": 2}, "okf_version"),
        (
            {
                "generated_at": "2026-01-15T12:00:00Z",
                "okf_version": "0.2",
                "git_revision": 1,
            },
            "git_revision",
        ),
        (
            {
                "generated_at": "2026-01-15T12:00:00Z",
                "okf_version": "0.2",
                "source_commands": ["okf graph"],
            },
            "source_commands",
        ),
    ],
    ids=["generated_at", "okf_version", "git_revision", "source_commands"],
)
def test_provenance_field_types_raise_graph_report_error(
    kwargs: dict[str, object], match: str
) -> None:
    model = _empty_model()
    provenance = GraphReportProvenance(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(GraphReportError, match=match):
        render_graph_report(
            model, analyze_normalized_graph(model), provenance=provenance
        )


@pytest.mark.parametrize(
    ("report_markdown", "graph_json", "match"),
    [
        (None, "{}\n", "report_markdown"),
        ("#\n", None, "graph_json"),
    ],
    ids=["report", "json"],
)
def test_writer_rejects_non_str_artifact_text(
    tmp_path: Path,
    report_markdown: object,
    graph_json: object,
    match: str,
) -> None:
    with pytest.raises(GraphReportError, match=match):
        write_bundle_graph_artifacts(
            tmp_path,
            "docs",
            report_markdown=report_markdown,  # type: ignore[arg-type]
            graph_json=graph_json,  # type: ignore[arg-type]
        )


def test_writer_rejects_non_str_slug(tmp_path: Path) -> None:
    with pytest.raises(GraphReportError, match="bundle_slug"):
        write_bundle_graph_artifacts(
            tmp_path,
            None,  # type: ignore[arg-type]
            report_markdown="#\n",
            graph_json="{}\n",
        )


def _inspection_model(kind: str, *, present: bool) -> NormalizedBundleGraph:
    if kind == "articulation":
        if present:
            return _model(
                _node("a", outbound=1, unique_out=1),
                _node("b", inbound=1, outbound=1, unique_in=1, unique_out=1),
                _node("c", inbound=1, unique_in=1),
                edges=(_edge("a", "b"), _edge("b", "c")),
            )
        return _model(
            _node("a", inbound=1, outbound=1, unique_in=1, unique_out=1),
            _node("b", inbound=1, outbound=1, unique_in=1, unique_out=1),
            _node("c", inbound=1, outbound=1, unique_in=1, unique_out=1),
            edges=(_edge("a", "b"), _edge("b", "c"), _edge("c", "a")),
        )
    if kind == "pagerank_zero_out":
        if present:
            return _model(
                _node("src", outbound=1, unique_out=1, pagerank=0.1),
                _node("sink", inbound=1, unique_in=1, pagerank=0.9),
                edges=(_edge("src", "sink"),),
            )
        return _model(
            _node(
                "src", outbound=1, inbound=1, unique_out=1, unique_in=1, pagerank=0.6
            ),
            _node(
                "sink", outbound=1, inbound=1, unique_out=1, unique_in=1, pagerank=0.4
            ),
            edges=(_edge("src", "sink"), _edge("sink", "src")),
        )
    if kind == "orphans":
        if present:
            return _model(_node("lone"))
        return _model(
            _node("a", outbound=1, unique_out=1),
            _node("b", inbound=1, unique_in=1),
            edges=(_edge("a", "b"),),
        )
    if present:
        return _model(
            _node("src", outbound=1, unique_out=1),
            _node("dst", inbound=1, unique_in=1),
            edges=(_edge("src", "dst"),),
            broken_links=(_broken("src", "gone.md"),),
        )
    return _model(
        _node("src", outbound=1, unique_out=1),
        _node("dst", inbound=1, unique_in=1),
        edges=(_edge("src", "dst"),),
    )

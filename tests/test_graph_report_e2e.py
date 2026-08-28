"""End-to-end determinism and isolation tests for ``okf graph-report``.

Constructs real Markdown under ``tmp_path`` and drives
:func:`okf_core.run_graph_report` (plus the CLI fail-closed path). The
OKF oracle is one ``scan_bundle`` reused by ``build_bundle_graph`` and
``list_concepts`` — this module does not rebuild analysis or assemble a
``NormalizedBundleGraph`` by hand.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from okf_core import (
    BundleConfig,
    BundleGraph,
    BundleListing,
    GraphReportError,
    GraphReportProvenance,
    GraphReportRunResult,
    OkfConfig,
    build_bundle_graph,
    list_concepts,
    load_config,
    run_graph_report,
    scan_bundle,
)
from okf_core.cli import cli

_FIXTURE_NAMES = (
    "healthy",
    "components",
    "degrees",
    "link-shapes",
    "problems",
    "outside",
    "ties",
    "empty",
    "multi",
)
_REPORT_ARTIFACT_NAMES = frozenset({"GRAPH_REPORT.md", "graph.json", "SUMMARY.md"})


def _write_concept(
    path: Path, body: str = "Body\n", *, title: str | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    heading = path.stem.title() if title is None else title
    path.write_text(
        f"---\ntype: concept\ntitle: {heading}\n---\n{body}\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_project(tmp_path: Path, bundles: Mapping[str, str]) -> Path:
    (tmp_path / "fleeting").mkdir(exist_ok=True)
    blocks = [
        f'[bundles.{name}]\nbundle_root = "{root}"\n' for name, root in bundles.items()
    ]
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text("\n".join(blocks), encoding="utf-8")
    return config_path


def _frozen_provenance() -> GraphReportProvenance:
    return GraphReportProvenance(
        generated_at="2026-01-15T12:00:00Z",
        okf_version="0.2",
        git_revision=None,
    )


def _okf_oracle(bundle: BundleConfig) -> tuple[BundleGraph, BundleListing]:
    manifest = scan_bundle(bundle)
    graph = build_bundle_graph(bundle, manifest)
    listing = list_concepts(bundle, manifest=manifest, graph=graph)
    return graph, listing


def _output_tree_bytes(output_dir: Path) -> dict[str, bytes]:
    if not output_dir.exists():
        return {}
    tree: dict[str, bytes] = {}
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            tree[path.relative_to(output_dir).as_posix()] = path.read_bytes()
    return tree


def _bundle_file_set(config: OkfConfig) -> dict[str, bytes]:
    roots = [bundle.bundle_root for bundle in config.bundles.values()]
    roots.append(config.project_root / "fleeting")
    files: dict[str, bytes] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                files[path.resolve().as_posix()] = path.read_bytes()
    return files


def _load(tmp_path: Path, bundles: Mapping[str, str]) -> OkfConfig:
    return load_config(config_path=_write_project(tmp_path, bundles))


def _healthy(tmp_path: Path) -> OkfConfig:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="See [B](b.md) and [C](c.md).")
    _write_concept(root / "b.md", body="See [A](a.md) and [C](c.md).")
    _write_concept(root / "c.md", body="See [A](a.md) and [B](b.md).")
    return _load(tmp_path, {"docs": "docs"})


def _components(tmp_path: Path) -> OkfConfig:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="See [B](b.md).")
    _write_concept(root / "b.md", body="See [A](a.md).")
    _write_concept(root / "c.md", body="See [D](d.md).")
    _write_concept(root / "d.md", body="See [C](c.md).")
    return _load(tmp_path, {"docs": "docs"})


def _degrees(tmp_path: Path) -> OkfConfig:
    root = tmp_path / "docs"
    _write_concept(root / "orphan.md")
    _write_concept(root / "source.md", body="See [Sink](sink.md).")
    _write_concept(root / "sink.md")
    return _load(tmp_path, {"docs": "docs"})


def _link_shapes(tmp_path: Path) -> OkfConfig:
    root = tmp_path / "docs"
    _write_concept(
        root / "a.md",
        body="See [B](b.md) and [B again](b.md) and [self](a.md).",
    )
    _write_concept(root / "b.md", body="Back to [A](a.md).")
    return _load(tmp_path, {"docs": "docs"})


def _problems(tmp_path: Path) -> OkfConfig:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="See [missing](gone.md).")
    (root / "untyped.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "untyped.md").write_text(
        "---\ntitle: Untyped\n---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "binary.md").write_bytes(b"\xff\xfe not utf-8")
    return _load(tmp_path, {"docs": "docs"})


def _outside(tmp_path: Path) -> OkfConfig:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="See [out](../outside.md).")
    _write_concept(tmp_path / "outside.md")
    return _load(tmp_path, {"docs": "docs"})


def _ties(tmp_path: Path) -> OkfConfig:
    return _components(tmp_path)


def _empty(tmp_path: Path) -> OkfConfig:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "index.md").write_text("# Index\n", encoding="utf-8", newline="\n")
    (root / "log.md").write_text("# Log\n", encoding="utf-8", newline="\n")
    return _load(tmp_path, {"docs": "docs"})


def _multi(tmp_path: Path) -> OkfConfig:
    _write_concept(tmp_path / "docs" / "alpha.md")
    _write_concept(tmp_path / "notes" / "beta.md")
    return _load(tmp_path, {"docs": "docs", "notes": "notes"})


_BUILDERS: dict[str, Callable[[Path], OkfConfig]] = {
    "healthy": _healthy,
    "components": _components,
    "degrees": _degrees,
    "link-shapes": _link_shapes,
    "problems": _problems,
    "outside": _outside,
    "ties": _ties,
    "empty": _empty,
    "multi": _multi,
}


def _run_report(
    config: OkfConfig, output_dir: Path, *, bundle_names: tuple[str, ...] | None = None
) -> GraphReportRunResult:
    return run_graph_report(
        config,
        bundle_names=bundle_names,
        output_dir=output_dir,
        provenance=_frozen_provenance(),
    )


def _graph_json(output_dir: Path, bundle_name: str) -> dict[str, Any]:
    return json.loads(
        (output_dir / bundle_name / "graph.json").read_text(encoding="utf-8")
    )


def _report_text(output_dir: Path, bundle_name: str) -> str:
    return (output_dir / bundle_name / "GRAPH_REPORT.md").read_text(encoding="utf-8")


def _overview_int(report: str, label: str) -> int:
    match = re.search(rf"^- {re.escape(label)}: (.+)$", report, re.MULTILINE)
    assert match is not None, label
    return int(match.group(1))


def _summary_cells(summary: str, bundle_name: str) -> list[str]:
    for line in summary.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == bundle_name:
            return cells
    raise AssertionError(f"no SUMMARY row for {bundle_name!r}")


def _listed_unique_edge_counts(
    graph: BundleGraph, listing: BundleListing
) -> dict[tuple[str, str], int]:
    listed = {row.concept_id for row in listing.concepts}
    counts: dict[tuple[str, str], int] = {}
    for link in graph.links:
        source = link.source_concept_id
        target = link.target_concept_id
        if target is None or source == target:
            continue
        if source not in listed or target not in listed:
            continue
        key = (source, target)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        found: list[str] = []
        for key, item in value.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_walk_strings(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_walk_strings(item))
        return found
    return []


def _assert_no_report_artifacts(root: Path) -> None:
    if not root.exists():
        return
    found = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.name in _REPORT_ARTIFACT_NAMES
    }
    assert found == set()


def _runner() -> CliRunner:
    if "mix_stderr" in inspect.signature(CliRunner).parameters:
        kwargs: dict[str, Any] = {"mix_stderr": False}
        return CliRunner(**kwargs)
    return CliRunner()


def _oracle_nodes_by_id(listing: BundleListing) -> dict[str, Any]:
    return {row.concept_id: row for row in listing.concepts}


def _assert_json_preserves_okf(
    bundle: BundleConfig, payload: dict[str, Any]
) -> tuple[BundleGraph, BundleListing]:
    graph, listing = _okf_oracle(bundle)
    nodes = payload["normalized_graph"]["nodes"]
    listed = _oracle_nodes_by_id(listing)
    assert {node["concept_id"] for node in nodes} == set(listed)
    assert payload["analysis"]["overview"]["concept_count"] == len(listing.concepts)
    unique = _listed_unique_edge_counts(graph, listing)
    assert payload["analysis"]["overview"]["unique_directed_edge_count"] == len(unique)
    assert payload["analysis"]["overview"]["link_instance_count"] == sum(
        unique.values()
    )
    assert len(payload["normalized_graph"]["broken_links"]) == len(graph.broken_links)
    json_kinds = {item["kind"] for item in payload["normalized_graph"]["problems"]}
    assert {problem.kind for problem in listing.problems} <= json_kinds
    assert {problem.kind for problem in graph.problems} <= json_kinds
    assert tuple(payload["analysis"]["diagnostics"]["orphans"]) == listing.orphans
    for node in nodes:
        row = listed[node["concept_id"]]
        assert node["inbound_link_count"] == row.inbound_link_count
        assert node["outbound_link_count"] == row.outbound_link_count
        assert node["pagerank"] == row.pagerank
        assert node["is_orphan"] is (row.concept_id in listing.orphans)
    return graph, listing


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_okf_counts_are_preserved_in_graph_json(tmp_path: Path, fixture: str) -> None:
    config = _BUILDERS[fixture](tmp_path)
    output = tmp_path / "out"
    result = _run_report(config, output)

    assert result.problems == ()
    for name in result.selected_bundle_names:
        _assert_json_preserves_okf(config.bundles[name], _graph_json(output, name))


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_okf_counts_are_preserved_in_graph_report_markdown(
    tmp_path: Path, fixture: str
) -> None:
    config = _BUILDERS[fixture](tmp_path)
    output = tmp_path / "out"
    result = _run_report(config, output)

    for name in result.selected_bundle_names:
        graph, listing = _okf_oracle(config.bundles[name])
        report = _report_text(output, name)
        unique = _listed_unique_edge_counts(graph, listing)
        assert _overview_int(report, "Concepts") == len(listing.concepts)
        assert _overview_int(report, "Link instances") == sum(unique.values())
        assert _overview_int(report, "Unique directed edges") == len(unique)
        for row in listing.concepts:
            if row.concept_id in listing.orphans:
                assert row.concept_id in report
        for link in graph.broken_links:
            assert link.target in report


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_okf_counts_are_preserved_in_summary(tmp_path: Path, fixture: str) -> None:
    config = _BUILDERS[fixture](tmp_path)
    output = tmp_path / "out"
    result = _run_report(config, output)
    summary = (output / "SUMMARY.md").read_text(encoding="utf-8")

    assert [row.bundle for row in result.rows] == list(result.selected_bundle_names)
    for name in result.selected_bundle_names:
        graph, listing = _okf_oracle(config.bundles[name])
        payload = _graph_json(output, name)
        cells = _summary_cells(summary, name)
        unique = _listed_unique_edge_counts(graph, listing)
        broken_and_problems = len(payload["normalized_graph"]["broken_links"]) + len(
            payload["normalized_graph"]["problems"]
        )
        assert cells[1] == str(len(listing.concepts))
        assert cells[2] == str(len(unique))
        assert cells[5] == str(len(listing.orphans))
        assert cells[8] == str(broken_and_problems)


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_two_runs_with_injected_provenance_are_byte_identical(
    tmp_path: Path, fixture: str
) -> None:
    config = _BUILDERS[fixture](tmp_path)
    first = tmp_path / "out-a"
    second = tmp_path / "out-b"

    _run_report(config, first)
    _run_report(config, second)

    assert _output_tree_bytes(first) == _output_tree_bytes(second)
    assert _output_tree_bytes(first)
    for relative, payload in _output_tree_bytes(first).items():
        if relative.endswith("GRAPH_REPORT.md") or relative == "SUMMARY.md":
            assert b"2026-01-15T12:00:00Z" in payload


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_run_does_not_write_inside_bundles_or_fleeting(
    tmp_path: Path, fixture: str
) -> None:
    config = _BUILDERS[fixture](tmp_path)
    output = tmp_path / "out"
    before = _bundle_file_set(config)

    result = _run_report(config, output)

    assert _bundle_file_set(config) == before
    fleeting = config.project_root / "fleeting"
    assert list(fleeting.rglob("*")) == []
    for path in result.written_paths:
        resolved = path.resolve()
        assert resolved.is_relative_to(output.resolve())
        for bundle in config.bundles.values():
            assert not resolved.is_relative_to(bundle.bundle_root.resolve())
        assert not resolved.is_relative_to(fleeting.resolve())


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_artifacts_contain_no_absolute_paths(tmp_path: Path, fixture: str) -> None:
    config = _BUILDERS[fixture](tmp_path)
    output = tmp_path / "out"
    _run_report(config, output)
    project = str(tmp_path.resolve())

    for relative, payload in _output_tree_bytes(output).items():
        text = payload.decode("utf-8")
        assert str(tmp_path) not in text
        assert project not in text
        if relative.endswith("graph.json"):
            for item in _walk_strings(json.loads(text)):
                assert not item.startswith("/"), item
                assert not Path(item).is_absolute(), item


def test_outside_target_stays_portable(tmp_path: Path) -> None:
    config = _outside(tmp_path)
    output = tmp_path / "out"
    _run_report(config, output)

    payload = _graph_json(output, "docs")
    report = _report_text(output, "docs")
    targets = [item["target"] for item in payload["normalized_graph"]["broken_links"]]
    target_paths = [
        item["target_path"] for item in payload["normalized_graph"]["broken_links"]
    ]
    assert "../outside.md" in targets
    assert "../outside.md" in target_paths
    assert "../outside.md" in report
    assert str(tmp_path.resolve() / "outside.md") not in report
    assert str(tmp_path.resolve() / "outside.md") not in json.dumps(payload)


def test_all_bundles_write_every_slug(tmp_path: Path) -> None:
    config = _multi(tmp_path)
    output = tmp_path / "out"

    result = _run_report(config, output)
    summary = (output / "SUMMARY.md").read_text(encoding="utf-8")

    assert result.selected_bundle_names == ("docs", "notes")
    assert result.is_subset is False
    assert (output / "docs" / "GRAPH_REPORT.md").is_file()
    assert (output / "notes" / "graph.json").is_file()
    assert "selected subset" not in summary
    assert "docs" in summary
    assert "notes" in summary


def test_selected_bundle_writes_only_that_slug(tmp_path: Path) -> None:
    config = _multi(tmp_path)
    output = tmp_path / "out"

    result = _run_report(config, output, bundle_names=("notes",))
    summary = (output / "SUMMARY.md").read_text(encoding="utf-8")

    assert result.selected_bundle_names == ("notes",)
    assert result.is_subset is True
    assert (output / "notes" / "GRAPH_REPORT.md").is_file()
    assert (output / "notes" / "graph.json").is_file()
    assert not (output / "docs").exists()
    assert "selected subset of configured bundles: notes" in summary
    assert "Configured bundles: docs, notes" in summary
    assert _summary_cells(summary, "notes")[0] == "notes"
    with pytest.raises(AssertionError, match="no SUMMARY row"):
        _summary_cells(summary, "docs")


def test_ranking_tie_breaks_are_slug_asc(tmp_path: Path) -> None:
    config = _ties(tmp_path)
    output = tmp_path / "out"
    _run_report(config, output)

    payload = _graph_json(output, "docs")
    pagerank = payload["analysis"]["top_by_pagerank"]
    inbound = payload["analysis"]["top_by_inbound"]
    pagerank_values = [row["value"] for row in pagerank]
    inbound_values = [row["value"] for row in inbound]
    assert pagerank_values == [pagerank_values[0]] * len(pagerank_values)
    assert inbound_values == [inbound_values[0]] * len(inbound_values)
    assert [row["concept_id"] for row in pagerank] == ["a", "b", "c", "d"]
    assert [row["concept_id"] for row in inbound] == ["a", "b", "c", "d"]
    assert payload["analysis"]["components"]["sizes"] == [2, 2]
    assert payload["analysis"]["components"]["other_memberships"] == [["c", "d"]]

    report = _report_text(output, "docs")
    assert [row["concept_id"] for row in pagerank] == _ranking_ids(
        report, "Top by PageRank"
    )
    assert [row["concept_id"] for row in inbound] == _ranking_ids(
        report, "Top by inbound degree"
    )


def _ranking_ids(report: str, heading: str) -> list[str]:
    start = report.find(f"### {heading}")
    assert start != -1, heading
    rest = report[start:]
    next_heading = re.search(r"\n### ", rest[1:])
    block = rest if next_heading is None else rest[: next_heading.start() + 1]
    ids: list[str] = []
    for line in block.splitlines():
        labeled = re.match(r"^\d+\. .+ \(([^)]+)\) — ", line)
        if labeled:
            ids.append(labeled.group(1))
            continue
        plain = re.match(r"^\d+\. (\S+) — ", line)
        if plain:
            ids.append(plain.group(1))
    return ids


def test_degrees_lists_orphan_and_zero_degree_ids(tmp_path: Path) -> None:
    config = _degrees(tmp_path)
    output = tmp_path / "out"
    _run_report(config, output)
    diagnostics = _graph_json(output, "docs")["analysis"]["diagnostics"]
    graph, listing = _okf_oracle(config.bundles["docs"])

    assert listing.orphans == ("orphan",)
    assert diagnostics["orphans"] == ["orphan"]
    assert diagnostics["zero_inbound"] == ["orphan", "source"]
    assert diagnostics["zero_outbound"] == ["orphan", "sink"]
    assert len(graph.links) == 1


def test_link_shapes_preserve_repeated_reciprocal_and_self(tmp_path: Path) -> None:
    config = _link_shapes(tmp_path)
    output = tmp_path / "out"
    _run_report(config, output)
    model = _graph_json(output, "docs")["normalized_graph"]
    edges = {(edge["source_id"], edge["target_id"]): edge for edge in model["edges"]}

    assert edges[("a", "b")]["instance_count"] == 2
    assert edges[("b", "a")]["instance_count"] == 1
    assert ("a", "a") not in edges
    assert len(model["self_links"]) == 1
    assert model["self_links"][0]["concept_id"] == "a"
    assert model["self_links"][0]["instance_count"] == 1


def test_problems_surface_broken_link_and_okf_problem(tmp_path: Path) -> None:
    config = _problems(tmp_path)
    output = tmp_path / "out"
    _run_report(config, output)
    graph, listing = _okf_oracle(config.bundles["docs"])
    payload = _graph_json(output, "docs")
    kinds = {item["kind"] for item in payload["normalized_graph"]["problems"]}
    targets = [item["target"] for item in payload["normalized_graph"]["broken_links"]]

    assert "gone.md" in targets
    assert any(link.target == "gone.md" for link in graph.broken_links)
    assert "missing-type" in kinds
    assert "decode-error" in kinds
    assert {problem.kind for problem in listing.problems} <= kinds
    assert "gone.md" in _report_text(output, "docs")


def test_empty_bundle_writes_zero_count_artifacts(tmp_path: Path) -> None:
    config = _empty(tmp_path)
    output = tmp_path / "out"
    result = _run_report(config, output)
    payload = _graph_json(output, "docs")

    assert result.rows[0].concepts == 0
    assert payload["analysis"]["overview"]["concept_count"] == 0
    assert payload["normalized_graph"]["nodes"] == []
    assert _overview_int(_report_text(output, "docs"), "Concepts") == 0
    assert (
        _summary_cells((output / "SUMMARY.md").read_text(encoding="utf-8"), "docs")[1]
        == "0"
    )


def test_unknown_bundle_raises_before_writing(tmp_path: Path) -> None:
    config = _healthy(tmp_path)
    output = tmp_path / "out"

    with pytest.raises(GraphReportError, match="Unknown bundle"):
        _run_report(config, output, bundle_names=("missing",))

    assert not output.exists()
    _assert_no_report_artifacts(tmp_path / "docs")


@pytest.mark.parametrize(
    ("scenario", "stderr_match"),
    [
        ("unknown-bundle", "Unknown bundle"),
        ("output-in-bundle", "forbidden"),
        ("output-in-fleeting", "forbidden"),
        ("output-at-project-root", "forbidden"),
        ("bad-config", "Configuration error"),
    ],
    ids=[
        "unknown-bundle",
        "output-in-bundle",
        "output-in-fleeting",
        "output-at-project-root",
        "bad-config",
    ],
)
def test_cli_fail_closed_exits_2_without_partial_tree(
    tmp_path: Path, scenario: str, stderr_match: str
) -> None:
    output = tmp_path / "out"
    args, guarded = _cli_fail_closed_args(tmp_path, scenario, output)

    result = _runner().invoke(cli, args)

    assert result.exit_code == 2
    assert stderr_match in result.stderr
    _assert_no_report_artifacts(output)
    for root in guarded:
        _assert_no_report_artifacts(root)


def _cli_fail_closed_args(
    tmp_path: Path, scenario: str, output: Path
) -> tuple[list[str], tuple[Path, ...]]:
    if scenario == "bad-config":
        config_path = tmp_path / "okf-core.toml"
        config_path.write_text("this is not toml {\n", encoding="utf-8")
        return ["graph-report", "--config", str(config_path)], (tmp_path,)

    config_path = _write_project(tmp_path, {"docs": "docs", "notes": "notes"})
    _write_concept(tmp_path / "docs" / "alpha.md")
    _write_concept(tmp_path / "notes" / "beta.md")
    guarded = (tmp_path, tmp_path / "docs", tmp_path / "notes", tmp_path / "fleeting")
    if scenario == "unknown-bundle":
        return (
            [
                "graph-report",
                "--config",
                str(config_path),
                "--bundle",
                "missing",
                "--output",
                str(output),
            ],
            guarded,
        )
    if scenario == "output-in-bundle":
        return (
            [
                "graph-report",
                "--config",
                str(config_path),
                "--output",
                str(tmp_path / "docs"),
            ],
            guarded,
        )
    if scenario == "output-in-fleeting":
        return (
            [
                "graph-report",
                "--config",
                str(config_path),
                "--output",
                str(tmp_path / "fleeting" / "reports"),
            ],
            guarded,
        )
    if scenario == "output-at-project-root":
        return (
            [
                "graph-report",
                "--config",
                str(config_path),
                "--output",
                str(tmp_path),
            ],
            guarded,
        )
    raise AssertionError(scenario)

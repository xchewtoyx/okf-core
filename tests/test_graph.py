from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import (
    BundleConfig,
    backlinks_to,
    build_bundle_graph,
    extract_markdown_links,
    links_from,
    neighborhood,
)


def test_extract_markdown_links_ignores_code_and_images() -> None:
    links = extract_markdown_links(
        "\n".join(
            [
                "See [real](target.md) and ![image](image.md).",
                "",
                "`[inline](ignored.md)`",
                "",
                "```",
                "[fenced](ignored.md)",
                "```",
            ]
        )
    )

    assert [(link.text, link.target) for link in links] == [("real", "target.md")]


def test_graph_resolves_outbound_links_and_backlinks(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="See [B](b.md).")
    _write_concept(root / "b.md", body="Back to [A](a.md).")

    graph = build_bundle_graph(_bundle(root))

    assert [
        (link.source_concept_id, link.target_concept_id) for link in graph.links
    ] == [
        ("a", "b"),
        ("b", "a"),
    ]
    assert [link.target_concept_id for link in links_from(graph, "a")] == ["b"]
    assert [link.source_concept_id for link in backlinks_to(graph, "a")] == ["b"]


def test_graph_resolves_nested_relative_and_bundle_absolute_links(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(
        root / "topics" / "a.md",
        body="See [sibling](./b.md), [parent](../root.md), and [absolute](/root.md#intro).",
    )
    _write_concept(root / "topics" / "b.md")
    _write_concept(root / "root.md")

    graph = build_bundle_graph(_bundle(root))

    assert [
        (link.target, link.target_concept_id) for link in links_from(graph, "topics/a")
    ] == [
        ("../root.md", "root"),
        ("/root.md#intro", "root"),
        ("./b.md", "topics/b"),
    ]


def test_graph_reports_broken_internal_links(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="See [missing](missing.md).")

    graph = build_bundle_graph(_bundle(root))

    assert graph.links == ()
    assert len(graph.broken_links) == 1
    broken = graph.broken_links[0]
    assert broken.source_concept_id == "a"
    assert broken.target == "missing.md"
    assert broken.target_concept_id == "missing"


def test_graph_reports_unresolvable_markdown_paths_as_broken(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="See [outside](../outside.md).")

    graph = build_bundle_graph(_bundle(root))

    assert graph.links == ()
    assert len(graph.broken_links) == 1
    assert graph.broken_links[0].target == "../outside.md"
    assert graph.broken_links[0].target_concept_id is None


def test_graph_reports_outside_reserved_filename_as_broken(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="See [outside reserved](../index.md).")

    graph = build_bundle_graph(_bundle(root))

    assert graph.links == ()
    assert len(graph.broken_links) == 1
    assert graph.broken_links[0].target == "../index.md"
    assert graph.broken_links[0].target_concept_id is None


def test_graph_ignores_reserved_files_and_external_targets(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(
        root / "a.md",
        body="See [index](/index.md), [log](log.md), [web](https://example.com), and [anchor](#local).",
    )
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "log.md").write_text("# Log\n", encoding="utf-8")

    graph = build_bundle_graph(_bundle(root))

    assert graph.links == ()
    assert graph.broken_links == ()
    assert [concept.concept_id for concept in graph.concepts] == ["a"]


def test_neighborhood_is_depth_limited_and_bidirectional(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", body="[B](b.md)")
    _write_concept(root / "b.md", body="[C](c.md)")
    _write_concept(root / "c.md")
    graph = build_bundle_graph(_bundle(root))

    assert neighborhood(graph, "a", depth=0) == ("a",)
    assert neighborhood(graph, "a", depth=1) == ("a", "b")
    assert neighborhood(graph, "a", depth=2) == ("a", "b", "c")
    assert neighborhood(graph, "c", depth=1) == ("b", "c")


def test_neighborhood_rejects_negative_depth(tmp_path: Path) -> None:
    graph = build_bundle_graph(_bundle(tmp_path))

    with pytest.raises(ValueError, match="depth"):
        neighborhood(graph, "a", depth=-1)


def _bundle(root: Path) -> BundleConfig:
    return BundleConfig(
        name="docs",
        bundle_root=root.resolve(strict=False),
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=Path(".okf-cache"),
    )


def _write_concept(path: Path, body: str = "Body\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: concept\ntitle: {path.stem.title()}\n---\n{body}\n",
        encoding="utf-8",
        newline="\n",
    )

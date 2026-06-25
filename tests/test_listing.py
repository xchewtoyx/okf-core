"""Tests for structured bundle listings."""

from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import (
    BundleConfig,
    build_bundle_graph,
    list_concepts,
    scan_bundle,
)


def test_list_concepts_returns_valid_entries_in_stable_order(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(
        root / "b.md",
        """
type: Playbook
title: "Line one
  line two"
description: " First description "
""",
    )
    _write_concept(root / "a.md", "type: Dataset\ntitle: Alpha\n")

    listing = list_concepts(_bundle(root))

    assert [concept.concept_id for concept in listing.concepts] == ["a", "b"]
    assert listing.concepts[0].type == "Dataset"
    assert listing.concepts[0].title == "Alpha"
    assert listing.concepts[1].title == "Line one line two"
    assert listing.concepts[1].description == "First description"
    assert listing.problems == ()


def test_list_concepts_excludes_reserved_files(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "concept.md", "type: concept\ntitle: Concept\n")
    _write_concept(root / "index.md", "type: concept\ntitle: Index\n")
    _write_concept(root / "log.md", "type: concept\ntitle: Log\n")

    listing = list_concepts(_bundle(root))

    assert [concept.concept_id for concept in listing.concepts] == ["concept"]


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        ("title: Missing Type\n", "got None"),
        ("type: ''\ntitle: Blank Type\n", "got ''"),
        ("type: [concept]\ntitle: List Type\n", "got ('concept',)"),
    ],
)
def test_list_concepts_reports_invalid_type_as_problem(
    tmp_path: Path,
    frontmatter: str,
    expected: str,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "bad.md", frontmatter)

    listing = list_concepts(_bundle(root))

    assert listing.concepts == ()
    assert len(listing.problems) == 1
    assert listing.problems[0].concept_id == "bad"
    assert listing.problems[0].kind == "missing-type"
    assert expected in listing.problems[0].message


def test_list_concepts_accepts_unknown_non_empty_types(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "note.md", "type: Producer Defined\ntitle: Note\n")

    listing = list_concepts(_bundle(root))

    assert listing.concepts[0].type == "Producer Defined"
    assert listing.problems == ()


def test_list_concepts_preserves_frontmatter_and_promotes_configured_fields(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(
        root / "triage.md",
        """
type: Playbook
title: Triage
tags: [incident, data]
activity: [triage, repair]
owner: data-eng
""",
    )

    listing = list_concepts(
        _bundle(root, listing_fields=("activity", "owner", "missing"))
    )

    concept = listing.concepts[0]
    assert concept.fields == {
        "activity": ("triage", "repair"),
        "owner": "data-eng",
    }
    assert concept.frontmatter["tags"] == ("incident", "data")
    assert concept.frontmatter["activity"] == ("triage", "repair")


def test_list_concepts_without_configured_fields_uses_pure_okf_fallback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "plain.md", "type: concept\nactivity: triage\n")

    listing = list_concepts(_bundle(root))

    assert listing.concepts[0].fields == {}
    assert listing.concepts[0].frontmatter["activity"] == "triage"


def test_list_concepts_reports_scan_problems_without_aborting(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "good.md", "type: concept\n")
    bad = root / "bad.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("---\ntype: [invalid\n---\nBody\n", encoding="utf-8")

    listing = list_concepts(_bundle(root))

    assert [concept.concept_id for concept in listing.concepts] == ["good"]
    assert len(listing.problems) == 1
    assert listing.problems[0].kind == "parse-error"
    assert listing.problems[0].path == bad.resolve()


def test_list_concepts_uses_supplied_manifest(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\n")
    bundle = _bundle(root)
    manifest = scan_bundle(bundle)

    listing = list_concepts(bundle, manifest=manifest)

    assert [concept.concept_id for concept in listing.concepts] == ["a"]


def test_list_concepts_populates_graph_counts_when_graph_is_supplied(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\n", body="See [B](b.md).\n")
    _write_concept(root / "b.md", "type: concept\n", body="See [A](a.md).\n")
    _write_concept(root / "c.md", "type: concept\n", body="See [A](a.md).\n")
    bundle = _bundle(root)
    manifest = scan_bundle(bundle)
    graph = build_bundle_graph(bundle, manifest=manifest)

    listing = list_concepts(bundle, manifest=manifest, graph=graph)

    by_id = {concept.concept_id: concept for concept in listing.concepts}
    assert by_id["a"].outbound_link_count == 1
    assert by_id["a"].inbound_link_count == 2
    assert by_id["b"].outbound_link_count == 1
    assert by_id["b"].inbound_link_count == 1
    assert by_id["c"].outbound_link_count == 1
    assert by_id["c"].inbound_link_count == 0


def test_list_concepts_omits_graph_counts_by_default(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\n")

    listing = list_concepts(_bundle(root))

    assert listing.concepts[0].outbound_link_count is None
    assert listing.concepts[0].inbound_link_count is None


def test_list_concepts_populates_content_when_with_content_is_true(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\ntitle: Alpha\n", body="Hello World\n")

    listing = list_concepts(_bundle(root), with_content=True)

    assert len(listing.concepts) == 1
    assert listing.concepts[0].content == "Hello World\n"
    assert listing.problems == ()


def test_list_concepts_defaults_content_to_none(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\n", body="Hello World\n")

    listing = list_concepts(_bundle(root))

    assert len(listing.concepts) == 1
    assert listing.concepts[0].content is None
    assert listing.problems == ()


def test_list_concepts_reports_content_read_error_as_problem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\n")
    bundle = _bundle(root)
    manifest = scan_bundle(bundle)

    # Clear the content cache and body cache to force a disk read during listing
    object.__setattr__(manifest.concepts[0], "_content_cache", None)
    object.__setattr__(manifest.concepts[0], "_body_cache", None)

    def mock_read_bytes(*args: object, **kwargs: object) -> bytes:
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "read_bytes", mock_read_bytes)

    listing = list_concepts(bundle, manifest=manifest, with_content=True)

    assert listing.concepts == ()
    assert len(listing.problems) == 1
    assert listing.problems[0].kind == "read-error"
    assert "Permission denied" in listing.problems[0].message


def test_list_concepts_reports_content_parse_error_as_problem(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\n")
    bundle = _bundle(root)
    manifest = scan_bundle(bundle)

    # Force a parse error on read by writing unterminated frontmatter to the file
    # and clearing the content and body caches so list_concepts reads the modified file from disk
    (root / "a.md").write_text("---\ntype: concept\ninvalid\n", encoding="utf-8")
    object.__setattr__(manifest.concepts[0], "_content_cache", None)
    object.__setattr__(manifest.concepts[0], "_body_cache", None)

    listing = list_concepts(bundle, manifest=manifest, with_content=True)

    assert listing.concepts == ()
    assert len(listing.problems) == 1
    assert listing.problems[0].kind == "parse-error"
    assert "Unterminated YAML frontmatter" in listing.problems[0].message


def _bundle(root: Path, *, listing_fields: tuple[str, ...] = ()) -> BundleConfig:
    return BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=root / ".cache",
        listing_fields=listing_fields,
    )


def _write_concept(path: Path, frontmatter: str, *, body: str = "Body\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")

"""Tests for deterministic seed-based context pack assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import BundleConfig, build_bundle_graph
from okf_core.context import ContextPack, build_context_pack


def test_seed_concept_context_pack(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha")
    bundle = _bundle(root)

    pack = build_context_pack(bundle, ["a"])

    assert pack.seeds == ("a",)
    assert len(pack.entries) == 1
    entry = pack.entries[0]
    assert entry.concept_id == "a"
    assert entry.title == "Alpha"
    assert entry.selection_reason == "seed"
    assert entry.graph_distance == 0
    assert entry.char_count == len(entry.content)
    assert pack.omitted_concept_ids == ()
    assert pack.problems == ()


def test_seed_content_matches_file(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha")
    expected = (root / "a.md").read_text(encoding="utf-8")

    pack = build_context_pack(_bundle(root), ["a"])

    assert pack.entries[0].content == expected


def test_outbound_graph_expansion(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha", body="See [B](b.md).\n")
    _write_concept(root / "b.md", title="Beta")

    pack = build_context_pack(_bundle(root), ["a"], depth=1, direction="outbound")

    ids = [e.concept_id for e in pack.entries]
    assert ids == ["a", "b"]
    assert pack.entries[0].selection_reason == "seed"
    assert pack.entries[1].selection_reason == "outbound-link"
    assert pack.entries[1].graph_distance == 1


def test_backlink_graph_expansion(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha", body="See [B](b.md).\n")
    _write_concept(root / "b.md", title="Beta")

    pack = build_context_pack(_bundle(root), ["b"], depth=1, direction="inbound")

    ids = [e.concept_id for e in pack.entries]
    assert ids == ["b", "a"]
    assert pack.entries[0].selection_reason == "seed"
    assert pack.entries[1].selection_reason == "backlink"
    assert pack.entries[1].graph_distance == 1


def test_direction_both_includes_outbound_and_backlink(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "center.md", title="Center", body="See [Out](out.md).\n")
    _write_concept(root / "out.md", title="Out")
    _write_concept(
        root / "incoming.md", title="Incoming", body="See [center](center.md).\n"
    )

    pack = build_context_pack(_bundle(root), ["center"], depth=1, direction="both")

    ids = [e.concept_id for e in pack.entries]
    assert "center" in ids
    assert "out" in ids
    assert "incoming" in ids
    assert pack.entries[0].concept_id == "center"


def test_direction_outbound_excludes_backlinks(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "center.md", title="Center", body="See [Out](out.md).\n")
    _write_concept(root / "out.md", title="Out")
    _write_concept(
        root / "incoming.md", title="Incoming", body="See [center](center.md).\n"
    )

    pack = build_context_pack(_bundle(root), ["center"], depth=1, direction="outbound")

    ids = [e.concept_id for e in pack.entries]
    assert "center" in ids
    assert "out" in ids
    assert "incoming" not in ids


def test_direction_inbound_excludes_outbound(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "center.md", title="Center", body="See [Out](out.md).\n")
    _write_concept(root / "out.md", title="Out")
    _write_concept(
        root / "incoming.md", title="Incoming", body="See [center](center.md).\n"
    )

    pack = build_context_pack(_bundle(root), ["center"], depth=1, direction="inbound")

    ids = [e.concept_id for e in pack.entries]
    assert "center" in ids
    assert "out" not in ids
    assert "incoming" in ids


def test_budget_enforcement_omits_entries_that_exceed_budget(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    # a.md links to b.md; both are seeds
    _write_concept(root / "a.md", title="Alpha", body="See [B](b.md).\n")
    _write_concept(root / "b.md", title="Beta")
    bundle = _bundle(root)

    a_size = len((root / "a.md").read_text(encoding="utf-8"))
    # Budget fits only a.md
    pack = build_context_pack(bundle, ["a", "b"], budget_chars=a_size)

    assert [e.concept_id for e in pack.entries] == ["a"]
    assert pack.omitted_concept_ids == ("b",)


def test_budget_zero_omits_all(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha")

    pack = build_context_pack(_bundle(root), ["a"], budget_chars=0)

    assert pack.entries == ()
    assert pack.omitted_concept_ids == ("a",)


def test_budget_none_includes_all(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha", body="See [B](b.md).\n")
    _write_concept(root / "b.md", title="Beta")

    pack = build_context_pack(_bundle(root), ["a"], depth=1, budget_chars=None)

    assert len(pack.entries) == 2
    assert pack.omitted_concept_ids == ()


def test_provenance_fields_selection_reason_and_distance(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "seed.md", title="Seed", body="[Hop1](hop1.md)\n")
    _write_concept(root / "hop1.md", title="Hop1", body="[Hop2](hop2.md)\n")
    _write_concept(root / "hop2.md", title="Hop2")

    pack = build_context_pack(_bundle(root), ["seed"], depth=2, direction="outbound")

    by_id = {e.concept_id: e for e in pack.entries}
    assert by_id["seed"].selection_reason == "seed"
    assert by_id["seed"].graph_distance == 0
    assert by_id["hop1"].selection_reason == "outbound-link"
    assert by_id["hop1"].graph_distance == 1
    assert by_id["hop2"].selection_reason == "outbound-link"
    assert by_id["hop2"].graph_distance == 2


def test_stable_ordering_seeds_before_expanded_same_result_on_repeat(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "s1.md", title="S1", body="[A](a.md)\n")
    _write_concept(root / "s2.md", title="S2", body="[B](b.md)\n")
    _write_concept(root / "a.md", title="A")
    _write_concept(root / "b.md", title="B")
    bundle = _bundle(root)

    pack1 = build_context_pack(bundle, ["s1", "s2"], depth=1, direction="outbound")
    pack2 = build_context_pack(bundle, ["s1", "s2"], depth=1, direction="outbound")

    assert [e.concept_id for e in pack1.entries] == [
        e.concept_id for e in pack2.entries
    ]
    assert pack1.entries[0].concept_id == "s1"
    assert pack1.entries[1].concept_id == "s2"


def test_seeds_appear_before_graph_expanded_entries(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "seed.md", title="Seed", body="[Neighbor](neighbor.md)\n")
    _write_concept(root / "neighbor.md", title="Neighbor")

    pack = build_context_pack(_bundle(root), ["seed"], depth=1)

    assert pack.entries[0].concept_id == "seed"
    assert pack.entries[1].concept_id == "neighbor"
    assert pack.entries[0].selection_reason == "seed"


def test_unknown_seed_reported_as_problem(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha")

    pack = build_context_pack(_bundle(root), ["a", "missing"])

    assert len(pack.entries) == 1
    assert pack.entries[0].concept_id == "a"
    assert len(pack.problems) == 1
    assert pack.problems[0].kind == "unknown-seed"
    assert pack.problems[0].concept_id == "missing"


def test_all_unknown_seeds_returns_empty_pack(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir(parents=True, exist_ok=True)

    pack = build_context_pack(_bundle(root), ["missing"])

    assert pack.entries == ()
    assert pack.seeds == ()
    assert len(pack.problems) == 1
    assert pack.problems[0].kind == "unknown-seed"


def test_duplicate_seeds_are_deduplicated(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha")

    pack = build_context_pack(_bundle(root), ["a", "a"])

    assert pack.seeds == ("a",)
    assert len(pack.entries) == 1


def test_graph_expanded_concept_already_in_seeds_not_duplicated(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha", body="[B](b.md)\n")
    _write_concept(root / "b.md", title="Beta")

    # Both a and b are seeds; b is also reachable from a
    pack = build_context_pack(_bundle(root), ["a", "b"], depth=1, direction="outbound")

    ids = [e.concept_id for e in pack.entries]
    assert ids.count("a") == 1
    assert ids.count("b") == 1
    # b remains a seed (distance=0)
    by_id = {e.concept_id: e for e in pack.entries}
    assert by_id["b"].selection_reason == "seed"
    assert by_id["b"].graph_distance == 0


def test_depth_zero_returns_only_seeds(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha", body="[B](b.md)\n")
    _write_concept(root / "b.md", title="Beta")

    pack = build_context_pack(_bundle(root), ["a"], depth=0)

    assert [e.concept_id for e in pack.entries] == ["a"]


def test_negative_depth_raises_value_error(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="depth"):
        build_context_pack(_bundle(root), [], depth=-1)


def test_invalid_direction_raises_value_error(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="direction"):
        build_context_pack(_bundle(root), [], direction="sideways")  # type: ignore[arg-type]


def test_accepts_prebuilt_graph(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha")
    bundle = _bundle(root)
    graph = build_bundle_graph(bundle)

    pack = build_context_pack(bundle, ["a"], graph=graph)

    assert len(pack.entries) == 1
    assert pack.entries[0].concept_id == "a"


def test_bundle_name_propagated(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha")

    pack = build_context_pack(_bundle(root), ["a"])

    assert pack.bundle_name == "docs"


def test_multiline_title_normalized_to_single_line(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "a.md"
    path.write_text(
        "---\ntype: concept\ntitle: |\n  Foo\n  Bar\n---\nBody.\n", encoding="utf-8"
    )

    pack = build_context_pack(_bundle(root), ["a"])

    assert pack.entries[0].title == "Foo Bar"


def test_unicode_decode_error_concept_in_omitted_and_problems(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "seed.md", title="Seed", body="[Bad](bad.md)\n")
    _write_concept(root / "bad.md", title="Bad")
    bundle = _bundle(root)
    graph = build_bundle_graph(bundle)
    # Overwrite with non-UTF-8 bytes after graph is built
    (root / "bad.md").write_bytes(b"\xff\xfe invalid utf-8")

    pack = build_context_pack(
        bundle, ["seed"], depth=1, direction="outbound", graph=graph
    )

    assert "bad" in pack.omitted_concept_ids
    assert any(p.kind == "read-error" and p.concept_id == "bad" for p in pack.problems)


def test_read_error_concept_in_omitted_and_problems(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "seed.md", title="Seed", body="[Missing](missing.md)\n")
    _write_concept(root / "missing.md", title="Missing")
    bundle = _bundle(root)

    # Build graph while both files exist, then remove missing.md so the read fails
    graph = build_bundle_graph(bundle)
    (root / "missing.md").unlink()

    pack = build_context_pack(
        bundle, ["seed"], depth=1, direction="outbound", graph=graph
    )

    assert "missing" in pack.omitted_concept_ids
    assert any(
        p.kind == "read-error" and p.concept_id == "missing" for p in pack.problems
    )


def test_budget_stops_at_first_over_budget_entry(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    # stable order: a (seed), b (dist=1), c (dist=1); b is large, c is small
    _write_concept(root / "a.md", title="Alpha", body="[B](b.md) [C](c.md)\n")
    _write_concept(root / "b.md", title="Beta", body="X" * 200 + "\n")
    _write_concept(root / "c.md", title="Gamma")
    bundle = _bundle(root)

    a_size = len((root / "a.md").read_text(encoding="utf-8"))
    b_size = len((root / "b.md").read_text(encoding="utf-8"))
    # Budget fits a but not b; c would fit individually (knapsack) but prefix stops at b
    pack = build_context_pack(
        bundle, ["a"], depth=1, direction="outbound", budget_chars=a_size + b_size - 1
    )

    assert [e.concept_id for e in pack.entries] == ["a"]
    assert "b" in pack.omitted_concept_ids
    assert "c" in pack.omitted_concept_ids


def _bundle(root: Path) -> BundleConfig:
    return BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=root / ".cache",
    )


def _write_concept(
    path: Path,
    *,
    title: str = "Concept",
    body: str = "Body.\n",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: concept\ntitle: {title}\n---\n{body}",
        encoding="utf-8",
    )

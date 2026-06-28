from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import (
    BundleConfig,
    SearchConfigError,
    find_unlinked_mentions,
    UnlinkedMentionsResult,
)


def _bundle(root: Path, *, okf_cache_dir: Path | None) -> BundleConfig:
    return BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=okf_cache_dir,
    )


def _write_concept(path: Path, *, title: str, body: str = "Body.\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: concept\ntitle: {title}\n---\n{body}",
        encoding="utf-8",
    )


def test_unlinked_mention_is_suggested(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "beta.md", title="Beta", body="See Alpha for details.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    result = find_unlinked_mentions(bundle)

    assert isinstance(result, UnlinkedMentionsResult)
    assert len(result.suggestions) == 1
    s = result.suggestions[0]
    assert s.source_concept_id == "beta"
    assert s.target_concept_id == "alpha"


def test_existing_link_suppresses_suggestion(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(
        root / "beta.md",
        title="Beta",
        body="See [Alpha](alpha.md) for details.\n",
    )
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    result = find_unlinked_mentions(bundle)

    assert result.suggestions == ()


def test_no_mention_produces_no_suggestion(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "beta.md", title="Beta", body="Nothing relevant here.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    assert find_unlinked_mentions(bundle).suggestions == ()


def test_self_mention_is_not_suggested(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha", body="Alpha is a concept.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    assert find_unlinked_mentions(bundle).suggestions == ()


def test_no_cache_dir_raises(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    bundle = _bundle(root, okf_cache_dir=None)

    with pytest.raises(SearchConfigError):
        find_unlinked_mentions(bundle)


def test_title_match_in_metadata_not_suggested(tmp_path: Path) -> None:
    """A target title appearing only in another concept's title should not be suggested."""
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    # beta's title contains "Alpha" but its body does not mention it
    _write_concept(
        root / "beta.md", title="Alpha Beta", body="Nothing relevant here.\n"
    )
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    assert find_unlinked_mentions(bundle).suggestions == ()


def test_read_error_surfaces_in_problems(tmp_path: Path) -> None:
    """A missing concept file surfaces as a read-error problem, not a crash."""
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha", body="Beta is related.\n")
    _write_concept(root / "beta.md", title="Beta")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    # Build the FTS index with both concepts present, then delete one so the
    # linked_pairs read fails on the next call.
    find_unlinked_mentions(bundle)
    (root / "beta.md").unlink()
    result = find_unlinked_mentions(bundle, refresh=False)

    assert any(p.kind == "read-error" for p in result.problems)


def test_mutual_unlinked_mentions_both_suggested(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha", body="Beta is related.\n")
    _write_concept(root / "beta.md", title="Beta", body="Alpha is related.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    result = find_unlinked_mentions(bundle)

    pairs = {(s.source_concept_id, s.target_concept_id) for s in result.suggestions}
    assert ("alpha", "beta") in pairs
    assert ("beta", "alpha") in pairs

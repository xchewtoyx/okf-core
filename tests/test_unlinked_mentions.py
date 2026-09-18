from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from okf_core import (
    BundleConfig,
    SearchConfigError,
    UnlinkedMentionsResult,
    find_unlinked_mentions,
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


@pytest.mark.parametrize(
    "body",
    [
        "```text\nGraph Chat API\n```\n",
        "    Graph Chat API\n",
        "Use `Graph Chat API` for details.\n",
        "See [documentation](https://example.test/graph-chat-api).\n",
        "![diagram](https://example.test/graph-chat-api)\n",
    ],
    ids=["fenced-code", "indented-code", "inline-code", "link-href", "image-href"],
)
def test_non_prose_title_match_is_not_suggested(tmp_path: Path, body: str) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "graph-chat-api.md", title="Graph Chat API")
    _write_concept(root / "source.md", title="Source", body=body)
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    assert find_unlinked_mentions(bundle).suggestions == ()


@pytest.mark.parametrize(
    "body",
    ["Foo`ignored`Bar\n", "Foo![ignored](image.png)Bar\n"],
    ids=["inline-code", "image"],
)
def test_excluded_inline_content_preserves_token_boundaries(
    tmp_path: Path, body: str
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "foobar.md", title="FooBar")
    _write_concept(root / "source.md", title="Source", body=body)
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    assert find_unlinked_mentions(bundle).suggestions == ()


def test_displayed_link_text_remains_eligible_prose(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "graph-chat-api.md", title="Graph Chat API")
    _write_concept(
        root / "source.md",
        title="Source",
        body="See [Graph Chat API](https://example.test/docs).\n",
    )
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    result = find_unlinked_mentions(bundle)

    assert [
        (suggestion.source_concept_id, suggestion.target_concept_id)
        for suggestion in result.suggestions
    ] == [("source", "graph-chat-api")]


def test_prose_match_remains_when_code_match_is_also_present(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "graph-chat-api.md", title="Graph Chat API")
    _write_concept(
        root / "source.md",
        title="Source",
        body="`Graph Chat API`\n\nUse Graph Chat API for details.\n",
    )
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    result = find_unlinked_mentions(bundle)

    assert [
        (suggestion.source_concept_id, suggestion.target_concept_id)
        for suggestion in result.suggestions
    ] == [("source", "graph-chat-api")]
    assert "[Graph] [Chat] [API]" in result.suggestions[0].matched_text


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


def test_unreadable_cache_becomes_search_config_error(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "okf-cache.db").write_bytes(b"this is not a sqlite database\n" * 4)
    bundle = _bundle(root, okf_cache_dir=cache_dir)

    with pytest.raises(SearchConfigError, match="could not be opened"):
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


def test_no_refresh_with_no_index_yields_nothing_and_creates_no_index(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "beta.md", title="Beta", body="See Alpha for details.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    result = find_unlinked_mentions(bundle, refresh=False)

    assert result.suggestions == ()
    assert result.problems == ()
    with sqlite3.connect(tmp_path / "cache" / "okf-cache.db") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "concept_fts" not in tables


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


def test_listing_problem_surfaces_as_graph_problem(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    (root / "untyped.md").write_text(
        "---\ntitle: Untyped\n---\nAlpha is related.\n", encoding="utf-8"
    )
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    result = find_unlinked_mentions(bundle)

    problem = next(p for p in result.problems if p.concept_id == "untyped")
    assert problem.kind == "missing-type"
    assert problem.path == root / "untyped.md"
    assert all(s.source_concept_id != "untyped" for s in result.suggestions)


def test_mutual_unlinked_mentions_both_suggested(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha", body="Beta is related.\n")
    _write_concept(root / "beta.md", title="Beta", body="Alpha is related.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    result = find_unlinked_mentions(bundle)

    pairs = {(s.source_concept_id, s.target_concept_id) for s in result.suggestions}
    assert ("alpha", "beta") in pairs
    assert ("beta", "alpha") in pairs

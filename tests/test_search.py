from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from okf_core import BundleConfig, SearchConfigError, scan_bundle, search_concepts
from okf_core.search import _ensure_search_schema


def test_search_creates_fts_schema_in_existing_cache_db(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    scan_bundle(bundle)
    assert bundle.okf_cache_dir is not None
    db_path = bundle.okf_cache_dir / "okf-cache.db"

    results = search_concepts(bundle, "Alpha")

    assert db_path.is_file()
    assert [result.concept_id for result in results.results] == ["alpha"]
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
    assert "concepts" in tables
    assert "links" in tables
    assert "concept_fts" in tables
    assert not (root / ".okf-cache").exists()


def test_search_matches_title_description_fields_and_body(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(
        root / "incident.md",
        title="Incident Triage",
        description="Debug production failures",
        extra="activity: [repair, escalation]\nowner: sre\n",
        body="Run the pager checklist before rollback.\n",
    )
    bundle = _bundle(
        root,
        okf_cache_dir=tmp_path / "cache",
        listing_fields=("activity", "owner"),
    )

    by_query = {
        query: search_concepts(bundle, query).results[0]
        for query in ("Incident", "failures", "repair", "pager")
    }

    assert set(by_query) == {"Incident", "failures", "repair", "pager"}
    for result in by_query.values():
        assert result.concept_id == "incident"
        assert result.path == root / "incident.md"
        assert result.title == "Incident Triage"
        assert result.description == "Debug production failures"
        assert result.snippets


def test_search_refresh_updates_changed_documents(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    path = root / "topic.md"
    _write_concept(path, title="Alpha", body="First body\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    assert [r.concept_id for r in search_concepts(bundle, "Alpha").results] == ["topic"]

    _write_concept(path, title="Beta", body="Second body\n")

    assert search_concepts(bundle, "Alpha").results == ()
    assert [r.concept_id for r in search_concepts(bundle, "Beta").results] == ["topic"]


def test_search_refresh_removes_deleted_excluded_and_reserved_documents(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    deleted = root / "deleted.md"
    _write_concept(deleted, title="Deleted")
    _write_concept(root / "drafts" / "skip.md", title="Excluded")
    _write_concept(root / "index.md", title="Reserved")
    bundle = _bundle(
        root,
        okf_cache_dir=tmp_path / "cache",
        exclude=("drafts/**",),
    )

    assert [r.concept_id for r in search_concepts(bundle, "Deleted").results] == [
        "deleted"
    ]
    deleted.unlink()

    assert search_concepts(bundle, "Deleted").results == ()
    assert search_concepts(bundle, "Excluded").results == ()
    assert search_concepts(bundle, "Reserved").results == ()


def test_search_refresh_avoids_sql_variable_limit_for_large_bundles(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    for index in range(1100):
        _write_concept(root / f"topic-{index}.md", title=f"Topic {index}")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    results = search_concepts(bundle, "Topic", limit=1)

    assert len(results.results) == 1


def test_search_backfills_missing_fts_rows_from_cached_concepts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "cached.md", title="Cached")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    scan_bundle(bundle)
    assert search_concepts(bundle, "Cached").results
    assert bundle.okf_cache_dir is not None
    with sqlite3.connect(bundle.okf_cache_dir / "okf-cache.db") as conn:
        conn.execute("DELETE FROM concept_fts")

    assert [r.concept_id for r in search_concepts(bundle, "Cached").results] == [
        "cached"
    ]


def test_search_no_refresh_uses_current_fts_rows_only(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "topic.md", title="Alpha")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    assert search_concepts(bundle, "Alpha", refresh=False).results == ()
    assert [r.concept_id for r in search_concepts(bundle, "Alpha").results] == ["topic"]


def test_search_requires_okf_cache_dir(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "topic.md", title="Alpha")
    bundle = _bundle(root, okf_cache_dir=None)

    with pytest.raises(SearchConfigError, match="okf_cache_dir"):
        search_concepts(bundle, "Alpha")


def test_fts5_schema_error_becomes_search_config_error() -> None:
    class MissingFtsConnection:
        def execute(self, _sql: str) -> None:
            raise sqlite3.OperationalError("no such module: fts5")

    with pytest.raises(SearchConfigError, match="SQLite FTS5 is not available"):
        _ensure_search_schema(MissingFtsConnection())  # type: ignore[arg-type]


def test_unrelated_schema_operational_error_is_not_masked() -> None:
    class BrokenConnection:
        def execute(self, _sql: str) -> None:
            raise sqlite3.OperationalError("attempt to write a readonly database")

    with pytest.raises(sqlite3.OperationalError, match="readonly database"):
        _ensure_search_schema(BrokenConnection())  # type: ignore[arg-type]


def test_search_limit_and_deterministic_tiebreak(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "b.md", title="Same")
    _write_concept(root / "a.md", title="Same")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    results = search_concepts(bundle, "Same", limit=1).results

    assert [result.concept_id for result in results] == ["a"]


def _bundle(
    root: Path,
    *,
    okf_cache_dir: Path | None,
    exclude: tuple[str, ...] = (),
    listing_fields: tuple[str, ...] = (),
) -> BundleConfig:
    return BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=exclude,
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        listing_fields=listing_fields,
        okf_cache_dir=okf_cache_dir,
    )


def _write_concept(
    path: Path,
    *,
    title: str,
    description: str = "",
    extra: str = "",
    body: str = "Body\n",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    description_line = f"description: {description}\n" if description else ""
    path.write_text(
        f"---\ntype: concept\ntitle: {title}\n{description_line}{extra}---\n{body}",
        encoding="utf-8",
    )

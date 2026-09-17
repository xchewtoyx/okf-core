"""Tests for the versioned cache file: classification, init, and migration.

Every fixture here is a real SQLite file written with hand-rolled DDL, so the
tests exercise the same on-disk shapes okf-core has shipped (0.4.0 caches
without ``ctime_ns``, search-only leftovers, current files) rather than fake
connections.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from okf_core import (
    BundleConfig,
    CacheDatabase,
    CacheMigrationError,
    CacheProblem,
    CacheSchemaError,
    CacheSchemaState,
    inspect_cache,
    migrate_cache,
    open_cache,
    plan_cache_migration,
)
from okf_core.cache_db import CURRENT_SCHEMA_VERSION, cache_db_path

MIGRATE_SENTENCE = "cache schema version 0 requires migration to 1; run okf migrate-db"


# ---------------------------------------------------------------------------
# Fixture builders: one per on-disk shape okf-core has to recognise.
# ---------------------------------------------------------------------------


def _bundle(tmp_path: Path, cache_dir: Path | None) -> BundleConfig:
    return BundleConfig(
        name="docs",
        bundle_root=tmp_path / "docs",
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=cache_dir,
    )


def _db_path(bundle: BundleConfig) -> Path:
    path = cache_db_path(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_empty_file(bundle: BundleConfig) -> Path:
    path = _db_path(bundle)
    path.touch()
    return path


def _write_unrelated_table(bundle: BundleConfig) -> Path:
    path = _db_path(bundle)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY);")
    return path


def _write_legacy_cache(bundle: BundleConfig) -> Path:
    """The 0.4.0/0.4.1 shape: concepts without ctime_ns, links, no indexes."""
    path = _db_path(bundle)
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE concepts (
                concept_id TEXT PRIMARY KEY,
                stable_id TEXT,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                frontmatter TEXT NOT NULL,
                links_resolved INTEGER DEFAULT 0,
                pagerank REAL DEFAULT 0.0
            );
            """)
        conn.execute("""
            CREATE TABLE links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_concept_id TEXT NOT NULL,
                target_concept_id TEXT,
                text TEXT NOT NULL,
                target TEXT NOT NULL,
                FOREIGN KEY (source_concept_id) REFERENCES concepts(concept_id) ON DELETE CASCADE
            );
            """)
        conn.execute(
            "INSERT INTO concepts (concept_id, path, sha256, mtime_ns, size, frontmatter)"
            " VALUES ('a', 'a.md', 'abc', 1, 2, '{}');"
        )
    return path


def _write_fts_only_cache(bundle: BundleConfig) -> Path:
    """A file ``okf search`` created before the concept cache ever ran."""
    path = _db_path(bundle)
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE VIRTUAL TABLE concept_fts USING fts5(
                concept_id UNINDEXED, path UNINDEXED, title, description, fields, body
            );
            """)
    return path


def _write_current_cache(bundle: BundleConfig) -> Path:
    open_cache(bundle)
    return cache_db_path(bundle)


def _write_stamped_version(bundle: BundleConfig, version: int) -> Path:
    path = _db_path(bundle)
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {version};")
    return path


def _write_newer_cache(bundle: BundleConfig) -> Path:
    return _write_stamped_version(bundle, CURRENT_SCHEMA_VERSION + 1)


# ---------------------------------------------------------------------------
# Read-only probes.
# ---------------------------------------------------------------------------


def _user_version(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute("PRAGMA user_version;").fetchone()[0])


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table';"
            )
        }


def _indexes(path: Path) -> set[str]:
    """Named indexes only; the PRIMARY KEY autoindex is not okf-owned."""
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index';"
            )
            if not row[0].startswith("sqlite_autoindex_")
        }


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table});")}


def _journal_mode(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()


def _assert_current_shape(path: Path) -> None:
    assert _user_version(path) == CURRENT_SCHEMA_VERSION
    assert {"concepts", "links"} <= _tables(path)
    assert "concept_fts" not in _tables(path)
    assert "ctime_ns" in _columns(path, "concepts")
    assert {"idx_links_source", "idx_concepts_path"} <= _indexes(path)


# ---------------------------------------------------------------------------
# Classification.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("writer", "expected_state", "expected_version"),
    [
        (None, CacheSchemaState.ABSENT, None),
        (_write_empty_file, CacheSchemaState.UNINITIALIZED, 0),
        (_write_unrelated_table, CacheSchemaState.UNINITIALIZED, 0),
        (_write_fts_only_cache, CacheSchemaState.OUTDATED, 0),
        (_write_legacy_cache, CacheSchemaState.OUTDATED, 0),
        (_write_current_cache, CacheSchemaState.CURRENT, 1),
        (_write_newer_cache, CacheSchemaState.UNSUPPORTED_NEWER, 2),
    ],
    ids=[
        "absent",
        "empty-file",
        "unrelated-table",
        "fts-only",
        "legacy-concepts",
        "current",
        "newer",
    ],
)
def test_inspect_cache_classifies_each_on_disk_shape(
    tmp_path: Path,
    writer: Callable[[BundleConfig], Path] | None,
    expected_state: CacheSchemaState,
    expected_version: int | None,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    if writer is not None:
        writer(bundle)

    status = inspect_cache(bundle)

    assert status.state is expected_state
    assert status.found_version == expected_version
    assert status.current_version == CURRENT_SCHEMA_VERSION
    assert status.db_path == tmp_path / "cache" / "okf-cache.db"


def test_stamped_version_is_trusted_over_table_shape(tmp_path: Path) -> None:
    """user_version is the only stamp: a version-1 file is CURRENT even if empty."""
    bundle = _bundle(tmp_path, tmp_path / "cache")
    _write_stamped_version(bundle, CURRENT_SCHEMA_VERSION)

    assert inspect_cache(bundle).state is CacheSchemaState.CURRENT


@pytest.mark.parametrize(
    ("writer", "kind", "fragment"),
    [
        (_write_legacy_cache, "cache-needs-migration", MIGRATE_SENTENCE),
        (_write_fts_only_cache, "cache-needs-migration", MIGRATE_SENTENCE),
        (_write_newer_cache, "cache-unsupported-version", "newer than the supported"),
    ],
    ids=["legacy", "fts-only", "newer"],
)
def test_status_problem_for_refused_states(
    tmp_path: Path,
    writer: Callable[[BundleConfig], Path],
    kind: str,
    fragment: str,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = writer(bundle)

    problem = inspect_cache(bundle).problem

    assert problem is not None
    assert problem == CacheProblem(db_path=path, kind=kind, message=problem.message)
    assert fragment in problem.message


@pytest.mark.parametrize(
    "writer",
    [None, _write_empty_file, _write_current_cache],
    ids=["absent", "empty-file", "current"],
)
def test_status_problem_is_none_for_usable_states(
    tmp_path: Path, writer: Callable[[BundleConfig], Path] | None
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    if writer is not None:
        writer(bundle)

    assert inspect_cache(bundle).problem is None


def test_inspect_cache_does_not_touch_the_file(tmp_path: Path) -> None:
    """Inspection is a pure read: no journal-mode switch, no WAL sidecar, no stamp."""
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _write_legacy_cache(bundle)
    assert _journal_mode(path) == "delete"

    inspect_cache(bundle)
    plan_cache_migration(bundle)

    assert _journal_mode(path) == "delete"
    assert not path.with_name(path.name + "-wal").exists()
    assert _user_version(path) == 0


@pytest.mark.parametrize(
    "operation",
    [inspect_cache, open_cache, plan_cache_migration, migrate_cache],
    ids=["inspect", "open", "plan", "migrate"],
)
def test_operations_require_okf_cache_dir(
    tmp_path: Path, operation: Callable[[BundleConfig], object]
) -> None:
    bundle = _bundle(tmp_path, None)

    with pytest.raises(ValueError, match="okf_cache_dir"):
        operation(bundle)


def test_unreadable_file_propagates_from_inspect_and_wraps_in_plan(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _db_path(bundle)
    path.write_bytes(b"this is not a sqlite database, not even close to one\n" * 4)

    with pytest.raises(sqlite3.DatabaseError):
        inspect_cache(bundle)
    with pytest.raises(CacheMigrationError, match="could not be read"):
        plan_cache_migration(bundle)


# ---------------------------------------------------------------------------
# Ordinary open: initialise what is empty, refuse what is stale, read what is
# current.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "writer",
    [None, _write_empty_file, _write_unrelated_table],
    ids=["absent", "empty-file", "unrelated-table"],
)
def test_open_cache_initializes_missing_or_empty_file_at_current_version(
    tmp_path: Path, writer: Callable[[BundleConfig], Path] | None
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache" / "nested")
    if writer is not None:
        writer(bundle)

    db = open_cache(bundle)

    assert db == CacheDatabase(path=tmp_path / "cache" / "nested" / "okf-cache.db")
    _assert_current_shape(db.path)
    assert inspect_cache(bundle).state is CacheSchemaState.CURRENT


@pytest.mark.parametrize(
    "writer", [_write_legacy_cache, _write_fts_only_cache], ids=["legacy", "fts-only"]
)
def test_open_cache_refuses_outdated_file_and_leaves_it_alone(
    tmp_path: Path, writer: Callable[[BundleConfig], Path]
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = writer(bundle)
    tables_before = _tables(path)
    columns_before = _columns(path, "concepts")

    with pytest.raises(CacheSchemaError, match=MIGRATE_SENTENCE) as excinfo:
        open_cache(bundle)

    assert excinfo.value.problem.kind == "cache-needs-migration"
    assert excinfo.value.problem.db_path == path
    assert str(excinfo.value) == MIGRATE_SENTENCE
    assert _tables(path) == tables_before
    assert _columns(path, "concepts") == columns_before
    assert _indexes(path) == set()
    assert _user_version(path) == 0


def test_open_cache_refuses_newer_file(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _write_newer_cache(bundle)

    with pytest.raises(CacheSchemaError, match="upgrade okf-core") as excinfo:
        open_cache(bundle)

    assert excinfo.value.problem.kind == "cache-unsupported-version"
    assert _user_version(path) == CURRENT_SCHEMA_VERSION + 1
    assert _tables(path) == set()


def test_open_cache_current_file_runs_no_ddl(tmp_path: Path) -> None:
    """A current file is trusted as-is: a dropped index is not put back.

    This is the deliberate inverse of the pre-versioning behaviour, where every
    open probed the shape and repaired it. Repair is now migrate-db's job.
    """
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _write_current_cache(bundle)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP INDEX idx_concepts_path;")

    open_cache(bundle)

    assert "idx_concepts_path" not in _indexes(path)
    assert _user_version(path) == CURRENT_SCHEMA_VERSION


def test_open_cache_current_file_needs_no_write_lock(tmp_path: Path) -> None:
    """Opening a current file must succeed while another writer holds the lock.

    The open is a PRAGMA user_version read; if it took BEGIN IMMEDIATE it would
    sit behind the blocker for the full busy timeout.
    """
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _write_current_cache(bundle)
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("PRAGMA busy_timeout = 0;")
    blocker.execute("BEGIN IMMEDIATE;")
    result: dict[str, object] = {}

    def open_pass() -> None:
        try:
            result["db"] = open_cache(bundle)
        except Exception as exc:  # noqa: BLE001 - collect failure # pragma: no cover
            result["error"] = exc

    worker = threading.Thread(target=open_pass)
    worker.start()
    worker.join(timeout=10)
    still_running = worker.is_alive()
    blocker.execute("COMMIT;")
    blocker.close()
    worker.join()

    assert not still_running, "open_cache blocked on the write lock"
    assert result.get("db") == CacheDatabase(path=path), result.get("error")


def test_concurrent_first_time_open_converges(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    results: list[CacheDatabase] = []
    errors: list[Exception] = []
    lock = threading.Lock()
    start = threading.Barrier(12)

    def open_pass() -> None:
        try:
            start.wait()
            db = open_cache(bundle)
        except Exception as exc:  # noqa: BLE001 - collect failure # pragma: no cover
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(db)

    workers = [threading.Thread(target=open_pass) for _ in range(12)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert errors == []
    assert len(results) == 12
    _assert_current_shape(cache_db_path(bundle))


def test_connect_applies_shared_connection_settings(tmp_path: Path) -> None:
    """The one connection helper carries every PRAGMA the cache relies on."""
    bundle = _bundle(tmp_path, tmp_path / "cache")
    conn = open_cache(bundle).connect()
    try:
        assert conn.isolation_level is None
        assert conn.execute("PRAGMA busy_timeout;").fetchone()[0] == 30000
        assert conn.execute("PRAGMA journal_mode;").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous;").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_keys;").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration: plan writes nothing; apply is a version-to-version step that is
# safe to repeat.
# ---------------------------------------------------------------------------


def test_plan_cache_migration_reports_legacy_upgrade_without_writing(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _write_legacy_cache(bundle)

    plan = plan_cache_migration(bundle)

    assert plan.status.state is CacheSchemaState.OUTDATED
    assert plan.would_change is True
    assert plan.would_initialize is False
    assert _user_version(path) == 0
    assert "ctime_ns" not in _columns(path, "concepts")
    assert _indexes(path) == set()


def test_plan_cache_migration_reports_initialization_without_creating(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")

    plan = plan_cache_migration(bundle)

    assert plan.status.state is CacheSchemaState.ABSENT
    assert plan.would_change is True
    assert plan.would_initialize is True
    assert not cache_db_path(bundle).exists()


def test_plan_cache_migration_current_file_would_not_change(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    _write_current_cache(bundle)

    plan = plan_cache_migration(bundle)

    assert plan.would_change is False
    assert plan.would_initialize is False


def test_plan_cache_migration_refuses_newer_file(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    _write_newer_cache(bundle)

    with pytest.raises(CacheMigrationError, match="newer than the supported"):
        plan_cache_migration(bundle)


def test_migrate_cache_upgrades_legacy_file_and_keeps_rows(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _write_legacy_cache(bundle)

    result = migrate_cache(bundle)

    assert result.changed is True
    assert result.initialized is False
    assert result.applied_versions == (1,)
    assert result.status.state is CacheSchemaState.OUTDATED
    _assert_current_shape(path)
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT concept_id, ctime_ns FROM concepts;").fetchall()
    assert rows == [("a", 0)]
    assert inspect_cache(bundle).state is CacheSchemaState.CURRENT
    assert open_cache(bundle) == CacheDatabase(path=path)


def test_migrate_cache_upgrades_fts_only_file_and_keeps_fts(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _write_fts_only_cache(bundle)

    result = migrate_cache(bundle)

    assert result.applied_versions == (1,)
    assert _user_version(path) == CURRENT_SCHEMA_VERSION
    assert {"concepts", "links", "concept_fts"} <= _tables(path)
    assert {"idx_links_source", "idx_concepts_path"} <= _indexes(path)


def test_migrate_cache_initializes_missing_file(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache" / "deeper")

    result = migrate_cache(bundle)

    assert result.changed is True
    assert result.initialized is True
    assert result.status.state is CacheSchemaState.ABSENT
    _assert_current_shape(cache_db_path(bundle))


def test_migrate_cache_twice_is_a_no_op(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _write_legacy_cache(bundle)
    migrate_cache(bundle)
    mtime_after_first = path.stat().st_mtime_ns

    second = migrate_cache(bundle)

    assert second.changed is False
    assert second.initialized is False
    assert second.applied_versions == ()
    assert second.status.state is CacheSchemaState.CURRENT
    assert path.stat().st_mtime_ns == mtime_after_first


def test_migrate_cache_refuses_newer_file_and_leaves_it_alone(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _write_newer_cache(bundle)

    with pytest.raises(CacheMigrationError, match="newer than the supported"):
        migrate_cache(bundle)

    assert _user_version(path) == CURRENT_SCHEMA_VERSION + 1
    assert _tables(path) == set()


def test_migrate_cache_surfaces_unrelated_operational_error_from_alter(
    tmp_path: Path,
) -> None:
    """The 0->1 ALTER must not swallow errors other than its own success.

    A view named ``concepts`` slips past ``CREATE TABLE IF NOT EXISTS`` and
    reports no ``ctime_ns`` column, so the step reaches its ALTER and SQLite
    rejects it. That failure has to surface (wrapped, with the SQLite error as
    its cause) and the whole step has to roll back.
    """
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _db_path(bundle)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE VIEW concepts AS SELECT 'a' AS concept_id;")

    with pytest.raises(CacheMigrationError, match="(?i)cannot add a column") as excinfo:
        migrate_cache(bundle)

    assert isinstance(excinfo.value.__cause__, sqlite3.OperationalError)
    assert _user_version(path) == 0
    assert "links" not in _tables(path)


def test_migrate_cache_rollback_leaves_legacy_file_openable_by_migrate_again(
    tmp_path: Path,
) -> None:
    """A failed step leaves the file exactly as found, so a fixed run can retry."""
    bundle = _bundle(tmp_path, tmp_path / "cache")
    path = _db_path(bundle)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE VIEW concepts AS SELECT 'a' AS concept_id;")
    with pytest.raises(CacheMigrationError):
        migrate_cache(bundle)

    with sqlite3.connect(path) as conn:
        conn.execute("DROP VIEW concepts;")
    result = migrate_cache(bundle)

    assert result.applied_versions == (1,)
    _assert_current_shape(path)

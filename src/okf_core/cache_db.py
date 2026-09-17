"""The ``okf-cache.db`` file: location, connections, schema version, migration.

This is the one module that knows where a bundle's SQLite cache lives, how a
connection to it is configured, which schema version it is at, how a missing
file is created, and how an older file is upgraded. It imports only the
standard library and :mod:`okf_core.config`, so every cache consumer
(:mod:`okf_core.cache`, :mod:`okf_core.search`, :mod:`okf_core.graph`) can
open the file through it without an import cycle.

The schema version is ``PRAGMA user_version``. Version ``0`` is what every
cache written before versioning reports, so a version-``0`` file is classified
by its tables: one that already carries an okf-owned table is OUTDATED and is
only ever written by :func:`migrate_cache`; one with no okf tables is
UNINITIALIZED and is created at the current version like a missing file.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from okf_core.config import BundleConfig

DB_FILENAME: Final = "okf-cache.db"

# Held long enough to ride out a concurrent writer's flush (a short burst, not
# a whole scan) without surfacing "database is locked".
_BUSY_TIMEOUT_MS: Final = 30_000


def _migrate_0_to_1(conn: sqlite3.Connection) -> None:
    """Bring an unversioned (or empty) file to the version 1 shape.

    Pre-versioning caches exist in two shapes: 0.4.0/0.4.1 files without
    ``ctime_ns`` and 0.4.2+ files that already match version 1. ``IF NOT
    EXISTS`` covers the tables and indexes; the column is the one thing an
    existing table has to be altered for, checked under the write lock the
    runner already holds so no concurrent step can add it in between.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS concepts (
            concept_id TEXT PRIMARY KEY,
            stable_id TEXT,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            frontmatter TEXT NOT NULL,
            links_resolved INTEGER DEFAULT 0,
            pagerank REAL DEFAULT 0.0,
            ctime_ns INTEGER DEFAULT 0 NOT NULL
        );
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_concept_id TEXT NOT NULL,
            target_concept_id TEXT,
            text TEXT NOT NULL,
            target TEXT NOT NULL,
            FOREIGN KEY (source_concept_id) REFERENCES concepts(concept_id) ON DELETE CASCADE
        );
        """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(concepts);")}
    if "ctime_ns" not in columns:
        conn.execute(
            "ALTER TABLE concepts ADD COLUMN ctime_ns INTEGER DEFAULT 0 NOT NULL;"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_concept_id);"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_concepts_path ON concepts(path);")


# MIGRATIONS[n] upgrades schema version n to n + 1. The runner stamps
# user_version after each step, so a step cannot forget to. concept_fts is
# deliberately absent: it is a derived search index owned by search.py and
# rebuilt from the bundle on every refresh, not versioned state.
MIGRATIONS: Final[tuple[Callable[[sqlite3.Connection], None], ...]] = (_migrate_0_to_1,)

CURRENT_SCHEMA_VERSION: Final[int] = len(MIGRATIONS)


class CacheSchemaState(str, Enum):
    """What :func:`inspect_cache` found, as a closed set of outcomes."""

    ABSENT = "absent"
    UNINITIALIZED = "uninitialized"
    CURRENT = "current"
    OUTDATED = "outdated"
    UNSUPPORTED_NEWER = "unsupported-newer"


@dataclass(frozen=True)
class CacheProblem:
    """A bundle-level report that the cache did not take part in a run.

    Deliberately not a ``ManifestProblem``: ``validate_bundle`` turns every
    manifest problem into an error-severity finding, and a stale cache must
    never make a bundle of valid documents fail validation.
    """

    db_path: Path
    kind: str
    message: str


# Templates for the two states an ordinary command refuses to use. The
# OUTDATED sentence is the one the CLI, SearchConfigError, and cache_problems
# all report, so it lives in exactly one place.
_REFUSALS: Final[dict[CacheSchemaState, tuple[str, str]]] = {
    CacheSchemaState.OUTDATED: (
        "cache-needs-migration",
        (
            "cache schema version {found} requires migration to {current}; "
            "run okf migrate-db"
        ),
    ),
    CacheSchemaState.UNSUPPORTED_NEWER: (
        "cache-unsupported-version",
        (
            "cache schema version {found} is newer than the supported version "
            "{current}; upgrade okf-core"
        ),
    ),
}


def _refusal(db_path: Path, state: CacheSchemaState, found: int) -> CacheProblem:
    kind, template = _REFUSALS[state]
    return CacheProblem(
        db_path=db_path,
        kind=kind,
        message=template.format(found=found, current=CURRENT_SCHEMA_VERSION),
    )


@dataclass(frozen=True)
class CacheSchemaStatus:
    """The classification of one cache file at one moment.

    ``found_version`` is ``None`` only for ABSENT; every other state carries
    the integer read from ``PRAGMA user_version``.
    """

    db_path: Path
    state: CacheSchemaState
    found_version: int | None
    current_version: int

    @property
    def problem(self) -> CacheProblem | None:
        """The problem an OUTDATED or UNSUPPORTED_NEWER file reports, else None."""
        if self.state not in _REFUSALS or self.found_version is None:
            return None
        return _refusal(self.db_path, self.state, self.found_version)


class CacheSchemaError(Exception):
    """Raised by :func:`open_cache` for a file an ordinary command must not use."""

    def __init__(self, problem: CacheProblem) -> None:
        super().__init__(problem.message)
        self.problem = problem


class CacheMigrationError(Exception):
    """Raised when the cache cannot be planned or brought to the current version."""


@dataclass(frozen=True)
class CacheDatabase:
    """A cache file verified to be at the current schema version.

    Only :func:`open_cache` constructs one. Holding it is the proof that the
    versioned schema is current, so every reader and writer downstream runs
    plain SQL with no shape probing, no ``IF NOT EXISTS``, and no ``ALTER``.
    """

    path: Path

    def connect(self) -> sqlite3.Connection:
        """Return a configured autocommit connection to the file."""
        return _connect_configured(self.path)


@dataclass(frozen=True)
class CacheMigrationPlan:
    """Read-only result of :func:`plan_cache_migration`. Never touches the file."""

    status: CacheSchemaStatus

    @property
    def would_initialize(self) -> bool:
        """True when applying would create the schema rather than upgrade one."""
        return self.status.state in (
            CacheSchemaState.ABSENT,
            CacheSchemaState.UNINITIALIZED,
        )

    @property
    def would_change(self) -> bool:
        """True when applying would write anything."""
        return self.status.state is not CacheSchemaState.CURRENT


@dataclass(frozen=True)
class CacheMigrationResult:
    """What :func:`migrate_cache` did to a file, whether or not that was anything."""

    status: CacheSchemaStatus
    applied_versions: tuple[int, ...] = ()

    @property
    def changed(self) -> bool:
        """True when at least one version step was applied and stamped."""
        return bool(self.applied_versions)

    @property
    def initialized(self) -> bool:
        """True when the schema was created from nothing rather than upgraded."""
        return self.changed and self.status.state in (
            CacheSchemaState.ABSENT,
            CacheSchemaState.UNINITIALIZED,
        )


def cache_db_path(bundle: BundleConfig) -> Path:
    """Return the bundle's cache file path; raises ValueError without okf_cache_dir."""
    if bundle.okf_cache_dir is None:
        raise ValueError("okf_cache_dir is not configured")
    return bundle.okf_cache_dir / DB_FILENAME


def inspect_cache(bundle: BundleConfig) -> CacheSchemaStatus:
    """Classify the bundle's cache file without creating or writing anything.

    Raises ``ValueError`` when the bundle has no ``okf_cache_dir``. Any
    ``sqlite3.Error`` from reading an unreadable or corrupt file propagates.
    """
    db_path = cache_db_path(bundle)
    if not db_path.exists():
        return CacheSchemaStatus(
            db_path, CacheSchemaState.ABSENT, None, CURRENT_SCHEMA_VERSION
        )
    conn = _connect(db_path)
    try:
        # A deferred (read) transaction gives the version and table reads one
        # snapshot. Without it a concurrent initializer can commit between the
        # two, and "version 0 with a concepts table" would misread as OUTDATED.
        conn.execute("BEGIN;")
        try:
            state, found = _classify(conn)
        finally:
            conn.execute("COMMIT;")
    finally:
        conn.close()
    return CacheSchemaStatus(db_path, state, found, CURRENT_SCHEMA_VERSION)


def open_cache(bundle: BundleConfig) -> CacheDatabase:
    """Return the bundle's cache at the current schema version.

    An ABSENT or UNINITIALIZED file is created at the current version under
    one ``BEGIN IMMEDIATE`` (re-classified inside the lock, so concurrent
    first-time openers converge). A CURRENT file is opened with a
    ``PRAGMA user_version`` read only: no DDL and no write lock. OUTDATED and
    UNSUPPORTED_NEWER raise :class:`CacheSchemaError`; an ordinary command
    never migrates an existing file. The lock is released before returning,
    so a caller may run a bundle scan afterwards without deadlocking.
    """
    status = inspect_cache(bundle)
    if status.state is CacheSchemaState.CURRENT:
        return CacheDatabase(status.db_path)
    problem = status.problem
    if problem is not None:
        raise CacheSchemaError(problem)
    status.db_path.parent.mkdir(parents=True, exist_ok=True)
    _upgrade_locked(status.db_path, migrate_outdated=False)
    return CacheDatabase(status.db_path)


def plan_cache_migration(bundle: BundleConfig) -> CacheMigrationPlan:
    """Plan the bundle's cache migration without writing or creating anything.

    Raises :class:`CacheMigrationError` for a file newer than this okf-core
    supports (a downgrade is not a migration) or one that cannot be read, and
    ``ValueError`` when the bundle has no ``okf_cache_dir``.
    """
    try:
        status = inspect_cache(bundle)
    except sqlite3.Error as exc:
        raise CacheMigrationError(f"cache database could not be read: {exc}") from exc
    if status.state is CacheSchemaState.UNSUPPORTED_NEWER:
        raise CacheMigrationError(
            _refusal(status.db_path, status.state, status.found_version or 0).message
        )
    return CacheMigrationPlan(status)


def migrate_cache(bundle: BundleConfig) -> CacheMigrationResult:
    """Bring the bundle's cache to the current schema version.

    Idempotent: an already-current file is left untouched and reported with
    ``changed=False``; a missing or uninitialized file is created at the
    current version; an OUTDATED file has each version step applied and
    stamped inside one ``BEGIN IMMEDIATE`` that re-classifies the file first,
    so a concurrent migrator that loses the race applies nothing. Never runs a
    bundle scan or any hook. Raises :class:`CacheMigrationError` for a newer
    file or any SQLite failure, and ``ValueError`` without ``okf_cache_dir``.
    """
    plan = plan_cache_migration(bundle)
    if not plan.would_change:
        return CacheMigrationResult(plan.status)
    try:
        plan.status.db_path.parent.mkdir(parents=True, exist_ok=True)
        applied = _upgrade_locked(plan.status.db_path, migrate_outdated=True)
    except (sqlite3.Error, CacheSchemaError) as exc:
        raise CacheMigrationError(f"cache migration failed: {exc}") from exc
    return CacheMigrationResult(plan.status, applied)


def _connect(db_path: Path) -> sqlite3.Connection:
    """Autocommit connection with the busy timeout and nothing else.

    ``isolation_level=None`` makes transaction boundaries explicit: reads never
    sit inside a lingering implicit transaction, and writes go through
    :func:`_write_transaction`. Used directly for inspection, which must not
    change the file's journal mode.
    """
    conn = sqlite3.connect(
        db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None
    )
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS};")
    return conn


def _connect_configured(db_path: Path) -> sqlite3.Connection:
    """Connection for reading and writing cache rows.

    WAL lets readers proceed while a writer is active; ``synchronous=NORMAL``
    is the WAL-safe setting that trims fsyncs so a write transaction releases
    its lock sooner; ``foreign_keys=ON`` makes deleting a concept cascade to
    its links.
    """
    conn = _connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextlib.contextmanager
def _write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside one ``BEGIN IMMEDIATE``, committing or rolling back."""
    conn.execute("BEGIN IMMEDIATE TRANSACTION;")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK;")
        raise
    conn.execute("COMMIT;")


# Any okf-owned table under user_version 0 marks a file written before
# versioning existed. An FTS-only leftover counts: an ordinary command must
# not add concepts/links to an existing file; only migrate-db may.
_VERSION_ZERO_MARKERS: Final = frozenset({"concepts", "concept_fts"})


def _classify(conn: sqlite3.Connection) -> tuple[CacheSchemaState, int]:
    version = conn.execute("PRAGMA user_version;").fetchone()[0]
    if version > CURRENT_SCHEMA_VERSION:
        return CacheSchemaState.UNSUPPORTED_NEWER, version
    if version == CURRENT_SCHEMA_VERSION:
        return CacheSchemaState.CURRENT, version
    if version > 0:
        return CacheSchemaState.OUTDATED, version
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")
    }
    if tables & _VERSION_ZERO_MARKERS:
        return CacheSchemaState.OUTDATED, 0
    return CacheSchemaState.UNINITIALIZED, 0


def _upgrade_locked(db_path: Path, *, migrate_outdated: bool) -> tuple[int, ...]:
    """Apply every pending version step under one write lock.

    Re-classifies inside the lock: a concurrent opener or migrator that won
    the race leaves the file CURRENT, and this call then applies nothing.
    With ``migrate_outdated=False`` (an ordinary open) an OUTDATED file found
    under the lock raises :class:`CacheSchemaError` instead of being upgraded.
    Returns the versions stamped, in order. The connection is closed before
    returning.
    """
    conn = _connect_configured(db_path)
    try:
        with _write_transaction(conn):
            state, found = _classify(conn)
            if state is CacheSchemaState.CURRENT:
                return ()
            if state is CacheSchemaState.UNINITIALIZED or (
                state is CacheSchemaState.OUTDATED and migrate_outdated
            ):
                return _apply_migrations(conn, found)
            raise CacheSchemaError(_refusal(db_path, state, found))
    finally:
        conn.close()


def _apply_migrations(conn: sqlite3.Connection, from_version: int) -> tuple[int, ...]:
    """Run each step after ``from_version`` and stamp ``user_version`` after it."""
    targets = tuple(range(from_version + 1, CURRENT_SCHEMA_VERSION + 1))
    for target in targets:
        MIGRATIONS[target - 1](conn)
        conn.execute(f"PRAGMA user_version = {target};")
    return targets

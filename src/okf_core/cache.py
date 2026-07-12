"""SQLite database cache plugin for OKF operations."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Generator
from urllib.parse import unquote, urlsplit

from okf_core.config import BundleConfig
from okf_core.manifest import (
    BundleManifest,
    ConceptManifestEntry,
    ManifestProblem,
    _freeze_value,
)
from okf_core.graph import ConceptLink, BundleGraph, GraphProblem, compute_pagerank
from okf_core.hooks import hookimpl

# Held long enough to ride out a concurrent writer's flush (which is now a
# short burst rather than a whole scan) without surfacing "database is locked".
_BUSY_TIMEOUT_MS = 30000


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply the shared PRAGMA configuration used by every OKF SQLite handle.

    WAL lets readers proceed while a writer is active; ``synchronous=NORMAL`` is
    the WAL-safe setting that trims fsyncs so a write transaction releases its
    lock sooner. ``busy_timeout`` is the backstop for the brief windows where
    two writers still collide.
    """
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")


def _add_ctime_ns_column_if_missing(
    conn: sqlite3.Connection, columns: set[str]
) -> None:
    """Add the legacy ctime_ns column, tolerating a concurrent migration winner."""
    if "ctime_ns" in columns:
        return
    try:
        conn.execute(
            "ALTER TABLE concepts ADD COLUMN ctime_ns INTEGER DEFAULT 0 NOT NULL;"
        )
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower() or "ctime_ns" not in str(
            exc
        ):
            raise


class SqliteCachePlugin:
    """SQLite caching plugin for OKF operations."""

    def __init__(self, bundle: BundleConfig) -> None:
        self.bundle = bundle
        if bundle.okf_cache_dir is None:
            raise ValueError("okf_cache_dir is not configured")

        self.cache_dir = bundle.okf_cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.cache_dir / "okf-cache.db"
        self._conn: sqlite3.Connection | None = None
        # Writes performed during a scan/graph phase are buffered here and
        # flushed in a single short transaction when the phase ends, so the
        # write lock is held for the flush rather than the whole phase. A warm
        # cache buffers nothing and therefore never takes a write lock.
        self._concept_ops: dict[str, tuple[str, tuple[Any, ...]]] = {}
        self._link_ops: dict[str, list[ConceptLink]] = {}
        self._active = False
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None puts the connection in autocommit mode so that
        # transaction boundaries are explicit: reads never sit inside a lingering
        # implicit transaction (which would pin the WAL and block checkpoints),
        # and writes are wrapped in an explicit BEGIN IMMEDIATE at flush time.
        conn = sqlite3.connect(
            self.db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None
        )
        _configure_connection(conn)
        return conn

    def _schema_ready(self, conn: sqlite3.Connection) -> bool:
        """Return True if the concepts/links schema is already present.

        Constructing a plugin happens on every bundle access; probing the
        schema with reads and only issuing DDL when something is missing keeps
        the common (already-initialised) path from taking a write lock and
        contending with a concurrent scan.
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(concepts);")}
        if not {"concept_id", "ctime_ns"} <= columns:
            return False
        link_columns = {row[1] for row in conn.execute("PRAGMA table_info(links);")}
        return "source_concept_id" in link_columns

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._connect() as conn:
            if self._schema_ready(conn):
                return
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
            # Create indexes for performance
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_concept_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_concepts_path ON concepts(path);"
            )
            # Migrate old schemas that predate ctime_ns.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(concepts);")}
            _add_ctime_ns_column_if_missing(conn, columns)

    def __del__(self) -> None:
        """Defensive fallback to close connection on garbage collection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.ProgrammingError:
                pass
            self._conn = None

    @contextlib.contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        if self._conn is not None:
            yield self._conn
        else:
            with self._connect() as conn:
                yield conn

    def _begin_batch(self) -> None:
        """Open a read connection for a scan/graph phase and reset write buffers."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.ProgrammingError:
                pass
        self._conn = self._connect()
        self._concept_ops = {}
        self._link_ops = {}
        self._active = True

    def _end_batch(self) -> None:
        """Discard buffers and close the phase connection."""
        self._concept_ops = {}
        self._link_ops = {}
        self._active = False
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.ProgrammingError:
                pass
            self._conn = None

    @staticmethod
    def _apply_concept_op(
        conn: sqlite3.Connection, kind: str, params: tuple[Any, ...]
    ) -> None:
        if kind == "stable_id":
            conn.execute(
                "UPDATE concepts SET stable_id = ? WHERE concept_id = ? AND mtime_ns = ? AND size = ? AND ctime_ns = ?",
                params,
            )
        else:  # "insert"
            conn.execute(
                """
                INSERT OR REPLACE INTO concepts (concept_id, stable_id, path, sha256, mtime_ns, size, frontmatter, links_resolved, pagerank, ctime_ns)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0.0, ?)
                """,
                params,
            )

    @staticmethod
    def _apply_link_op(
        conn: sqlite3.Connection, source_concept_id: str, links: list[ConceptLink]
    ) -> None:
        conn.execute(
            "DELETE FROM links WHERE source_concept_id = ?", (source_concept_id,)
        )
        for link in links:
            conn.execute(
                """
                INSERT INTO links (source_concept_id, target_concept_id, text, target)
                VALUES (?, ?, ?, ?)
                """,
                (source_concept_id, link.target_concept_id, link.text, link.target),
            )
        conn.execute(
            "UPDATE concepts SET links_resolved = 1 WHERE concept_id = ?",
            (source_concept_id,),
        )

    @hookimpl
    def okf_start_scan(
        self,
        bundle: BundleConfig,
    ) -> None:
        self._begin_batch()

    @hookimpl
    def okf_end_scan(
        self,
        bundle: BundleConfig,
        manifest: BundleManifest,
    ) -> None:
        if self._conn is None:
            return

        active_ids = {entry.concept_id for entry in manifest.concepts}
        cached_ids = {
            row[0] for row in self._conn.execute("SELECT concept_id FROM concepts")
        }
        obsolete_ids = cached_ids - active_ids

        # Flush every buffered write plus the obsolete-row pruning in one short
        # transaction. Nothing to flush (warm cache) skips the write lock.
        if self._concept_ops or obsolete_ids:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE TRANSACTION;")
            try:
                for kind, params in self._concept_ops.values():
                    self._apply_concept_op(conn, kind, params)
                if obsolete_ids:
                    conn.executemany(
                        "DELETE FROM concepts WHERE concept_id = ?",
                        [(obs_id,) for obs_id in obsolete_ids],
                    )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        self._end_batch()

    @hookimpl
    def okf_abort_scan(
        self,
        bundle: BundleConfig,
    ) -> None:
        # Buffered writes were never applied, so aborting only means dropping
        # them and closing the connection; there is no transaction to roll back.
        self._end_batch()

    @hookimpl
    def okf_start_graph(
        self,
        bundle: BundleConfig,
    ) -> None:
        self._begin_batch()

    @hookimpl
    def okf_end_graph(
        self,
        bundle: BundleConfig,
        graph: BundleGraph,
    ) -> None:
        if self._conn is None:
            return

        # Recompute PageRank
        nodes = {entry.concept_id for entry in graph.concepts}
        edges = []
        for link in graph.links:
            if link.target_concept_id:
                edges.append((link.source_concept_id, link.target_concept_id))

        pageranks = compute_pagerank(nodes, edges)

        # Only write PageRank rows whose value actually moved. compute_pagerank
        # is deterministic, so a warm graph reproduces the stored values exactly
        # and this leaves nothing to flush — keeping repeat graph builds off the
        # write lock entirely.
        stored = {
            row[0]: row[1]
            for row in self._conn.execute("SELECT concept_id, pagerank FROM concepts")
        }
        pagerank_updates = [
            (concept_id, pr_value)
            for concept_id, pr_value in pageranks.items()
            if abs(stored.get(concept_id, -1.0) - pr_value) > 1e-12
        ]

        if self._link_ops or pagerank_updates:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE TRANSACTION;")
            try:
                for source_concept_id, links in self._link_ops.items():
                    self._apply_link_op(conn, source_concept_id, links)
                for concept_id, pr_value in pagerank_updates:
                    conn.execute(
                        "UPDATE concepts SET pagerank = ? WHERE concept_id = ?",
                        (pr_value, concept_id),
                    )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        self._end_batch()

    @hookimpl
    def okf_abort_graph(
        self,
        bundle: BundleConfig,
    ) -> None:
        self._end_batch()

    @hookimpl
    def okf_fetch_scan_concept(
        self,
        path: Path,
        root: Path,
        bundle: BundleConfig,
    ) -> ConceptManifestEntry | None:
        rel_path = path.relative_to(root).as_posix()

        try:
            stat = path.stat()
            mtime_ns = stat.st_mtime_ns
            size = stat.st_size
            ctime_ns = stat.st_ctime_ns
        except OSError:
            # File unreadable or absent
            return None

        # Cache validity is based on filesystem metadata only: mtime_ns, size,
        # and ctime_ns.  The sha256 stored in the database is the hash computed
        # during the last full parse; it is returned as-is on a hit so callers
        # receive a consistent ConceptManifestEntry, but it is NOT re-computed
        # or compared here.  Metadata-only validation avoids a full file read on
        # every scan while still detecting content changes (mtime/size change)
        # and out-of-band metadata updates such as rsync -a or utime resets
        # (ctime change).
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT concept_id, stable_id, sha256, frontmatter
                FROM concepts
                WHERE path = ? AND mtime_ns = ? AND size = ? AND ctime_ns = ?
                """,
                (rel_path, mtime_ns, size, ctime_ns),
            )
            row = cursor.fetchone()
            if row is not None:
                concept_id, _stored_stable_id, sha256, fm_json = row
                try:
                    frontmatter = json.loads(fm_json)
                except json.JSONDecodeError:
                    return None

                # Re-derive stable_id from the cached frontmatter using the
                # current bundle config so that changing stable_id_field takes
                # effect without requiring a cache flush.
                stable_id: str | None = None
                if bundle.stable_id_field is not None:
                    val = frontmatter.get(bundle.stable_id_field)
                    if val is not None and not (
                        isinstance(val, str) and not val.strip()
                    ):
                        stable_id = str(val).strip()

                return ConceptManifestEntry(
                    concept_id=concept_id,
                    path=path,
                    bundle_root=root,
                    mtime_ns=mtime_ns,
                    size=size,
                    sha256=sha256,
                    frontmatter=_freeze_value(frontmatter),
                    stable_id=stable_id,
                )
        return None

    @hookimpl
    def okf_exit_scan_concept(
        self,
        entry: ConceptManifestEntry | None,
        problem: ManifestProblem | None,
        path: Path,
        root: Path,
        bundle: BundleConfig,
    ) -> None:
        if entry is None:
            return
        rel_path = path.relative_to(root).as_posix()
        fm_json = json.dumps(_unfreeze_value(entry.frontmatter))

        stable_id = entry.stable_id

        try:
            ctime_ns = path.stat().st_ctime_ns
        except OSError:
            ctime_ns = 0

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT stable_id FROM concepts WHERE concept_id = ? AND mtime_ns = ? AND size = ? AND ctime_ns = ?",
                (entry.concept_id, entry.mtime_ns, entry.size, ctime_ns),
            )
            row = cursor.fetchone()
            if row is not None and row[0] == stable_id:
                # Already cached and up to date; avoid redundant write (and resetting links_resolved to 0)
                return

            op: tuple[str, tuple[Any, ...]]
            if row is not None:
                # File is unchanged but stable_id drifted (e.g. config change); update only
                # stable_id so links_resolved/pagerank are preserved.
                op = (
                    "stable_id",
                    (stable_id, entry.concept_id, entry.mtime_ns, entry.size, ctime_ns),
                )
            else:
                op = (
                    "insert",
                    (
                        entry.concept_id,
                        stable_id,
                        rel_path,
                        entry.sha256,
                        entry.mtime_ns,
                        entry.size,
                        fm_json,
                        ctime_ns,
                    ),
                )

            if self._active:
                self._concept_ops[entry.concept_id] = op
            else:
                # Out-of-band call outside a scan phase: apply immediately.
                conn.execute("BEGIN IMMEDIATE TRANSACTION;")
                try:
                    self._apply_concept_op(conn, op[0], op[1])
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise

    @hookimpl
    def okf_fetch_resolve_links(
        self,
        entry: ConceptManifestEntry,
        bundle: BundleConfig,
    ) -> list[ConceptLink] | None:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT links_resolved FROM concepts WHERE concept_id = ?",
                (entry.concept_id,),
            )
            row = cursor.fetchone()
            if row is None or row[0] == 0:
                # Concept is not cached or links are not resolved
                return None

            cursor.execute(
                "SELECT target_concept_id, text, target FROM links WHERE source_concept_id = ?",
                (entry.concept_id,),
            )
            rows = cursor.fetchall()

            links = []
            for target_concept_id, text, target in rows:
                parsed = urlsplit(target)
                # Mirror graph.py's _resolve_concept_link decoding exactly,
                # including per-segment decoding, so a cached target_path
                # matches what a fresh (uncached) scan would compute for the
                # same percent-encoded href.
                segments = [unquote(segment) for segment in parsed.path.split("/")]
                if any("/" in segment for segment in segments):
                    continue
                target_path_str = "/".join(segments)
                try:
                    if target_path_str.startswith("/"):
                        target_path = (
                            bundle.bundle_root / target_path_str.lstrip("/")
                        ).resolve(strict=False)
                    else:
                        target_path = (entry.path.parent / target_path_str).resolve(
                            strict=False
                        )
                except ValueError:
                    # A decoded href can contain characters invalid in a
                    # filesystem path (e.g. an embedded NUL); such a link
                    # would never have resolved to a real target, so skip it.
                    continue

                links.append(
                    ConceptLink(
                        source_concept_id=entry.concept_id,
                        source_path=entry.path,
                        text=text,
                        target=target,
                        target_path=target_path,
                        target_concept_id=target_concept_id,
                    )
                )
            return links

    @hookimpl
    def okf_exit_resolve_links(
        self,
        entry: ConceptManifestEntry,
        links: Sequence[ConceptLink] | None,
        problem: GraphProblem | None,
        bundle: BundleConfig,
    ) -> None:
        if links is None:
            return
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT links_resolved FROM concepts WHERE concept_id = ?",
                (entry.concept_id,),
            )
            row = cursor.fetchone()
            if row is None:
                # Concept is not cached; writing links would violate the FK.
                return
            if row[0] == 1:
                # Links are already marked as resolved; avoid redundant write
                return

            if self._active:
                self._link_ops[entry.concept_id] = list(links)
            else:
                conn.execute("BEGIN IMMEDIATE TRANSACTION;")
                try:
                    self._apply_link_op(conn, entry.concept_id, list(links))
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise


def get_cache_plugin(bundle: BundleConfig) -> SqliteCachePlugin:
    """Create and return the SQLite Cache Plugin instance."""
    return SqliteCachePlugin(bundle)


def _unfreeze_value(value: Any) -> Any:
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return {key: _unfreeze_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_unfreeze_value(val) for val in value]
    return value

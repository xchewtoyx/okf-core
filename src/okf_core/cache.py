"""SQLite database cache plugin for OKF operations."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from okf_core.cache_db import CacheDatabase, _write_transaction
from okf_core.config import BundleConfig
from okf_core.graph import BundleGraph, ConceptLink, compute_pagerank
from okf_core.hooks import hookimpl
from okf_core.manifest import (
    BundleManifest,
    ConceptManifestEntry,
    _freeze_value,
)


class SqliteCachePlugin:
    """SQLite caching plugin for OKF operations.

    Takes an already-opened :class:`~okf_core.cache_db.CacheDatabase`, which
    is the proof that the file exists at the current schema version. The
    plugin therefore never creates directories, inspects the schema, or runs
    DDL: every statement it issues is plain row-level SQL against a shape it
    can rely on. See :func:`okf_core.cache_db.open_cache`.
    """

    def __init__(self, bundle: BundleConfig, db: CacheDatabase) -> None:
        self.bundle = bundle
        self.db = db
        self._conn: sqlite3.Connection | None = None
        # Writes performed during a scan/graph phase are buffered here and
        # flushed in a single short transaction when the phase ends, so the
        # write lock is held for the flush rather than the whole phase. A warm
        # cache buffers nothing and therefore never takes a write lock.
        self._concept_ops: dict[str, tuple[str, tuple[Any, ...]]] = {}
        self._link_ops: dict[str, list[ConceptLink]] = {}
        self._active = False

    def _connect(self) -> sqlite3.Connection:
        return self.db.connect()

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
    def okf_start_scan(self) -> None:
        self._begin_batch()

    @hookimpl
    def okf_end_scan(
        self,
        manifest: BundleManifest,
    ) -> None:
        if self._conn is None:
            return

        try:
            active_ids = {entry.concept_id for entry in manifest.concepts}
            cached_ids = {
                row[0] for row in self._conn.execute("SELECT concept_id FROM concepts")
            }
            obsolete_ids = cached_ids - active_ids

            # Flush every buffered write plus the obsolete-row pruning in one
            # short transaction. Nothing to flush (warm cache) skips the lock.
            if self._concept_ops or obsolete_ids:
                with _write_transaction(self._conn) as conn:
                    for kind, params in self._concept_ops.values():
                        self._apply_concept_op(conn, kind, params)
                    if obsolete_ids:
                        conn.executemany(
                            "DELETE FROM concepts WHERE concept_id = ?",
                            [(obs_id,) for obs_id in obsolete_ids],
                        )
        finally:
            # Always drop buffers and close the phase connection, even if the
            # flush raised: otherwise the read connection lingers and pins the
            # WAL until the next phase or GC.
            self._end_batch()

    @hookimpl
    def okf_abort_scan(self) -> None:
        # Buffered writes were never applied, so aborting only means dropping
        # them and closing the connection; there is no transaction to roll back.
        self._end_batch()

    @hookimpl
    def okf_start_graph(self) -> None:
        self._begin_batch()

    @hookimpl
    def okf_end_graph(
        self,
        graph: BundleGraph,
    ) -> None:
        if self._conn is None:
            return

        try:
            # Recompute PageRank
            nodes = {entry.concept_id for entry in graph.concepts}
            edges = []
            for link in graph.links:
                if link.target_concept_id:
                    edges.append((link.source_concept_id, link.target_concept_id))

            pageranks = compute_pagerank(nodes, edges)

            # Only write PageRank rows whose value actually moved.
            # compute_pagerank is deterministic, so a warm graph reproduces the
            # stored values exactly and this leaves nothing to flush — keeping
            # repeat graph builds off the write lock entirely.
            stored = {
                row[0]: row[1]
                for row in self._conn.execute(
                    "SELECT concept_id, pagerank FROM concepts"
                )
            }
            pagerank_updates = [
                (concept_id, pr_value)
                for concept_id, pr_value in pageranks.items()
                if abs(stored.get(concept_id, -1.0) - pr_value) > 1e-12
            ]

            if self._link_ops or pagerank_updates:
                with _write_transaction(self._conn) as conn:
                    for source_concept_id, links in self._link_ops.items():
                        self._apply_link_op(conn, source_concept_id, links)
                    for concept_id, pr_value in pagerank_updates:
                        conn.execute(
                            "UPDATE concepts SET pagerank = ? WHERE concept_id = ?",
                            (pr_value, concept_id),
                        )
        finally:
            # Always drop buffers and close the phase connection, even if the
            # flush raised (see okf_end_scan).
            self._end_batch()

    @hookimpl
    def okf_abort_graph(self) -> None:
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
        path: Path,
        root: Path,
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
                with _write_transaction(conn):
                    self._apply_concept_op(conn, op[0], op[1])

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
                with _write_transaction(conn):
                    self._apply_link_op(conn, entry.concept_id, list(links))


def _unfreeze_value(value: Any) -> Any:
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return {key: _unfreeze_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_unfreeze_value(val) for val in value]
    return value

"""SQLite database cache plugin for OKF operations."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Generator
from urllib.parse import urlsplit

from okf_core.config import BundleConfig
from okf_core.manifest import (
    BundleManifest,
    ConceptManifestEntry,
    ManifestProblem,
    _freeze_value,
)
from okf_core.graph import ConceptLink, BundleGraph, GraphProblem
from okf_core.hooks import hookimpl


def compute_pagerank(
    nodes: set[str],
    edges: list[tuple[str, str]],
    d: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Compute PageRank centrality scores for a directed graph."""
    if not nodes:
        return {}

    n = len(nodes)
    sorted_nodes = sorted(nodes)
    pr = {node: 1.0 / n for node in sorted_nodes}

    out_links: dict[str, list[str]] = {node: [] for node in sorted_nodes}
    in_links: dict[str, list[str]] = {node: [] for node in sorted_nodes}

    for src, dst in edges:
        if src in nodes and dst in nodes:
            out_links[src].append(dst)
            in_links[dst].append(src)

    for node in sorted_nodes:
        out_links[node].sort()
        in_links[node].sort()

    sinks = [node for node in sorted_nodes if not out_links[node]]

    for _ in range(max_iter):
        next_pr = {}
        sink_sum = sum(pr[sink] for sink in sinks)

        for node in sorted_nodes:
            rank_sum = sum(
                pr[neighbor] / len(out_links[neighbor]) for neighbor in in_links[node]
            )
            rank_sum += sink_sum / n
            next_pr[node] = (1.0 - d) / n + d * rank_sum

        err = sum(abs(next_pr[node] - pr[node]) for node in sorted_nodes)
        pr = next_pr
        if err < tol:
            break

    return pr


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
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
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
            # Migrate old schemas if ctime_ns is missing
            try:
                conn.execute(
                    "ALTER TABLE concepts ADD COLUMN ctime_ns INTEGER DEFAULT 0 NOT NULL;"
                )
            except sqlite3.OperationalError:
                pass

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
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON;")
                yield conn

    @hookimpl
    def okf_start_scan(
        self,
        bundle: BundleConfig,
    ) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.ProgrammingError:
                pass
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("BEGIN TRANSACTION;")

    @hookimpl
    def okf_end_scan(
        self,
        bundle: BundleConfig,
        manifest: BundleManifest,
    ) -> None:
        active_ids = {entry.concept_id for entry in manifest.concepts}

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT concept_id FROM concepts")
            cached_ids = {row[0] for row in cursor.fetchall()}

            obsolete_ids = cached_ids - active_ids
            if obsolete_ids:
                conn.executemany(
                    "DELETE FROM concepts WHERE concept_id = ?",
                    [(obs_id,) for obs_id in obsolete_ids],
                )

        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    @hookimpl
    def okf_abort_scan(
        self,
        bundle: BundleConfig,
    ) -> None:
        if self._conn is not None:
            try:
                self._conn.rollback()
                self._conn.close()
            except sqlite3.ProgrammingError:
                pass
            self._conn = None

    @hookimpl
    def okf_start_graph(
        self,
        bundle: BundleConfig,
    ) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.ProgrammingError:
                pass
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("BEGIN TRANSACTION;")

    @hookimpl
    def okf_end_graph(
        self,
        bundle: BundleConfig,
        graph: BundleGraph,
    ) -> None:
        # Recompute PageRank
        nodes = {entry.concept_id for entry in graph.concepts}
        edges = []
        for link in graph.links:
            if link.target_concept_id:
                edges.append((link.source_concept_id, link.target_concept_id))

        pageranks = compute_pagerank(nodes, edges)

        with self._connection() as conn:
            for concept_id, pr_value in pageranks.items():
                conn.execute(
                    "UPDATE concepts SET pagerank = ? WHERE concept_id = ?",
                    (pr_value, concept_id),
                )

        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    @hookimpl
    def okf_abort_graph(
        self,
        bundle: BundleConfig,
    ) -> None:
        if self._conn is not None:
            try:
                self._conn.rollback()
                self._conn.close()
            except sqlite3.ProgrammingError:
                pass
            self._conn = None

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

            conn.execute(
                """
                INSERT OR REPLACE INTO concepts (concept_id, stable_id, path, sha256, mtime_ns, size, frontmatter, links_resolved, pagerank, ctime_ns)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0.0, ?)
                """,
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
                target_path_str = parsed.path
                if target_path_str.startswith("/"):
                    target_path = (
                        bundle.bundle_root / target_path_str.lstrip("/")
                    ).resolve(strict=False)
                else:
                    target_path = (entry.path.parent / target_path_str).resolve(
                        strict=False
                    )

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
                return
            if row[0] == 1:
                # Links are already marked as resolved; avoid redundant write
                return

            conn.execute(
                "DELETE FROM links WHERE source_concept_id = ?", (entry.concept_id,)
            )
            for link in links:
                conn.execute(
                    """
                    INSERT INTO links (source_concept_id, target_concept_id, text, target)
                    VALUES (?, ?, ?, ?)
                    """,
                    (entry.concept_id, link.target_concept_id, link.text, link.target),
                )
            conn.execute(
                "UPDATE concepts SET links_resolved = 1 WHERE concept_id = ?",
                (entry.concept_id,),
            )


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

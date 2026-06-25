"""SQLite database cache plugin for OKF operations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from okf_core.config import BundleConfig
from okf_core.manifest import BundleManifest, ConceptManifestEntry, _freeze_value
from okf_core.graph import ConceptLink
from okf_core.hooks import hookimpl


class SqliteCachePlugin:
    """SQLite caching plugin for OKF operations."""

    def __init__(self, bundle: BundleConfig) -> None:
        self.bundle = bundle
        if bundle.okf_cache_dir is None:
            raise ValueError("okf_cache_dir is not configured")

        self.cache_dir = bundle.okf_cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.cache_dir / "okf-cache.db"
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
                    links_resolved INTEGER DEFAULT 0
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

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @hookimpl
    def okf_enter_scan_concept(
        self,
        path: Path,
        root: Path,
        bundle: BundleConfig,
    ) -> ConceptManifestEntry | None:
        rel_path = str(path.relative_to(root))

        try:
            stat = path.stat()
            mtime_ns = stat.st_mtime_ns
            size = stat.st_size
        except OSError:
            # File unreadable or absent
            return None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT concept_id, stable_id, sha256, frontmatter
                FROM concepts
                WHERE path = ? AND mtime_ns = ? AND size = ?
                """,
                (rel_path, mtime_ns, size),
            )
            row = cursor.fetchone()
            if row is not None:
                concept_id, stable_id, sha256, fm_json = row
                try:
                    frontmatter = json.loads(fm_json)
                except json.JSONDecodeError:
                    return None

                return ConceptManifestEntry(
                    concept_id=concept_id,
                    path=path,
                    bundle_root=root,
                    mtime_ns=mtime_ns,
                    size=size,
                    sha256=sha256,
                    frontmatter=_freeze_value(frontmatter),
                )
        return None

    @hookimpl
    def okf_exit_scan_concept(
        self,
        entry: ConceptManifestEntry,
        path: Path,
        root: Path,
        bundle: BundleConfig,
    ) -> None:
        rel_path = str(path.relative_to(root))
        fm_json = json.dumps(_unfreeze_value(entry.frontmatter))
        stable_id = None

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO concepts (concept_id, stable_id, path, sha256, mtime_ns, size, frontmatter, links_resolved)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    entry.concept_id,
                    stable_id,
                    rel_path,
                    entry.sha256,
                    entry.mtime_ns,
                    entry.size,
                    fm_json,
                ),
            )

    @hookimpl
    def okf_enter_resolve_links(
        self,
        entry: ConceptManifestEntry,
        bundle: BundleConfig,
    ) -> list[ConceptLink] | None:
        with self._get_connection() as conn:
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
                if target.startswith("/"):
                    target_path = (bundle.bundle_root / target.lstrip("/")).resolve(
                        strict=False
                    )
                else:
                    target_path = (entry.path.parent / target).resolve(strict=False)

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
        links: Sequence[ConceptLink],
        bundle: BundleConfig,
    ) -> None:
        with self._get_connection() as conn:
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

    @hookimpl
    def okf_scan_end(
        self,
        bundle: BundleConfig,
        manifest: BundleManifest,
    ) -> None:
        active_ids = {entry.concept_id for entry in manifest.concepts}

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT concept_id FROM concepts")
            cached_ids = {row[0] for row in cursor.fetchall()}

            obsolete_ids = cached_ids - active_ids
            if obsolete_ids:
                placeholders = ",".join("?" for _ in obsolete_ids)
                conn.execute(
                    f"DELETE FROM concepts WHERE concept_id IN ({placeholders})",
                    tuple(obsolete_ids),
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

"""FTS5 lexical search for OKF bundles.

The ``concept_fts`` table lives inside the bundle's ``okf-cache.db`` but is
not part of its versioned schema: it is a derived index, rebuilt from the
bundle on every refresh, so it is created lazily here (only when a refresh is
about to write it) rather than by a cache migration step.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from okf_core.cache_db import (
    CacheDatabase,
    CacheSchemaError,
    _write_transaction,
    open_cache,
)
from okf_core.config import BundleConfig
from okf_core.listing import BundleListing, ListingProblem, list_concepts
from okf_core.manifest import BundleManifest, scan_bundle


class SearchConfigError(Exception):
    """Raised when lexical search cannot be configured for a bundle.

    Covers a bundle without ``okf_cache_dir``, a SQLite build without FTS5,
    and a cache file whose schema version an ordinary command must not use
    (one that needs ``okf migrate-db``, or one newer than this okf-core).
    """


@dataclass(frozen=True)
class SearchResult:
    """One lexical search candidate."""

    concept_id: str
    path: Path
    title: str | None
    description: str | None
    score: float
    snippets: tuple[str, ...] = ()


@dataclass(frozen=True)
class BundleSearchResults:
    """Search results and non-fatal refresh/listing problems for a bundle."""

    bundle_name: str
    query: str
    results: tuple[SearchResult, ...] = ()
    problems: tuple[ListingProblem, ...] = ()


def search_concepts(
    bundle: BundleConfig,
    query: str,
    *,
    limit: int = 10,
    refresh: bool = True,
    manifest: BundleManifest | None = None,
) -> BundleSearchResults:
    """Search a bundle's opt-in SQLite cache with FTS5.

    The cache is opened through :func:`okf_core.cache_db.open_cache`: a missing
    file is created at the current schema version, while a file that needs
    ``okf migrate-db`` (or is newer than this okf-core supports) raises
    :class:`SearchConfigError` before anything is scanned. With
    ``refresh=True`` the bundle is scanned and the FTS index rebuilt inside one
    write transaction; with ``refresh=False`` the existing index is queried
    as-is and nothing is written -- an index that was never built yields zero
    results rather than being created.
    """

    if limit < 0:
        raise ValueError("limit must be greater than or equal to 0")
    db = _open_search_cache(bundle, "search")
    fts_query = _build_fts_query(query)

    problems: tuple[ListingProblem, ...] = ()
    listing: BundleListing | None = None
    if refresh:
        resolved_manifest = manifest if manifest is not None else scan_bundle(bundle)
        listing = list_concepts(bundle, manifest=resolved_manifest, with_content=True)
        problems = listing.problems

    with contextlib.closing(db.connect()) as conn:
        indexed = _prepare_search_index(conn, bundle, listing)
        if not indexed or limit == 0 or fts_query is None:
            return BundleSearchResults(
                bundle_name=bundle.name,
                query=query,
                results=(),
                problems=problems,
            )

        rows = conn.execute(
            """
            SELECT
                concept_id,
                path,
                title,
                description,
                bm25(concept_fts) AS rank,
                snippet(concept_fts, -1, '[', ']', '...', 16) AS snippet
            FROM concept_fts
            WHERE concept_fts MATCH ?
            ORDER BY rank, concept_id
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()

    return BundleSearchResults(
        bundle_name=bundle.name,
        query=query,
        results=tuple(_row_to_result(bundle, row) for row in rows),
        problems=problems,
    )


def _open_search_cache(bundle: BundleConfig, feature: str) -> CacheDatabase:
    """Open the bundle's cache for FTS use, mapping every refusal to SearchConfigError."""
    if bundle.okf_cache_dir is None:
        raise SearchConfigError(
            "okf_cache_dir is not configured; enable bundle-level caching to use "
            f"{feature}"
        )
    try:
        return open_cache(bundle)
    except CacheSchemaError as exc:
        raise SearchConfigError(str(exc)) from exc


def _prepare_search_index(
    conn: sqlite3.Connection,
    bundle: BundleConfig,
    listing: BundleListing | None,
) -> bool:
    if listing is None:
        return _has_search_index(conn)
    with _write_transaction(conn):
        _ensure_search_schema(conn)
        _refresh_search_index(conn, bundle, listing)
    return True


def _has_search_index(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'concept_fts';"
    ).fetchone()
    return row is not None


def _ensure_search_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS concept_fts USING fts5(
                concept_id UNINDEXED,
                path UNINDEXED,
                title,
                description,
                fields,
                body,
                tokenize = 'unicode61'
            );
            """)
    except sqlite3.OperationalError as exc:
        if "no such module: fts5" not in str(exc).lower():
            raise
        raise SearchConfigError(
            "SQLite FTS5 is not available; install or use a Python SQLite build with FTS5 support"
        ) from exc


def _refresh_search_index(
    conn: sqlite3.Connection,
    bundle: BundleConfig,
    listing: BundleListing,
) -> None:
    active_ids = {concept.concept_id for concept in listing.concepts}
    cached_ids = {row[0] for row in conn.execute("SELECT concept_id FROM concept_fts")}
    obsolete_ids = cached_ids - active_ids
    if obsolete_ids:
        conn.executemany(
            "DELETE FROM concept_fts WHERE concept_id = ?",
            [(concept_id,) for concept_id in sorted(obsolete_ids)],
        )

    for concept in listing.concepts:
        rel_path = concept.path.relative_to(bundle.bundle_root).as_posix()
        conn.execute(
            "DELETE FROM concept_fts WHERE concept_id = ?",
            (concept.concept_id,),
        )
        conn.execute(
            """
            INSERT INTO concept_fts (
                concept_id, path, title, description, fields, body
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                concept.concept_id,
                rel_path,
                concept.title or "",
                concept.description or "",
                _fields_text(concept.fields),
                concept.content or "",
            ),
        )


def _fields_text(fields: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in sorted(fields):
        parts.append(str(key))
        parts.extend(_flatten_field_value(fields[key]))
    return "\n".join(parts)


def _flatten_field_value(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key in sorted(value):
            parts.append(str(key))
            parts.extend(_flatten_field_value(value[key]))
        return parts
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            parts.extend(_flatten_field_value(item))
        return parts
    if isinstance(value, (set, frozenset)):
        parts = []
        for item in sorted(value, key=str):
            parts.extend(_flatten_field_value(item))
        return parts
    if value is None:
        return []
    return [str(value)]


def _build_fts_query(query: str) -> str | None:
    terms = re.findall(r"\w+", query, flags=re.UNICODE)
    if not terms:
        return None
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def _row_to_result(bundle: BundleConfig, row: tuple[Any, ...]) -> SearchResult:
    concept_id, rel_path, title, description, score, snippet = row
    return SearchResult(
        concept_id=concept_id,
        path=bundle.bundle_root / rel_path,
        title=title or None,
        description=description or None,
        score=float(score),
        snippets=(snippet,) if snippet else (),
    )

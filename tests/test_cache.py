"""Tests for the lightweight SQLite caching system."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from okf_core.config import BundleConfig
from okf_core.graph import build_bundle_graph
from okf_core.manifest import scan_bundle


def test_no_cache_created_when_disabled(tmp_path: Path) -> None:
    # Set up a bundle with okf_cache_dir = None
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\ntitle: Alpha\n")

    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=root / ".cache",
        okf_cache_dir=None,
    )

    manifest = scan_bundle(bundle)
    assert len(manifest.concepts) == 1

    # Verify no cache database file or directory was created
    cache_dir = root / ".okf-cache"
    assert not cache_dir.exists()


def test_cache_created_and_populated_when_enabled(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\ntitle: Alpha\n")

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=root / ".cache",
        okf_cache_dir=cache_dir,
    )

    # 1. First scan: database is created and populated
    manifest = scan_bundle(bundle)
    assert len(manifest.concepts) == 1

    db_path = cache_dir / "okf-cache.db"
    assert db_path.is_file()

    # Query database directly to verify contents
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT concept_id, path, sha256, size FROM concepts")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "a"
        assert rows[0][1] == "a.md"

    # 2. Build graph: link records are populated
    graph = build_bundle_graph(bundle, manifest=manifest)
    assert len(graph.concepts) == 1

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT source_concept_id, target_concept_id, text FROM links")
        # No links in a.md yet
        assert len(cursor.fetchall()) == 0


def test_cache_hits_skip_file_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\ntitle: Alpha\n")

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=root / ".cache",
        okf_cache_dir=cache_dir,
    )

    # First scan to populate the cache
    scan_bundle(bundle)

    # Mock read_bytes on Path to verify it is NOT called during second scan
    read_called = False

    def mock_read_bytes(self: Path) -> bytes:
        nonlocal read_called
        read_called = True
        return b""

    monkeypatch.setattr(Path, "read_bytes", mock_read_bytes)

    # Second scan should hit the cache and skip read_bytes entirely
    manifest = scan_bundle(bundle)
    assert len(manifest.concepts) == 1
    assert manifest.concepts[0].concept_id == "a"
    assert not read_called


def test_cache_invalidation_on_file_modification(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    a_path = root / "a.md"
    _write_concept(a_path, "type: concept\ntitle: Alpha\n")

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=root / ".cache",
        okf_cache_dir=cache_dir,
    )

    # First scan
    scan_bundle(bundle)

    # Update file size and mtime
    time.sleep(0.01)  # Ensure modification time differs
    _write_concept(a_path, "type: concept\ntitle: AlphaModified\n")

    # Second scan: should detect change and update the cache
    manifest = scan_bundle(bundle)
    assert len(manifest.concepts) == 1
    assert manifest.concepts[0].frontmatter["title"] == "AlphaModified"

    # Query DB to check updated frontmatter
    db_path = cache_dir / "okf-cache.db"
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT frontmatter FROM concepts WHERE concept_id = 'a'")
        fm_json = cursor.fetchone()[0]
        assert "AlphaModified" in fm_json


def test_cache_pruning_on_file_deletion(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    a_path = root / "a.md"
    b_path = root / "b.md"
    _write_concept(a_path, "type: concept\ntitle: Alpha\n")
    _write_concept(b_path, "type: concept\ntitle: Beta\n")

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=root / ".cache",
        okf_cache_dir=cache_dir,
    )

    # Populate cache with both concepts
    scan_bundle(bundle)

    # Delete b.md
    b_path.unlink()

    # Scan again
    manifest = scan_bundle(bundle)
    assert len(manifest.concepts) == 1
    assert manifest.concepts[0].concept_id == "a"

    # Verify b is deleted from SQLite
    db_path = cache_dir / "okf-cache.db"
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM concepts WHERE concept_id = 'b'")
        assert cursor.fetchone()[0] == 0


def test_links_caching_and_inbound_outbound(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\n", body="See [B](b.md).\n")
    _write_concept(root / "b.md", "type: concept\n")

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=root / ".cache",
        okf_cache_dir=cache_dir,
    )

    # First run: Scan and build graph (populates cache)
    manifest = scan_bundle(bundle)
    graph = build_bundle_graph(bundle, manifest=manifest)
    assert len(graph.links) == 1
    assert graph.links[0].source_concept_id == "a"
    assert graph.links[0].target_concept_id == "b"

    # Query DB to verify link is saved
    db_path = cache_dir / "okf-cache.db"
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT source_concept_id, target_concept_id, text FROM links")
        row = cursor.fetchone()
        assert row[0] == "a"
        assert row[1] == "b"
        assert row[2] == "B"

    # Second run: Build graph again (should hit link cache and avoid markdown link extraction)
    # We test this by using manifest from previous run (cached concepts)
    # We build graph and verify links are correctly restored
    graph2 = build_bundle_graph(bundle, manifest=manifest)
    assert len(graph2.links) == 1
    assert graph2.links[0].source_concept_id == "a"
    assert graph2.links[0].target_concept_id == "b"
    assert graph2.links[0].text == "B"


def _write_concept(path: Path, frontmatter: str, *, body: str = "Body\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")

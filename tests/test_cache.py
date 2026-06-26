"""Tests for the lightweight SQLite caching system."""

from __future__ import annotations

import sqlite3
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


def test_cache_initialization_does_not_create_search_schema(tmp_path: Path) -> None:
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
        okf_cache_dir=cache_dir,
    )

    scan_bundle(bundle)

    with sqlite3.connect(cache_dir / "okf-cache.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM sqlite_master WHERE name = 'concept_fts'")
        assert cursor.fetchone()[0] == 0


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
    import os

    root = tmp_path / "docs"
    a_path = root / "a.md"
    content1 = "type: concept\ntitle: Alpha\n"
    _write_concept(a_path, content1)

    # Get initial stat and explicit ns time to use
    stat1 = a_path.stat()
    mtime1 = stat1.st_mtime_ns

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=cache_dir,
    )

    # First scan
    scan_bundle(bundle)

    # Modify content without changing size (Alpha -> A_pha)
    content2 = "type: concept\ntitle: A_pha\n"
    assert len(content1) == len(content2)
    _write_concept(a_path, content2)

    # Explicitly advance mtime by 1 second (1,000,000,000 ns)
    new_mtime_ns = mtime1 + 1000000000
    os.utime(a_path, ns=(new_mtime_ns, new_mtime_ns))

    # Second scan: should detect change and update the cache
    manifest = scan_bundle(bundle)
    assert len(manifest.concepts) == 1
    assert manifest.concepts[0].frontmatter["title"] == "A_pha"

    # Query DB to check updated frontmatter
    db_path = cache_dir / "okf-cache.db"
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT frontmatter FROM concepts WHERE concept_id = 'a'")
        fm_json = cursor.fetchone()[0]
        assert "A_pha" in fm_json


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


def test_pagerank_calculation_and_storage(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    # Create a small graph: A -> B -> C, and C -> A
    _write_concept(root / "a.md", "type: concept\n", body="[B](b.md)\n")
    _write_concept(root / "b.md", "type: concept\n", body="[C](c.md)\n")
    _write_concept(root / "c.md", "type: concept\n", body="[A](a.md)\n")

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=cache_dir,
    )

    manifest = scan_bundle(bundle)
    build_bundle_graph(bundle, manifest=manifest)

    # Verify PageRank values are computed and stored in the DB
    db_path = cache_dir / "okf-cache.db"
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT concept_id, pagerank FROM concepts ORDER BY concept_id")
        rows = cursor.fetchall()
        assert len(rows) == 3
        # Since it is a symmetric ring (A -> B -> C -> A), all PageRank values should be equal
        # and non-zero (specifically around 0.333333)
        assert rows[0][0] == "a"
        assert rows[1][0] == "b"
        assert rows[2][0] == "c"

        pr_a, pr_b, pr_c = rows[0][1], rows[1][1], rows[2][1]
        assert pr_a > 0.3
        assert abs(pr_a - pr_b) < 1e-5
        assert abs(pr_b - pr_c) < 1e-5


def test_pagerank_calculation_with_orphans(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    # A -> B, and C is orphan
    _write_concept(root / "a.md", "type: concept\n", body="[B](b.md)\n")
    _write_concept(root / "b.md", "type: concept\n")
    _write_concept(root / "c.md", "type: concept\n")

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=cache_dir,
    )

    manifest = scan_bundle(bundle)
    build_bundle_graph(bundle, manifest=manifest)

    db_path = cache_dir / "okf-cache.db"
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT concept_id, pagerank FROM concepts ORDER BY concept_id")
        rows = cursor.fetchall()
        assert len(rows) == 3
        # Every concept should have a PageRank score, and all should be > 0.
        for concept_id, pr in rows:
            assert pr > 0.0


def test_cache_invalidation_on_metadata_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    root = tmp_path / "docs"
    a_path = root / "a.md"
    _write_concept(a_path, "type: concept\ntitle: Alpha\n")

    # Ensure permissions are standard 0o644
    os.chmod(a_path, 0o644)
    stat1 = a_path.stat()
    ctime1 = stat1.st_ctime_ns
    mtime1 = stat1.st_mtime_ns
    size1 = stat1.st_size

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=cache_dir,
    )

    # First scan: populates cache
    scan_bundle(bundle)

    # Modify ctime by changing permissions to 0o755 (leaving size and mtime unchanged)
    os.chmod(a_path, 0o755)
    stat2 = a_path.stat()
    ctime2 = stat2.st_ctime_ns
    mtime2 = stat2.st_mtime_ns
    size2 = stat2.st_size

    # Verify that mtime and size did not change, but ctime did!
    if ctime2 == ctime1:
        pytest.skip("Filesystem does not support distinct ctime updates on chmod()")

    assert mtime2 == mtime1
    assert size2 == size1

    # Mock read_bytes to verify it IS called (since cache misses on ctime mismatch)
    read_called = False
    original_read_bytes = Path.read_bytes

    def mock_read_bytes(self: Path) -> bytes:
        nonlocal read_called
        read_called = True
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", mock_read_bytes)

    # Second scan: should detect change and hit the disk
    scan_bundle(bundle)
    assert read_called


def test_transaction_rollback_on_scan_abort(
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
        okf_cache_dir=cache_dir,
    )

    # Mock _scan_concept_path to raise an error mid-scan
    from okf_core import manifest

    def mock_scan_concept_path(*args, **kwargs):
        raise ValueError("Simulated scan failure")

    monkeypatch.setattr(manifest, "_scan_concept_path", mock_scan_concept_path)

    # Run scan_bundle, which should fail
    with pytest.raises(ValueError, match="Simulated scan failure"):
        scan_bundle(bundle)

    # Verify that the DB file exists, but it has no entries (rolled back)
    db_path = cache_dir / "okf-cache.db"
    assert db_path.is_file()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM concepts")
        assert cursor.fetchone()[0] == 0


def test_graph_building_with_precomputed_manifest_without_cached_concepts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    # Write a concept that links to another
    _write_concept(root / "a.md", "type: concept\n", body="[B](b.md)\n")
    _write_concept(root / "b.md", "type: concept\n")

    # 1. Run scan_bundle WITHOUT caching enabled
    no_cache_bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=None,
    )
    manifest = scan_bundle(no_cache_bundle)
    assert len(manifest.concepts) == 2

    # 2. Run build_bundle_graph WITH caching enabled, passing the precomputed manifest.
    # The concepts are not in the SQLite database, but this should not raise IntegrityError.
    cache_dir = tmp_path / "custom-cache"
    cache_bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=cache_dir,
    )

    from okf_core.graph import build_bundle_graph

    graph = build_bundle_graph(cache_bundle, manifest=manifest)
    assert len(graph.links) == 1
    assert graph.links[0].source_concept_id == "a"
    assert graph.links[0].target_concept_id == "b"

    # Verify that nothing was actually written to the links table
    db_path = cache_dir / "okf-cache.db"
    assert db_path.is_file()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM links")
        assert cursor.fetchone()[0] == 0


def _write_concept(path: Path, frontmatter: str, *, body: str = "Body\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")


def test_hooks_execution_order_and_symmetry(tmp_path: Path) -> None:
    from typing import Any
    from collections.abc import Sequence
    from okf_core.manifest import ConceptManifestEntry, ManifestProblem
    from okf_core.graph import ConceptLink, GraphProblem

    root = tmp_path / "docs"
    _write_concept(root / "a.md", "type: concept\ntitle: Alpha\n", body="[B](b.md)\n")
    _write_concept(root / "b.md", "type: concept\ntitle: Beta\n")

    cache_dir = tmp_path / "custom-cache"
    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=cache_dir,
    )

    calls: list[tuple[Any, ...]] = []

    class TrackingPlugin:
        from okf_core.hooks import hookimpl

        @hookimpl
        def okf_enter_scan_concept(
            self, path: Path, root: Path, bundle: BundleConfig
        ) -> None:
            calls.append(("enter_scan", path.name))

        @hookimpl
        def okf_fetch_scan_concept(
            self, path: Path, root: Path, bundle: BundleConfig
        ) -> None:
            calls.append(("fetch_scan", path.name))

        @hookimpl
        def okf_exit_scan_concept(
            self,
            entry: ConceptManifestEntry | None,
            problem: ManifestProblem | None,
            path: Path,
            root: Path,
            bundle: BundleConfig,
        ) -> None:
            calls.append(
                ("exit_scan", path.name, entry is not None, problem is not None)
            )

        @hookimpl
        def okf_enter_resolve_links(
            self, entry: ConceptManifestEntry, bundle: BundleConfig
        ) -> None:
            calls.append(("enter_resolve", entry.concept_id))

        @hookimpl
        def okf_fetch_resolve_links(
            self, entry: ConceptManifestEntry, bundle: BundleConfig
        ) -> None:
            calls.append(("fetch_resolve", entry.concept_id))

        @hookimpl
        def okf_exit_resolve_links(
            self,
            entry: ConceptManifestEntry,
            links: Sequence[ConceptLink] | None,
            problem: GraphProblem | None,
            bundle: BundleConfig,
        ) -> None:
            calls.append(
                (
                    "exit_resolve",
                    entry.concept_id,
                    links is not None,
                    problem is not None,
                )
            )

    from okf_core import hooks

    original_get_hook_manager = hooks.get_hook_manager

    def mock_get_hook_manager(b: BundleConfig):
        pm = original_get_hook_manager(b)
        pm.register(TrackingPlugin())
        return pm

    from unittest.mock import patch

    with patch("okf_core.hooks.get_hook_manager", mock_get_hook_manager):
        # 1. First run: cache is empty
        manifest = scan_bundle(bundle)
        graph = build_bundle_graph(bundle, manifest=manifest)
        assert len(graph.links) == 1

        # Verify calls for a.md
        assert calls.count(("enter_scan", "a.md")) == 1
        assert calls.count(("fetch_scan", "a.md")) == 1
        assert ("exit_scan", "a.md", True, False) in calls

        # Verify calls for b.md
        assert calls.count(("enter_scan", "b.md")) == 1
        assert calls.count(("fetch_scan", "b.md")) == 1
        assert ("exit_scan", "b.md", True, False) in calls

        # Verify resolve calls for a
        assert calls.count(("enter_resolve", "a")) == 1
        assert calls.count(("fetch_resolve", "a")) == 1
        assert ("exit_resolve", "a", True, False) in calls

        calls.clear()

        # 2. Second run: cache is populated
        manifest2 = scan_bundle(bundle)
        graph2 = build_bundle_graph(bundle, manifest=manifest2)
        assert len(graph2.links) == 1

        # Verify calls for a.md still fire symmetrically on cache hit
        assert calls.count(("enter_scan", "a.md")) == 1
        assert calls.count(("fetch_scan", "a.md")) == 1
        assert ("exit_scan", "a.md", True, False) in calls

        # Verify resolve calls for a still fire symmetrically on cache hit
        assert calls.count(("enter_resolve", "a")) == 1
        assert calls.count(("fetch_resolve", "a")) == 1
        assert ("exit_resolve", "a", True, False) in calls

        # 3. Third run: failure path testing
        # Add a malformed concept file that causes parse-error during scanning
        _write_concept(
            root / "malformed.md", "type: concept\nmalformed_yaml:\n  - [abc\n"
        )
        calls.clear()

        _manifest3 = scan_bundle(bundle)
        # Verify that exit scan was called for malformed.md and it received the problem (entry=None, problem=True)
        assert calls.count(("enter_scan", "malformed.md")) == 1
        assert calls.count(("fetch_scan", "malformed.md")) == 1
        assert ("exit_scan", "malformed.md", False, True) in calls

        # 4. Fourth run: link resolution failure.
        # We write c.md, scan it with a bundle config that has caching DISABLED.
        _write_concept(root / "c.md", "type: concept\n")
        no_cache_bundle = BundleConfig(
            name="docs",
            bundle_root=root,
            include=("**/*.md",),
            exclude=(),
            reserved_filenames=("index.md", "log.md"),
            concept_path_strategy="relative-path",
            okf_cache_dir=None,
        )
        manifest_no_cache = scan_bundle(no_cache_bundle)

        # Clear the in-memory content cache of the entry for c to force a disk read
        c_entry = next(e for e in manifest_no_cache.concepts if e.concept_id == "c")
        object.__setattr__(c_entry, "_content_cache", None)

        # Now delete c.md so that reading it raises OSError
        (root / "c.md").unlink()
        calls.clear()

        # Build graph with caching ENABLED
        _graph3 = build_bundle_graph(bundle, manifest=manifest_no_cache)
        # We expect exit_resolve to be called for c with links=None, problem=True
        assert ("exit_resolve", "c", False, True) in calls

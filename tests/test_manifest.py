from __future__ import annotations
from typing import Any

from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

import pytest

from okf_core import BundleConfig, scan_bundle
from okf_core.manifest import ConceptManifestEntry


def test_scan_bundle_finds_nested_concepts(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    _write_concept(root / "topics" / "example.md", title="Example")

    manifest = scan_bundle(_bundle("docs", root))

    assert manifest.bundle_name == "docs"
    assert manifest.problems == ()
    assert len(manifest.concepts) == 1
    assert manifest.concepts[0].concept_id == "topics/example"
    assert manifest.concepts[0].path == root / "topics" / "example.md"
    assert manifest.concepts[0].bundle_root == root
    assert manifest.concepts[0].frontmatter == {
        "type": "concept",
        "title": "Example",
    }


def test_scan_bundle_applies_include_and_exclude_globs(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "concepts" / "keep.md", title="Keep")
    _write_concept(root / "concepts" / "drafts" / "skip.md", title="Skip")
    _write_concept(root / "other.md", title="Other")

    manifest = scan_bundle(
        _bundle(
            "filtered",
            root,
            include=("concepts/**/*.md",),
            exclude=("**/drafts/**",),
        )
    )

    assert [entry.concept_id for entry in manifest.concepts] == ["concepts/keep"]


def test_scan_bundle_ignores_reserved_filenames(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "topic.md", title="Topic")
    _write_concept(root / "index.md", title="Index")
    _write_concept(root / "nested" / "LOG.md", title="Log")

    manifest = scan_bundle(_bundle("docs", root))

    assert [entry.concept_id for entry in manifest.concepts] == ["topic"]
    assert manifest.problems == ()


def test_scan_bundle_records_hash_mtime_size_and_frontmatter(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    path = root / "topic.md"
    content = _write_concept(path, title="Topic", extra="kept")
    stat = path.stat()

    manifest = scan_bundle(_bundle("docs", root))
    entry = manifest.concepts[0]

    assert entry.sha256 == sha256(content.encode("utf-8")).hexdigest()
    assert entry.mtime_ns == stat.st_mtime_ns
    assert entry.size == len(content.encode("utf-8"))
    assert entry.frontmatter == {
        "type": "concept",
        "title": "Topic",
        "extra": "kept",
    }
    assert entry.content == content


def test_scan_bundle_content_uses_scanned_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    content = _write_concept(root / "topic.md", title="Topic")
    entry = scan_bundle(_bundle("docs", root)).concepts[0]

    def fail_read_bytes(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("content should come from the scanned snapshot")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    assert entry.content == content


def test_manual_manifest_entry_content_lazy_loads_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\ntitle: Topic\n---\nBody\n", encoding="utf-8")
    entry = ConceptManifestEntry(
        concept_id="topic",
        path=path,
        bundle_root=tmp_path,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType({"type": "concept"}),
    )
    original_read_bytes = Path.read_bytes
    calls = 0

    def counting_read_bytes(
        read_path: Path,
        *args: object,
        **kwargs: object,
    ) -> bytes:
        nonlocal calls
        calls += 1
        return original_read_bytes(read_path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    first = entry.content
    second = entry.content

    assert first == second
    assert calls == 1


def test_scan_bundle_size_matches_hashed_content_when_stat_size_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    path = root / "topic.md"
    content = _write_concept(path, title="Topic")
    original_stat = Path.stat

    def stale_size_stat(
        stat_path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        stat_result = original_stat(stat_path, *args, **kwargs)
        if stat_path == path:
            values = list(stat_result)
            values[6] = stat_result.st_size + 100
            return type(stat_result)(values)
        return stat_result

    monkeypatch.setattr(Path, "stat", stale_size_stat)

    entry = scan_bundle(_bundle("docs", root)).concepts[0]

    assert entry.sha256 == sha256(content.encode("utf-8")).hexdigest()
    assert entry.size == len(content.encode("utf-8"))


def test_scan_bundle_freezes_frontmatter_values(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    path = root / "topic.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
type: concept
title: Topic
metadata:
  owners:
    - docs
---
Body
""",
        encoding="utf-8",
    )

    entry = scan_bundle(_bundle("docs", root)).concepts[0]

    assert entry.frontmatter == {
        "type": "concept",
        "title": "Topic",
        "metadata": {"owners": ("docs",)},
    }
    with pytest.raises(TypeError):
        entry.frontmatter["title"] = "Changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        entry.frontmatter["metadata"]["status"] = "draft"  # type: ignore[index]


def test_scan_bundle_reports_malformed_documents_without_aborting(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "valid.md", title="Valid")
    malformed = root / "broken.md"
    malformed.write_text("---\ntype: [unterminated\n---\nBody\n", encoding="utf-8")

    manifest = scan_bundle(_bundle("docs", root))

    assert [entry.concept_id for entry in manifest.concepts] == ["valid"]
    assert len(manifest.problems) == 1
    assert manifest.problems[0].path == malformed
    assert manifest.problems[0].kind == "parse-error"
    assert "Invalid YAML frontmatter" in manifest.problems[0].message


def test_scan_bundle_orders_entries_deterministically(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "zeta.md", title="Zeta")
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "nested" / "beta.md", title="Beta")

    manifest = scan_bundle(_bundle("docs", root))

    assert [entry.concept_id for entry in manifest.concepts] == [
        "alpha",
        "nested/beta",
        "zeta",
    ]


def test_scan_bundle_deduplicates_repeated_include_matches(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "topic.md", title="Topic")

    manifest = scan_bundle(_bundle("docs", root, include=("**/*.md", "topic.md")))

    assert [entry.concept_id for entry in manifest.concepts] == ["topic"]


def test_scan_bundle_normalizes_date_frontmatter_to_iso_string(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    path = root / "dated.md"
    path.parent.mkdir(parents=True)
    # PyYAML parses bare YYYY-MM-DD values as datetime.date; _freeze_value must
    # convert them to ISO-8601 strings so callers never receive raw date objects.
    path.write_text(
        "---\ntype: concept\ntitle: Dated\ntimestamp: 2024-01-01\n---\nBody\n",
        encoding="utf-8",
    )

    entry = scan_bundle(_bundle("docs", root)).concepts[0]

    assert entry.frontmatter["timestamp"] == "2024-01-01"
    assert isinstance(entry.frontmatter["timestamp"], str)


def test_scan_bundle_returns_empty_manifest_for_missing_bundle_root(
    tmp_path: Path,
) -> None:
    manifest = scan_bundle(_bundle("docs", tmp_path / "missing"))

    assert manifest.concepts == ()
    assert manifest.problems == ()


def test_scan_bundle_expands_tilde_in_bundle_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    root = home / "docs"
    _write_concept(root / "topic.md", title="Topic")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    bundle = BundleConfig(
        name="docs",
        bundle_root=Path("~/docs"),
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
    )

    assert bundle.bundle_root == root.resolve()

    manifest = scan_bundle(bundle)

    assert len(manifest.concepts) == 1
    assert manifest.concepts[0].concept_id == "topic"
    assert manifest.concepts[0].bundle_root == root.resolve()


def _bundle(
    name: str,
    root: Path,
    include: tuple[str, ...] = ("**/*.md",),
    exclude: tuple[str, ...] = (),
) -> BundleConfig:
    return BundleConfig(
        name=name,
        bundle_root=root.resolve(strict=False),
        include=include,
        exclude=exclude,
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
    )


def _write_concept(path: Path, *, title: str, extra: str | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    extra_line = f"extra: {extra}\n" if extra is not None else ""
    content = f"---\ntype: concept\ntitle: {title}\n{extra_line}---\nBody\n"
    path.write_text(content, encoding="utf-8", newline="\n")
    return content


def test_scan_bundle_extracts_stable_id(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha", extra="uuid-123")

    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        stable_id_field="extra",
    )

    manifest = scan_bundle(bundle)
    assert len(manifest.concepts) == 1
    assert manifest.concepts[0].stable_id == "uuid-123"
    assert manifest.problems == ()


def test_scan_bundle_reports_missing_stable_id(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha")  # missing "extra" field
    _write_concept(root / "b.md", title="Beta", extra="  ")  # empty/whitespace

    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        stable_id_field="extra",
    )

    manifest = scan_bundle(bundle)
    assert len(manifest.concepts) == 2
    assert manifest.concepts[0].stable_id is None
    assert manifest.concepts[1].stable_id is None

    assert len(manifest.problems) == 2
    problems = sorted(manifest.problems, key=lambda p: p.path.name)
    assert problems[0].kind == "stable-id-missing"
    assert problems[0].path.name == "a.md"
    assert problems[1].kind == "stable-id-missing"
    assert problems[1].path.name == "b.md"


def test_scan_bundle_reports_stable_id_conflicts(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "a.md", title="Alpha", extra="shared-uuid")
    _write_concept(root / "b.md", title="Beta", extra="shared-uuid")
    _write_concept(root / "c.md", title="Gamma", extra="shared-uuid")
    _write_concept(root / "d.md", title="Delta", extra="unique-uuid")

    bundle = BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        stable_id_field="extra",
    )

    manifest = scan_bundle(bundle)
    assert len(manifest.concepts) == 4

    # Check that a, b, c generate stable-id-conflict problems
    conflict_problems = [p for p in manifest.problems if p.kind == "stable-id-conflict"]
    assert len(conflict_problems) == 3
    conflict_paths = {p.path.name for p in conflict_problems}
    assert conflict_paths == {"a.md", "b.md", "c.md"}

    # Check message contains the other conflicting files
    a_problem = next(p for p in conflict_problems if p.path.name == "a.md")
    assert "b.md" in a_problem.message
    assert "c.md" in a_problem.message
    assert "d.md" not in a_problem.message

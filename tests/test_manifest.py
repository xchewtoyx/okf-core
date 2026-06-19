from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from okf_core import BundleConfig, scan_bundle


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


def test_scan_bundle_supports_multiple_roots(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    notes = tmp_path / "notes"
    _write_concept(docs / "alpha.md", title="Alpha")
    _write_concept(notes / "nested" / "beta.md", title="Beta")

    manifest = scan_bundle(_bundle("mixed", docs, notes))

    assert [entry.concept_id for entry in manifest.concepts] == [
        "alpha",
        "nested/beta",
    ]
    assert {entry.bundle_root for entry in manifest.concepts} == {docs, notes}


def test_scan_bundle_prefers_nested_bundle_root_ownership(tmp_path: Path) -> None:
    parent = tmp_path / "project"
    nested = parent / "knowledge"
    _write_concept(nested / "topic.md", title="Topic")

    manifest = scan_bundle(_bundle("nested", parent, nested))

    assert len(manifest.concepts) == 1
    assert manifest.concepts[0].concept_id == "topic"
    assert manifest.concepts[0].path == nested / "topic.md"
    assert manifest.concepts[0].bundle_root == nested


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
    assert entry.size == stat.st_size
    assert entry.frontmatter == {
        "type": "concept",
        "title": "Topic",
        "extra": "kept",
    }


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


def test_scan_bundle_skips_missing_bundle_roots(tmp_path: Path) -> None:
    existing = tmp_path / "docs"
    _write_concept(existing / "topic.md", title="Topic")

    manifest = scan_bundle(_bundle("docs", tmp_path / "missing", existing))

    assert [entry.concept_id for entry in manifest.concepts] == ["topic"]
    assert manifest.problems == ()


def _bundle(
    name: str,
    *roots: Path,
    include: tuple[str, ...] = ("**/*.md",),
    exclude: tuple[str, ...] = (),
) -> BundleConfig:
    return BundleConfig(
        name=name,
        bundle_roots=tuple(root.resolve(strict=False) for root in roots),
        include=include,
        exclude=exclude,
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        index_cache=Path(".okf-cache"),
    )


def _write_concept(path: Path, *, title: str, extra: str | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    extra_line = f"extra: {extra}\n" if extra is not None else ""
    content = f"---\ntype: concept\ntitle: {title}\n{extra_line}---\nBody\n"
    path.write_text(content, encoding="utf-8")
    return content

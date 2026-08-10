"""Unit tests for the private index-generation helpers extracted from index_cmd."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

import okf_core.cli as cli_module
from okf_core.change_envelope import DocumentChangeConflictError
from okf_core.cli import _concept_directories, _echo_index_result, _index_one_directory
from okf_core.config import BundleConfig
from okf_core.manifest import BundleManifest, ConceptManifestEntry, ManifestProblem


def _entry(
    path: Path,
    bundle_root: Path,
    *,
    concept_id: str = "stub",
    title: str | None = None,
) -> ConceptManifestEntry:
    return ConceptManifestEntry(
        concept_id=concept_id,
        path=path,
        bundle_root=bundle_root,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType({"type": "concept", "title": title or concept_id}),
    )


def _bundle(bundle_root: Path) -> BundleConfig:
    return BundleConfig(
        name="docs",
        bundle_root=bundle_root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md",),
        concept_path_strategy="relative-path",
    )


# ---------------------------------------------------------------------------
# _concept_directories
# ---------------------------------------------------------------------------


def test_concept_directories_includes_nested_intermediate_dirs(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    resolved_bundle_root = bundle_root.resolve()

    nested_path = bundle_root / "a" / "b" / "c.md"
    nested_path.parent.mkdir(parents=True)
    nested_entry = _entry(nested_path, bundle_root, concept_id="nested")

    manifest = BundleManifest(bundle_name="docs", concepts=(nested_entry,))

    concept_dirs, concepts_by_parent = _concept_directories(
        manifest, resolved_bundle_root
    )

    assert concept_dirs == {
        resolved_bundle_root,
        (bundle_root / "a").resolve(),
        (bundle_root / "a" / "b").resolve(),
    }
    assert concepts_by_parent == {(bundle_root / "a" / "b").resolve(): [nested_entry]}


def test_concept_directories_swallows_out_of_root_concept(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    resolved_bundle_root = bundle_root.resolve()

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_path = outside_dir / "x.md"
    outside_entry = _entry(outside_path, bundle_root, concept_id="outside")

    manifest = BundleManifest(bundle_name="docs", concepts=(outside_entry,))

    concept_dirs, concepts_by_parent = _concept_directories(
        manifest, resolved_bundle_root
    )

    # No ValueError propagates, and the out-of-root directory never becomes an
    # ancestor entry in concept_dirs (the walk that populates it is skipped
    # once relative_to() raises).
    assert concept_dirs == {resolved_bundle_root}
    assert outside_dir.resolve() not in concept_dirs
    # concepts_by_parent still records it under its own parent, since that
    # assignment happens before the relative_to() check that raises.
    assert concepts_by_parent == {outside_dir.resolve(): [outside_entry]}


# ---------------------------------------------------------------------------
# _index_one_directory
# ---------------------------------------------------------------------------


def test_index_one_directory_matches_only_problems_within_directory(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    sub_dir = bundle_root / "sub"
    sub_dir.mkdir()
    bundle = _bundle(bundle_root)

    inside_problem = ManifestProblem(
        path=sub_dir / "broken.md",
        kind="parse-error",
        message="bad frontmatter",
    )
    outside_problem = ManifestProblem(
        path=bundle_root / "other" / "broken2.md",
        kind="parse-error",
        message="bad frontmatter 2",
    )
    problems_resolved = [
        (inside_problem.path.resolve(), inside_problem),
        (outside_problem.path.resolve(), outside_problem),
    ]

    result = _index_one_directory(
        sub_dir.resolve(),
        bundle=bundle,
        concepts_by_parent={},
        concept_dirs={sub_dir.resolve()},
        problems_resolved=problems_resolved,
        profile_cfg=None,
        project_taxonomy=None,
        force=False,
    )

    assert result["scan_problems"] == [
        {
            "path": str(inside_problem.path),
            "kind": "parse-error",
            "message": "bad frontmatter",
        }
    ]
    assert result["entries"] == 0
    assert result["problems"] == []
    assert result["excluded_reserved_files"] == []
    assert result["write_conflict"] is None
    # A directory with zero entries and zero subdirs generates an empty
    # index body; apply_document_change treats "empty proposed content
    # against a missing target" as a no-op (matching original_sha256), so
    # no index.md is created here -- unlike test_index_one_directory_writes_
    # direct_entries below, where the generated body is non-empty.
    assert not (sub_dir / "index.md").exists()


def test_index_one_directory_writes_direct_entries(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    bundle = _bundle(bundle_root)

    concept_path = bundle_root / "alpha.md"
    concept_path.write_text(
        "---\ntype: concept\ntitle: Alpha\n---\nBody\n", encoding="utf-8"
    )
    entry = _entry(concept_path, bundle_root, concept_id="alpha", title="Alpha")
    resolved_bundle_root = bundle_root.resolve()

    result = _index_one_directory(
        resolved_bundle_root,
        bundle=bundle,
        concepts_by_parent={resolved_bundle_root: [entry]},
        concept_dirs={resolved_bundle_root},
        problems_resolved=[],
        profile_cfg=None,
        project_taxonomy=None,
        force=False,
    )

    assert result["entries"] == 1
    assert result["problems"] == []
    assert result["scan_problems"] == []
    index_path = bundle_root / "index.md"
    assert index_path.is_file()
    assert "Alpha" in index_path.read_text(encoding="utf-8")


def test_index_one_directory_reports_zero_entries_on_write_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write conflict must never let the discarded body's candidate count
    leak into ``entries`` -- ``write_conflict`` means index.md was NOT
    written, so ``entries`` must report 0, not the count from the generated
    body that never reached disk."""
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    bundle = _bundle(bundle_root)

    concept_path = bundle_root / "alpha.md"
    concept_path.write_text(
        "---\ntype: concept\ntitle: Alpha\n---\nBody\n", encoding="utf-8"
    )
    entry = _entry(concept_path, bundle_root, concept_id="alpha", title="Alpha")
    resolved_bundle_root = bundle_root.resolve()

    def raising_plan_document_change(*args: Any, **kwargs: Any) -> Any:
        index_path = resolved_bundle_root / "index.md"
        raise DocumentChangeConflictError(index_path, "deadbeef", "cafef00d")

    monkeypatch.setattr(
        cli_module, "plan_document_change", raising_plan_document_change
    )

    result = _index_one_directory(
        resolved_bundle_root,
        bundle=bundle,
        concepts_by_parent={resolved_bundle_root: [entry]},
        concept_dirs={resolved_bundle_root},
        problems_resolved=[],
        profile_cfg=None,
        project_taxonomy=None,
        force=False,
    )

    assert result["write_conflict"] is not None
    assert result["entries"] == 0
    assert not (bundle_root / "index.md").exists()


# ---------------------------------------------------------------------------
# _echo_index_result
# ---------------------------------------------------------------------------


def test_echo_index_result_on_conflict_omits_success_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """On a conflict, only the conflict message (plus a "not written" note)
    should be echoed -- never the success-style "Wrote index.md" line, which
    would contradict it."""
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    bundle = _bundle(bundle_root)
    dir_result = {
        "path": str(bundle_root / "index.md"),
        "entries": 0,
        "problems": [],
        "scan_problems": [],
        "excluded_reserved_files": [],
        "write_conflict": "Document changed after planning: ...",
    }

    _echo_index_result(bundle, bundle_root.resolve(), dir_result, recurse=False)

    captured = capsys.readouterr()
    assert "Document changed after planning" in captured.err
    assert "not written" in captured.err
    assert "Wrote index.md" not in captured.err


def test_echo_index_result_without_conflict_prints_success_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a conflict, the usual success-style summary line is printed."""
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    bundle = _bundle(bundle_root)
    dir_result = {
        "path": str(bundle_root / "index.md"),
        "entries": 3,
        "problems": [],
        "scan_problems": [],
        "excluded_reserved_files": [],
        "write_conflict": None,
    }

    _echo_index_result(bundle, bundle_root.resolve(), dir_result, recurse=False)

    captured = capsys.readouterr()
    assert "Wrote index.md" in captured.err
    assert "3 entries" in captured.err

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from okf_core import (
    BundleConfig,
    DocumentChangeApplyError,
    DocumentChangeConflictError,
    DocumentChangePlanningError,
    DocumentChangeSafetyError,
    apply_document_change,
    plan_document_change,
)
from okf_core import patching


def _bundle(root: Path) -> BundleConfig:
    return BundleConfig(
        name="test",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
    )


def _digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def test_plan_is_inspectable_without_mutating_target(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "Original\r\n"
    proposed = "Updated\r\n"
    path.write_bytes(original.encode())
    before = path.stat()

    plan = plan_document_change(_bundle(tmp_path), "topic.md", proposed)

    assert plan.bundle_root == tmp_path.resolve()
    assert plan.path == path.resolve()
    assert plan.original_content == original
    assert plan.proposed_content == proposed
    assert plan.original_sha256 == _digest(original.encode())
    assert plan.proposed_sha256 == _digest(proposed.encode())
    assert plan.changed is True
    assert path.read_bytes() == original.encode()
    assert path.stat().st_mtime_ns == before.st_mtime_ns


def test_plan_detects_noop(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Same\n", encoding="utf-8", newline="")

    plan = plan_document_change(_bundle(tmp_path), path, "Same\n")

    assert plan.changed is False
    assert plan.original_sha256 == plan.proposed_sha256


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("missing.md", "does not exist"),
        ("directory", "regular file"),
    ],
)
def test_plan_rejects_invalid_target(tmp_path: Path, target: str, message: str) -> None:
    (tmp_path / "directory").mkdir()

    with pytest.raises(DocumentChangePlanningError, match=message):
        plan_document_change(_bundle(tmp_path), target, "Proposed\n")


def test_plan_rejects_target_outside_bundle(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("Outside\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="outside bundle root"):
        plan_document_change(_bundle(root), outside, "Proposed\n")


def test_plan_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    target.write_text("Target\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(target)

    with pytest.raises(DocumentChangePlanningError, match="symbolic link"):
        plan_document_change(_bundle(tmp_path), link, "Proposed\n")


def test_plan_rejects_non_utf8_target(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_bytes(b"\xff")

    with pytest.raises(DocumentChangePlanningError, match="UTF-8"):
        plan_document_change(_bundle(tmp_path), path, "Proposed\n")


def test_plan_reports_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")

    def fail_read_bytes(self: Path) -> bytes:
        raise OSError("read failed")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    with pytest.raises(DocumentChangePlanningError, match="read failed"):
        plan_document_change(_bundle(tmp_path), path, "Proposed\n")


def test_plan_rejects_non_string_proposed_content(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="must be a string"):
        plan_document_change(_bundle(tmp_path), path, b"Proposed\n")  # type: ignore[arg-type]


def test_plan_reports_proposed_content_that_cannot_encode_as_utf8(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="encode.*UTF-8") as caught:
        plan_document_change(_bundle(tmp_path), path, "\ud800")

    assert isinstance(caught.value.__cause__, UnicodeEncodeError)
    assert path.read_text(encoding="utf-8") == "Original\n"


def test_plan_rejects_unsafe_bundle_version(tmp_path: Path) -> None:
    index_path = tmp_path / "index.md"
    index_path.write_text(
        "---\nokf_version: '1.0'\n---\n# Index\n",
        encoding="utf-8",
    )
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")

    with pytest.raises(DocumentChangeSafetyError, match="unsupported") as caught:
        plan_document_change(_bundle(tmp_path), path, "Proposed\n")

    assert caught.value.path == index_path


def test_apply_replaces_content_and_preserves_mode(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    path.chmod(0o640)
    plan = plan_document_change(_bundle(tmp_path), path, "Updated\r\n")

    result = apply_document_change(_bundle(tmp_path), plan)

    assert result.path == path.resolve()
    assert result.original_sha256 == plan.original_sha256
    assert result.resulting_sha256 == plan.proposed_sha256
    assert result.changed is True
    assert path.read_bytes() == b"Updated\r\n"
    assert path.stat().st_mode & 0o777 == 0o640


def test_apply_noop_does_not_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Same\n", encoding="utf-8")
    plan = plan_document_change(_bundle(tmp_path), path, "Same\n")
    before = path.stat()

    result = apply_document_change(_bundle(tmp_path), plan)

    after = path.stat()
    assert result.changed is False
    assert result.original_sha256 == result.resulting_sha256
    assert after.st_ino == before.st_ino
    assert after.st_mtime_ns == before.st_mtime_ns


def test_apply_reports_stale_content(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    plan = plan_document_change(_bundle(tmp_path), path, "Proposed\n")
    path.write_text("Concurrent\n", encoding="utf-8")

    with pytest.raises(DocumentChangeConflictError) as caught:
        apply_document_change(_bundle(tmp_path), plan)

    assert caught.value.path == path.resolve()
    assert caught.value.expected_sha256 == plan.original_sha256
    assert caught.value.actual_sha256 == _digest(b"Concurrent\n")
    assert path.read_text(encoding="utf-8") == "Concurrent\n"


def test_apply_reports_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    plan = plan_document_change(_bundle(tmp_path), path, "Proposed\n")

    def fail_read_bytes(self: Path) -> bytes:
        raise OSError("read failed")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    with pytest.raises(DocumentChangeApplyError, match="read failed"):
        apply_document_change(_bundle(tmp_path), plan)

    assert path.read_text(encoding="utf-8") == "Original\n"


def test_apply_reports_proposed_content_that_cannot_encode_as_utf8(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    plan = plan_document_change(_bundle(tmp_path), path, "Proposed\n")
    malformed_plan = replace(plan, proposed_content="\ud800")

    with pytest.raises(DocumentChangeApplyError, match="encode.*UTF-8") as caught:
        apply_document_change(_bundle(tmp_path), malformed_plan)

    assert isinstance(caught.value.__cause__, UnicodeEncodeError)
    assert path.read_text(encoding="utf-8") == "Original\n"


@pytest.mark.parametrize("replacement", ["missing", "symlink"])
def test_apply_reports_replaced_target(tmp_path: Path, replacement: str) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    plan = plan_document_change(_bundle(tmp_path), path, "Proposed\n")
    path.unlink()
    if replacement == "symlink":
        other = tmp_path / "other.md"
        other.write_text("Other\n", encoding="utf-8")
        path.symlink_to(other)

    with pytest.raises(DocumentChangeConflictError) as caught:
        apply_document_change(_bundle(tmp_path), plan)

    assert caught.value.actual_sha256 is None


def test_apply_reports_parent_replaced_by_symlink(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    original_parent = bundle_root / "topics"
    original_parent.mkdir(parents=True)
    path = original_parent / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    bundle = _bundle(bundle_root)
    plan = plan_document_change(bundle, path, "Proposed\n")

    moved_parent = bundle_root / "topics-old"
    original_parent.rename(moved_parent)
    outside_parent = tmp_path / "outside"
    outside_parent.mkdir()
    (outside_parent / "topic.md").write_text("Outside\n", encoding="utf-8")
    original_parent.symlink_to(outside_parent, target_is_directory=True)

    with pytest.raises(DocumentChangeConflictError) as caught:
        apply_document_change(bundle, plan)

    assert caught.value.actual_sha256 is None
    assert (outside_parent / "topic.md").read_text(encoding="utf-8") == "Outside\n"


def test_apply_rejects_different_bundle(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    path = first / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    plan = plan_document_change(_bundle(first), path, "Proposed\n")

    with pytest.raises(DocumentChangeApplyError, match="belongs to bundle root"):
        apply_document_change(_bundle(second), plan)


def test_apply_rechecks_bundle_safety(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    plan = plan_document_change(_bundle(tmp_path), path, "Proposed\n")
    index_path = tmp_path / "index.md"
    index_path.write_text(
        "---\nokf_version: '1.0'\n---\n# Index\n",
        encoding="utf-8",
    )

    with pytest.raises(DocumentChangeSafetyError, match="unsupported") as caught:
        apply_document_change(_bundle(tmp_path), plan)

    assert caught.value.path == index_path
    assert path.read_text(encoding="utf-8") == "Original\n"


def test_apply_rechecks_hash_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    plan = plan_document_change(_bundle(tmp_path), path, "Proposed\n")
    original_write = patching._write_temporary_file

    def concurrent_write(target: Path, content: bytes, mode: int) -> Path:
        temp_path = original_write(target, content, mode)
        target.write_text("Concurrent\n", encoding="utf-8")
        return temp_path

    monkeypatch.setattr(patching, "_write_temporary_file", concurrent_write)

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(_bundle(tmp_path), plan)

    assert path.read_text(encoding="utf-8") == "Concurrent\n"
    assert not tuple(tmp_path.glob(".topic.md.*.tmp"))


@pytest.mark.parametrize(
    "failure_point", ["mkstemp", "write", "fsync", "chmod", "replace"]
)
def test_apply_failure_preserves_original_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("Original\n", encoding="utf-8")
    plan = plan_document_change(_bundle(tmp_path), path, "Proposed\n")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError(f"{failure_point} failed")

    if failure_point == "mkstemp":
        monkeypatch.setattr(patching.tempfile, "mkstemp", fail)
    else:
        monkeypatch.setattr(patching.os, failure_point, fail)

    with pytest.raises(DocumentChangeApplyError, match="Could not apply"):
        apply_document_change(_bundle(tmp_path), plan)

    assert path.read_text(encoding="utf-8") == "Original\n"
    assert not tuple(tmp_path.glob(".topic.md.*.tmp"))

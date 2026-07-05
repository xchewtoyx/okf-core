from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import (
    BundleConfig,
    DocumentChangeApplyError,
    DocumentChangePlanningError,
    DocumentChangeSafetyError,
    FileMoveConflictError,
    apply_file_move,
    plan_file_move,
)


def _bundle(root: Path) -> BundleConfig:
    return BundleConfig(
        name="test",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
    )


def _can_symlink() -> bool:
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            target = p / "target"
            target.touch()
            link = p / "link"
            link.symlink_to(target)
            return True
    except (OSError, NotImplementedError):
        return False


def test_plan_and_apply_file_move_success(tmp_path: Path) -> None:
    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    dest = tmp_path / "new.md"

    plan = plan_file_move(_bundle(tmp_path), source, dest)
    result = apply_file_move(_bundle(tmp_path), plan)

    assert result.moved is True
    assert not source.exists()
    assert dest.read_text(encoding="utf-8") == "Body.\n"


def test_plan_file_move_noop_when_source_equals_dest(tmp_path: Path) -> None:
    source = tmp_path / "topic.md"
    source.write_text("Body.\n", encoding="utf-8")

    plan = plan_file_move(_bundle(tmp_path), source, source)

    assert plan.noop is True


def test_apply_file_move_noop_does_not_touch_filesystem(tmp_path: Path) -> None:
    source = tmp_path / "topic.md"
    source.write_text("Body.\n", encoding="utf-8")
    plan = plan_file_move(_bundle(tmp_path), source, source)
    mtime_before = source.stat().st_mtime_ns

    result = apply_file_move(_bundle(tmp_path), plan)

    assert result.moved is False
    assert source.stat().st_mtime_ns == mtime_before
    assert source.read_text(encoding="utf-8") == "Body.\n"


def test_plan_file_move_source_not_found_raises_planning_error(tmp_path: Path) -> None:
    with pytest.raises(DocumentChangePlanningError, match="does not exist"):
        plan_file_move(_bundle(tmp_path), tmp_path / "missing.md", tmp_path / "new.md")


def test_plan_file_move_source_is_symlink_raises_planning_error(tmp_path: Path) -> None:
    if not _can_symlink():
        pytest.skip("System does not support symlinks or requires elevated privileges")
    target = tmp_path / "target.md"
    target.write_text("Body.\n", encoding="utf-8")
    source = tmp_path / "link.md"
    source.symlink_to(target)

    with pytest.raises(DocumentChangePlanningError, match="symbolic link"):
        plan_file_move(_bundle(tmp_path), source, tmp_path / "new.md")


def test_plan_file_move_source_not_found_error_uses_move_specific_wording(
    tmp_path: Path,
) -> None:
    with pytest.raises(DocumentChangePlanningError, match="Move source does not exist"):
        plan_file_move(_bundle(tmp_path), tmp_path / "missing.md", tmp_path / "new.md")


def test_plan_file_move_dest_symlink_to_source_is_rejected_not_treated_as_noop(
    tmp_path: Path,
) -> None:
    if not _can_symlink():
        pytest.skip("System does not support symlinks or requires elevated privileges")
    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    dest = tmp_path / "link.md"
    dest.symlink_to(source)

    with pytest.raises(DocumentChangePlanningError, match="symbolic link"):
        plan_file_move(_bundle(tmp_path), source, dest)


def test_plan_file_move_dest_already_exists_raises_planning_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    dest = tmp_path / "new.md"
    dest.write_text("Existing.\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="already exists"):
        plan_file_move(_bundle(tmp_path), source, dest)

    assert dest.read_text(encoding="utf-8") == "Existing.\n"


def test_plan_file_move_dest_outside_bundle_root_raises_planning_error(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    source = bundle_root / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    outside_dest = tmp_path / "outside.md"

    with pytest.raises(DocumentChangePlanningError, match="outside bundle root"):
        plan_file_move(_bundle(bundle_root), source, outside_dest)


def test_plan_file_move_dest_outside_bundle_root_error_uses_move_specific_wording(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    source = bundle_root / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    outside_dest = tmp_path / "outside.md"

    with pytest.raises(
        DocumentChangePlanningError, match="Move destination is outside bundle root"
    ):
        plan_file_move(_bundle(bundle_root), source, outside_dest)


def test_plan_file_move_dest_is_symlink_raises_planning_error(tmp_path: Path) -> None:
    if not _can_symlink():
        pytest.skip("System does not support symlinks or requires elevated privileges")
    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    target = tmp_path / "elsewhere.md"
    target.write_text("Other.\n", encoding="utf-8")
    dest = tmp_path / "new.md"
    dest.symlink_to(target)

    with pytest.raises(DocumentChangePlanningError, match="symbolic link"):
        plan_file_move(_bundle(tmp_path), source, dest)


def test_apply_file_move_creates_missing_parent_directories(tmp_path: Path) -> None:
    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    dest = tmp_path / "nested" / "dir" / "new.md"

    plan = plan_file_move(_bundle(tmp_path), source, dest)
    result = apply_file_move(_bundle(tmp_path), plan)

    assert result.moved is True
    assert dest.read_text(encoding="utf-8") == "Body.\n"
    assert not source.exists()


def test_apply_file_move_stale_source_hash_raises_conflict_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    dest = tmp_path / "new.md"
    plan = plan_file_move(_bundle(tmp_path), source, dest)

    source.write_text("Concurrently modified.\n", encoding="utf-8")

    with pytest.raises(FileMoveConflictError):
        apply_file_move(_bundle(tmp_path), plan)

    assert not dest.exists()
    assert source.read_text(encoding="utf-8") == "Concurrently modified.\n"


def test_apply_file_move_dest_appeared_after_planning_raises_conflict_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    dest = tmp_path / "new.md"
    plan = plan_file_move(_bundle(tmp_path), source, dest)

    dest.write_text("Concurrently created.\n", encoding="utf-8")

    with pytest.raises(FileMoveConflictError):
        apply_file_move(_bundle(tmp_path), plan)

    assert dest.read_text(encoding="utf-8") == "Concurrently created.\n"
    assert source.read_text(encoding="utf-8") == "Body.\n"


def test_apply_file_move_write_safety_refused(tmp_path: Path) -> None:
    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    dest = tmp_path / "new.md"
    plan = plan_file_move(_bundle(tmp_path), source, dest)

    (tmp_path / "index.md").write_text(
        "---\nokf_version: '1.0'\n---\n# Index\n", encoding="utf-8"
    )

    with pytest.raises(DocumentChangeSafetyError, match="unsupported"):
        apply_file_move(_bundle(tmp_path), plan)

    assert source.exists()
    assert not dest.exists()


def test_apply_file_move_dest_parent_swapped_for_symlink_raises_conflict_error(
    tmp_path: Path,
) -> None:
    if not _can_symlink():
        pytest.skip("symlinks not supported in this environment")

    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    dest = tmp_path / "linkdir" / "new.md"
    plan = plan_file_move(_bundle(tmp_path), source, dest)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "linkdir").symlink_to(elsewhere)

    with pytest.raises(FileMoveConflictError):
        apply_file_move(_bundle(tmp_path), plan)

    assert source.exists()
    assert not dest.exists()
    assert list(elsewhere.iterdir()) == []


def test_apply_file_move_never_creates_directories_through_a_swapped_ancestor(
    tmp_path: Path,
) -> None:
    if not _can_symlink():
        pytest.skip("symlinks not supported in this environment")

    source = tmp_path / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    # "sub" does not exist under "elsewhere" yet -- only mkdir(parents=True)
    # would create it, and only by walking through the swapped-in symlink.
    dest = tmp_path / "linkdir" / "sub" / "new.md"
    plan = plan_file_move(_bundle(tmp_path), source, dest)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "linkdir").symlink_to(elsewhere)

    with pytest.raises(FileMoveConflictError):
        apply_file_move(_bundle(tmp_path), plan)

    assert source.exists()
    assert not dest.exists()
    # The symlink check must run before mkdir(parents=True), so no "sub"
    # directory is ever created inside the swapped-in location.
    assert list(elsewhere.iterdir()) == []


def test_apply_file_move_wrong_bundle_root_raises_apply_error(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_a.mkdir()
    root_b = tmp_path / "b"
    root_b.mkdir()
    source = root_a / "old.md"
    source.write_text("Body.\n", encoding="utf-8")
    dest = root_a / "new.md"

    plan = plan_file_move(_bundle(root_a), source, dest)

    with pytest.raises(DocumentChangeApplyError, match="bundle root"):
        apply_file_move(_bundle(root_b), plan)

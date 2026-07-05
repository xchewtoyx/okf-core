from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import (
    BundleConfig,
    ConceptPathError,
    DocumentChangeConflictError,
    DocumentChangePlan,
    DocumentChangePlanningError,
    DocumentChangeSafetyError,
    apply_document_change,
    move_concept,
    plan_move_concept,
)
import okf_core.moves as moves_module


def _bundle(root: Path) -> BundleConfig:
    return BundleConfig(
        name="test",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_move_concept_rewrites_single_referring_file(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    new = tmp_path / "new.md"

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert result.updated_files == (a,)
    assert not old.exists()
    assert new.read_text(encoding="utf-8") == "Body.\n"
    assert a.read_text(encoding="utf-8") == "See [link](new.md).\n"


def test_move_concept_rewrites_multiple_referring_files(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    b = tmp_path / "b.md"
    _write(b, "Also [link](old.md).\n")
    new = tmp_path / "new.md"

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert set(result.updated_files) == {a, b}
    assert a.read_text(encoding="utf-8") == "See [link](new.md).\n"
    assert b.read_text(encoding="utf-8") == "Also [link](new.md).\n"


def test_move_concept_dedupes_multiple_links_to_same_target_in_one_file(
    tmp_path: Path,
) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "First [x](old.md) and second [y](old.md).\n")
    new = tmp_path / "new.md"

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert (
        a.read_text(encoding="utf-8") == "First [x](new.md) and second [y](new.md).\n"
    )


def test_move_concept_idempotent_noop_when_source_equals_dest(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    mtime_a = a.stat().st_mtime_ns
    mtime_old = old.stat().st_mtime_ns

    result = move_concept(_bundle(tmp_path), old, old)

    assert result.moved is False
    assert result.updated_files == ()
    assert a.stat().st_mtime_ns == mtime_a
    assert old.stat().st_mtime_ns == mtime_old


def test_move_concept_preserves_fragment_and_query_in_rewritten_link(
    tmp_path: Path,
) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md#section?x=1).\n")
    new = tmp_path / "new.md"

    move_concept(_bundle(tmp_path), old, new)

    assert a.read_text(encoding="utf-8") == "See [link](new.md#section?x=1).\n"


def test_move_concept_preserves_absolute_style_links(tmp_path: Path) -> None:
    old = tmp_path / "topics" / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "other" / "a.md"
    _write(a, "See [link](/topics/old.md).\n")
    new = tmp_path / "topics" / "renamed.md"

    move_concept(_bundle(tmp_path), old, new)

    assert a.read_text(encoding="utf-8") == "See [link](/topics/renamed.md).\n"


def test_move_concept_rewrites_relative_links_across_sibling_and_cousin_directories(
    tmp_path: Path,
) -> None:
    old = tmp_path / "topics" / "sub1" / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "topics" / "sub2" / "a.md"
    _write(a, "See [link](../sub1/old.md).\n")
    new = tmp_path / "topics" / "sub3" / "deep" / "new.md"

    move_concept(_bundle(tmp_path), old, new)

    assert a.read_text(encoding="utf-8") == "See [link](../sub3/deep/new.md).\n"


def test_move_concept_handles_self_referential_link(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    _write(old, "Self: [self](old.md)\n")
    new = tmp_path / "renamed.md"

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert not old.exists()
    assert new.read_text(encoding="utf-8") == "Self: [self](renamed.md)\n"


def test_move_concept_ignores_unrelated_similarly_named_links(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    unrelated = tmp_path / "unrelated.md"
    _write(unrelated, "Other.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](unrelated.md).\n")
    new = tmp_path / "new.md"

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.updated_files == ()
    assert a.read_text(encoding="utf-8") == "See [link](unrelated.md).\n"


def test_move_concept_source_missing_raises_document_change_planning_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(DocumentChangePlanningError, match="does not exist"):
        move_concept(_bundle(tmp_path), tmp_path / "missing.md", tmp_path / "new.md")


def test_move_concept_dest_already_exists_raises_planning_error(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    dest = tmp_path / "new.md"
    _write(dest, "Existing.\n")

    with pytest.raises(DocumentChangePlanningError, match="already exists"):
        move_concept(_bundle(tmp_path), old, dest)


def test_move_concept_dest_outside_bundle_root_raises_concept_path_error(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    old = bundle_root / "old.md"
    _write(old, "Body.\n")
    outside = tmp_path / "outside.md"

    with pytest.raises(ConceptPathError):
        move_concept(_bundle(bundle_root), old, outside)


def test_move_concept_source_escapes_bundle_root_raises_concept_path_error(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    outside_source = tmp_path / "outside.md"
    _write(outside_source, "Body.\n")
    dest = bundle_root / "new.md"

    with pytest.raises(ConceptPathError):
        move_concept(_bundle(bundle_root), outside_source, dest)


def test_move_concept_accepts_absolute_paths_inside_bundle_root(tmp_path: Path) -> None:
    old = (tmp_path / "old.md").resolve()
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    new = (tmp_path / "new.md").resolve()

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert new.exists()
    assert a.read_text(encoding="utf-8") == "See [link](new.md).\n"


def test_move_concept_write_safety_refused_leaves_everything_untouched(
    tmp_path: Path,
) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    _write(tmp_path / "index.md", "---\nokf_version: '1.0'\n---\n# Index\n")
    new = tmp_path / "new.md"

    with pytest.raises(DocumentChangeSafetyError, match="unsupported"):
        move_concept(_bundle(tmp_path), old, new)

    assert old.exists()
    assert not new.exists()
    assert a.read_text(encoding="utf-8") == "See [link](old.md).\n"


def test_plan_move_concept_is_read_only(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    new = tmp_path / "new.md"
    mtime_old = old.stat().st_mtime_ns
    mtime_a = a.stat().st_mtime_ns

    prep = plan_move_concept(_bundle(tmp_path), old, new)

    assert prep.file_move_plan.noop is False
    assert len(prep.link_rewrite_plans) == 1
    (plan,) = prep.link_rewrite_plans.values()
    assert plan.changed is True
    assert old.exists()
    assert not new.exists()
    assert old.stat().st_mtime_ns == mtime_old
    assert a.stat().st_mtime_ns == mtime_a


def test_move_concept_resumes_after_manual_partial_application(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    b = tmp_path / "b.md"
    _write(b, "Also [link](old.md).\n")
    new = tmp_path / "new.md"
    bundle = _bundle(tmp_path)

    # Simulate a previous, interrupted run that only got as far as rewriting a.md.
    prep = plan_move_concept(bundle, old, new)
    apply_document_change(bundle, prep.link_rewrite_plans[a])
    assert a.read_text(encoding="utf-8") == "See [link](new.md).\n"
    assert old.exists()

    result = move_concept(bundle, old, new)

    assert result.moved is True
    assert not old.exists()
    assert new.read_text(encoding="utf-8") == "Body.\n"
    assert a.read_text(encoding="utf-8") == "See [link](new.md).\n"
    assert b.read_text(encoding="utf-8") == "Also [link](new.md).\n"


def test_move_concept_raises_and_leaves_source_at_original_location_on_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    b = tmp_path / "b.md"
    _write(b, "Also [link](old.md).\n")
    new = tmp_path / "new.md"
    bundle = _bundle(tmp_path)

    real_apply_document_change = moves_module.apply_document_change

    def failing_apply(bundle_arg: BundleConfig, plan: DocumentChangePlan) -> object:
        if plan.path == b:
            raise DocumentChangeConflictError(b, "deadbeef", None)
        return real_apply_document_change(bundle_arg, plan)

    monkeypatch.setattr(moves_module, "apply_document_change", failing_apply)

    with pytest.raises(DocumentChangeConflictError):
        move_concept(bundle, old, new)

    # The move itself is attempted last, so a mid-loop failure leaves the
    # concept file exactly where it started.
    assert old.exists()
    assert not new.exists()
    assert a.read_text(encoding="utf-8") == "See [link](new.md).\n"
    assert b.read_text(encoding="utf-8") == "Also [link](old.md).\n"

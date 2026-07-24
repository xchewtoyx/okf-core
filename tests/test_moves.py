from __future__ import annotations

from pathlib import Path

import pytest

import okf_core.moves as moves_module
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


def test_move_concept_percent_encodes_special_characters_in_rewritten_link(
    tmp_path: Path,
) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    new = tmp_path / "new file.md"

    move_concept(_bundle(tmp_path), old, new)

    assert a.read_text(encoding="utf-8") == "See [link](new%20file.md).\n"


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


def test_move_concept_rebases_self_link_when_directory_changes(tmp_path: Path) -> None:
    old = tmp_path / "sub1" / "old.md"
    _write(old, "Self: [self](old.md)\n")
    new = tmp_path / "sub2" / "deep" / "new.md"

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert not old.exists()
    assert new.read_text(encoding="utf-8") == "Self: [self](new.md)\n"


def test_move_concept_rebases_own_outbound_links_when_directory_changes(
    tmp_path: Path,
) -> None:
    old = tmp_path / "sub1" / "old.md"
    _write(old, "See [sibling](sibling.md).\n")
    sibling = tmp_path / "sub1" / "sibling.md"
    _write(sibling, "Body.\n")
    new = tmp_path / "sub2" / "deep" / "new.md"

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert new.read_text(encoding="utf-8") == "See [sibling](../../sub1/sibling.md).\n"
    # The sibling itself is untouched -- only its href text in the moved
    # file changed, not the sibling file or its own content.
    assert sibling.read_text(encoding="utf-8") == "Body.\n"


def test_move_concept_does_not_rebase_own_outbound_links_in_same_directory(
    tmp_path: Path,
) -> None:
    old = tmp_path / "old.md"
    _write(old, "See [sibling](sibling.md).\n")
    sibling = tmp_path / "sibling.md"
    _write(sibling, "Body.\n")
    new = tmp_path / "renamed.md"

    move_concept(_bundle(tmp_path), old, new)

    assert new.read_text(encoding="utf-8") == "See [sibling](sibling.md).\n"


def test_move_concept_preserves_absolute_style_outbound_links(tmp_path: Path) -> None:
    old = tmp_path / "sub1" / "old.md"
    _write(old, "See [abs](/sub1/sibling.md).\n")
    sibling = tmp_path / "sub1" / "sibling.md"
    _write(sibling, "Body.\n")
    new = tmp_path / "sub2" / "new.md"

    move_concept(_bundle(tmp_path), old, new)

    assert new.read_text(encoding="utf-8") == "See [abs](/sub1/sibling.md).\n"


def test_move_concept_aborts_when_a_file_fails_to_scan(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    bad = tmp_path / "bad.md"
    _write(bad, "---\ntype: concept\n")
    new = tmp_path / "new.md"

    with pytest.raises(DocumentChangePlanningError) as excinfo:
        move_concept(_bundle(tmp_path), old, new)

    assert str(bad) in str(excinfo.value)
    assert old.exists()
    assert not new.exists()
    assert a.read_text(encoding="utf-8") == "See [link](old.md).\n"


def test_move_concept_succeeds_despite_stable_id_only_manifest_problems(
    tmp_path: Path,
) -> None:
    bundle = BundleConfig(
        name="test",
        bundle_root=tmp_path,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        stable_id_field="id",
    )
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n")
    new = tmp_path / "new.md"

    result = move_concept(bundle, old, new)

    assert result.moved is True
    assert a.read_text(encoding="utf-8") == "See [link](new.md).\n"


def test_move_concept_regenerates_index_in_source_and_dest_directories(
    tmp_path: Path,
) -> None:
    old = tmp_path / "sub1" / "old.md"
    _write(old, "---\ntype: concept\n---\nBody.\n")
    _write(tmp_path / "sub1" / "index.md", "stale placeholder\n")
    new = tmp_path / "sub2" / "new.md"
    _write(tmp_path / "sub2" / "index.md", "stale placeholder\n")

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert set(result.regenerated_indexes) == {
        tmp_path / "sub1" / "index.md",
        tmp_path / "sub2" / "index.md",
    }
    source_index = (tmp_path / "sub1" / "index.md").read_text(encoding="utf-8")
    dest_index = (tmp_path / "sub2" / "index.md").read_text(encoding="utf-8")
    assert "old.md" not in source_index
    assert "* [new](new.md)" in dest_index


def test_move_concept_does_not_create_index_where_none_existed(
    tmp_path: Path,
) -> None:
    old = tmp_path / "sub1" / "old.md"
    _write(old, "---\ntype: concept\n---\nBody.\n")
    _write(tmp_path / "sub1" / "index.md", "stale placeholder\n")
    new = tmp_path / "sub2" / "new.md"

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert result.regenerated_indexes == (tmp_path / "sub1" / "index.md",)
    assert not (tmp_path / "sub2" / "index.md").exists()


def test_move_concept_regenerates_index_for_same_directory_rename(
    tmp_path: Path,
) -> None:
    old = tmp_path / "old.md"
    _write(old, "---\ntype: concept\n---\nBody.\n")
    _write(tmp_path / "index.md", "stale placeholder\n")
    new = tmp_path / "new.md"

    result = move_concept(_bundle(tmp_path), old, new)

    assert result.moved is True
    assert result.regenerated_indexes == (tmp_path / "index.md",)
    index_content = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "old.md" not in index_content
    assert "* [new](new.md)" in index_content


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


def test_move_concept_reports_all_unplannable_files_not_just_first(
    tmp_path: Path,
) -> None:
    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    # Both referring files contain a reference-style link definition elsewhere
    # in the document, unrelated to the actual link being rewritten -- this
    # makes plan_markdown_link_rewrite reject the whole file.
    a = tmp_path / "a.md"
    _write(a, "See [link](old.md).\n\n[ref]: dest\n")
    b = tmp_path / "b.md"
    _write(b, "Also [link](old.md).\n\n[ref2]: dest2\n")
    new = tmp_path / "new.md"

    with pytest.raises(DocumentChangePlanningError) as excinfo:
        move_concept(_bundle(tmp_path), old, new)

    message = str(excinfo.value)
    assert str(a) in message
    assert str(b) in message
    assert "2 referring file" in message
    # Nothing should have been touched.
    assert old.exists()
    assert not new.exists()
    assert a.read_text(encoding="utf-8") == "See [link](old.md).\n\n[ref]: dest\n"
    assert b.read_text(encoding="utf-8") == "Also [link](old.md).\n\n[ref2]: dest2\n"


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


def test_move_concept_source_argument_symlink_is_rejected_not_followed(
    tmp_path: Path,
) -> None:
    if not _can_symlink():
        pytest.skip("symlinks not supported in this environment")

    real = tmp_path / "real.md"
    _write(real, "Body.\n")
    linked = tmp_path / "linked.md"
    linked.symlink_to(real)
    new = tmp_path / "new.md"

    with pytest.raises(DocumentChangePlanningError):
        move_concept(_bundle(tmp_path), linked, new)

    assert real.exists()
    assert not new.exists()


def test_move_concept_dest_argument_symlink_is_rejected_not_followed(
    tmp_path: Path,
) -> None:
    if not _can_symlink():
        pytest.skip("symlinks not supported in this environment")

    old = tmp_path / "old.md"
    _write(old, "Body.\n")
    somewhere_else = tmp_path / "somewhere_else.md"
    _write(somewhere_else, "Other.\n")
    linked_dest = tmp_path / "linked_dest.md"
    linked_dest.symlink_to(somewhere_else)

    with pytest.raises(DocumentChangePlanningError):
        move_concept(_bundle(tmp_path), old, linked_dest)

    assert old.exists()
    assert somewhere_else.read_text(encoding="utf-8") == "Other.\n"


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

"""Tests for the log_concept_move write primitive (concept move logging)."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from okf_core import BundleConfig, ConceptPathError
from okf_core.logs import (
    ParsedLog,
    load_log,
    log_concept_move,
    parse_log,
    plan_log_concept_move,
)
from okf_core.patching import (
    DocumentChangeConflictError,
    DocumentChangePlanningError,
    apply_document_change,
)

_TODAY = datetime.date(2026, 7, 25)


def _bundle(root: Path, **overrides: object) -> BundleConfig:
    defaults: dict[str, object] = {
        "name": "test",
        "bundle_root": root,
        "include": ("**/*.md",),
        "exclude": (),
        "reserved_filenames": ("index.md", "log.md"),
        "concept_path_strategy": "relative-path",
    }
    defaults.update(overrides)
    return BundleConfig(**defaults)  # type: ignore[arg-type]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _moved_entry_text(old: str, new: str) -> str:
    return f'[{old}]({new} "moved to")'


# ---------------------------------------------------------------------------
# plan_log_concept_move: happy path insertion
# ---------------------------------------------------------------------------


def test_plan_fresh_bundle_creates_log_with_move_entry(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)

    assert plan.original_exists is False
    assert plan.changed is True
    parsed = parse_log(plan.proposed_content)
    assert [s.date for s in parsed.sections] == ["2026-07-25"]
    assert len(parsed.sections[0].entries) == 1
    entry = parsed.sections[0].entries[0]
    assert entry.label == "Moved"
    assert entry.text == _moved_entry_text("topics/old", "topics/new.md")


def test_plan_existing_log_with_todays_section_prepends_entry(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    _write(
        tmp_path / "log.md",
        "## 2026-07-25\n* **Update**: Earlier change today.\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)

    parsed = parse_log(plan.proposed_content)
    assert [s.date for s in parsed.sections] == ["2026-07-25"]
    section = parsed.sections[0]
    assert len(section.entries) == 2
    assert section.entries[0].label == "Moved"
    assert section.entries[0].text == _moved_entry_text("topics/old", "topics/new.md")
    assert section.entries[1].text == "Earlier change today."


def test_plan_existing_log_without_todays_section_sorts_newest_first(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    _write(
        tmp_path / "log.md",
        "## 2026-01-01\n* **Update**: New year.\n\n"
        "## 2020-06-15\n* **Creation**: Old entry.\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)

    parsed = parse_log(plan.proposed_content)
    assert [s.date for s in parsed.sections] == [
        "2026-07-25",
        "2026-01-01",
        "2020-06-15",
    ]
    assert parsed.sections[0].entries[0].label == "Moved"


# ---------------------------------------------------------------------------
# Idempotency / dedup
# ---------------------------------------------------------------------------


def test_second_identical_move_is_a_noop(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    bundle = _bundle(tmp_path)

    first = log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)
    assert first.changed is True
    before = (tmp_path / "log.md").stat()

    second_plan = plan_log_concept_move(
        bundle, "topics/old", "topics/new.md", today=_TODAY
    )
    assert second_plan.changed is False

    second_result = log_concept_move(
        bundle, "topics/old", "topics/new.md", today=_TODAY
    )
    after = (tmp_path / "log.md").stat()

    assert second_result.changed is False
    assert after.st_mtime_ns == before.st_mtime_ns

    parsed = load_log(tmp_path / "log.md")
    moved_entries = [
        entry
        for section in parsed.sections
        for entry in section.entries
        if entry.label == "Moved"
    ]
    assert len(moved_entries) == 1


def test_chained_rename_produces_two_distinct_entries(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "b.md", "# B\n")
    bundle = _bundle(tmp_path)
    log_concept_move(bundle, "topics/a", "topics/b.md", today=_TODAY)

    _write(tmp_path / "topics" / "c.md", "# C\n")
    log_concept_move(bundle, "topics/b", "topics/c.md", today=_TODAY)

    parsed = load_log(tmp_path / "log.md")
    moved_entries = [
        entry
        for section in parsed.sections
        for entry in section.entries
        if entry.label == "Moved"
    ]
    assert len(moved_entries) == 2
    assert {entry.text for entry in moved_entries} == {
        _moved_entry_text("topics/a", "topics/b.md"),
        _moved_entry_text("topics/b", "topics/c.md"),
    }


def test_old_equals_new_after_resolution_is_a_noop(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(bundle, "topics/new", "topics/new.md", today=_TODAY)

    assert plan.changed is False
    assert plan.original_exists is False


# ---------------------------------------------------------------------------
# Apply-time conflict
# ---------------------------------------------------------------------------


def test_apply_reports_stale_log_content(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    _write(tmp_path / "log.md", "## 2020-01-01\n* **Update**: Old.\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)
    _write(tmp_path / "log.md", "## 2020-01-01\n* **Update**: Concurrent edit.\n")

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)


def test_apply_reports_log_created_concurrently(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)
    _write(tmp_path / "log.md", "## 2020-01-01\n* **Update**: Concurrent create.\n")

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)


# ---------------------------------------------------------------------------
# Negative / validation paths
# ---------------------------------------------------------------------------


def test_plan_rejects_new_missing_on_disk(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="does not exist"):
        plan_log_concept_move(bundle, "topics/old", "topics/missing.md", today=_TODAY)


def test_plan_rejects_old_outside_bundle_root(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    bundle = _bundle(tmp_path)

    with pytest.raises(ConceptPathError):
        plan_log_concept_move(bundle, "../outside", "topics/new.md", today=_TODAY)


def test_plan_rejects_new_outside_bundle_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-new.md"
    outside.write_text("Outside\n", encoding="utf-8")
    bundle = _bundle(tmp_path)

    with pytest.raises(ConceptPathError):
        plan_log_concept_move(bundle, "topics/old", outside, today=_TODAY)


@pytest.mark.parametrize("old_or_new", ["old", "new"])
def test_plan_rejects_reserved_filename_shaped_path(
    tmp_path: Path, old_or_new: str
) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    _write(tmp_path / "log.md", "")
    bundle = _bundle(tmp_path)
    old = "log" if old_or_new == "old" else "topics/old"
    new = "log.md" if old_or_new == "new" else "topics/new.md"

    with pytest.raises(ConceptPathError):
        plan_log_concept_move(bundle, old, new, today=_TODAY)


def test_plan_rejects_unsupported_concept_path_strategy(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    bundle = _bundle(tmp_path, concept_path_strategy="concept-id-map")

    with pytest.raises(ConceptPathError, match="Unsupported concept path strategy"):
        plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)


def test_plan_uses_real_today_by_default(tmp_path: Path) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(bundle, "topics/old", "topics/new.md")

    parsed: ParsedLog = parse_log(plan.proposed_content)
    expected_today = datetime.datetime.now(tz=datetime.timezone.utc).date()
    assert parsed.sections[0].date == expected_today.isoformat()

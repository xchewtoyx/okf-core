"""Tests for the structure-free log_append write primitive (#101)."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from freezegun import freeze_time
from hypothesis import assume, given
from hypothesis import strategies as st

from okf_core import BundleConfig
from okf_core.logs import (
    LogEntry,
    ParsedLog,
    _canonicalize_log_entry_prose,
    _insert_entry_for_date,
    _render_log_entry,
    _require_representable_log_entry,
    load_log,
    log_append,
    parse_log,
    plan_log_append,
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


# ---------------------------------------------------------------------------
# Happy path: fresh log, existing section, explicit dates
# ---------------------------------------------------------------------------


def test_plan_fresh_bundle_creates_log_with_entry(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "Did a thing.", date=_TODAY, kind="Update")

    assert plan.original_exists is False
    assert plan.changed is True
    parsed = parse_log(plan.proposed_content)
    assert [s.date for s in parsed.sections] == ["2026-07-25"]
    entry = parsed.sections[0].entries[0]
    assert entry.label == "Update"
    assert entry.text == "Did a thing."


def test_plan_appends_into_existing_todays_section(tmp_path: Path) -> None:
    _write(
        tmp_path / "log.md",
        "## 2026-07-25\n* **Update**: Earlier change today.\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "Newer change today.", date=_TODAY)

    parsed = parse_log(plan.proposed_content)
    assert [s.date for s in parsed.sections] == ["2026-07-25"]
    section = parsed.sections[0]
    assert len(section.entries) == 2
    assert section.entries[0].text == "Newer change today."
    assert section.entries[1].text == "Earlier change today."


def test_plan_explicit_past_date_sorts_into_history(tmp_path: Path) -> None:
    _write(
        tmp_path / "log.md",
        "## 2026-07-25\n* **Update**: Today's entry.\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "Backfilled entry.", date=datetime.date(2020, 1, 1))

    parsed = parse_log(plan.proposed_content)
    assert [s.date for s in parsed.sections] == ["2026-07-25", "2020-01-01"]
    assert parsed.sections[1].entries[0].text == "Backfilled entry."


def test_plan_explicit_future_date_sorts_before_existing(tmp_path: Path) -> None:
    _write(
        tmp_path / "log.md",
        "## 2026-07-25\n* **Update**: Today's entry.\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "Scheduled entry.", date=datetime.date(2030, 1, 1))

    parsed = parse_log(plan.proposed_content)
    assert [s.date for s in parsed.sections] == ["2030-01-01", "2026-07-25"]
    assert parsed.sections[0].entries[0].text == "Scheduled entry."


def test_plan_uses_real_today_by_default(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    # See test_log_writing.py's identical-purpose test for why this freezes
    # an instant near the UTC day boundary rather than a midday moment.
    frozen_instant = datetime.datetime(
        2026, 7, 25, 23, 59, 30, tzinfo=datetime.timezone.utc
    )
    with freeze_time(frozen_instant):
        plan = plan_log_append(bundle, "Untimed entry.")

    parsed = parse_log(plan.proposed_content)
    assert parsed.sections[0].date == frozen_instant.date().isoformat()


def test_log_append_writes_to_disk(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    result = log_append(bundle, "Did a thing.", date=_TODAY, kind="Update")

    assert result.changed is True
    parsed = load_log(tmp_path / "log.md")
    assert parsed.sections[0].entries[0].text == "Did a thing."
    assert parsed.sections[0].entries[0].label == "Update"


# ---------------------------------------------------------------------------
# No dedup (divergence from plan_log_concept_move, documented in logs.py)
# ---------------------------------------------------------------------------


def test_log_append_does_not_deduplicate_repeated_calls(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    log_append(bundle, "Same content.", date=_TODAY)
    log_append(bundle, "Same content.", date=_TODAY)

    parsed = load_log(tmp_path / "log.md")
    matching = [e for e in parsed.sections[0].entries if e.text == "Same content."]
    assert len(matching) == 2


# ---------------------------------------------------------------------------
# Unparseable existing log.md: refuse rather than lossily rewrite
# ---------------------------------------------------------------------------


def test_plan_rejects_existing_log_with_parse_problems(tmp_path: Path) -> None:
    existing_log = "## 2020-01-01\nA bare paragraph, not a bullet.\n"
    _write(tmp_path / "log.md", existing_log)
    bundle = _bundle(tmp_path)
    assert parse_log(existing_log).problems

    with pytest.raises(DocumentChangePlanningError, match="unparseable"):
        plan_log_append(bundle, "New entry.", date=_TODAY)


def test_plan_does_not_raise_for_missing_log(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "New entry.", date=_TODAY)

    assert plan.original_exists is False
    assert plan.changed is True


# ---------------------------------------------------------------------------
# Negative / validation paths: content and kind shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [None, 123, [], ""],
    ids=["none", "int", "list", "empty-string"],
)
def test_plan_rejects_non_string_or_empty_content(
    tmp_path: Path, content: object
) -> None:
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="content"):
        plan_log_append(bundle, content, date=_TODAY)  # type: ignore[arg-type]


def test_plan_rejects_content_with_surrounding_whitespace(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="surrounding whitespace"):
        plan_log_append(bundle, "  padded content  ", date=_TODAY)


def test_plan_rejects_whitespace_only_content(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="content"):
        plan_log_append(bundle, "   ", date=_TODAY)


@pytest.mark.parametrize(
    "kind",
    [123, [], "", "   ", "line1\nline2", " padded "],
    ids=["int", "list", "empty", "whitespace-only", "newline", "surrounding-space"],
)
def test_plan_rejects_invalid_kind_shape(tmp_path: Path, kind: object) -> None:
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="kind"):
        plan_log_append(bundle, "Some content.", date=_TODAY, kind=kind)  # type: ignore[arg-type]


def test_plan_accepts_kind_none(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "Some content.", date=_TODAY, kind=None)

    parsed = parse_log(plan.proposed_content)
    assert parsed.sections[0].entries[0].label is None


# ---------------------------------------------------------------------------
# Negative / validation paths: representability (structural + round trip)
# ---------------------------------------------------------------------------


def test_plan_rejects_multi_paragraph_content(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="representable"):
        plan_log_append(bundle, "First paragraph.\n\nSecond paragraph.", date=_TODAY)


@pytest.mark.parametrize(
    "content",
    [
        "- nested bullet content",
        "1. nested ordered content",
        "# heading-shaped content",
    ],
    ids=["bullet-marker", "ordered-marker", "heading-marker"],
)
def test_plan_rejects_content_shaped_as_a_nested_block(
    tmp_path: Path, content: str
) -> None:
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="representable"):
        plan_log_append(bundle, content, date=_TODAY)


def test_plan_rejects_label_ambiguous_content_without_kind(tmp_path: Path) -> None:
    """Content that itself starts with a "**word**: " prefix renders
    indistinguishably from a genuinely labelled entry when no kind is
    supplied, so it must be rejected rather than silently misread on the
    next read.
    """
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="unambiguously"):
        plan_log_append(bundle, "**Update**: looks labelled.", date=_TODAY)


def test_plan_accepts_label_shaped_content_with_matching_kind(tmp_path: Path) -> None:
    """The same content that's rejected without a kind is fine when the
    caller passes the label explicitly via kind instead of embedding it in
    content -- it is only ambiguous when the prefix's origin is unclear.
    """
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "looks labelled.", date=_TODAY, kind="Update")

    parsed = parse_log(plan.proposed_content)
    entry = parsed.sections[0].entries[0]
    assert entry.label == "Update"
    assert entry.text == "looks labelled."


def test_plan_rejects_kind_that_cannot_survive_its_own_rendering(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="unambiguously"):
        plan_log_append(bundle, "Some content.", date=_TODAY, kind="a**b")


def test_plan_accepts_content_with_canonicalizable_markdown(tmp_path: Path) -> None:
    """A link title's quote style is canonicalized on re-render (see
    _canonicalize_log_entry_prose); that is expected canonicalization, not a
    representability failure.
    """
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "See [x](y.md 'title') here.", date=_TODAY)

    parsed = parse_log(plan.proposed_content)
    assert parsed.sections[0].entries[0].text == 'See [x](y.md "title") here.'


# ---------------------------------------------------------------------------
# Plan/apply conflict
# ---------------------------------------------------------------------------


def test_apply_reports_stale_log_content(tmp_path: Path) -> None:
    _write(tmp_path / "log.md", "## 2020-01-01\n* **Update**: Old.\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "New entry.", date=_TODAY)
    _write(tmp_path / "log.md", "## 2020-01-01\n* **Update**: Concurrent edit.\n")

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)


def test_apply_reports_log_created_concurrently(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    plan = plan_log_append(bundle, "New entry.", date=_TODAY)
    _write(tmp_path / "log.md", "## 2020-01-01\n* **Update**: Concurrent create.\n")

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)


# ---------------------------------------------------------------------------
# Hypothesis: arbitrary date order always yields reverse-chronological
# sections (shared _insert_entry_for_date internals, generalized beyond
# plan_log_concept_move's own coverage in tests/test_log_writing.py).
# ---------------------------------------------------------------------------


@given(
    dates=st.lists(
        st.dates(
            min_value=datetime.date(2000, 1, 1),
            max_value=datetime.date(2100, 12, 31),
        ),
        min_size=1,
        max_size=10,
    )
)
def test_insert_entry_for_date_arbitrary_order_yields_reverse_chronological(
    dates: list[datetime.date],
) -> None:
    parsed = ParsedLog(title=None, sections=(), problems=())
    for index, target_date in enumerate(dates):
        parsed = _insert_entry_for_date(
            parsed, LogEntry(text=f"Entry {index}"), target_date
        )

    section_dates = [s.date for s in parsed.sections]
    assert section_dates == sorted(section_dates, reverse=True)
    assert section_dates == sorted({d.isoformat() for d in dates}, reverse=True)


# ---------------------------------------------------------------------------
# Hypothesis: round trip with markdown-significant characters
# ---------------------------------------------------------------------------

# Letters, digits, and markdown-significant punctuation a prose entry can
# legitimately contain: quotes and parens have no special meaning as bare
# text, and no '\n' means no risk of accidentally forming a second
# paragraph or block. '[' / ']' are excluded because forming a real link
# from randomly generated text is exactly the kind of accidental-match this
# alphabet is meant to avoid, not because they're rejected outright --
# test_plan_accepts_content_with_canonicalizable_markdown above already
# covers a real link directly.
_PROSE_ALPHABET = 'ab01 .-_/"()'


@given(content=st.text(alphabet=_PROSE_ALPHABET, min_size=1, max_size=16))
def test_log_append_content_round_trips_when_representable(content: str) -> None:
    assume(content == content.strip())

    try:
        _require_representable_log_entry(content, None, Path("log.md"))
    except DocumentChangePlanningError:
        # Some generated strings collide with unrelated Markdown block
        # syntax (e.g. a leading ordered-list-marker-shaped "0. " prefix)
        # and are legitimately rejected as non-representable single-entry
        # content -- not the property under test here.
        return

    entry = LogEntry(text=content, label=None)
    rendered = f"## {_TODAY.isoformat()}\n{_render_log_entry(entry)}\n"
    parsed = parse_log(rendered)

    assert parsed.problems == ()
    assert len(parsed.sections) == 1
    assert len(parsed.sections[0].entries) == 1
    recovered = parsed.sections[0].entries[0]
    assert recovered.label is None
    assert recovered.text == _canonicalize_log_entry_prose(content)

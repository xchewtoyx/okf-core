"""Tests for the log_concept_move write primitive (concept move logging)."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from freezegun import freeze_time
from hypothesis import assume, given
from hypothesis import strategies as st
from markdown_it import MarkdownIt
from markdown_it.token import Token

from okf_core import BundleConfig, ConceptPathError
from okf_core._markdown_inline import inline_token_source
from okf_core.logs import (
    _MARKDOWN,
    _MOVE_ENTRY_TITLE,
    ParsedLog,
    _build_move_entry,
    _has_balanced_parens,
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

_MD = MarkdownIt("commonmark")

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
# Single-read planning (no stale-read race between parse and hash baseline)
# ---------------------------------------------------------------------------


def test_plan_reads_log_content_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dedup/insert/render decision must come from the same read that
    gets hashed as the plan's baseline. An earlier implementation read
    log.md once (via load_log's Path.read_text) to build the proposed
    content and let plan_document_change read it again, separately (via
    Path.read_bytes), to compute the hash baseline -- so a concurrent edit
    between those two reads could be silently discarded: the plan's hash
    would match the second read while the proposed content reflected the
    first. Counting every disk read of log.md across both read mechanisms is
    a direct regression test for that race, not just for its symptoms --
    counting only one mechanism would miss a reintroduced second read that
    happens to use the other one.
    """
    _write(tmp_path / "topics" / "new.md", "# New\n")
    _write(tmp_path / "log.md", "## 2020-01-01\n* **Update**: Existing.\n")
    bundle = _bundle(tmp_path)
    log_path = (tmp_path / "log.md").resolve()

    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text
    calls: list[Path] = []

    def counting_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        if self.resolve() == log_path:
            calls.append(self)
        return real_read_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    def counting_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.resolve() == log_path:
            calls.append(self)
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)
    monkeypatch.setattr(Path, "read_text", counting_read_text)

    plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)

    assert len(calls) == 1


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

    # Freeze an instant in the last minute of a UTC day rather than some
    # arbitrary midday moment: the frozen instant's UTC date must appear
    # verbatim in the produced log section. An instant this close to the
    # day boundary means any off-by-one error in how the default `today`
    # is derived from the frozen clock -- e.g. resolving to the wrong side
    # of midnight -- shows up as a failing exact-equality assertion below,
    # rather than passing by coincidence the way a midday instant could.
    frozen_instant = datetime.datetime(
        2026, 7, 25, 23, 59, 30, tzinfo=datetime.timezone.utc
    )
    with freeze_time(frozen_instant):
        plan = plan_log_concept_move(bundle, "topics/old", "topics/new.md")

    parsed: ParsedLog = parse_log(plan.proposed_content)
    assert parsed.sections[0].date == frozen_instant.date().isoformat()


# ---------------------------------------------------------------------------
# Unparseable existing log.md: refuse to plan rather than lossily rewrite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("existing_log", "description"),
    [
        (
            "## 2020-01-01\nA bare paragraph, not a bullet.\n",
            "stray block under a date heading",
        ),
        (
            "## not-a-date\n* **Update**: Entry under malformed heading.\n",
            "malformed date heading",
        ),
    ],
)
def test_plan_rejects_existing_log_with_parse_problems(
    tmp_path: Path, existing_log: str, description: str
) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    _write(tmp_path / "log.md", existing_log)
    bundle = _bundle(tmp_path)
    assert parse_log(existing_log).problems, f"fixture has no problems: {description}"

    with pytest.raises(DocumentChangePlanningError, match="unparseable"):
        plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)


def test_plan_still_succeeds_against_existing_log_with_zero_problems(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    _write(tmp_path / "log.md", "## 2020-01-01\n* **Update**: Fine.\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)

    assert plan.changed is True


def test_plan_does_not_raise_for_missing_log(tmp_path: Path) -> None:
    """parse_log("") never reports problems, so the missing-log path (which
    plans against an empty original_content) is naturally unaffected by the
    new problems check above -- confirmed explicitly here rather than left
    to be an accident of the empty-log fixture other tests happen to use.
    """
    _write(tmp_path / "topics" / "new.md", "# New\n")
    bundle = _bundle(tmp_path)
    assert not parse_log("").problems

    plan = plan_log_concept_move(bundle, "topics/old", "topics/new.md", today=_TODAY)

    assert plan.original_exists is False
    assert plan.changed is True


# ---------------------------------------------------------------------------
# Concept IDs that can't be represented as Markdown link text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old", ["topics/old]", "topics/[old", "top[ic]s/old"])
def test_plan_rejects_old_concept_id_with_brackets(tmp_path: Path, old: str) -> None:
    _write(tmp_path / "topics" / "new.md", "# New\n")
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match=r"\[.*\]"):
        plan_log_concept_move(bundle, old, "topics/new.md", today=_TODAY)


def test_plan_bracket_rejection_does_not_apply_to_noop_move(tmp_path: Path) -> None:
    """A concept ID with brackets that resolves to the same path as `new` is
    still a no-op: no entry is ever built from `old` and the log is never
    re-rendered, so there's nothing for the bracket to corrupt -- the
    rejection in _require_representable_concept_id is only reachable once an
    entry is actually about to be built.
    """
    _write(tmp_path / "topics" / "new].md", "# New\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(bundle, "topics/new]", "topics/new].md", today=_TODAY)

    assert plan.changed is False


# ---------------------------------------------------------------------------
# `new` relative paths that can't be represented as a Markdown link
# destination (unbalanced parens)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "new_filename",
    ["foo)bar.md", "foo(bar.md", "(foo.md", "foo).md"],
    ids=["unmatched-close", "unmatched-open", "leading-open", "trailing-close"],
)
def test_plan_rejects_new_with_unbalanced_parens(
    tmp_path: Path, new_filename: str
) -> None:
    _write(tmp_path / "topics" / new_filename, "# New\n")
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match=r"[()]"):
        plan_log_concept_move(
            bundle, "topics/old", f"topics/{new_filename}", today=_TODAY
        )


@pytest.mark.parametrize(
    "new_filename",
    ["foo(bar)baz.md", "(foo)bar.md", "foo(bar)(baz).md"],
    ids=["mid-pair", "leading-pair", "two-pairs"],
)
def test_plan_accepts_new_with_balanced_parens(
    tmp_path: Path, new_filename: str
) -> None:
    """Balanced parens in `new` are not rejected: markdown-it's link
    destination parser tracks paren nesting depth and consumes a balanced
    run as one href (confirmed directly against markdown-it-py), so
    `topics/foo(bar)baz.md` round-trips as a correct link and rejecting it
    would be over-rejecting a path CommonMark actually handles correctly.
    """
    _write(tmp_path / "topics" / new_filename, "# New\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(
        bundle, "topics/old", f"topics/{new_filename}", today=_TODAY
    )

    assert plan.changed is True
    parsed = parse_log(plan.proposed_content)
    entry = parsed.sections[0].entries[0]
    assert entry.text == _moved_entry_text("topics/old", f"topics/{new_filename}")


def test_plan_paren_rejection_does_not_apply_to_noop_move(tmp_path: Path) -> None:
    """Mirrors test_plan_bracket_rejection_does_not_apply_to_noop_move: a `new`
    with unbalanced parens that resolves to the same path as `old` is still a
    no-op, since no entry is ever built and the log is never re-rendered.
    """
    _write(tmp_path / "topics" / "old)new.md", "# New\n")
    bundle = _bundle(tmp_path)

    plan = plan_log_concept_move(
        bundle, "topics/old)new", "topics/old)new.md", today=_TODAY
    )

    assert plan.changed is False


# ---------------------------------------------------------------------------
# Hypothesis round-trip: _build_move_entry's interpolated text must parse
# back to the same (old_concept_id, href, title) it was built from.
# ---------------------------------------------------------------------------

# Ordinary text plus markdown-significant punctuation that a concept ID or
# relative path can legitimately contain: quotes and parens have no special
# meaning as raw link text/destination content. `[`, `]`, `\`, and `:` are
# excluded because upstream validation already rejects them before an ID or
# path ever reaches `_build_move_entry` -- `[`/`]` via
# `_require_representable_concept_id`, and `\`/`:` via
# `_concept_id_to_relative_markdown_path` (see that function's docstring on
# `_require_representable_concept_id`) -- so a fuzzed string containing them
# would be testing an input class `_build_move_entry` never actually sees.
_PATH_LIKE_ALPHABET = 'ab01 .-_/"()'


def _recovered_link(entry_text: str) -> tuple[Token, str]:
    """Parse a `_build_move_entry` `.text` value and return (link_open, text).

    Reuses `inline_token_source` -- the same primitive `render_linked_span`
    itself is built from -- to reconstitute the inner text, so a token like an
    emphasis delimiter (e.g. a lone `_`) is counted correctly instead of
    silently dropped by only looking at `text`-typed tokens.
    """
    children = _MD.parseInline(entry_text)[0].children
    assert children is not None and children[0].type == "link_open"
    assert children[-1].type == "link_close"
    parts = [
        content
        for child in children[1:-1]
        if (content := inline_token_source(child)) is not None
    ]
    return children[0], "".join(parts)


@given(
    old_concept_id=st.text(alphabet=_PATH_LIKE_ALPHABET, max_size=12),
    new_relative_path=st.text(alphabet=_PATH_LIKE_ALPHABET, min_size=1, max_size=12),
)
def test_build_move_entry_round_trips(
    old_concept_id: str, new_relative_path: str
) -> None:
    """`_build_move_entry`'s entry text parses back to the same ID/path/title.

    `new_relative_path` is filtered to balanced parens via the real
    `_has_balanced_parens` guard (not a reimplementation of it) so this stays
    in sync with `_require_representable_move_target`, the precondition
    `_build_move_entry`'s own docstring says callers must already have
    checked. It's further filtered to a non-empty normalized href: an empty
    destination has no way to carry the fixed title too (CommonMark's
    unencoded destination grammar can't express "empty destination, then a
    title" -- `( "title")` parses as a literal href, not an empty one) --
    the same gap `render_linked_span`'s own round-trip test works around,
    here on the write side instead of the read side.
    """
    assume(_has_balanced_parens(new_relative_path))
    href = _MARKDOWN.normalizeLink(new_relative_path)
    assume(href != "")

    entry = _build_move_entry(old_concept_id, new_relative_path)
    link, text = _recovered_link(entry.text)

    assert text == old_concept_id
    assert link.attrGet("href") == href
    assert link.attrGet("title") == _MOVE_ENTRY_TITLE

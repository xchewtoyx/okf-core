"""Tests for OKF v0.2 §5 trust/lifecycle stamping operations (#196).

Covers the actor-string and ISO 8601 datetime/date validators (shared by all
four operations) and the four `plan_stamp_*`/`stamp_*` primitives:
`generated` (§5.2), `verified` (§5.2), `status` (§5.4), `stale_after` (§5.5).
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import pytest
from freezegun import freeze_time
from hypothesis import assume, given
from hypothesis import strategies as st

from okf_core import (
    BundleConfig,
    DocumentChangeConflictError,
    DocumentChangePlanningError,
    apply_document_change,
    parse_concept_document,
    plan_stamp_generated,
    plan_stamp_stale_after,
    plan_stamp_status,
    plan_stamp_verified,
    stamp_generated,
    stamp_stale_after,
    stamp_status,
    stamp_verified,
)


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


VALID_AT = "2026-06-20T22:53:05Z"
VALID_AT_DT = datetime.datetime(2026, 6, 20, 22, 53, 5, tzinfo=datetime.timezone.utc)

# Mirrors `patching.py`'s private `_ACTOR_PATTERN` so
# `test_actor_arbitrary_text_never_produces_a_write_without_matching_shape`
# can exclude Hypothesis-generated strings that happen to already be
# conformant, without importing a module-private implementation detail.
_ACTOR_LIKE = re.compile(r"(?:[^\s/:]+/[^\s/:]+)|(?:human:\S+)|(?:process:\S+)")


# ---------------------------------------------------------------------------
# AC1/AC2: actor-string validation (shared by generated.by and verified[].by)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "by",
    [
        "reference_agent/gemini-2.5-pro",
        "human:ahormati",
        "process:finance-nightly",
        "a/b",
        "human:has-hyphen",
        "process:has_underscore",
    ],
    ids=[
        "producer-version",
        "human",
        "process",
        "minimal-producer-version",
        "human-hyphen",
        "process-underscore",
    ],
)
def test_plan_stamp_generated_accepts_valid_actor_shapes(
    tmp_path: Path, by: str
) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, by, at=VALID_AT)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["generated"]["by"] == by


@pytest.mark.parametrize(
    "by",
    [
        "",
        "no-slash-no-prefix",
        "human:",
        "process:",
        "human: has space",
        "a/b/c",
        "producer/",
        "/version",
        "human:a b",
        None,
        123,
        ["human:alice"],
        {"actor": "human:alice"},
    ],
    ids=[
        "empty",
        "bare-word",
        "human-empty-id",
        "process-empty-id",
        "human-embedded-space",
        "three-segments",
        "trailing-slash",
        "leading-slash",
        "human-id-with-space",
        "none",
        "int",
        "list",
        "dict",
    ],
)
def test_plan_stamp_generated_rejects_invalid_actor(tmp_path: Path, by: Any) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="actor string"):
        plan_stamp_generated(bundle, path, by, at=VALID_AT)

    assert path.read_text(encoding="utf-8") == original


# Producer/version and id segments free of the characters the actor grammar
# itself treats as structural (whitespace, "/", ":") -- this alphabet only
# needs to stay inside what `_ACTOR_PATTERN` accepts for producer/version and
# human/process ids, not cover every Unicode edge case.
_ACTOR_SEGMENT_ALPHABET = "ab01-_."


@given(
    producer=st.text(alphabet=_ACTOR_SEGMENT_ALPHABET, min_size=1, max_size=8),
    version=st.text(alphabet=_ACTOR_SEGMENT_ALPHABET, min_size=1, max_size=8),
)
def test_actor_producer_version_shape_round_trips(
    tmp_path_factory: pytest.TempPathFactory, producer: str, version: str
) -> None:
    """AC1: any conformant `<producer>/<version>` actor produces a stamp
    whose `generated.by` re-parses back to exactly that string.

    Uses `tmp_path_factory` (a fresh directory per Hypothesis example)
    rather than the function-scoped `tmp_path` fixture: Hypothesis's own
    health check refuses a function-scoped fixture under `@given` because
    it is not reset between generated examples within one test call.
    """
    by = f"{producer}/{version}"
    tmp_path = tmp_path_factory.mktemp("stamp")
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, by, at=VALID_AT)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["generated"] == {"by": by, "at": VALID_AT_DT}


@given(
    prefix=st.sampled_from(["human", "process"]),
    identifier=st.text(alphabet=_ACTOR_SEGMENT_ALPHABET, min_size=1, max_size=8),
)
def test_actor_human_process_shape_round_trips(
    tmp_path_factory: pytest.TempPathFactory, prefix: str, identifier: str
) -> None:
    """AC1: any conformant `human:<id>`/`process:<id>` actor round-trips.

    See `test_actor_producer_version_shape_round_trips` for why
    `tmp_path_factory`, not `tmp_path`, is used here.
    """
    by = f"{prefix}:{identifier}"
    tmp_path = tmp_path_factory.mktemp("stamp")
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, by, at=VALID_AT)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["generated"]["by"] == by


@given(garbage=st.text(max_size=20))
def test_actor_arbitrary_text_never_produces_a_write_without_matching_shape(
    tmp_path_factory: pytest.TempPathFactory, garbage: str
) -> None:
    """AC2: any string that doesn't match one of §7's three shapes is
    rejected before any write; nothing is ever silently accepted as a
    malformed actor. Strings that happen to already be conformant (a valid
    `<producer>/<version>`, `human:<id>`, or `process:<id>`) are excluded --
    those are covered by the round-trip tests above, and are the one case
    this test is not making a claim about. See
    `test_actor_producer_version_shape_round_trips` for why
    `tmp_path_factory`, not `tmp_path`, is used here.
    """
    assume(not _ACTOR_LIKE.fullmatch(garbage))
    tmp_path = tmp_path_factory.mktemp("stamp")
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="actor string"):
        plan_stamp_generated(bundle, path, garbage, at=VALID_AT)

    assert path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# AC1/AC2: ISO 8601 datetime validation (generated.at / verified[].at)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "at",
    [
        "2026-06-20T22:53:05Z",
        "2026-06-20T22:53:05z",
        "2026-06-20T22:53:05+00:00",
        "2026-06-20T22:53:05-05:00",
        "2026-01-01T00:00:00.123456Z",
    ],
    ids=["z-upper", "z-lower", "explicit-utc-offset", "negative-offset", "fraction"],
)
def test_plan_stamp_generated_accepts_valid_datetime_strings(
    tmp_path: Path, at: str
) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, "human:alice", at=at)

    parsed = parse_concept_document(plan.proposed_content)
    assert isinstance(parsed.frontmatter["generated"]["at"], datetime.datetime)


def test_plan_stamp_generated_accepts_native_datetime_object(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, "human:alice", at=VALID_AT_DT)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["generated"]["at"] == VALID_AT_DT


@pytest.mark.parametrize(
    "at",
    [
        "not-a-date",
        "2026-13-40T99:99:99Z",
        "2026-06-20",
        "2026-06-20T22:53:05",  # naive: no UTC offset
        "",
        123,
        datetime.date(2026, 6, 20),  # a date, not a datetime
        ["2026-06-20T22:53:05Z"],
    ],
    ids=[
        "garbage",
        "out-of-range",
        "date-only",
        "naive-no-offset",
        "empty",
        "int",
        "date-not-datetime",
        "list",
    ],
)
def test_plan_stamp_generated_rejects_invalid_datetime(tmp_path: Path, at: Any) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError):
        plan_stamp_generated(bundle, path, "human:alice", at=at)

    assert path.read_text(encoding="utf-8") == original


@given(
    year=st.integers(min_value=2000, max_value=2099),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28),
    hour=st.integers(min_value=0, max_value=23),
    minute=st.integers(min_value=0, max_value=59),
    second=st.integers(min_value=0, max_value=59),
)
def test_datetime_z_suffix_round_trips_for_any_valid_calendar_instant(
    tmp_path_factory: pytest.TempPathFactory,
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
) -> None:
    """AC1: any valid calendar instant, formatted with a trailing 'Z' (§5.2's
    own example form), produces a `generated.at` for the same instant. See
    `test_actor_producer_version_shape_round_trips` for why
    `tmp_path_factory`, not `tmp_path`, is used here.
    """
    at = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z"
    expected = datetime.datetime(
        year, month, day, hour, minute, second, tzinfo=datetime.timezone.utc
    )
    tmp_path = tmp_path_factory.mktemp("stamp")
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, "human:alice", at=at)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["generated"]["at"] == expected


# ---------------------------------------------------------------------------
# `generated.at` default: real current UTC datetime, overridable
# ---------------------------------------------------------------------------


def test_plan_stamp_generated_at_defaults_to_real_utc_now(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    # Freeze an instant in the last minute of a UTC day, matching the
    # boundary-sensitive pattern `test_plan_uses_real_today_by_default`
    # (tests/test_log_writing.py) uses for `plan_log_append`'s `date`
    # default -- an instant this close to midnight surfaces an off-by-one
    # in the default's derivation as a failing exact-equality assertion,
    # rather than passing by coincidence near midday.
    frozen_instant = datetime.datetime(
        2026, 7, 25, 23, 59, 30, tzinfo=datetime.timezone.utc
    )
    with freeze_time(frozen_instant):
        plan = plan_stamp_generated(bundle, path, "human:alice")

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["generated"]["at"] == frozen_instant


def test_plan_stamp_verified_at_defaults_to_real_utc_now(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    frozen_instant = datetime.datetime(
        2026, 7, 25, 23, 59, 30, tzinfo=datetime.timezone.utc
    )
    with freeze_time(frozen_instant):
        plan = plan_stamp_verified(bundle, path, "human:alice")

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["verified"] == [
        {"by": "human:alice", "at": frozen_instant}
    ]


# ---------------------------------------------------------------------------
# AC1/AC2: `stale_after` date validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["2026-09-23", datetime.date(2026, 9, 23)], ids=["string", "date-object"]
)
def test_plan_stamp_stale_after_accepts_valid_dates(tmp_path: Path, value: Any) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_stale_after(bundle, path, value)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["stale_after"] == datetime.date(2026, 9, 23)


@pytest.mark.parametrize(
    "value",
    [
        "not-a-date",
        "2026-13-40",
        "2026/09/23",
        "",
        123,
        datetime.datetime(2026, 9, 23, 12, 0, 0, tzinfo=datetime.timezone.utc),
        None,
        ["2026-09-23"],
    ],
    ids=[
        "garbage",
        "out-of-range",
        "wrong-separators",
        "empty",
        "int",
        "datetime-not-date",
        "none",
        "list",
    ],
)
def test_plan_stamp_stale_after_rejects_invalid_dates(
    tmp_path: Path, value: Any
) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError):
        plan_stamp_stale_after(bundle, path, value)

    assert path.read_text(encoding="utf-8") == original


@given(
    year=st.integers(min_value=2000, max_value=2099),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28),
)
def test_stale_after_string_round_trips_for_any_valid_calendar_date(
    tmp_path_factory: pytest.TempPathFactory, year: int, month: int, day: int
) -> None:
    """See `test_actor_producer_version_shape_round_trips` for why
    `tmp_path_factory`, not `tmp_path`, is used here."""
    value = f"{year:04d}-{month:02d}-{day:02d}"
    expected = datetime.date(year, month, day)
    tmp_path = tmp_path_factory.mktemp("stamp")
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_stale_after(bundle, path, value)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["stale_after"] == expected


# ---------------------------------------------------------------------------
# `status` enum validation (§5.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["draft", "stable", "deprecated"])
def test_plan_stamp_status_accepts_valid_values(tmp_path: Path, status: str) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_status(bundle, path, status)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["status"] == status


@pytest.mark.parametrize(
    "status",
    ["", "published", "STABLE", None, 1, ["stable"]],
    ids=["empty", "unknown-word", "wrong-case", "none", "int", "list"],
)
def test_plan_stamp_status_rejects_invalid_values(tmp_path: Path, status: Any) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="draft.*stable.*deprecated"):
        plan_stamp_status(bundle, path, status)

    assert path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# AC3: no-op semantics
# ---------------------------------------------------------------------------


def test_plan_stamp_generated_same_value_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\ngenerated: { by: human:alice, at: "
        f"{VALID_AT} }}\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, "human:alice", at=VALID_AT)

    assert plan.changed is False
    assert plan.proposed_content == plan.original_content


def test_plan_stamp_generated_different_value_is_not_noop(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\ngenerated: { by: human:alice, at: "
        f"{VALID_AT} }}\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, "human:bob", at=VALID_AT)

    assert plan.changed is True


def test_plan_stamp_generated_overwrites_offset_naive_existing_at(
    tmp_path: Path,
) -> None:
    """A pre-existing, non-conformant `generated.at` with no UTC offset
    (this operation always writes an offset-aware one, but a hand-authored
    document is not required to) is correctly treated as unequal to a
    freshly validated, offset-aware candidate -- Python's `datetime.__eq__`
    returns `False` rather than raising for a naive-vs-aware comparison, so
    the merge proceeds and the ambiguous existing value is replaced,
    exactly as `_validate_datetime_value`'s docstring on requiring a UTC
    offset describes (a naive existing value could otherwise never be
    recognized as a no-op against an equivalent aware candidate)."""
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\ngenerated: { by: human:alice, at: "
        "2026-06-20T22:53:05 }\n---\nBody\n",  # no trailing 'Z': naive
    )
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, "human:alice", at=VALID_AT)

    assert plan.changed is True
    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["generated"] == {"by": "human:alice", "at": VALID_AT_DT}


def test_plan_stamp_status_same_value_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\nstatus: stable\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_status(bundle, path, "stable")

    assert plan.changed is False
    assert plan.proposed_content == plan.original_content


def test_plan_stamp_stale_after_same_value_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\nstale_after: 2026-09-23\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_stale_after(bundle, path, "2026-09-23")

    assert plan.changed is False
    assert plan.proposed_content == plan.original_content


def test_plan_stamp_verified_identical_event_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nverified:\n  - { by: human:ahormati, at: "
        f"{VALID_AT} }}\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_stamp_verified(bundle, path, "human:ahormati", at=VALID_AT)

    assert plan.changed is False
    assert plan.proposed_content == plan.original_content


def test_plan_stamp_verified_same_by_different_at_is_not_noop(tmp_path: Path) -> None:
    """§5.2: two checks by the same actor at different times are distinct
    events -- unlike `plan_source_upsert`'s single-field identity key."""
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nverified:\n  - { by: human:ahormati, at: "
        f"{VALID_AT} }}\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_stamp_verified(
        bundle, path, "human:ahormati", at="2026-06-25T09:00:00Z"
    )

    assert plan.changed is True
    parsed = parse_concept_document(plan.proposed_content)
    assert len(parsed.frontmatter["verified"]) == 2


def test_plan_stamp_verified_true_noop_preserves_bare_mapping_shape(
    tmp_path: Path,
) -> None:
    """AC3 + the adjudicated canonical-form decision: a true no-op (an
    identical event already present) must not rewrite an existing bare
    `{by, at}` mapping into list form just to canonicalize it -- no-op
    means no-op, zero bytes changed."""
    path = tmp_path / "topic.md"
    original = (
        "---\ntype: concept\nverified: { by: human:ahormati, at: "
        f"{VALID_AT} }}\n---\nBody\n"
    )
    _write(path, original)
    bundle = _bundle(tmp_path)

    plan = plan_stamp_verified(bundle, path, "human:ahormati", at=VALID_AT)

    assert plan.changed is False
    assert plan.proposed_content == original


def test_plan_stamp_verified_append_onto_bare_mapping_produces_a_list(
    tmp_path: Path,
) -> None:
    """When an append genuinely happens, the result is always a list --
    even starting from an existing bare mapping -- per the adjudicated
    canonical-form decision (never a bare mapping once this operation
    manages the field)."""
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nverified: { by: human:ahormati, at: "
        f"{VALID_AT} }}\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_stamp_verified(
        bundle, path, "process:finance-nightly", at="2026-06-26T02:00:00Z"
    )

    assert plan.changed is True
    parsed = parse_concept_document(plan.proposed_content)
    assert isinstance(parsed.frontmatter["verified"], list)
    assert parsed.frontmatter["verified"] == [
        {"by": "human:ahormati", "at": VALID_AT_DT},
        {
            "by": "process:finance-nightly",
            "at": datetime.datetime(2026, 6, 26, 2, 0, 0, tzinfo=datetime.timezone.utc),
        },
    ]


def test_plan_stamp_verified_creates_list_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_verified(bundle, path, "human:alice", at=VALID_AT)

    assert plan.changed is True
    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["verified"] == [{"by": "human:alice", "at": VALID_AT_DT}]


# ---------------------------------------------------------------------------
# Existing malformed `verified` content: reported explicitly, no write
# ---------------------------------------------------------------------------


def test_plan_stamp_verified_rejects_non_list_non_mapping_existing_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\nverified: not-a-mapping-or-list\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="mapping or list"):
        plan_stamp_verified(bundle, path, "human:alice", at=VALID_AT)

    assert path.read_text(encoding="utf-8") == original


def test_plan_stamp_verified_rejects_existing_entry_that_is_not_a_mapping(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\nverified:\n  - just-a-string\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="must be a mapping"):
        plan_stamp_verified(bundle, path, "human:alice", at=VALID_AT)

    assert path.read_text(encoding="utf-8") == original


def test_plan_stamp_verified_rejects_malformed_existing_entry(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = (
        "---\ntype: concept\nverified:\n  - { by: not-an-actor, at: "
        f"{VALID_AT} }}\n---\nBody\n"
    )
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="actor string"):
        plan_stamp_verified(bundle, path, "human:alice", at=VALID_AT)

    assert path.read_text(encoding="utf-8") == original


def test_plan_stamp_verified_rejects_unparseable_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: [unterminated\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="parse"):
        plan_stamp_verified(bundle, path, "human:alice", at=VALID_AT)

    assert path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Validation happens before any read, even against a missing target
# ---------------------------------------------------------------------------


def test_plan_stamp_generated_rejects_malformed_actor_before_reading_target(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    missing_path = tmp_path / "does-not-exist.md"

    with pytest.raises(DocumentChangePlanningError, match="actor string"):
        plan_stamp_generated(bundle, missing_path, "not-an-actor", at=VALID_AT)


# ---------------------------------------------------------------------------
# AC4: #110 plan/apply envelope -- dry-run safety, apply, conflict detection
# ---------------------------------------------------------------------------


def test_dry_run_plan_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    plan_stamp_generated(bundle, path, "human:alice", at=VALID_AT)
    plan_stamp_verified(bundle, path, "human:alice", at=VALID_AT)
    plan_stamp_status(bundle, path, "stable")
    plan_stamp_stale_after(bundle, path, "2026-09-23")

    assert path.read_text(encoding="utf-8") == original


def test_stamp_generated_writes_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    result = stamp_generated(bundle, path, "human:alice", at=VALID_AT)

    assert result.changed is True
    parsed = parse_concept_document(path.read_text(encoding="utf-8"))
    assert parsed.frontmatter["generated"] == {"by": "human:alice", "at": VALID_AT_DT}


def test_stamp_verified_writes_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    result = stamp_verified(bundle, path, "human:alice", at=VALID_AT)

    assert result.changed is True
    parsed = parse_concept_document(path.read_text(encoding="utf-8"))
    assert parsed.frontmatter["verified"] == [{"by": "human:alice", "at": VALID_AT_DT}]


def test_stamp_status_writes_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    result = stamp_status(bundle, path, "draft")

    assert result.changed is True
    parsed = parse_concept_document(path.read_text(encoding="utf-8"))
    assert parsed.frontmatter["status"] == "draft"


def test_stamp_stale_after_writes_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    result = stamp_stale_after(bundle, path, "2026-09-23")

    assert result.changed is True
    parsed = parse_concept_document(path.read_text(encoding="utf-8"))
    assert parsed.frontmatter["stale_after"] == datetime.date(2026, 9, 23)


def test_apply_stamp_generated_reports_stale_content_conflict(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_generated(bundle, path, "human:alice", at=VALID_AT)
    _write(path, "---\ntype: concept\nstatus: draft\n---\nBody\n")

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)

    # The concurrent write must survive untouched.
    parsed = parse_concept_document(path.read_text(encoding="utf-8"))
    assert parsed.frontmatter == {"type": "concept", "status": "draft"}


def test_apply_stamp_verified_reports_stale_content_conflict(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_stamp_verified(bundle, path, "human:alice", at=VALID_AT)
    _write(
        path,
        "---\ntype: concept\nverified:\n  - { by: process:x, at: "
        f"{VALID_AT} }}\n---\nBody\n",
    )

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)

    parsed = parse_concept_document(path.read_text(encoding="utf-8"))
    assert parsed.frontmatter["verified"] == [{"by": "process:x", "at": VALID_AT_DT}]


def test_plan_stamp_verified_reads_target_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the single-read design `plan_stamp_verified`
    documents, mirroring `plan_source_upsert`'s equivalent test: deriving
    the new `verified` list from a separately-read snapshot, then handing a
    static value to a second, independent read/merge, would silently
    discard a concurrent edit landing between the two reads.
    """
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nverified:\n  - { by: human:ahormati, at: "
        f"{VALID_AT} }}\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    read_count = 0
    real_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path, *args: Any, **kwargs: Any) -> bytes:
        nonlocal read_count
        if self == path:
            read_count += 1
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    plan_stamp_verified(bundle, path, "process:nightly", at="2026-06-26T02:00:00Z")

    assert read_count == 1

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ruamel.yaml.comments import CommentedMap

from okf_core import (
    BundleConfig,
    DocumentChangeConflictError,
    DocumentChangePlanningError,
    apply_document_change,
    parse_concept_document,
    plan_frontmatter_merge,
)
from okf_core.patching import _dump_frontmatter, _load_frontmatter, _yaml_values_equal


def _bundle(root: Path) -> BundleConfig:
    return BundleConfig(
        name="test",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
    )


def test_merge_supports_multiline_string_value(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "---\ntype: concept\ndescription: old\nother: keep\n---\nBody\n",
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"description": "first line\nsecond line\n"},
    )

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["description"] == "first line\nsecond line\n"
    assert "other: keep\n---\nBody\n" in plan.proposed_content


def test_merge_applies_multiple_replacements_correctly(tmp_path: Path) -> None:
    """Multiple simultaneous targeted replacements land at their own keys.

    Under the old span-splice engine this was a byte-exact pin (the property
    it protected: several replacements applied in one pass don't corrupt
    each other's source spans -- an offset-drift risk specific to splicing
    text at computed byte ranges). The mutate-in-place ruamel engine has no
    offsets to drift, so the semantic equivalent is simply: every targeted
    value lands correctly and nothing else is disturbed.
    """
    path = tmp_path / "topic.md"
    path.write_text(
        "---\ntype: old\ntitle: Old\ncount: 1\n---\nBody\n",
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"type": "new", "title": "New", "count": 2},
    )

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter == {"type": "new", "title": "New", "count": 2}
    assert parsed.body == "Body\n"


def test_merge_appends_missing_fields_in_input_order(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "---\ntype: concept\n---\nBody\n",
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"owner": "team", "status": "active"},
    )

    parsed = parse_concept_document(plan.proposed_content)
    assert list(parsed.frontmatter.keys()) == ["type", "owner", "status"]
    assert parsed.frontmatter == {
        "type": "concept",
        "owner": "team",
        "status": "active",
    }


def test_merge_populates_empty_frontmatter_with_lf_regardless_of_source(
    tmp_path: Path,
) -> None:
    """ADR-0002 mandates LF-always for frontmatter output, even when the
    source document uses CRLF; the body's own line endings are untouched."""
    path = tmp_path / "topic.md"
    path.write_bytes(b"---\r\n---\r\nBody\r\n")

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"type": "concept", "tags": ["one", "two"]},
    )

    assert plan.proposed_content == (
        "---\n" "type: concept\n" "tags:\n" "- one\n" "- two\n" "---\n" "Body\r\n"
    )


def test_merge_uses_lf_for_existing_crlf_frontmatter(tmp_path: Path) -> None:
    """LF-always applies to editing existing frontmatter, not just the
    populate-from-empty case above -- the body keeps its own CRLF."""
    path = tmp_path / "topic.md"
    path.write_bytes(b"---\r\ntype: concept\r\ntitle: Old\r\n---\r\nBody\r\n")

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"title": "New"})

    yaml_block = plan.proposed_content.split("---\n", 1)[1].split("\n---\n", 1)[0]
    assert "\r" not in yaml_block
    assert plan.proposed_content.endswith("Body\r\n")


def test_merge_creates_frontmatter_leaves_body_untouched(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    body = "# Body\n\nKeep exactly.\n"
    path.write_text(body, encoding="utf-8")

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"type": "concept", "owner": None},
    )

    assert plan.proposed_content.endswith(body)
    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.body == body
    assert parsed.frontmatter == {"type": "concept", "owner": None}


def test_merge_preserves_comment_on_untargeted_key(tmp_path: Path) -> None:
    """A comment attached to a key the merge does not target survives,
    per ADR-0002's documented preservation contract."""
    path = tmp_path / "topic.md"
    path.write_text(
        "---\n"
        "type: concept\n"
        "owner: team  # tracked in ownership sheet\n"
        "title: Old\n"
        "---\n"
        "Body\n",
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"title": "New"})

    assert "owner: team  # tracked in ownership sheet" in plan.proposed_content
    assert parse_concept_document(plan.proposed_content).frontmatter == {
        "type": "concept",
        "owner": "team",
        "title": "New",
    }


def test_merge_canonicalizes_noncanonical_input_on_first_edit(tmp_path: Path) -> None:
    """A conformant but non-canonical document (a flow-style collection) is
    accepted with no "canonicalize the document first" precondition, and its
    first edit converges the touched value to block style (R-C2/R-C3)."""
    path = tmp_path / "topic.md"
    path.write_text(
        "---\ntype: concept\ntags: [alpha, beta]\n---\nBody\n",
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"tags": ["alpha", "beta", "gamma"]},
    )

    assert plan.changed is True
    assert "tags:\n- alpha\n- beta\n- gamma\n" in plan.proposed_content
    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["tags"] == ["alpha", "beta", "gamma"]


def test_merge_returns_noop_for_type_equivalent_nested_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    original = (
        "---\n"
        "type: concept\n"
        "meta:\n"
        "  owner: team\n"
        "  flags: [one, two]\n"
        "---\n"
        "Body\n"
    )
    path.write_text(original, encoding="utf-8")

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"meta": {"flags": ["one", "two"], "owner": "team"}},
    )

    assert plan.changed is False
    assert plan.proposed_content == original


def test_merge_does_not_treat_boolean_and_integer_as_equivalent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\nvalue: true\n---\n", encoding="utf-8")

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"value": 1})

    assert plan.changed is True
    assert parse_concept_document(plan.proposed_content).frontmatter["value"] == 1


@pytest.mark.parametrize(
    "field_source",
    [
        "owner:\n",
        "owner: # keep this comment\n",
    ],
)
def test_merge_replaces_implicit_null_value(
    tmp_path: Path,
    field_source: str,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        f"---\ntype: concept\n{field_source}next: keep\n---\n",
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"owner": "team"})

    assert parse_concept_document(plan.proposed_content).frontmatter == {
        "type": "concept",
        "owner": "team",
        "next": "keep",
    }
    assert "owner: team" in plan.proposed_content


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("2026-07-04", date(2026, 7, 4)),
        ("2026-07-04 12:30:00", datetime(2026, 7, 4, 12, 30)),
        (
            "2026-07-04 12:30:00+00:00",
            datetime(2026, 7, 4, 12, 30, tzinfo=timezone.utc),
        ),
    ],
)
def test_merge_returns_noop_for_equivalent_date_values(
    tmp_path: Path,
    source: str,
    value: date | datetime,
) -> None:
    path = tmp_path / "topic.md"
    original = f"---\ntype: concept\nvalue: {source}\n---\n"
    path.write_text(original, encoding="utf-8")

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"value": value})

    assert plan.changed is False
    assert plan.proposed_content == original


def test_merge_distinguishes_date_from_string(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        '---\ntype: concept\nvalue: "2026-07-04"\n---\n',
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"value": date(2026, 7, 4)},
    )

    assert plan.changed is True
    assert (
        type(parse_concept_document(plan.proposed_content).frontmatter["value"]) is date
    )


@pytest.mark.parametrize(
    "content",
    [
        "---\ntype: [unterminated\n---\nBody\n",
        "---\n- not\n- a mapping\n---\nBody\n",
        "---\ntype: concept\nBody\n",
    ],
)
def test_merge_rejects_malformed_frontmatter(
    tmp_path: Path,
    content: str,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError):
        plan_frontmatter_merge(_bundle(tmp_path), path, {"title": "New"})

    assert path.read_text(encoding="utf-8") == content


def test_merge_rejects_duplicate_top_level_keys(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "---\ntype: concept\ntitle: First\ntitle: Second\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(DocumentChangePlanningError, match="duplicate"):
        plan_frontmatter_merge(_bundle(tmp_path), path, {"title": "New"})


def test_merge_empty_updates_is_noop_even_for_malformed_frontmatter(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: First\ntitle: Second\n---\n"
    path.write_text(original, encoding="utf-8")

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {})

    assert plan.proposed_content == original


def test_merge_preserves_untargeted_aliases(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "---\n" "type: concept\n" "a: &shared value\n" "b: *shared\n" "---\n"
    path.write_text(original, encoding="utf-8")

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"type": "updated"})

    assert plan.proposed_content == original.replace(
        "type: concept",
        "type: updated",
    )


@pytest.mark.parametrize("target", ["a", "b"])
def test_merge_rejects_targeted_alias_relationship(
    tmp_path: Path,
    target: str,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "---\ntype: concept\na: &shared value\nb: *shared\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(DocumentChangePlanningError, match="cannot be changed"):
        plan_frontmatter_merge(_bundle(tmp_path), path, {target: "updated"})


@pytest.mark.parametrize(
    "value",
    [
        (1, 2),
        {1, 2},
        OrderedDict([("owner", "docs")]),
        MappingProxyType({"owner": "docs"}),
        object(),
        float("nan"),
        float("inf"),
        float("-inf"),
        time(12, 30),
        timedelta(days=1),
    ],
)
def test_merge_rejects_unsupported_update_values(
    tmp_path: Path,
    value: Any,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\n---\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="supported"):
        plan_frontmatter_merge(_bundle(tmp_path), path, {"value": value})


def test_merge_rejects_non_string_nested_mapping_key(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\n---\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="string keys"):
        plan_frontmatter_merge(_bundle(tmp_path), path, {"value": {1: "one"}})


def test_merge_rejects_cyclic_update_value(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\n---\n", encoding="utf-8")
    recursive: list[Any] = []
    recursive.append(recursive)

    with pytest.raises(DocumentChangePlanningError, match="shared or cyclic"):
        plan_frontmatter_merge(_bundle(tmp_path), path, {"value": recursive})


def test_merge_rejects_shared_update_container(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\n---\n", encoding="utf-8")
    shared = ["one"]

    with pytest.raises(DocumentChangePlanningError, match="shared or cyclic"):
        plan_frontmatter_merge(
            _bundle(tmp_path),
            path,
            {"first": shared, "second": shared},
        )


@pytest.mark.parametrize(
    "updates",
    [
        [],
        {1: "value"},
        {"": "value"},
        {"   ": "value"},
        {" title ": "value"},
        {"title ": "value"},
        {"title\n": "value"},
        {"line1\nline2": "value"},
        {"value": object()},
    ],
)
def test_merge_rejects_invalid_updates(tmp_path: Path, updates: Any) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\n---\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError):
        plan_frontmatter_merge(_bundle(tmp_path), path, updates)


def test_merge_empty_updates_returns_noop(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\n---\nBody\n"
    path.write_text(original, encoding="utf-8")

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {})

    assert plan.changed is False
    assert plan.proposed_content == original


def test_merge_empty_updates_does_not_create_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "# Body\n"
    path.write_text(original, encoding="utf-8")

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {})

    assert plan.changed is False
    assert plan.proposed_content == original


def test_merge_reads_target_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\n---\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes
    reads = 0

    def count_reads(self: Path) -> bytes:
        nonlocal reads
        if self == path:
            reads += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", count_reads)

    plan_frontmatter_merge(_bundle(tmp_path), path, {"type": "updated"})

    assert reads == 1


def test_frontmatter_merge_plan_applies_with_safe_write_api(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\ntitle: Old\n---\n", encoding="utf-8")
    bundle = _bundle(tmp_path)
    plan = plan_frontmatter_merge(bundle, path, {"title": "New"})

    result = apply_document_change(bundle, plan)

    assert result.changed is True
    assert parse_concept_document(path.read_text()).frontmatter["title"] == "New"


def test_frontmatter_merge_plan_retains_stale_hash_protection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\ntitle: Old\n---\n", encoding="utf-8")
    bundle = _bundle(tmp_path)
    plan = plan_frontmatter_merge(bundle, path, {"title": "New"})
    path.write_text("---\ntype: concept\ntitle: Concurrent\n---\n", encoding="utf-8")

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)

    assert parse_concept_document(path.read_text()).frontmatter["title"] == "Concurrent"


def test_load_frontmatter_wraps_ruamel_parse_errors_narrowly() -> None:
    """`_load_frontmatter` catches ruamel's own `YAMLError` hierarchy and
    reports it through `DocumentChangePlanningError`, rather than a bare
    `except Exception` swallowing unrelated bugs (#195; see the narrow-except
    finding logged against #194 in docs/decisions/failure-ledger.md for the
    opposite failure mode this guards against)."""
    with pytest.raises(DocumentChangePlanningError, match="Could not parse"):
        _load_frontmatter(Path("frontmatter.yaml"), "a: [unterminated\n")


# ---------------------------------------------------------------------------
# _yaml_values_equal -- ruamel round-trip scalar subtype normalization (#195)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("yaml_source", "key", "plain_value"),
    [
        ('value: "quoted"\n', "value", "quoted"),
        ("value: 'quoted'\n", "value", "quoted"),
        ("value: 0x10\n", "value", 16),
        ("value: 1_000\n", "value", 1000),
        ("value: 1.5\n", "value", 1.5),
    ],
)
def test_yaml_values_equal_normalizes_ruamel_scalar_subtypes(
    yaml_source: str,
    key: str,
    plain_value: Any,
) -> None:
    """A ruamel round-trip scalar (e.g. ``DoubleQuotedScalarString``,
    ``HexInt``) must compare equal to the plain Python value it represents.

    Without normalizing these formatting subclasses to their base type
    first, ``type(left) is not type(right)`` rejects every one of these
    pairs even though they are the same value -- exactly the bug this test
    pins: re-applying an update whose value already matches, just via a
    differently-formatted source scalar, must stay a no-op.
    """
    data = _load_frontmatter(Path("frontmatter.yaml"), yaml_source)
    loaded_value = data[key]

    assert type(loaded_value) is not type(plain_value)
    assert _yaml_values_equal(loaded_value, plain_value) is True


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (True, 1),
        (False, 0),
        (date(2026, 7, 4), "2026-07-04"),
        (datetime(2026, 7, 4, 12, 0), "2026-07-04 12:00:00"),
    ],
)
def test_yaml_values_equal_preserves_real_type_distinctions(
    left: Any,
    right: Any,
) -> None:
    """Normalizing ruamel's formatting subclasses must not blur the
    semantic type distinctions the merge contract already relies on."""
    assert _yaml_values_equal(left, right) is False


def test_merge_reapplying_same_update_is_noop_despite_quote_style_change(
    tmp_path: Path,
) -> None:
    """Applying, writing, then re-planning the identical update is a no-op
    end to end, even though the first apply's dump may format the value
    differently (e.g. adopting the surrounding quote style) than the plain
    value the caller supplied."""
    path = tmp_path / "topic.md"
    path.write_text("---\ntype: concept\ntitle: 'Old'\n---\n", encoding="utf-8")
    bundle = _bundle(tmp_path)

    first = plan_frontmatter_merge(bundle, path, {"title": "New"})
    apply_document_change(bundle, first)

    second = plan_frontmatter_merge(bundle, path, {"title": "New"})

    assert second.changed is False


# ---------------------------------------------------------------------------
# Canonical serialization form (ADR-0002): idempotence property test
# ---------------------------------------------------------------------------

_FRONTMATTER_KEY = st.text(
    alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    min_size=1,
    max_size=8,
)
_FRONTMATTER_TEXT = st.text(
    alphabet=st.characters(
        min_codepoint=0x20,
        max_codepoint=0x2FFF,
        blacklist_categories=("Cs", "Cc"),
    ),
    max_size=15,
)
# Excludes tiny-magnitude/subnormal floats: ruamel's format-preserving
# ScalarFloat re-renders a reloaded value's digits independently of Python's
# own repr(), and for extreme exponents that can pick a different (still
# numerically equal) last digit on redump -- a ruamel formatting quirk
# orthogonal to what this property test is checking (the merge engine's own
# dump/load round trip), so it is filtered out rather than chased here.
_FRONTMATTER_FLOAT = st.floats(
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
    min_value=-1e6,
    max_value=1e6,
).filter(lambda value: value == 0 or abs(value) >= 1e-4)
_FRONTMATTER_SCALAR = st.one_of(
    _FRONTMATTER_TEXT,
    st.booleans(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    _FRONTMATTER_FLOAT,
    st.none(),
    st.dates(),
    st.datetimes(),
)
_FRONTMATTER_VALUE = st.recursive(
    _FRONTMATTER_SCALAR,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(_FRONTMATTER_KEY, children, max_size=3),
    ),
    max_leaves=6,
)


@given(data=st.dictionaries(_FRONTMATTER_KEY, _FRONTMATTER_VALUE, max_size=5))
def test_frontmatter_dump_is_idempotent(data: dict[str, Any]) -> None:
    """``serialize(parse(serialize(x))) == serialize(x)`` (ADR-0002):
    re-serializing already-canonical frontmatter output is a fixed point."""
    once = _dump_frontmatter(CommentedMap(data))
    reloaded = _load_frontmatter(Path("frontmatter.yaml"), once)
    twice = _dump_frontmatter(reloaded)

    assert twice == once

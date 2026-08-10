"""Tests for the source/provenance bookkeeping write primitive (#113)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from okf_core import (
    BundleConfig,
    DocumentChangeConflictError,
    DocumentChangePlanningError,
    apply_document_change,
    parse_concept_document,
    plan_source_upsert,
    source_upsert,
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


# ---------------------------------------------------------------------------
# AC1 / AC2: add to an existing list; create a conformant list when absent
# ---------------------------------------------------------------------------


def test_plan_creates_sources_list_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_source_upsert(bundle, path, {"resource": "https://example.com/a"})

    assert plan.changed is True
    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["sources"] == [{"resource": "https://example.com/a"}]


def test_plan_appends_to_existing_sources_list(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nsources:\n  - resource: https://example.com/a\n"
        "    id: a\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_source_upsert(
        bundle, path, {"resource": "https://example.com/b", "id": "b"}
    )

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["sources"] == [
        {"resource": "https://example.com/a", "id": "a"},
        {"resource": "https://example.com/b", "id": "b"},
    ]


def test_plan_stores_full_entry_shape(tmp_path: Path) -> None:
    """§5.1: resource required; id, title, and credibility signals optional."""
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    source = {
        "id": "ga4-schema",
        "resource": "https://developers.google.com/analytics/bigquery/export-schema",
        "title": "GA4 BigQuery Export schema",
        "author": "team:ga4-docs",
        "usage_count": 5000,
        "last_modified": date(2026, 5, 30),
    }

    plan = plan_source_upsert(bundle, path, source)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["sources"] == [source]


def test_plan_stores_per_entry_usage_window_override_as_given(tmp_path: Path) -> None:
    """A caller-supplied per-entry `usage_window` is passed through untouched."""
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    source = {
        "resource": "https://example.com/a",
        "usage_window": {"from": date(2026, 1, 1), "to": date(2026, 1, 31)},
    }

    plan = plan_source_upsert(bundle, path, source)

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["sources"][0]["usage_window"] == {
        "from": date(2026, 1, 1),
        "to": date(2026, 1, 31),
    }


# ---------------------------------------------------------------------------
# AC4 / AC5: identity match is a full no-op, not a merge or duplicate
# ---------------------------------------------------------------------------


def test_identity_match_via_id_is_full_noop(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nsources:\n  - resource: https://example.com/old\n"
        "    id: ga4-schema\n    title: Original title\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    # Same id, but every other field differs -- must not be merged in.
    plan = plan_source_upsert(
        bundle,
        path,
        {
            "id": "ga4-schema",
            "resource": "https://example.com/new",
            "title": "Different title",
        },
    )

    assert plan.changed is False
    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["sources"] == [
        {
            "resource": "https://example.com/old",
            "id": "ga4-schema",
            "title": "Original title",
        }
    ]


def test_identity_match_via_resource_fallback_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nsources:\n  - resource: https://example.com/a\n"
        "---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_source_upsert(bundle, path, {"resource": "https://example.com/a"})

    assert plan.changed is False
    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["sources"] == [{"resource": "https://example.com/a"}]


def test_resource_only_candidate_does_not_match_different_id_same_resource(
    tmp_path: Path,
) -> None:
    """Identity is per-entry id-falling-back-to-resource, not a merged namespace:
    an id-bearing existing entry is keyed by its id, so a resource-only
    candidate for the same resource is a distinct identity and is appended.
    """
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nsources:\n  - resource: https://example.com/a\n"
        "    id: some-id\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_source_upsert(bundle, path, {"resource": "https://example.com/a"})

    assert plan.changed is True
    parsed = parse_concept_document(plan.proposed_content)
    assert len(parsed.frontmatter["sources"]) == 2


def test_repeated_upsert_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    source_upsert(bundle, path, {"resource": "https://example.com/a", "id": "a"})
    result = source_upsert(
        bundle, path, {"resource": "https://example.com/a", "id": "a"}
    )

    assert result.changed is False
    parsed = parse_concept_document(path.read_text(encoding="utf-8"))
    assert parsed.frontmatter["sources"] == [
        {"resource": "https://example.com/a", "id": "a"}
    ]


# ---------------------------------------------------------------------------
# AC4: existing entries retain content and order; AC6: sibling keys untouched
# ---------------------------------------------------------------------------


def test_existing_entries_order_and_content_preserved_on_append(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nsources:\n"
        "  - resource: https://example.com/a\n    id: a\n"
        "  - resource: https://example.com/b\n    id: b\n"
        "---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_source_upsert(
        bundle, path, {"resource": "https://example.com/c", "id": "c"}
    )

    parsed = parse_concept_document(plan.proposed_content)
    assert [entry["id"] for entry in parsed.frontmatter["sources"]] == ["a", "b", "c"]


def test_unknown_sibling_frontmatter_keys_untouched(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\ncustom_field: keep-me\n"
        "usage_window: { from: 2026-06-01, to: 2026-06-30 }\n---\nBody\n",
    )
    bundle = _bundle(tmp_path)

    plan = plan_source_upsert(bundle, path, {"resource": "https://example.com/a"})

    parsed = parse_concept_document(plan.proposed_content)
    assert parsed.frontmatter["custom_field"] == "keep-me"
    assert parsed.frontmatter["usage_window"] == {
        "from": date(2026, 6, 1),
        "to": date(2026, 6, 30),
    }


# ---------------------------------------------------------------------------
# AC7: malformed existing `sources` content is reported explicitly, no write
# ---------------------------------------------------------------------------


def test_plan_rejects_document_with_unparseable_frontmatter(tmp_path: Path) -> None:
    """A document whose frontmatter fails to parse at all (not just its
    `sources` value) must also be refused before any write, not just the
    narrower "sources value itself is malformed" case."""
    path = tmp_path / "topic.md"
    original = "---\ntype: [unterminated\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="parse"):
        plan_source_upsert(bundle, path, {"resource": "https://example.com/a"})

    assert path.read_text(encoding="utf-8") == original


def test_plan_rejects_non_list_existing_sources(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\nsources: not-a-list\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="must be a list"):
        plan_source_upsert(bundle, path, {"resource": "https://example.com/a"})

    assert path.read_text(encoding="utf-8") == original


def test_plan_rejects_existing_entry_that_is_not_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\nsources:\n  - just-a-string\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="must be a mapping"):
        plan_source_upsert(bundle, path, {"resource": "https://example.com/a"})

    assert path.read_text(encoding="utf-8") == original


def test_plan_rejects_existing_entry_missing_resource(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\nsources:\n  - id: a\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="resource"):
        plan_source_upsert(bundle, path, {"resource": "https://example.com/b"})

    assert path.read_text(encoding="utf-8") == original


def test_plan_rejects_existing_entry_with_empty_id(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = (
        "---\ntype: concept\nsources:\n"
        "  - resource: https://example.com/a\n    id: ''\n---\nBody\n"
    )
    _write(path, original)
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="id"):
        plan_source_upsert(bundle, path, {"resource": "https://example.com/b"})

    assert path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Candidate (`source` argument) shape validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [None, "a string", 123, ["list", "not", "mapping"]],
    ids=["none", "string", "int", "list"],
)
def test_plan_rejects_non_mapping_candidate(tmp_path: Path, source: Any) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="mapping"):
        plan_source_upsert(bundle, path, source)


@pytest.mark.parametrize(
    "source",
    [{}, {"resource": ""}, {"resource": None}, {"resource": 123}, {"title": "x"}],
    ids=["missing", "empty", "none", "int", "other-fields-only"],
)
def test_plan_rejects_candidate_missing_resource(
    tmp_path: Path, source: dict[str, Any]
) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="resource"):
        plan_source_upsert(bundle, path, source)


@pytest.mark.parametrize(
    "candidate_id",
    ["", 123, []],
    ids=["empty-string", "int", "list"],
)
def test_plan_rejects_candidate_with_invalid_id(
    tmp_path: Path, candidate_id: Any
) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    with pytest.raises(DocumentChangePlanningError, match="id"):
        plan_source_upsert(
            bundle, path, {"resource": "https://example.com/a", "id": candidate_id}
        )


def test_plan_accepts_candidate_with_none_id(tmp_path: Path) -> None:
    """`id: None` is treated the same as `id` being absent."""
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_source_upsert(
        bundle, path, {"resource": "https://example.com/a", "id": None}
    )

    assert plan.changed is True


def test_plan_rejects_malformed_candidate_before_reading_target(
    tmp_path: Path,
) -> None:
    """Validation happens before the target is even resolved/read."""
    bundle = _bundle(tmp_path)
    missing_path = tmp_path / "does-not-exist.md"

    with pytest.raises(DocumentChangePlanningError, match="resource"):
        plan_source_upsert(bundle, missing_path, {"title": "no resource"})


# ---------------------------------------------------------------------------
# AC8: participates in the #110 plan/apply contract
# ---------------------------------------------------------------------------


def test_source_upsert_writes_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    result = source_upsert(bundle, path, {"resource": "https://example.com/a"})

    assert result.changed is True
    parsed = parse_concept_document(path.read_text(encoding="utf-8"))
    assert parsed.frontmatter["sources"] == [{"resource": "https://example.com/a"}]


def test_apply_reports_stale_content_conflict(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    _write(path, "---\ntype: concept\n---\nBody\n")
    bundle = _bundle(tmp_path)

    plan = plan_source_upsert(bundle, path, {"resource": "https://example.com/a"})
    _write(path, "---\ntype: concept\nsources:\n  - resource: concurrent\n---\nBody\n")

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)

    # The concurrent write must survive untouched -- not be silently
    # clobbered by a plan built from an earlier, separate read.
    parsed = parse_concept_document(path.read_text(encoding="utf-8"))
    assert parsed.frontmatter["sources"] == [{"resource": "concurrent"}]


def test_plan_reads_target_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the single-read design `plan_source_upsert` documents.

    Deriving the new `sources` list from a separately-read snapshot, then
    handing a static value to `plan_frontmatter_merge` (which performs its
    own, second read), would silently discard a concurrent edit landing
    between the two reads -- exactly the risk
    `plan_document_change_from_reader`'s own docstring describes. Counting
    `Path.read_bytes` calls (the primitive `_read_for_planning` uses) is a
    black-box way to confirm the implementation still reads the target only
    once, regardless of internal refactoring.
    """
    path = tmp_path / "topic.md"
    _write(
        path,
        "---\ntype: concept\nsources:\n  - resource: https://example.com/a\n"
        "---\nBody\n",
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

    plan_source_upsert(bundle, path, {"resource": "https://example.com/b"})

    assert read_count == 1


def test_dry_run_plan_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: concept\n---\nBody\n"
    _write(path, original)
    bundle = _bundle(tmp_path)

    plan_source_upsert(bundle, path, {"resource": "https://example.com/a"})

    assert path.read_text(encoding="utf-8") == original

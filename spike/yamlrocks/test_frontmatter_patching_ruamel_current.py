from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from okf_core import (
    BundleConfig,
    DocumentChangeConflictError,
    DocumentChangePlanningError,
    apply_document_change,
    parse_concept_document,
)
from spike.patching_ruamel_prototype import (
    plan_frontmatter_merge_ruamel as plan_frontmatter_merge,
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


def test_merge_replaces_scalar_and_preserves_unrelated_bytes(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = (
        "---\r\n"
        "# keep this comment\r\n"
        "type: concept\r\n"
        'title: "Old" # keep inline\r\n'
        "custom: 'unchanged'\r\n"
        "---\r\n"
        "# Body\r\n"
    )
    path.write_bytes(original.encode())

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"title": "New"})

    assert plan.proposed_content == (
        "---\r\n"
        "# keep this comment\r\n"
        "type: concept\r\n"
        "title: New # keep inline\r\n"
        "custom: 'unchanged'\r\n"
        "---\r\n"
        "# Body\r\n"
    )
    assert path.read_bytes() == original.encode()


def test_merge_changes_inline_scalar_to_flow_collection(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "---\ntype: concept\ntags: old # keep\n---\nBody\n",
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"tags": ["alpha", "beta"]})

    assert plan.proposed_content == (
        "---\ntype: concept\ntags: [alpha, beta] # keep\n---\nBody\n"
    )


def test_merge_changes_block_collection_without_touching_next_field(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "---\n"
        "type: concept\n"
        "tags:\n"
        "  - alpha\n"
        "  - beta\n"
        "next: keep\n"
        "---\n"
        "Body\n",
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"tags": "single"})

    assert plan.proposed_content == (
        "---\n" "type: concept\n" "tags:\n" "  single\n" "next: keep\n" "---\n" "Body\n"
    )
    assert parse_concept_document(plan.proposed_content).frontmatter["tags"] == "single"


def test_merge_replaces_block_mapping_with_preserved_indentation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "---\n"
        "type: concept\n"
        "meta:\n"
        "  owner: old\n"
        "  count: 1\n"
        "next: keep\n"
        "---\n",
        encoding="utf-8",
    )

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"meta": {"owner": "new", "count": 2}},
    )

    assert plan.proposed_content == (
        "---\n"
        "type: concept\n"
        "meta:\n"
        "  owner: new\n"
        "  count: 2\n"
        "next: keep\n"
        "---\n"
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


def test_merge_applies_multiple_replacements_without_offset_drift(
    tmp_path: Path,
) -> None:
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

    assert plan.proposed_content == (
        "---\ntype: new\ntitle: New\ncount: 2\n---\nBody\n"
    )


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

    assert plan.proposed_content == (
        "---\n" "type: concept\n" "owner: team\n" "status: active\n" "---\n" "Body\n"
    )


def test_merge_populates_empty_frontmatter_with_document_line_endings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_bytes(b"---\r\n---\r\nBody\r\n")

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"type": "concept", "tags": ["one", "two"]},
    )

    assert plan.proposed_content == (
        "---\r\n"
        "type: concept\r\n"
        "tags:\r\n"
        "- one\r\n"
        "- two\r\n"
        "---\r\n"
        "Body\r\n"
    )


def test_merge_creates_frontmatter_without_changing_body(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    body = "# Body\n\nKeep exactly.\n"
    path.write_text(body, encoding="utf-8")

    plan = plan_frontmatter_merge(
        _bundle(tmp_path),
        path,
        {"type": "concept", "owner": None},
    )

    assert plan.proposed_content == (
        "---\n" "type: concept\n" "owner: null\n" "---\n" f"{body}"
    )


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
    if "#" in field_source:
        assert " # keep this comment\n" in plan.proposed_content


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


def test_merge_preserves_untargeted_richer_yaml_value(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = (
        "---\n"
        "type: concept\n"
        "published: 2026-07-04\n"
        "metadata: {owners: [docs, platform]}\n"
        "---\n"
    )
    path.write_text(original, encoding="utf-8")

    plan = plan_frontmatter_merge(_bundle(tmp_path), path, {"type": "updated"})

    assert plan.proposed_content == original.replace(
        "type: concept",
        "type: updated",
    )


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

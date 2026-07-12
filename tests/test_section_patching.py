from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from okf_core import (
    BundleConfig,
    DocumentChangeConflictError,
    DocumentChangePlanningError,
    apply_document_change,
    plan_markdown_section_patch,
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


def test_plan_section_patch_replaces_atx_body_and_preserves_surroundings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    original = (
        "---\n"
        "type: concept\n"
        "custom: keep\n"
        "---\n"
        "Introduction.\n"
        "\n"
        "## Target\n"
        "Old body.\n"
        "### Nested\n"
        "Old nested body.\n"
        "\n"
        "## Next\n"
        "Keep this.\n"
    )
    path.write_text(original, encoding="utf-8")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Target", "New body.", level=2
    )

    assert plan.original_content == original
    assert plan.proposed_content == (
        "---\n"
        "type: concept\n"
        "custom: keep\n"
        "---\n"
        "Introduction.\n"
        "\n"
        "## Target\n"
        "New body.\n"
        "## Next\n"
        "Keep this.\n"
    )
    assert path.read_text(encoding="utf-8") == original


def test_plan_section_patch_preserves_setext_heading(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "Target\n" "======\n" "Old body.\n" "\n" "# Next\n" "Keep this.\n",
        encoding="utf-8",
    )

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Target", "New body.", level=1
    )

    assert plan.proposed_content == (
        "Target\n" "======\n" "New body.\n" "# Next\n" "Keep this.\n"
    )


def test_plan_section_patch_ignores_heading_text_in_fenced_code(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "```markdown\n" "## Target\n" "```\n" "\n" "## Target\n" "Old body.\n",
        encoding="utf-8",
    )

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Target", "New body.", level=2
    )

    assert plan.proposed_content == (
        "```markdown\n" "## Target\n" "```\n" "\n" "## Target\n" "New body.\n"
    )


def test_plan_section_patch_matches_exact_markdown_heading_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("## My *Section*\nOld body.\n", encoding="utf-8")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "My *Section*", "New body.", level=2
    )

    assert plan.proposed_content == "## My *Section*\nNew body.\n"


def test_plan_section_patch_uses_case_sensitive_heading_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("# Target\nOld body.\n", encoding="utf-8")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "target", "New body.", level=1
    )

    assert plan.proposed_content == (
        "# Target\n" "Old body.\n" "\n" "# target\n" "New body.\n"
    )


def test_plan_section_patch_rejects_duplicate_matching_headings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "## Target\nFirst.\n## Other\nOther.\n## Target\nSecond.\n",
        encoding="utf-8",
    )

    with pytest.raises(DocumentChangePlanningError, match="multiple"):
        plan_markdown_section_patch(
            _bundle(tmp_path), path, "Target", "New body.", level=2
        )


def test_plan_section_patch_distinguishes_same_name_at_other_levels(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "# Target\nLevel one.\n## Target\nLevel two.\n",
        encoding="utf-8",
    )

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Target", "Updated.", level=2
    )

    assert plan.proposed_content == ("# Target\nLevel one.\n## Target\nUpdated.\n")


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("", "## Added\nBody.\n"),
        ("Introduction.", "Introduction.\n\n## Added\nBody.\n"),
        ("Introduction.\n", "Introduction.\n\n## Added\nBody.\n"),
        ("Introduction.\n\n", "Introduction.\n\n## Added\nBody.\n"),
    ],
)
def test_plan_section_patch_appends_missing_section_deterministically(
    tmp_path: Path,
    original: str,
    expected: str,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(original, encoding="utf-8", newline="")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Added", "Body.", level=2
    )

    assert plan.proposed_content == expected


def test_plan_section_patch_uses_document_crlf_for_generated_structure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_bytes(b"# Existing\r\nBody.\r\n")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Added", "New body.", level=2
    )

    assert plan.proposed_content == (
        "# Existing\r\n" "Body.\r\n" "\r\n" "## Added\r\n" "New body.\r\n"
    )


def test_plan_section_patch_does_not_add_separator_after_mixed_blank_line(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_bytes(b"Introduction.\r\n\n")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Added", "Body.", level=2
    )

    assert plan.proposed_content == (
        "Introduction.\r\n" "\n" "## Added\r\n" "Body.\r\n"
    )


def test_plan_section_patch_preserves_supplied_body_line_endings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_bytes(b"## Target\r\nOld.\r\n## Next\r\nKeep.\r\n")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path),
        path,
        "Target",
        "First\nSecond",
        level=2,
    )

    assert plan.proposed_content == (
        "## Target\r\n" "First\n" "Second\r\n" "## Next\r\n" "Keep.\r\n"
    )


def test_plan_section_patch_returns_noop_for_equivalent_body(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("## Target\nBody.\n", encoding="utf-8")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Target", "Body.", level=2
    )

    assert plan.changed is False
    assert plan.proposed_content == plan.original_content


@pytest.mark.parametrize("heading", [None, "", " Target", "Target ", "Target\nOther"])
def test_plan_section_patch_rejects_invalid_heading(
    tmp_path: Path, heading: Any
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("# Existing\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="heading"):
        plan_markdown_section_patch(_bundle(tmp_path), path, heading, "Body.", level=1)


@pytest.mark.parametrize("heading", ["Target #", "Target ##"])
def test_plan_section_patch_rejects_heading_that_does_not_round_trip(
    tmp_path: Path,
    heading: str,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("# Existing\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="ATX"):
        plan_markdown_section_patch(_bundle(tmp_path), path, heading, "Body.", level=2)


@pytest.mark.parametrize("level", [0, 7, True])
def test_plan_section_patch_rejects_invalid_level(tmp_path: Path, level: Any) -> None:
    path = tmp_path / "topic.md"
    path.write_text("# Existing\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="level"):
        plan_markdown_section_patch(
            _bundle(tmp_path), path, "Target", "Body.", level=level
        )


def test_plan_section_patch_rejects_non_string_body(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("# Existing\n", encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="body"):
        plan_markdown_section_patch(
            _bundle(tmp_path), path, "Target", b"Body.", level=1  # type: ignore[arg-type]
        )


def test_plan_section_patch_reports_malformed_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "---\ntype: [unterminated\n---\n# Target\nOld.\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(DocumentChangePlanningError, match="frontmatter"):
        plan_markdown_section_patch(_bundle(tmp_path), path, "Target", "New.", level=1)

    assert path.read_text(encoding="utf-8") == original


def test_plan_section_patch_reads_target_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("## Target\nOld.\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes
    reads = 0

    def count_reads(self: Path) -> bytes:
        nonlocal reads
        if self == path:
            reads += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", count_reads)

    plan_markdown_section_patch(_bundle(tmp_path), path, "Target", "New.", level=2)

    assert reads == 1


def test_section_patch_plan_applies_with_existing_safe_write_api(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("## Target\nOld.\n", encoding="utf-8")
    bundle = _bundle(tmp_path)
    plan = plan_markdown_section_patch(bundle, path, "Target", "New.", level=2)

    result = apply_document_change(bundle, plan)

    assert result.changed is True
    assert path.read_text(encoding="utf-8") == "## Target\nNew.\n"


def test_section_patch_plan_retains_stale_hash_protection(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("## Target\nOld.\n", encoding="utf-8")
    bundle = _bundle(tmp_path)
    plan = plan_markdown_section_patch(bundle, path, "Target", "New.", level=2)
    path.write_text("## Target\nConcurrent.\n", encoding="utf-8")

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)

    assert path.read_text(encoding="utf-8") == "## Target\nConcurrent.\n"

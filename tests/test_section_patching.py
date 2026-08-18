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


# --- Retired byte-preservation cases (#198) ---------------------------------
#
# The section-patch engine used to splice replacement bytes into an
# unmodified source string (`_patch_markdown_section`'s line-offset splicer),
# so untouched surrounding bytes -- including exact blank-line counts and
# line-ending style -- were part of the observable contract. #198 replaced
# that with parse -> mutate token tree -> re-render through `mdformat`'s
# `MDRenderer`, whose contract is canonical, idempotent Markdown (ADR-0002),
# not byte identity. The following pre-#198 test cases asserted exactly the
# byte-identity behavior that contract retired, and are retired here rather
# than updated in place -- their *values* were drift oracles (pinning "what
# the current splicer happens to produce"), not correctness oracles (nothing
# in the section-patch contract ever required these specific bytes):
#
# - test_plan_section_patch_uses_document_crlf_for_generated_structure --
#   asserted a *generated* heading/separator inherits the document's CRLF
#   style. The canonical engine always emits LF (ADR-0002 amendment,
#   mirroring the YAML-side frontmatter engine's own LF-always choice); see
#   test_plan_section_patch_crlf_input_converges_to_lf_canonical_output below
#   for the replacement canonical-form assertion.
# - test_plan_section_patch_does_not_add_separator_after_mixed_blank_line --
#   asserted a specific blank-line-counting edge case (CRLF then LF) in the
#   old append splicer's own trailing-newline bookkeeping
#   (`_count_trailing_line_endings`). That bookkeeping -- and the "how many
#   line-ending bytes already trail the document" question it answered --
#   no longer exists; the renderer's own block-separator logic decides
#   spacing uniformly, covered by
#   test_plan_section_patch_appends_missing_section_deterministically below.
# - test_plan_section_patch_preserves_supplied_body_line_endings -- asserted
#   a caller-supplied body's internal LF is preserved while trailing CRLF
#   is added to match the document. The canonical engine parses the
#   supplied body as Markdown and re-renders it (always LF), so "preserve
#   the caller's literal line-ending bytes" is no longer the contract;
#   test_plan_section_patch_replaces_atx_body_and_preserves_surroundings
#   below still covers "the replacement body's *content* lands under the
#   right heading."
#
# `test_plan_section_patch_preserves_setext_heading` is not retired but
# repurposed below (renamed to
# `test_plan_section_patch_matches_setext_heading_and_canonicalizes_to_atx`)
# since matching a Setext heading by content is still a correctness oracle;
# only its byte-preservation assertion (Setext markup surviving to output)
# was retired.


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
    # Canonical output (ADR-0002): a blank line always separates a heading
    # from its body/next heading, regardless of the source's spacing -- the
    # "### Nested" subsection is replaced along with the rest of "##
    # Target"'s body, and "## Next" and its own body survive untouched in
    # substance (heading text, level, and body content unchanged).
    assert plan.proposed_content == (
        "---\n"
        "type: concept\n"
        "custom: keep\n"
        "---\n"
        "Introduction.\n"
        "\n"
        "## Target\n"
        "\n"
        "New body.\n"
        "\n"
        "## Next\n"
        "\n"
        "Keep this.\n"
    )
    assert path.read_text(encoding="utf-8") == original


def test_plan_section_patch_matches_setext_heading_and_canonicalizes_to_atx(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(
        "Target\n" "======\n" "Old body.\n" "\n" "# Next\n" "Keep this.\n",
        encoding="utf-8",
    )

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Target", "New body.", level=1
    )

    # A Setext heading is still located by its parsed content/level, but
    # canonical Markdown has no Setext form (ADR-0002) -- it renders as ATX,
    # the same convergence-on-first-touch churn frontmatter's block-style
    # normalization already documents.
    assert plan.proposed_content == (
        "# Target\n" "\n" "New body.\n" "\n" "# Next\n" "\n" "Keep this.\n"
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
        "```markdown\n" "## Target\n" "```\n" "\n" "## Target\n" "\n" "New body.\n"
    )


def test_plan_section_patch_matches_exact_markdown_heading_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("## My *Section*\nOld body.\n", encoding="utf-8")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "My *Section*", "New body.", level=2
    )

    assert plan.proposed_content == "## My *Section*\n\nNew body.\n"


def test_plan_section_patch_uses_case_sensitive_heading_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text("# Target\nOld body.\n", encoding="utf-8")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "target", "New body.", level=1
    )

    assert plan.proposed_content == (
        "# Target\n" "\n" "Old body.\n" "\n" "# target\n" "\n" "New body.\n"
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

    assert plan.proposed_content == (
        "# Target\n" "\n" "Level one.\n" "\n" "## Target\n" "\n" "Updated.\n"
    )


@pytest.mark.parametrize(
    "original",
    ["", "Introduction.", "Introduction.\n", "Introduction.\n\n"],
)
def test_plan_section_patch_appends_missing_section_deterministically(
    tmp_path: Path,
    original: str,
) -> None:
    # Canonical rendering (ADR-0002) normalizes block separation uniformly,
    # so every variant of "how much trailing whitespace already followed the
    # introduction" converges to the exact same appended-section output --
    # the determinism this test's name promises, now proven across a wider
    # set of inputs than the old splicer's own separator-counting logic
    # needed to special-case.
    path = tmp_path / "topic.md"
    path.write_text(original, encoding="utf-8", newline="")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Added", "Body.", level=2
    )

    expected = (
        "## Added\n\nBody.\n"
        if not original.strip()
        else "Introduction.\n\n## Added\n\nBody.\n"
    )
    assert plan.proposed_content == expected


def test_plan_section_patch_crlf_input_converges_to_lf_canonical_output(
    tmp_path: Path,
) -> None:
    """Replaces the retired CRLF-preservation cases (see module banner).

    A CRLF-sourced document converges to canonical LF output on its first
    edit (R-C2/R-C3 convergence-on-first-touch) -- this is a one-time,
    accepted formatting cost, not a defect, mirroring the YAML frontmatter
    engine's own documented LF-always choice.
    """
    path = tmp_path / "topic.md"
    path.write_bytes(b"# Existing\r\nBody.\r\n")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Added", "New body.", level=2
    )

    assert "\r" not in plan.proposed_content
    assert plan.proposed_content == (
        "# Existing\n" "\n" "Body.\n" "\n" "## Added\n" "\n" "New body.\n"
    )


def test_plan_section_patch_returns_noop_for_equivalent_body(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("## Target\nBody.\n", encoding="utf-8")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Target", "Body.", level=2
    )

    assert plan.changed is False
    assert plan.proposed_content == plan.original_content


def test_plan_section_patch_noop_detection_is_semantic_not_byte_for_byte(
    tmp_path: Path,
) -> None:
    """A request that changes only source formatting -- not the section's
    canonical rendering -- is still a no-op (R-C3): the comparison that
    decides "did this edit change anything" is over each side's canonical
    render, not the caller's or the document's literal bytes."""
    path = tmp_path / "topic.md"
    # Non-canonical spacing: no blank line after the heading, and an extra
    # blank line before the next heading -- neither affects the section's
    # canonical (post-render) content.
    path.write_text("## Target\nBody.\n\n\n## Next\nMore.\n", encoding="utf-8")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Target", "Body.\n", level=2
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
    assert path.read_text(encoding="utf-8") == "## Target\n\nNew.\n"


def test_section_patch_plan_retains_stale_hash_protection(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("## Target\nOld.\n", encoding="utf-8")
    bundle = _bundle(tmp_path)
    plan = plan_markdown_section_patch(bundle, path, "Target", "New.", level=2)
    path.write_text("## Target\nConcurrent.\n", encoding="utf-8")

    with pytest.raises(DocumentChangeConflictError):
        apply_document_change(bundle, plan)

    assert path.read_text(encoding="utf-8") == "## Target\nConcurrent.\n"


def test_plan_section_patch_leaves_untargeted_gfm_table_intact(
    tmp_path: Path,
) -> None:
    # AC1: an untargeted GFM table must survive semantically through
    # plan_markdown_section_patch's parse -> mutate -> re-render pipeline,
    # not just the heading/body text this module otherwise exercises.
    path = tmp_path / "topic.md"
    original = (
        "---\n"
        "type: concept\n"
        "---\n"
        "## Data\n"
        "\n"
        "| A | B |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n"
        "\n"
        "## Target\n"
        "Old body.\n"
    )
    path.write_text(original, encoding="utf-8")

    plan = plan_markdown_section_patch(
        _bundle(tmp_path), path, "Target", "New body.", level=2
    )

    assert plan.proposed_content == (
        "---\n"
        "type: concept\n"
        "---\n"
        "## Data\n"
        "\n"
        "| A   | B   |\n"
        "| --- | --- |\n"
        "| 1   | 2   |\n"
        "\n"
        "## Target\n"
        "\n"
        "New body.\n"
    )

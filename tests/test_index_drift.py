"""Tests for diff_index: semantic drift comparison between a committed
index.md and a fresh regeneration of the directory it describes (#200)."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from okf_core.index import (
    diff_index,
    generate_index,
    parse_index,
)
from okf_core.manifest import ConceptManifestEntry


def _entry(
    path: Path,
    bundle_root: Path,
    *,
    concept_id: str = "stub",
    type_: str = "concept",
    title: str | None = None,
    description: str | None = None,
) -> ConceptManifestEntry:
    fm: dict = {"type": type_}
    if title is not None:
        fm["title"] = title
    if description is not None:
        fm["description"] = description
    return ConceptManifestEntry(
        concept_id=concept_id,
        path=path,
        bundle_root=bundle_root,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType(fm),
    )


# ---------------------------------------------------------------------------
# Clean comparisons
# ---------------------------------------------------------------------------


def test_diff_index_clean_round_trip(tmp_path: Path) -> None:
    """generate_index -> parse_index on the same content reports no drift."""
    alpha = _entry(tmp_path / "alpha.md", tmp_path, concept_id="alpha", title="Alpha")
    beta = _entry(
        tmp_path / "beta.md",
        tmp_path,
        concept_id="beta",
        title="Beta",
        description="Beta's description",
    )
    generated = generate_index(tmp_path, [alpha, beta])
    committed = parse_index(generated.body)

    assert diff_index(generated, committed) == ()


def test_diff_index_empty_both_sides_is_clean(tmp_path: Path) -> None:
    generated = generate_index(tmp_path, [])
    committed = parse_index("")

    assert diff_index(generated, committed) == ()


def test_diff_index_reordered_entries_within_heading_is_clean(tmp_path: Path) -> None:
    """AC2: a manually reordered but content-identical index reports no drift
    -- comparison is keyed by link, not list position."""
    alpha = _entry(tmp_path / "alpha.md", tmp_path, concept_id="alpha", title="Alpha")
    beta = _entry(tmp_path / "beta.md", tmp_path, concept_id="beta", title="Beta")
    generated = generate_index(tmp_path, [alpha, beta])

    # generate_index sorts entries alphabetically by title (Alpha, then
    # Beta); hand-write the committed content with the same two entries in
    # reverse order under the same heading.
    committed = parse_index("# Concept\n\n* [Beta](beta.md)\n* [Alpha](alpha.md)\n")

    assert diff_index(generated, committed) == ()


# ---------------------------------------------------------------------------
# Seeded drift
# ---------------------------------------------------------------------------


def test_diff_index_reports_added_file_missing_from_committed(tmp_path: Path) -> None:
    """A new concept exists on disk but the committed index.md predates it."""
    alpha = _entry(tmp_path / "alpha.md", tmp_path, concept_id="alpha", title="Alpha")
    beta = _entry(tmp_path / "beta.md", tmp_path, concept_id="beta", title="Beta")
    generated = generate_index(tmp_path, [alpha, beta])

    committed = parse_index("# Concept\n\n* [Alpha](alpha.md)\n")

    findings = diff_index(generated, committed)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "warning"
    assert finding.field == "beta.md"
    assert "beta.md" in finding.message
    assert "missing" in finding.message


def test_diff_index_reports_removed_file_stale_in_committed(tmp_path: Path) -> None:
    """The committed index.md still lists a concept that no longer exists."""
    alpha = _entry(tmp_path / "alpha.md", tmp_path, concept_id="alpha", title="Alpha")
    generated = generate_index(tmp_path, [alpha])

    committed = parse_index("# Concept\n\n* [Alpha](alpha.md)\n* [Beta](beta.md)\n")

    findings = diff_index(generated, committed)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "warning"
    assert finding.field == "beta.md"
    assert "beta.md" in finding.message
    assert "does not correspond to any current concept" in finding.message


def test_diff_index_reports_changed_description(tmp_path: Path) -> None:
    alpha = _entry(
        tmp_path / "alpha.md",
        tmp_path,
        concept_id="alpha",
        title="Alpha",
        description="New description",
    )
    generated = generate_index(tmp_path, [alpha])

    committed = parse_index("# Concept\n\n* [Alpha](alpha.md) - Old description\n")

    findings = diff_index(generated, committed)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "warning"
    assert finding.field == "alpha.md"
    assert "description changed from 'Old description' to 'New description'" in (
        finding.message
    )


def test_diff_index_reports_changed_title(tmp_path: Path) -> None:
    alpha = _entry(
        tmp_path / "alpha.md", tmp_path, concept_id="alpha", title="New Title"
    )
    generated = generate_index(tmp_path, [alpha])

    committed = parse_index("# Concept\n\n* [Old Title](alpha.md)\n")

    findings = diff_index(generated, committed)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "warning"
    assert finding.field == "alpha.md"
    assert "title changed from 'Old Title' to 'New Title'" in finding.message


def test_diff_index_reports_changed_section(tmp_path: Path) -> None:
    """A concept's `type` changed since the committed index.md was written,
    moving its entry to a different generated heading. Title is held equal
    on both sides so this isolates the section-change signal specifically."""
    alpha = _entry(
        tmp_path / "alpha.md",
        tmp_path,
        concept_id="alpha",
        type_="decision",
        title="Alpha",
    )
    generated = generate_index(tmp_path, [alpha])

    committed = parse_index("# Concept\n\n* [Alpha](alpha.md)\n")

    findings = diff_index(generated, committed)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "warning"
    assert finding.field == "alpha.md"
    assert finding.message == (
        "index.md entry 'alpha.md' is stale: "
        "section changed from 'Concept' to 'Decision'"
    )


def test_diff_index_reports_multiple_changed_fields_in_one_finding(
    tmp_path: Path,
) -> None:
    """Title and description both drifting is one finding naming both changes,
    not two separate findings for the same link."""
    alpha = _entry(
        tmp_path / "alpha.md",
        tmp_path,
        concept_id="alpha",
        title="New Title",
        description="New description",
    )
    generated = generate_index(tmp_path, [alpha])

    committed = parse_index("# Concept\n\n* [Old Title](alpha.md) - Old description\n")

    findings = diff_index(generated, committed)
    assert len(findings) == 1
    assert "title changed" in findings[0].message
    assert "description changed" in findings[0].message


def test_diff_index_reports_malformed_committed_entry(tmp_path: Path) -> None:
    """A malformed committed list item (parse_index's own IndexParseProblem)
    surfaces as an "unknown extra content"-class finding -- previously this
    channel had no consumer anywhere in the codebase."""
    generated = generate_index(tmp_path, [])
    committed = parse_index("# Concept\n\n* no link here, just text\n")

    findings = diff_index(generated, committed)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "warning"
    assert finding.field == "Concept"
    assert "unrecognized content" in finding.message
    assert "missing link target" in finding.message


# ---------------------------------------------------------------------------
# Negative coverage
# ---------------------------------------------------------------------------


def test_diff_index_unchanged_description_of_none_is_clean(tmp_path: Path) -> None:
    """An entry with no description on either side is not reported as a
    changed description (None == None, not a drift)."""
    alpha = _entry(tmp_path / "alpha.md", tmp_path, concept_id="alpha", title="Alpha")
    generated = generate_index(tmp_path, [alpha])
    committed = parse_index("# Concept\n\n* [Alpha](alpha.md)\n")

    assert diff_index(generated, committed) == ()


def test_diff_index_all_findings_are_advisory_severity(tmp_path: Path) -> None:
    """Every drift finding kind reports severity="warning" -- drift is
    advisory and must never affect okf validate's exit code (#200)."""
    alpha = _entry(tmp_path / "alpha.md", tmp_path, concept_id="alpha", title="Alpha")
    generated = generate_index(tmp_path, [alpha])
    committed = parse_index(
        "# Concept\n\n"
        "* [Alpha](alpha.md) - stale description\n"
        "* [Gone](gone.md)\n"
        "* no link here\n"
    )

    findings = diff_index(generated, committed)
    assert len(findings) == 3
    assert {f.severity for f in findings} == {"warning"}


def test_diff_index_incidental_whitespace_is_not_drift(tmp_path: Path) -> None:
    """Comparison is canonical-form (semantic), not byte-for-byte: a committed
    entry with extra incidental whitespace around the link/description --
    which markdown-it's tokenizer discards during parsing -- must not report
    as drift just because the raw text differs byte-for-byte from the
    generated form."""
    alpha = _entry(
        tmp_path / "alpha.md",
        tmp_path,
        concept_id="alpha",
        title="Alpha",
        description="A description",
    )
    generated = generate_index(tmp_path, [alpha])
    committed = parse_index(
        "# Concept\n\n*   [Alpha](alpha.md)   -   A description   \n"
    )

    assert diff_index(generated, committed) == ()

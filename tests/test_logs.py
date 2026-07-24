"""Tests for log.md file parsing, rendering, and loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from okf_core.logs import (
    LogDateSection,
    LogEntry,
    ParsedLog,
    load_log,
    parse_log,
    render_log,
)

# ---------------------------------------------------------------------------
# parse_log: conformant content
# ---------------------------------------------------------------------------

_SPEC_SAMPLE = """# Directory Update Log

## 2026-05-22
* **Update**: Added a BigQuery table reference for [Customer Metrics](/tables/customer-metrics.md).
* **Creation**: Established the [Dataplex Playbook](/playbooks/dataplex.md).

## 2026-05-15
* **Initialization**: Created foundational directory structure.
"""


def test_parse_conformant_log_title() -> None:
    parsed = parse_log(_SPEC_SAMPLE)
    assert parsed.title == "Directory Update Log"
    assert parsed.problems == ()


def test_parse_conformant_log_sections_in_document_order() -> None:
    parsed = parse_log(_SPEC_SAMPLE)
    assert [s.date for s in parsed.sections] == ["2026-05-22", "2026-05-15"]


def test_parse_conformant_log_entries() -> None:
    parsed = parse_log(_SPEC_SAMPLE)
    first, second = parsed.sections
    assert first.entries == (
        LogEntry(
            text="Added a BigQuery table reference for "
            "[Customer Metrics](/tables/customer-metrics.md).",
            label="Update",
        ),
        LogEntry(
            text="Established the [Dataplex Playbook](/playbooks/dataplex.md).",
            label="Creation",
        ),
    )
    assert second.entries == (
        LogEntry(
            text="Created foundational directory structure.", label="Initialization"
        ),
    )


def test_parse_missing_title_returns_none() -> None:
    content = "## 2026-05-15\n* An entry.\n"
    parsed = parse_log(content)
    assert parsed.title is None
    assert parsed.sections[0].date == "2026-05-15"


# ---------------------------------------------------------------------------
# parse_log: empty / whitespace content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content", ["", "   ", "\n\n\n"])
def test_parse_empty_or_whitespace_content_returns_empty_log(content: str) -> None:
    parsed = parse_log(content)
    assert parsed == ParsedLog(title=None, sections=(), problems=())


# ---------------------------------------------------------------------------
# parse_log: malformed date headings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    ["not-a-date", "2026/05/15", "05-15-2026", "2026-5-15"],
)
def test_parse_non_iso_date_heading_reported_and_section_skipped(heading: str) -> None:
    content = f"## {heading}\n* Should be skipped.\n\n## 2026-05-15\n* Kept entry.\n"
    parsed = parse_log(content)

    assert len(parsed.sections) == 1
    assert parsed.sections[0].date == "2026-05-15"
    assert parsed.sections[0].entries == (LogEntry(text="Kept entry."),)
    assert len(parsed.problems) == 1
    assert "not ISO 8601 YYYY-MM-DD form" in parsed.problems[0].message


def test_parse_invalid_calendar_date_heading_reported() -> None:
    content = "## 2026-02-30\n* Should be skipped.\n"
    parsed = parse_log(content)

    assert parsed.sections == ()
    assert len(parsed.problems) == 1
    assert "not a valid calendar date" in parsed.problems[0].message


def test_parse_malformed_heading_does_not_abort_later_sections() -> None:
    content = (
        "## garbage\n* Dropped.\n\n"
        "## 2026-05-15\n* First kept.\n\n"
        "## also-garbage\n* Also dropped.\n\n"
        "## 2026-05-01\n* Second kept.\n"
    )
    parsed = parse_log(content)

    assert [s.date for s in parsed.sections] == ["2026-05-15", "2026-05-01"]
    assert len(parsed.problems) == 2


# ---------------------------------------------------------------------------
# parse_log: labeled vs. unlabeled entries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bullet", "expected"),
    [
        (
            "* **Update**: Something changed.",
            LogEntry(text="Something changed.", label="Update"),
        ),
        ("* Plain prose, no label.", LogEntry(text="Plain prose, no label.")),
        (
            "* **Deprecation**: No longer supported.",
            LogEntry(text="No longer supported.", label="Deprecation"),
        ),
    ],
)
def test_parse_labeled_and_unlabeled_entries(bullet: str, expected: LogEntry) -> None:
    content = f"## 2026-05-15\n{bullet}\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (expected,)


def test_parse_bold_word_without_colon_is_not_a_label() -> None:
    # "**Bold**" with no trailing colon does not match the label convention
    content = "## 2026-05-15\n* **Bold** but no colon follows.\n"
    parsed = parse_log(content)
    entry = parsed.sections[0].entries[0]
    assert entry.label is None
    assert entry.text == "**Bold** but no colon follows."


# ---------------------------------------------------------------------------
# parse_log: entries before any date heading are ignored
# ---------------------------------------------------------------------------


def test_parse_entries_before_first_date_heading_are_ignored() -> None:
    content = "# Log\n* Ignored, no date heading yet.\n\n## 2026-05-15\n* Kept.\n"
    parsed = parse_log(content)
    assert len(parsed.sections) == 1
    assert parsed.sections[0].entries == (LogEntry(text="Kept."),)


def test_parse_entries_with_no_heading_at_all_produce_no_sections() -> None:
    content = "* An entry with no heading anywhere.\n"
    parsed = parse_log(content)
    assert parsed.sections == ()
    assert parsed.problems == ()


# ---------------------------------------------------------------------------
# parse_log: embedded markdown links preserved verbatim
# ---------------------------------------------------------------------------


def test_parse_entry_with_embedded_link_preserved_verbatim() -> None:
    content = (
        "## 2026-05-15\n"
        "* **Update**: See [Customer Metrics](/tables/customer-metrics.md) for details.\n"
    )
    parsed = parse_log(content)
    entry = parsed.sections[0].entries[0]
    assert entry.label == "Update"
    assert (
        entry.text == "See [Customer Metrics](/tables/customer-metrics.md) for details."
    )


def test_parse_entry_with_multiple_links_preserved() -> None:
    content = "## 2026-05-15\n* See [A](a.md) and [B](b.md).\n"
    parsed = parse_log(content)
    entry = parsed.sections[0].entries[0]
    assert entry.text == "See [A](a.md) and [B](b.md)."


# ---------------------------------------------------------------------------
# parse_log: malformed entries
# ---------------------------------------------------------------------------


def test_parse_empty_entry_reported_and_skipped() -> None:
    content = "## 2026-05-15\n* \n* A real entry.\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (LogEntry(text="A real entry."),)
    assert len(parsed.problems) == 1
    assert "empty entry text" in parsed.problems[0].message
    assert parsed.problems[0].date == "2026-05-15"


@pytest.mark.parametrize(
    "bullet",
    ["* **Update**:\n", "* **Update**:   \n"],
    ids=["no-trailing-space", "trailing-whitespace-only"],
)
def test_parse_empty_entry_with_label_reported_and_skipped(bullet: str) -> None:
    # A label with no prose after the colon must still be treated as empty --
    # the presence of `label` must not suppress the empty-entry check.
    content = f"## 2026-05-15\n{bullet}* A real entry.\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (LogEntry(text="A real entry."),)
    assert len(parsed.problems) == 1
    assert "empty entry text" in parsed.problems[0].message
    assert parsed.problems[0].date == "2026-05-15"


def test_parse_entry_with_only_unrenderable_content_reported_and_skipped() -> None:
    # An image has no Markdown-source rendering in log entry prose, so this
    # entry renders to empty text even though the source line isn't blank.
    content = "## 2026-05-15\n* ![alt](img.png)\n* A real entry.\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (LogEntry(text="A real entry."),)
    assert len(parsed.problems) == 1
    assert "empty entry text" in parsed.problems[0].message


def test_parse_loose_bullet_continuation_paragraph_folded_into_text() -> None:
    # A loose bullet item (blank line between paragraphs) must not drop its
    # second paragraph -- it's continuation prose for the same entry.
    content = (
        "## 2026-05-15\n"
        "* First paragraph of entry.\n"
        "\n"
        "  Second paragraph, same item.\n"
    )
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (
        LogEntry(text="First paragraph of entry. Second paragraph, same item."),
    )
    assert parsed.problems == ()


def test_parse_loose_bullet_continuation_preserves_label() -> None:
    content = (
        "## 2026-05-15\n"
        "* **Update**: First paragraph.\n"
        "\n"
        "  Second paragraph.\n"
    )
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (
        LogEntry(text="First paragraph. Second paragraph.", label="Update"),
    )


def test_parse_loose_bullet_three_paragraphs_all_folded() -> None:
    content = "## 2026-05-15\n" "* One.\n" "\n" "  Two.\n" "\n" "  Three.\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (LogEntry(text="One. Two. Three."),)


def test_parse_stray_paragraph_under_date_heading_reported_and_skipped() -> None:
    content = "## 2026-05-15\nJust a paragraph, no bullet at all.\n"
    parsed = parse_log(content)
    assert parsed.sections == (LogDateSection(date="2026-05-15", entries=()),)
    assert len(parsed.problems) == 1
    assert "stray paragraph" in parsed.problems[0].message
    assert parsed.problems[0].date == "2026-05-15"


def test_parse_stray_paragraph_does_not_suppress_later_valid_entries() -> None:
    content = "## 2026-05-15\nStray paragraph.\n\n* A real entry.\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (LogEntry(text="A real entry."),)
    assert len(parsed.problems) == 1


def test_parse_nested_sub_bullet_reported_and_skipped() -> None:
    # The LogEntry model has no representation for nested sub-bullets --
    # the parent entry's own text is kept, and the nested content is
    # reported rather than silently merged or dropped.
    content = "## 2026-05-15\n* Top entry.\n  * Nested sub note.\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (LogEntry(text="Top entry."),)
    assert len(parsed.problems) == 1
    assert "nested sub-list" in parsed.problems[0].message
    assert parsed.problems[0].date == "2026-05-15"


def test_parse_nested_sub_bullet_does_not_affect_sibling_entries() -> None:
    content = (
        "## 2026-05-15\n"
        "* Top entry.\n"
        "  * Nested sub note.\n"
        "* Another top-level entry.\n"
    )
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (
        LogEntry(text="Top entry."),
        LogEntry(text="Another top-level entry."),
    )
    assert len(parsed.problems) == 1


# ---------------------------------------------------------------------------
# render_log
# ---------------------------------------------------------------------------


def test_render_log_includes_title_and_headings() -> None:
    parsed = ParsedLog(
        title="Directory Update Log",
        sections=(
            LogDateSection(
                date="2026-05-22",
                entries=(LogEntry(text="Added a thing.", label="Update"),),
            ),
        ),
    )
    body = render_log(parsed)
    assert "# Directory Update Log" in body
    assert "## 2026-05-22" in body
    assert "* **Update**: Added a thing." in body


def test_render_log_without_title_omits_h1() -> None:
    parsed = ParsedLog(
        title=None,
        sections=(LogDateSection(date="2026-05-15", entries=(LogEntry(text="X."),)),),
    )
    body = render_log(parsed)
    assert not body.splitlines()[0].startswith("# ")
    assert "* X." in body


def test_render_log_unlabeled_entry_has_no_bold_prefix() -> None:
    parsed = ParsedLog(
        title=None,
        sections=(
            LogDateSection(date="2026-05-15", entries=(LogEntry(text="Plain."),)),
        ),
    )
    body = render_log(parsed)
    assert "* Plain." in body
    assert "**" not in body


def test_render_log_empty_produces_empty_string() -> None:
    assert render_log(ParsedLog(title=None, sections=())) == ""


# ---------------------------------------------------------------------------
# parse -> render -> parse round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        _SPEC_SAMPLE,
        "## 2026-05-15\n* No title, one plain entry.\n",
        "# Only Title\n",
        "# T\n\n## 2026-01-01\n* **Update**: See [X](x.md) and [Y](y.md).\n",
    ],
)
def test_parse_render_parse_round_trip_is_structurally_equivalent(content: str) -> None:
    first = parse_log(content)
    rendered = render_log(first)
    second = parse_log(rendered)

    assert second.title == first.title
    assert second.sections == first.sections
    # Round-tripping never invents new problems from clean input.
    assert second.problems == ()


# ---------------------------------------------------------------------------
# load_log
# ---------------------------------------------------------------------------


def test_load_log_missing_file_returns_empty_parsed_log(tmp_path: Path) -> None:
    result = load_log(tmp_path / "does-not-exist" / "log.md")
    assert result == ParsedLog(title=None, sections=(), problems=())


def test_load_log_reads_and_parses_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "log.md"
    path.write_text(_SPEC_SAMPLE, encoding="utf-8")
    result = load_log(path)
    assert result.title == "Directory Update Log"
    assert len(result.sections) == 2


def test_load_log_empty_file_returns_empty_parsed_log(tmp_path: Path) -> None:
    path = tmp_path / "log.md"
    path.write_text("", encoding="utf-8")
    result = load_log(path)
    assert result == ParsedLog(title=None, sections=(), problems=())


def test_load_log_directory_path_is_treated_as_missing(tmp_path: Path) -> None:
    # a directory named log.md (unusual, but not a file) is not readable as one
    directory = tmp_path / "log.md"
    directory.mkdir()
    result = load_log(directory)
    assert result == ParsedLog(title=None, sections=(), problems=())


# ---------------------------------------------------------------------------
# LogParseProblem structure
# ---------------------------------------------------------------------------


def test_malformed_heading_problem_has_line_number() -> None:
    content = "## bad-heading\n* skipped\n"
    parsed = parse_log(content)
    assert parsed.problems[0].line == 1


def test_malformed_heading_problem_carries_raw_heading_as_date() -> None:
    content = "## bad-heading\n* skipped\n"
    parsed = parse_log(content)
    assert parsed.problems[0].date == "bad-heading"

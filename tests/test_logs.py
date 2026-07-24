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
    assert "skipped stray block" in parsed.problems[0].message
    assert "bare paragraph" in parsed.problems[0].message
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
    assert "skipped unexpected nested block" in parsed.problems[0].message
    assert "nested bullet list" in parsed.problems[0].message
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
# parse_log: any other unexpected block nested *inside* a list item (ordered
# sub-list, fence, indented code, hr, blockquote, sub-heading, html_block) --
# generalizes the nested-bullet-list case above (and the fenced-code-as-a-
# loose-item's-second-block gap found during #145 acceptance review) to any
# block-level construct a list item can directly contain beyond its own (and,
# for a loose item, continuation) paragraphs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "nested_block", "descriptor"),
    [
        ("ordered_list", "  1. nested item.\n", "ordered_list_open"),
        ("fence", "\n  ```\n  code\n  ```\n", "fenced or indented code block"),
        ("indented_code", "\n      code here\n", "fenced or indented code block"),
        ("hr", "\n  ---\n", "thematic break"),
        ("blockquote", "\n  > quoted\n", "blockquote"),
        ("h3_heading", "\n  ### nested heading\n", "sub-heading"),
        ("html_block", "\n  <div>hi</div>\n", "raw HTML block"),
    ],
)
def test_parse_unexpected_nested_block_in_entry_reported_and_skipped(
    label: str, nested_block: str, descriptor: str
) -> None:
    content = f"## 2026-05-15\n* Top entry.\n{nested_block}* Another entry.\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (
        LogEntry(text="Top entry."),
        LogEntry(text="Another entry."),
    ), label
    assert len(parsed.problems) == 1, label
    assert "skipped unexpected nested block" in parsed.problems[0].message, label
    assert descriptor in parsed.problems[0].message, label
    assert parsed.problems[0].date == "2026-05-15", label


@pytest.mark.parametrize(
    ("label", "nested_block"),
    [
        ("ordered_list", "  1. nested item.\n"),
        ("fence", "\n  ```\n  code\n  ```\n"),
        ("indented_code", "\n      code here\n"),
        ("hr", "\n  ---\n"),
        ("blockquote", "\n  > quoted\n"),
        ("h3_heading", "\n  ### nested heading\n"),
        ("html_block", "\n  <div>hi</div>\n"),
    ],
)
def test_parse_unexpected_nested_block_in_entry_does_not_affect_sibling_entries(
    label: str, nested_block: str
) -> None:
    content = (
        f"## 2026-05-15\n* A preceding entry.\n{nested_block}* A following entry.\n"
    )
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (
        LogEntry(text="A preceding entry."),
        LogEntry(text="A following entry."),
    ), label
    assert len(parsed.problems) == 1, label


def test_parse_unexpected_nested_fence_as_loose_items_second_block() -> None:
    # The specific #145 acceptance-review repro: a loose item's second
    # block (blank line then an indented fence) is not continuation prose
    # for the entry -- it must be reported, not silently merged or dropped.
    content = "## 2026-05-15\n* Entry text.\n\n  ```\n  code\n  ```\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (LogEntry(text="Entry text."),)
    assert len(parsed.problems) == 1
    assert "skipped unexpected nested block" in parsed.problems[0].message
    assert "fenced or indented code block" in parsed.problems[0].message


def test_parse_list_item_with_only_unexpected_content_yields_no_entry() -> None:
    # A bullet whose sole content is itself unexpected (no leading paragraph
    # at all) never sets `_consume_list_item`'s `captured` flag, so it falls
    # through to the empty-entry tail check *in addition to* the per-block
    # report -- two problems, zero entries, from a single malformed item.
    content = "## 2026-05-15\n* \n  ### only a heading\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == ()
    assert len(parsed.problems) == 2
    assert any(
        "skipped unexpected nested block" in p.message and "sub-heading" in p.message
        for p in parsed.problems
    )
    assert any(
        "skipped malformed log entry: empty entry text" in p.message
        for p in parsed.problems
    )


def test_parse_multiple_unexpected_blocks_in_same_item_reported_separately() -> None:
    # Two distinct unexpected blocks in one item (an hr, then a blockquote)
    # are walked and reported independently by `_consume_list_item`'s loop,
    # not collapsed into a single problem.
    content = (
        "## 2026-05-15\n"
        "* A preceding entry.\n"
        "\n"
        "  ---\n"
        "\n"
        "  > quoted stuff\n"
        "* A following entry.\n"
    )
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (
        LogEntry(text="A preceding entry."),
        LogEntry(text="A following entry."),
    )
    assert len(parsed.problems) == 2
    assert "thematic break" in parsed.problems[0].message
    assert "blockquote" in parsed.problems[1].message


def test_parse_unexpected_content_nested_two_levels_deep_reported_once() -> None:
    # An unexpected nested bullet list that itself contains further
    # unexpected content (a heading) is reported once, for the outer nested
    # list's whole span -- `_skip_matching_block`'s level/nesting walk
    # resolves that span (and everything inside it) before
    # `_skip_unexpected_item_block` ever runs, so the inner heading is never
    # independently walked or reported.
    content = (
        "## 2026-05-15\n"
        "* Top entry.\n"
        "  * nested item\n"
        "    ### nested heading\n"
        "* Another entry.\n"
    )
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (
        LogEntry(text="Top entry."),
        LogEntry(text="Another entry."),
    )
    assert len(parsed.problems) == 1
    assert "skipped unexpected nested block" in parsed.problems[0].message
    assert "nested bullet list" in parsed.problems[0].message


# ---------------------------------------------------------------------------
# parse_log: any other stray block under a date heading (fence, hr,
# html_block, sub-heading, blockquote) -- generalizes the stray-paragraph
# case above to any block-level construct that isn't a date/title heading
# or the entry bullet list.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "stray_block", "descriptor"),
    [
        ("fence", "```\ncode\n```\n", "fenced or indented code block"),
        ("hr", "---\n", "thematic break"),
        ("html_block", "<div>raw</div>\n\n", "raw HTML block"),
        ("h3_heading", "### sub heading\n", "sub-heading"),
        ("blockquote", "> quoted stuff\n", "blockquote"),
        ("ordered_list", "1. foo\n", "ordered_list_open"),
    ],
)
def test_parse_stray_block_under_date_heading_reported_and_skipped(
    label: str, stray_block: str, descriptor: str
) -> None:
    content = f"## 2026-05-15\n{stray_block}* A real entry.\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (LogEntry(text="A real entry."),), label
    assert len(parsed.problems) == 1, label
    assert "skipped stray block" in parsed.problems[0].message, label
    assert descriptor in parsed.problems[0].message, label
    assert parsed.problems[0].date == "2026-05-15", label


@pytest.mark.parametrize(
    ("label", "stray_block"),
    [
        ("fence", "```\ncode\n```\n"),
        ("hr", "---\n"),
        ("html_block", "<div>raw</div>\n\n"),
        ("h3_heading", "### sub heading\n"),
        ("blockquote", "> quoted stuff\n"),
        ("ordered_list", "1. foo\n"),
    ],
)
def test_parse_stray_block_does_not_affect_preceding_valid_entries(
    label: str, stray_block: str
) -> None:
    content = f"## 2026-05-15\n* A preceding entry.\n{stray_block}"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == (LogEntry(text="A preceding entry."),), label
    assert len(parsed.problems) == 1, label


# ---------------------------------------------------------------------------
# parse_log: an out-of-place h1 (ATX or setext) directly under a date
# heading -- must be classified as a stray block like any other errant
# heading, never silently dropped and never mistaken for the document
# title, regardless of whether a real title was already captured.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "stray_h1"),
    [
        ("atx", "# Stray Heading\n"),
        ("setext", "Stray Heading\n=============\n"),
    ],
)
def test_parse_stray_h1_under_date_heading_with_title_already_set(
    label: str, stray_h1: str
) -> None:
    content = f"# Real Title\n\n## 2026-05-15\n{stray_h1}* A real entry.\n"
    parsed = parse_log(content)
    assert parsed.title == "Real Title", label
    assert parsed.sections[0].entries == (LogEntry(text="A real entry."),), label
    assert len(parsed.problems) == 1, label
    assert "skipped stray block" in parsed.problems[0].message, label
    assert parsed.problems[0].date == "2026-05-15", label


@pytest.mark.parametrize(
    ("label", "stray_h1"),
    [
        ("atx", "# Stray Heading\n"),
        ("setext", "Stray Heading\n=============\n"),
    ],
)
def test_parse_stray_h1_under_date_heading_with_no_title_yet(
    label: str, stray_h1: str
) -> None:
    # With no earlier `# Title`, a naive unconditional title-capture branch
    # would misattribute this date-section h1's text as the document
    # title -- active data corruption, not just a missed diagnostic. It
    # must instead be reported as a stray block and leave .title as None.
    content = f"## 2026-05-15\n{stray_h1}* A real entry.\n"
    parsed = parse_log(content)
    assert parsed.title is None, label
    assert parsed.sections[0].entries == (LogEntry(text="A real entry."),), label
    assert len(parsed.problems) == 1, label
    assert "skipped stray block" in parsed.problems[0].message, label
    assert parsed.problems[0].date == "2026-05-15", label


def test_parse_h1_before_date_heading_still_captured_as_title() -> None:
    # Regression check: the happy path -- a legitimate `# Title` appearing
    # before any date heading -- must be unaffected by gating the h1
    # branch on `current_date is None`.
    content = "# Directory Update Log\n\n## 2026-05-15\n* An entry.\n"
    parsed = parse_log(content)
    assert parsed.title == "Directory Update Log"
    assert parsed.problems == ()


# ---------------------------------------------------------------------------
# parse_log: round 5 regression -- a stray h1 landing after a *malformed*
# date heading, not just after a valid one. `current_date` resets to None
# both in the preamble and after a malformed heading, so a naive
# `current_date is None` gate can't tell the two positions apart; the fix
# tracks them as distinct phases so an h1 in either non-preamble position is
# reported as a stray block, never silently dropped or captured as title.
# ---------------------------------------------------------------------------


def test_parse_stray_h1_after_malformed_date_heading_with_title_already_set() -> None:
    content = (
        "# Real Title\n\n"
        "## 2026-05-15\n"
        "* A kept entry.\n\n"
        "## also-garbage\n"
        "# Stray Heading\n"
        "* An entry that belongs to no valid section.\n"
    )
    parsed = parse_log(content)
    assert parsed.title == "Real Title"
    assert parsed.sections == (
        LogDateSection(date="2026-05-15", entries=(LogEntry(text="A kept entry."),)),
    )
    assert len(parsed.problems) == 2
    stray_problems = [p for p in parsed.problems if "skipped stray block" in p.message]
    assert len(stray_problems) == 1
    assert stray_problems[0].date is None


def test_parse_stray_h1_after_malformed_date_heading_with_no_title_yet() -> None:
    # No earlier `# Title` at all: a naive `current_date is None` gate would
    # misattribute this h1's text as the document title, treating it as if
    # it were legitimate preamble content instead of a stray block under an
    # already-abandoned (malformed) date section.
    content = (
        "## 2026-05-15\n" "* A kept entry.\n\n" "## also-garbage\n" "# Stray Heading\n"
    )
    parsed = parse_log(content)
    assert parsed.title is None
    assert parsed.sections == (
        LogDateSection(date="2026-05-15", entries=(LogEntry(text="A kept entry."),)),
    )
    assert len(parsed.problems) == 2
    stray_problems = [p for p in parsed.problems if "skipped stray block" in p.message]
    assert len(stray_problems) == 1
    assert stray_problems[0].date is None


def test_parse_valid_section_recovers_after_stray_h1_in_skipped_section() -> None:
    # The full round-5 sequence: valid section -> entry -> malformed
    # section -> stray h1 -> a further valid section. The state machine
    # must still recognize the later `## 2026-05-01` as a fresh, valid
    # section boundary rather than getting stuck by the intervening stray
    # h1.
    content = (
        "## 2026-05-15\n* First kept.\n\n"
        "## also-garbage\n"
        "# Stray Heading\n"
        "## 2026-05-01\n* Second kept.\n"
    )
    parsed = parse_log(content)
    assert [s.date for s in parsed.sections] == ["2026-05-15", "2026-05-01"]
    assert parsed.title is None


def test_parse_html_block_without_blank_line_absorbs_following_bullet() -> None:
    # CommonMark's HTML-block rule keeps consuming lines until a blank line,
    # so an HTML block with no blank line before the next bullet swallows it
    # into the same html_block token before this parser ever sees a
    # separate list. The swallowed entry can't be recovered -- what this
    # fix guarantees is that the loss is now reported as a LogParseProblem
    # instead of vanishing with zero diagnostics.
    content = "## 2026-05-15\n<div>raw</div>\n* An entry that gets absorbed.\n"
    parsed = parse_log(content)
    assert parsed.sections[0].entries == ()
    assert len(parsed.problems) == 1
    assert "skipped stray block" in parsed.problems[0].message
    assert "raw HTML block" in parsed.problems[0].message


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

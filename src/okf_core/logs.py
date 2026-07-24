"""Log file (``log.md``) parsing, rendering, and loading for OKF bundles."""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from okf_core._markdown_inline import render_linked_span, token_line

_MARKDOWN = MarkdownIt("commonmark")

_DATE_FORM = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class LogEntry:
    """A single log entry.

    ``label`` is the leading bold convention word (``Update``, ``Creation``,
    ``Deprecation``, ...) when the entry follows the ``**Word**: ...``
    convention described in the OKF spec's Log Files section; it is ``None``
    for plain-prose entries. ``text`` is the entry's prose with the label
    prefix (if any) removed, rendered back to Markdown source -- embedded
    links and other inline markup are preserved verbatim, not resolved.
    """

    text: str
    label: str | None = None


@dataclass(frozen=True)
class LogDateSection:
    """A single ``## YYYY-MM-DD`` date heading and its entries."""

    date: str
    entries: tuple[LogEntry, ...]


@dataclass(frozen=True)
class LogParseProblem:
    """A non-fatal problem encountered while parsing a log.md file."""

    date: str | None
    line: int | None
    message: str


@dataclass(frozen=True)
class ParsedLog:
    """Structured representation of a parsed log.md file."""

    title: str | None
    sections: tuple[LogDateSection, ...]
    problems: tuple[LogParseProblem, ...] = ()


def parse_log(content: str) -> ParsedLog:
    """Parse a log.md body into a title, date sections, and entries.

    ``# Title`` is captured as ``.title`` when present, else ``None``. Each
    ``## YYYY-MM-DD`` heading starts a date section; headings that are not
    valid ISO 8601 ``YYYY-MM-DD`` calendar dates are reported as
    ``LogParseProblem`` objects and that section's entries are skipped rather
    than raising. Entries that appear before the first date heading, or under
    a malformed date heading, are silently ignored -- they belong to no valid
    section. Each bullet-list item becomes a ``LogEntry``; a leading
    ``**Word**: `` bold prefix is captured as ``.label`` and stripped from
    ``.text``, otherwise ``.label`` is ``None`` and ``.text`` is the full
    rendered prose. A "loose" item's second and later paragraphs are folded
    into that same entry's ``.text`` (space-joined) rather than dropped. Any
    block-level construct other than the expected ``## YYYY-MM-DD`` date
    heading and the entry bullet list -- a bare paragraph, fenced or
    indented code block, thematic break, any heading (including an
    out-of-place ``# Title``-shaped ``h1``, ATX or setext), raw HTML block,
    blockquote, or a nested sub-list within an entry -- placed directly
    under a date heading is reported as a ``LogParseProblem`` and skipped,
    since none of them has a representation in the flat ``LogEntry`` model.
    Note that ``# Title`` is only ever captured as ``.title`` when it
    appears before the first date heading; once a date section has opened,
    a later ``h1`` is uniformly classified as a stray block like any other
    out-of-place heading, not silently merged into or mistaken for the
    document title.
    Note that an HTML block with no blank line before a following bullet can
    still be absorbed into that block by CommonMark's own HTML-block rule
    before this parser ever sees it as a separate list; that case is still
    reported as a problem, but the swallowed entry cannot be recovered.
    """
    tokens = _MARKDOWN.parse(content)
    title: str | None = None
    sections: list[LogDateSection] = []
    problems: list[LogParseProblem] = []
    current_date: str | None = None
    current_entries: list[LogEntry] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open" and token.tag == "h1" and current_date is None:
            i += 1
            if i < len(tokens) and tokens[i].type == "inline" and title is None:
                title = tokens[i].content
        elif token.type == "heading_open" and token.tag == "h2":
            if current_date is not None:
                sections.append(
                    LogDateSection(date=current_date, entries=tuple(current_entries))
                )
            line = _token_line(token)
            i += 1
            heading_text = (
                tokens[i].content
                if i < len(tokens) and tokens[i].type == "inline"
                else None
            )
            current_date, problem = _date_section_from_heading(heading_text, line)
            if problem is not None:
                problems.append(problem)
            current_entries = []
        elif token.type == "bullet_list_open":
            i = _consume_bullet_list(tokens, i, current_date, current_entries, problems)
            continue
        elif current_date is not None and _is_stray_top_level_block(token):
            i = _skip_stray_block(tokens, i, current_date, problems)
            continue
        i += 1

    if current_date is not None:
        sections.append(
            LogDateSection(date=current_date, entries=tuple(current_entries))
        )

    return ParsedLog(title=title, sections=tuple(sections), problems=tuple(problems))


def render_log(parsed: ParsedLog) -> str:
    """Render a ``ParsedLog`` back to spec-shape log.md Markdown.

    This is a pure structural serialization: a title heading (if present),
    then a ``## YYYY-MM-DD`` heading and bullet entries for each date section,
    in the order given. It performs no merge, insert, or deduplication
    against an existing log -- callers that need to append or merge entries
    compose this with their own logic.
    """
    lines: list[str] = []
    if parsed.title is not None:
        lines.append(f"# {parsed.title}")
        lines.append("")
    for section in parsed.sections:
        lines.append(f"## {section.date}")
        for entry in section.entries:
            lines.append(_render_log_entry(entry))
        lines.append("")
    return "\n".join(lines)


def load_log(path: Path) -> ParsedLog:
    """Read and parse ``path`` as a log.md file, tolerating a missing file.

    Mirrors ``scan_bundle``'s missing-root tolerance: if ``path`` does not
    exist, an empty ``ParsedLog`` is returned instead of raising.
    """
    if not path.is_file():
        return ParsedLog(title=None, sections=(), problems=())
    content = path.read_text(encoding="utf-8")
    return parse_log(content)


def _consume_bullet_list(
    tokens: Sequence[Any],
    start: int,
    current_date: str | None,
    current_entries: list[LogEntry],
    problems: list[LogParseProblem],
) -> int:
    """Walk one ``bullet_list_open`` ... ``bullet_list_close`` run, in place.

    Appends entries to ``current_entries`` and problems to ``problems`` as a
    side effect (both caller-owned lists), and returns the token index just
    past the matching ``bullet_list_close``. A list item that closes without
    ever producing a captured inline token (a blank bullet, or one whose
    only content has no Markdown-source rendering) is itself reported as an
    empty-entry problem here, since ``_entry_from_list_item`` is never
    reached for it. A "loose" item's second and later paragraphs are folded
    into the same entry's ``.text`` via ``_merge_continuation_paragraph``
    rather than dropped, since they are continuation prose for one entry,
    not a new one. A nested sub-list within an item is reported and skipped
    via ``_skip_nested_sub_list``, since the ``LogEntry`` model has no
    representation for nested bullets.
    """
    list_level = tokens[start].level
    item_captured = False
    item_entry_index: int | None = None
    item_open_line: int | None = None
    i = start + 1
    while i < len(tokens):
        t = tokens[i]
        if t.level == list_level and t.nesting == -1:
            i += 1
            break
        if t.level == list_level + 1 and t.type == "list_item_open":
            item_captured = False
            item_entry_index = None
            item_open_line = _token_line(t)
        elif t.level == list_level + 1 and t.type == "list_item_close":
            if not item_captured and current_date is not None:
                problems.append(
                    LogParseProblem(
                        date=current_date,
                        line=item_open_line,
                        message="skipped malformed log entry: empty entry text",
                    )
                )
        elif t.type == "bullet_list_open" and t.level > list_level:
            i = _skip_nested_sub_list(tokens, i, current_date, problems)
            continue
        elif t.type == "inline" and list_level < t.level <= list_level + 3:
            if current_date is not None:
                if not item_captured:
                    entry, problem = _entry_from_list_item(
                        t, current_date, _token_line(t)
                    )
                    if entry is not None:
                        current_entries.append(entry)
                        item_entry_index = len(current_entries) - 1
                    elif problem is not None:
                        problems.append(problem)
                        item_entry_index = None
                else:
                    item_entry_index = _merge_continuation_paragraph(
                        t, current_entries, item_entry_index
                    )
            item_captured = True
        i += 1
    return i


def _skip_nested_sub_list(
    tokens: Sequence[Any],
    start: int,
    current_date: str | None,
    problems: list[LogParseProblem],
) -> int:
    """Skip a nested bullet list found inside a log entry's list item.

    The ``LogEntry`` model represents one flat prose entry per bullet, with
    no place for nested sub-bullets, so a sub-list can't be captured
    without either misreading it as continuation prose for the parent
    entry (wrong -- it's a distinct nested point, not more prose for the
    same one) or inventing hierarchy the model doesn't have. This reports
    it as a ``LogParseProblem`` and skips to the matching
    ``bullet_list_close`` via ``_skip_matching_block``, returning the index
    just past it, so the parent entry's own text is unaffected.
    """
    if current_date is not None:
        problems.append(
            LogParseProblem(
                date=current_date,
                line=_token_line(tokens[start]),
                message=(
                    "skipped nested sub-list under log entry: nested bullets "
                    "are not supported, only the entry's own text is kept"
                ),
            )
        )
    return _skip_matching_block(tokens, start)


def _skip_matching_block(tokens: Sequence[Any], start: int) -> int:
    """Skip from a container-opening token to just past its matching close.

    Tracks nesting via each token's ``level`` and markdown-it's ``nesting``
    convention (``+1`` for an opening token, ``-1`` for its close, ``0`` for
    a self-contained block) rather than a specific type name, so the same
    walk works uniformly for any balanced container -- a nested
    ``bullet_list_open``/``bullet_list_close`` pair, a stray
    ``blockquote_open``, an errant sub-heading, or any other construct --
    without a bespoke skip loop per type. Returns the index just past
    ``start``'s matching close.
    """
    level = tokens[start].level
    i = start + 1
    depth = 1
    while i < len(tokens) and depth > 0:
        if tokens[i].level == level:
            depth += tokens[i].nesting
        i += 1
    return i


_TOP_LEVEL_HEADING_TAGS = frozenset({"h2"})


def _is_stray_top_level_block(token: Any) -> bool:
    """Whether ``token`` opens a block that has no place directly under a
    date heading: anything other than the ``## YYYY-MM-DD`` date heading
    (already handled by its own branch) or the entry bullet list. This
    function is only ever consulted once a date section is already open
    (``current_date is not None``), so an ``h1`` reaching here is by
    construction an out-of-place heading, not a document title -- the title
    branch in ``parse_log`` only claims an ``h1`` while ``current_date`` is
    still ``None``, and falls through to this classifier otherwise. Matches
    on markdown-it's ``nesting`` convention (``>= 0`` covers both a
    container's opening token and a self-contained block like ``fence``/
    ``hr``/``html_block``) so new block types need no additional case here
    -- only a closing token (``nesting == -1``, e.g. a heading's own close
    falling through one token at a time) is excluded.
    """
    if token.type == "heading_open":
        return token.tag not in _TOP_LEVEL_HEADING_TAGS
    return bool(token.nesting >= 0)


def _skip_stray_block(
    tokens: Sequence[Any],
    start: int,
    current_date: str,
    problems: list[LogParseProblem],
) -> int:
    """Report and skip one stray top-level block placed under a date heading.

    Covers any block-level construct other than the expected date/title
    headings and the entry bullet list -- a bare paragraph, fenced or
    indented code block, thematic break, sub-heading, raw HTML block,
    blockquote, and so on -- as a single ``LogParseProblem`` category, since
    none of them has a representation in the flat ``LogEntry`` model. A
    self-contained block (``token.nesting == 0``, e.g. ``fence``/``hr``/
    ``html_block``) is a single token and needs no further skip; a
    container (``token.nesting == 1``, e.g. ``blockquote_open`` or an
    errant sub-``heading_open``) is skipped to its matching close via
    ``_skip_matching_block`` so sibling content after it is unaffected.
    """
    token = tokens[start]
    problems.append(
        LogParseProblem(
            date=current_date,
            line=_token_line(token),
            message=(
                "skipped stray block under date heading: log entries must "
                f"be bullet-list items, not {_stray_block_descriptor(token)}"
            ),
        )
    )
    if token.nesting == 0:
        return start + 1
    return _skip_matching_block(tokens, start)


def _stray_block_descriptor(token: Any) -> str:
    """Human-readable noun phrase for a stray block's ``LogParseProblem``."""
    token_type = token.type
    if token_type == "paragraph_open":
        return "a bare paragraph"
    if token_type == "heading_open":
        return f"a {token.tag!r} sub-heading"
    if token_type in ("fence", "code_block"):
        return "a fenced or indented code block"
    if token_type == "hr":
        return "a thematic break"
    if token_type == "html_block":
        return "a raw HTML block"
    if token_type == "blockquote_open":
        return "a blockquote"
    return f"a {token_type!r} block"


def _merge_continuation_paragraph(
    token: object,
    current_entries: list[LogEntry],
    item_entry_index: int | None,
) -> int | None:
    """Fold a loose list item's second-and-later paragraph into its entry.

    CommonMark renders a "loose" bullet item (one with a blank line between
    paragraphs) as sibling paragraphs within the same list item, all at the
    same token depth as the first. Only the first paragraph is turned into
    a ``LogEntry`` by ``_entry_from_list_item``; later ones are the same
    entry's continuation prose, not a new entry, so they're appended to
    ``.text`` here (space-joined) instead of being silently dropped.
    Returns the entry's (possibly new) index in ``current_entries``, so the
    caller can keep folding further paragraphs into the same entry.
    """
    children = getattr(token, "children", None) or []
    continuation_text = _render_prose(children).strip()
    if not continuation_text:
        return item_entry_index
    if item_entry_index is None:
        current_entries.append(LogEntry(text=continuation_text))
        return len(current_entries) - 1
    prior = current_entries[item_entry_index]
    current_entries[item_entry_index] = LogEntry(
        text=f"{prior.text} {continuation_text}", label=prior.label
    )
    return item_entry_index


def _date_section_from_heading(
    heading_text: str | None, line: int | None
) -> tuple[str | None, LogParseProblem | None]:
    """Validate a ``## heading`` as an ISO 8601 ``YYYY-MM-DD`` date.

    Returns ``(date, None)`` on success, or ``(None, LogParseProblem)`` when
    the heading is empty or not a valid calendar date in that form.
    """
    text = (heading_text or "").strip()
    if not text:
        return None, LogParseProblem(
            date=None,
            line=line,
            message="skipped malformed date heading: empty heading",
        )
    if not _DATE_FORM.fullmatch(text):
        return None, LogParseProblem(
            date=text,
            line=line,
            message=f"skipped malformed date heading: {text!r} is not ISO 8601 YYYY-MM-DD form",
        )
    try:
        datetime.date.fromisoformat(text)
    except ValueError as exc:
        return None, LogParseProblem(
            date=text,
            line=line,
            message=f"skipped malformed date heading: {text!r} is not a valid calendar date ({exc})",
        )
    return text, None


def _label_from_children(children: Sequence[Any]) -> tuple[str | None, int]:
    """Detect a leading ``**word**:`` bold label in a list item's children.

    Returns ``(label, colon_index)`` where ``colon_index`` is the index of
    the text token holding the separating colon, or ``(None, -1)`` if no such
    prefix is present.
    """
    idx = 0
    n = len(children)
    while idx < n and children[idx].type == "text" and not children[idx].content:
        idx += 1
    if idx >= n or children[idx].type != "strong_open":
        return None, -1

    idx += 1
    label_parts: list[str] = []
    while idx < n and children[idx].type != "strong_close":
        if children[idx].type != "text":
            return None, -1
        label_parts.append(children[idx].content)
        idx += 1
    if idx >= n:
        return None, -1

    idx += 1  # move past strong_close
    if (
        idx >= n
        or children[idx].type != "text"
        or not children[idx].content.startswith(":")
    ):
        return None, -1

    label = "".join(label_parts).strip()
    if not label:
        return None, -1
    return label, idx


def _entry_from_list_item(
    token: object, date: str, line: int | None
) -> tuple[LogEntry | None, LogParseProblem | None]:
    """Build a ``LogEntry`` from one bullet list item's inline token."""
    children = getattr(token, "children", None) or []

    label, colon_idx = _label_from_children(children)
    if label is None:
        text = _render_prose(children).strip()
    else:
        colon_text = children[colon_idx].content
        remainder_prefix = colon_text[1:].removeprefix(" ")
        text = (remainder_prefix + _render_prose(children[colon_idx + 1 :])).strip()

    if not text:
        return None, LogParseProblem(
            date=date,
            line=line,
            message="skipped malformed log entry: empty entry text",
        )
    return LogEntry(text=text, label=label), None


_render_prose = render_linked_span
"""Render a run of inline child tokens back to Markdown source.

Links are reconstituted verbatim as ``[text](href)``; every other token
passes through ``inline_token_source``. Unlike index.py's entry-title
renderer, log entry prose has no positional restriction on links -- any
number may appear anywhere in the entry. Shared with index.py's
suffix-span renderer via ``_markdown_inline.render_linked_span`` -- see
that module for the implementation.
"""


def _render_log_entry(entry: LogEntry) -> str:
    if entry.label:
        return f"* **{entry.label}**: {entry.text}"
    return f"* {entry.text}"


_token_line = token_line

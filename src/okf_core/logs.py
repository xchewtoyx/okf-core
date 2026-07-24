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
    rendered prose.
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
        if token.type == "heading_open" and token.tag == "h1":
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
    reached for it.
    """
    list_level = tokens[start].level
    item_captured = False
    item_open_line: int | None = None
    i = start + 1
    while i < len(tokens):
        t = tokens[i]
        if t.level == list_level and t.nesting == -1:
            i += 1
            break
        if t.level == list_level + 1 and t.type == "list_item_open":
            item_captured = False
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
        elif (
            t.type == "inline"
            and list_level < t.level <= list_level + 3
            and not item_captured
        ):
            if current_date is not None:
                entry, problem = _entry_from_list_item(t, current_date, _token_line(t))
                if entry is not None:
                    current_entries.append(entry)
                elif problem is not None:
                    problems.append(problem)
            item_captured = True
        i += 1
    return i


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

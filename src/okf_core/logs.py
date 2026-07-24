"""Log file (``log.md``) parsing, rendering, and loading for OKF bundles."""

from __future__ import annotations

import datetime
import enum
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
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


class _SectionState(enum.Enum):
    """Which of three phases of a log.md document ``parse_log`` is in.

    Distinguishes "no date heading seen yet" (``PREAMBLE``) from "inside an
    open, valid date section" (``IN_SECTION``, where ``current_date`` on the
    parse state carries the date) from "inside a section whose heading was
    malformed and therefore never opened" (``IN_SKIPPED_SECTION``). The bug
    this replaces used ``current_date is None`` to mean both ``PREAMBLE`` and
    ``IN_SKIPPED_SECTION`` -- so an ``h1`` landing after a malformed date
    heading was indistinguishable from one landing in the preamble, and
    could be silently dropped or misread as the document title. Making the
    three states explicit removes that ambiguity: every (phase, block kind)
    pair is handled by its own case in ``_dispatch_block``.
    """

    PREAMBLE = "preamble"
    IN_SECTION = "in_section"
    IN_SKIPPED_SECTION = "in_skipped_section"


class BlockKind(enum.Enum):
    """The structural shape of a ``_partition_top_level_blocks`` block.

    Purely about markdown-it token shape, not log.md semantics: an ``h1``
    heading, an ``h2`` heading, a bullet list, or anything else.
    """

    HEADING_H1 = "heading_h1"
    HEADING_H2 = "heading_h2"
    BULLET_LIST = "bullet_list"
    OTHER = "other"


@dataclass(frozen=True)
class TopLevelBlock:
    """One top-level block produced by ``_partition_top_level_blocks``.

    ``start``/``end`` are token indices (``end`` exclusive) spanning the
    block, already resolved past any nested content via
    ``_skip_matching_block``'s level/nesting walk. ``inline_text`` is the
    heading's rendered text for ``HEADING_H1``/``HEADING_H2`` blocks, and
    ``None`` for every other kind. ``token`` is the block's own opening (or
    self-contained) token, kept only so a stray-block report can describe
    it (``_stray_block_descriptor`` needs its type/tag).
    """

    kind: BlockKind
    start: int
    end: int
    line: int | None
    inline_text: str | None
    token: Any


@dataclass
class _ParseState:
    """Mutable accumulator threaded through ``_dispatch_block`` calls.

    ``phase``/``current_date`` are the explicit state ``_SectionState``
    replaces ``current_date is None`` overloading with; ``current_date`` is
    only meaningful while ``phase is _SectionState.IN_SECTION``.
    ``current_entries`` accumulates the presently-open section's entries and
    is reset by ``_close_open_section`` at every section boundary.
    """

    title: str | None = None
    phase: _SectionState = _SectionState.PREAMBLE
    current_date: str | None = None
    sections: list[LogDateSection] = field(default_factory=list)
    current_entries: list[LogEntry] = field(default_factory=list)
    problems: list[LogParseProblem] = field(default_factory=list)


def parse_log(content: str) -> ParsedLog:
    """Parse a log.md body into a title, date sections, and entries.

    Parsing runs in two phases. First, ``_partition_top_level_blocks`` walks
    the raw markdown-it token stream once and groups it into a flat list of
    top-level ``TopLevelBlock`` objects, each classified by shape alone (an
    ``h1`` heading, an ``h2`` heading, a bullet list, or anything else) --
    this pass knows nothing about log.md semantics. Second, that block list
    is walked once with explicit state: which of three phases
    (``_SectionState``) the parse is currently in -- before any date heading
    (preamble), inside a valid open date section, or inside a section whose
    heading was malformed -- rather than overloading ``current_date is
    None`` to mean two different things. Every (phase, block kind)
    combination is handled by its own case in ``_dispatch_block``, so no
    block type/position combination is reachable only by falling through an
    unrelated guard.

    ``# Title`` is captured as ``.title`` only when it is the first ``h1``
    seen while still in the preamble (before any ``## YYYY-MM-DD`` heading);
    a second preamble ``h1`` is preamble content like any other and does not
    replace it. Once the document has moved past the preamble -- whether
    into a valid date section or a malformed one -- any further ``h1`` is
    uniformly reported as a stray block (see below) and never captured as or
    merged into ``.title``, however many date headings, valid or malformed,
    intervene.

    Each ``## YYYY-MM-DD`` heading starts a date section; headings that are
    not valid ISO 8601 ``YYYY-MM-DD`` calendar dates are reported as
    ``LogParseProblem`` objects and that section's entries are skipped
    rather than raising. Entries, and any other non-heading content, that
    appear before the first date heading or under a malformed date heading
    are silently ignored -- they belong to no valid section, and there is no
    date to attribute a problem to. Each bullet-list item under a valid,
    open date section becomes a ``LogEntry``; a leading ``**Word**: `` bold
    prefix is captured as ``.label`` and stripped from ``.text``, otherwise
    ``.label`` is ``None`` and ``.text`` is the full rendered prose. A
    "loose" item's second and later paragraphs are folded into that same
    entry's ``.text`` (space-joined) rather than dropped. Any block-level
    construct other than the expected ``## YYYY-MM-DD`` date heading and the
    entry bullet list -- a bare paragraph, fenced or indented code block,
    thematic break, any heading (including an out-of-place ``# Title``-
    shaped ``h1``, ATX or setext), raw HTML block, or blockquote -- placed
    directly under an *open, valid* date heading is reported as a
    ``LogParseProblem`` and skipped, since none of them has a representation
    in the flat ``LogEntry`` model. This guarantee holds one nesting level
    deeper too: within a list item itself, anything after the item's own
    (and, for a loose item, continuation) paragraphs -- a nested bullet or
    ordered list, fenced or indented code, a thematic break, a blockquote,
    a heading, or raw HTML -- is unexpected nested content and is likewise
    reported and skipped rather than silently discarded, without corrupting
    that entry's own captured text or any sibling entry.
    Note that an HTML block with no blank line before a following bullet can
    still be absorbed into that block by CommonMark's own HTML-block rule
    before this parser ever sees it as a separate list; that case is still
    reported as a problem, but the swallowed entry cannot be recovered.
    """
    tokens = _MARKDOWN.parse(content)
    blocks = _partition_top_level_blocks(tokens)
    state = _ParseState()

    for block in blocks:
        _dispatch_block(block, tokens, state)
    _close_open_section(state)

    return ParsedLog(
        title=state.title,
        sections=tuple(state.sections),
        problems=tuple(state.problems),
    )


def _partition_top_level_blocks(tokens: Sequence[Any]) -> list[TopLevelBlock]:
    """Partition markdown-it's flat token stream into top-level blocks.

    Every token belongs to exactly one block: a container's opening token
    (``nesting == 1``) claims tokens up to its matching close via
    ``_skip_matching_block``'s level/nesting walk; a self-contained token
    (``nesting == 0``, e.g. ``hr``/``fence``/``html_block``) is a one-token
    block. Closing tokens are never block starts -- they're consumed as
    part of the block they close. This pass is purely structural and knows
    nothing about log.md semantics (dates, titles, entries); it only groups
    markdown-it structure so ``_dispatch_block`` can interpret it.
    """
    blocks: list[TopLevelBlock] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        end = i + 1 if token.nesting == 0 else _skip_matching_block(tokens, i)
        blocks.append(_classify_block(tokens, i, end, token))
        i = end
    return blocks


def _classify_block(
    tokens: Sequence[Any], start: int, end: int, token: Any
) -> TopLevelBlock:
    """Classify one already-spanned block by markdown-it token shape alone."""
    if token.type == "heading_open":
        inline_text = (
            tokens[start + 1].content
            if start + 1 < end and tokens[start + 1].type == "inline"
            else None
        )
        if token.tag == "h1":
            kind = BlockKind.HEADING_H1
        elif token.tag == "h2":
            kind = BlockKind.HEADING_H2
        else:
            kind = BlockKind.OTHER
    elif token.type == "bullet_list_open":
        kind, inline_text = BlockKind.BULLET_LIST, None
    else:
        kind, inline_text = BlockKind.OTHER, None
    return TopLevelBlock(
        kind=kind,
        start=start,
        end=end,
        line=_token_line(token),
        inline_text=inline_text,
        token=token,
    )


def _dispatch_block(
    block: TopLevelBlock, tokens: Sequence[Any], state: _ParseState
) -> None:
    """Route one partitioned top-level block to its phase's own dispatcher.

    An outer ``match state.phase:`` with one case per ``_SectionState``
    member, each delegating to a per-phase helper (``_dispatch_preamble_block``,
    ``_dispatch_in_section_block``, ``_dispatch_skipped_section_block``) that
    itself runs an inner ``match block.kind:`` with one case per
    ``BlockKind`` member -- so every (phase, block kind) combination is
    still an explicit case, and no case is reachable only by falling
    through an unrelated guard. Splitting the per-phase inner match into its
    own helper (rather than nesting it directly here) is what keeps this
    function's own branching low enough for the C901 complexity budget; the
    nesting itself, not any one function's size, is what lets mypy verify
    completeness. This is nested single-value matching rather than a flat
    match on the ``(phase, kind)`` tuple specifically so mypy's
    exhaustiveness checker (``--enable-error-code exhaustive-match``) can
    actually verify completeness: it can reason about exhaustiveness over a
    single enum-typed match subject, but not over a tuple of two enums, so a
    flat tuple match reports "unhandled case" even when every combination is
    present. None of these matches has a ``case _:`` wildcard, so a future
    ``_SectionState`` or ``BlockKind`` member with no corresponding case is
    a real mypy error, not silently swallowed.
    """
    match state.phase:
        case _SectionState.PREAMBLE:
            _dispatch_preamble_block(block, state)
        case _SectionState.IN_SECTION:
            _dispatch_in_section_block(block, tokens, state)
        case _SectionState.IN_SKIPPED_SECTION:
            _dispatch_skipped_section_block(block, state)


def _dispatch_preamble_block(block: TopLevelBlock, state: _ParseState) -> None:
    """Handle one top-level block while still in ``_SectionState.PREAMBLE``.

    A block kind that is a deliberate no-op here (a bullet list or other
    prose before any date heading) still has its own case -- it is silence
    by design, not by omission.
    """
    match block.kind:
        case BlockKind.HEADING_H1:
            _capture_title(block, state)
        case BlockKind.HEADING_H2:
            _open_or_skip_section(block, state)
        case BlockKind.BULLET_LIST:
            pass  # no date section is open yet; entries belong to no valid section
        case BlockKind.OTHER:
            pass  # preamble prose other than the title has no place in the model


def _dispatch_in_section_block(
    block: TopLevelBlock, tokens: Sequence[Any], state: _ParseState
) -> None:
    """Handle one top-level block while in ``_SectionState.IN_SECTION``."""
    match block.kind:
        case BlockKind.HEADING_H1:
            _skip_stray_block(block, state.current_date, state.problems)
        case BlockKind.HEADING_H2:
            _open_or_skip_section(block, state)
        case BlockKind.BULLET_LIST:
            _consume_bullet_list(
                tokens,
                block.start,
                state.current_date,
                state.current_entries,
                state.problems,
            )
        case BlockKind.OTHER:
            _skip_stray_block(block, state.current_date, state.problems)


def _dispatch_skipped_section_block(block: TopLevelBlock, state: _ParseState) -> None:
    """Handle one top-level block while in ``_SectionState.IN_SKIPPED_SECTION``.

    A block kind that is a deliberate no-op here (a bullet list or other
    content under a malformed date heading) still has its own case -- it is
    silence by design, not by omission.
    """
    match block.kind:
        case BlockKind.HEADING_H1:
            _skip_stray_block(block, None, state.problems)
        case BlockKind.HEADING_H2:
            _open_or_skip_section(block, state)
        case BlockKind.BULLET_LIST:
            pass  # no valid section is open; entries belong to no valid section
        case BlockKind.OTHER:
            pass  # no valid section is open; non-entry content is not attributable


def _capture_title(block: TopLevelBlock, state: _ParseState) -> None:
    """Capture a preamble ``h1``'s inline text as the document title.

    Only the first such heading wins -- a second ``h1`` seen while still in
    the preamble is preamble content like any other and is left alone,
    matching how other non-title preamble content is treated.
    """
    if state.title is None and block.inline_text is not None:
        state.title = block.inline_text


def _open_or_skip_section(block: TopLevelBlock, state: _ParseState) -> None:
    """Close the previously open section (if any) and open the next one.

    Called for every ``## heading`` block regardless of whether the section
    it's closing was a valid, open date section or a skipped malformed one
    -- either way a new heading starts a fresh section boundary. The new
    heading's own validity determines the next phase: ``IN_SECTION`` with
    its date on success, or ``IN_SKIPPED_SECTION`` on failure, in which case
    ``_date_section_from_heading``'s ``LogParseProblem`` is appended here.
    """
    _close_open_section(state)
    date, problem = _date_section_from_heading(block.inline_text, block.line)
    if problem is not None:
        state.problems.append(problem)
        state.phase = _SectionState.IN_SKIPPED_SECTION
        state.current_date = None
        return
    state.phase = _SectionState.IN_SECTION
    state.current_date = date


def _close_open_section(state: _ParseState) -> None:
    """Append the just-finished section to ``state.sections``, if one was open.

    A no-op in ``PREAMBLE`` or ``IN_SKIPPED_SECTION`` (there is no valid
    section to close); resets ``current_entries`` for the next section
    either way, and is also called once after the block loop ends to flush
    a still-open final section.
    """
    if state.phase == _SectionState.IN_SECTION and state.current_date is not None:
        state.sections.append(
            LogDateSection(
                date=state.current_date, entries=tuple(state.current_entries)
            )
        )
    state.current_entries = []


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


class _ItemBlockKind(enum.Enum):
    """The structural shape of one block found directly inside a list item.

    Mirrors ``BlockKind``'s role for top-level blocks, one nesting level
    down: a ``paragraph_open`` (the entry's own text, or a loose item's
    continuation paragraph -- both fold into the entry, via
    ``_entry_from_list_item``/``_merge_continuation_paragraph``) versus
    anything else a list item can directly contain -- a nested bullet or
    ordered list, a fenced or indented code block, a thematic break, a
    blockquote, a heading, or raw HTML -- none of which the flat
    ``LogEntry`` model has room for. A closed two-member set, checked by an
    exhaustive ``match`` in ``_consume_list_item``, for the same reason
    ``BlockKind`` is: a future markdown-it block type falls into ``OTHER``
    automatically rather than needing a new case, but the *handling* of
    ``PARAGRAPH`` vs. everything else can never silently go unhandled.
    """

    PARAGRAPH = "paragraph"
    OTHER = "other"


@dataclass(frozen=True)
class _ItemBlock:
    """One block-level construct found directly inside a list item.

    Parallels ``TopLevelBlock`` one nesting level down -- see there for the
    general field meanings. ``inline_token`` is the paragraph's own
    ``inline`` child token, kept (rather than pre-rendered text, unlike
    ``TopLevelBlock.inline_text``) because entry text needs the raw child
    tokens for label detection and link-preserving rendering; it is
    ``None`` for every ``_ItemBlockKind.OTHER`` block.
    """

    kind: _ItemBlockKind
    start: int
    end: int
    line: int | None
    token: Any
    inline_token: Any | None


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
    past the matching ``bullet_list_close``. Each ``list_item_open`` ...
    ``list_item_close`` span is resolved via ``_skip_matching_block`` and
    handed whole to ``_consume_list_item``, which does the actual per-item
    work.
    """
    list_level = tokens[start].level
    item_level = list_level + 1
    child_level = list_level + 2
    i = start + 1
    while i < len(tokens):
        token = tokens[i]
        if token.level == list_level and token.nesting == -1:
            i += 1
            break
        if token.level == item_level and token.type == "list_item_open":
            item_end = _skip_matching_block(tokens, i)
            _consume_list_item(
                tokens,
                i,
                item_end,
                child_level,
                current_date,
                current_entries,
                problems,
            )
            i = item_end
            continue
        i += 1
    return i


def _consume_list_item(
    tokens: Sequence[Any],
    item_start: int,
    item_end: int,
    child_level: int,
    current_date: str | None,
    current_entries: list[LogEntry],
    problems: list[LogParseProblem],
) -> None:
    """Handle one list item's direct block-level children, in place.

    ``item_start``/``item_end`` bound the item's own already-resolved span
    (its ``list_item_open`` up to, exclusive, past its matching
    ``list_item_close``); ``child_level`` is the level those direct
    children sit at. The children are partitioned by
    ``_partition_item_blocks`` -- the same shape-only classification
    ``_partition_top_level_blocks``/``_classify_block`` use one nesting
    level up -- and walked in order via an exhaustive ``match`` over
    ``_ItemBlockKind``: the first ``PARAGRAPH`` becomes the entry via
    ``_entry_from_list_item``; a later one is a loose item's continuation
    paragraph and folds into that same entry via
    ``_merge_continuation_paragraph``; anything else (``OTHER`` -- a nested
    bullet or ordered list, fenced or indented code, a thematic break, a
    blockquote, a heading, raw HTML) is unexpected nested content, reported
    once per block via ``_skip_unexpected_item_block`` and otherwise
    ignored, since ``_partition_item_blocks`` already resolved its full
    span past any nesting of its own -- it can't be misread as more of the
    entry's own text. A list item that closes without ever capturing a
    paragraph (a blank bullet, one whose only content has no
    Markdown-source rendering, or one whose only content is itself
    unexpected) is reported separately as an empty entry, matching prior
    behaviour.
    """
    item_open_line = _token_line(tokens[item_start])
    captured = False
    item_entry_index: int | None = None
    blocks = _partition_item_blocks(tokens, item_start + 1, item_end - 1, child_level)
    for block in blocks:
        match block.kind:
            case _ItemBlockKind.PARAGRAPH:
                if current_date is not None:
                    if not captured:
                        entry, problem = _entry_from_list_item(
                            block.inline_token, current_date, block.line
                        )
                        if entry is not None:
                            current_entries.append(entry)
                            item_entry_index = len(current_entries) - 1
                        elif problem is not None:
                            problems.append(problem)
                            item_entry_index = None
                    else:
                        item_entry_index = _merge_continuation_paragraph(
                            block.inline_token, current_entries, item_entry_index
                        )
                captured = True
            case _ItemBlockKind.OTHER:
                _skip_unexpected_item_block(block, current_date, problems)
    if not captured and current_date is not None:
        problems.append(
            LogParseProblem(
                date=current_date,
                line=item_open_line,
                message="skipped malformed log entry: empty entry text",
            )
        )


def _partition_item_blocks(
    tokens: Sequence[Any], start: int, end: int, level: int
) -> list[_ItemBlock]:
    """Partition one list item's direct block-level children.

    ``start``/``end`` bound the item's own content span (just past its
    ``list_item_open`` up to, but not including, its matching
    ``list_item_close``); ``level`` is the level those direct children sit
    at. Structurally identical to ``_partition_top_level_blocks`` one
    nesting level down: a container-opening token (``nesting == 1``) claims
    tokens up to its matching close via ``_skip_matching_block``; a
    self-contained token (``nesting == 0``, e.g.
    ``hr``/``fence``/``code_block``/``html_block``) is a one-token block. An
    empty item (no children at all -- ``start == end``) yields no blocks,
    which is how an empty/blank bullet is represented.
    """
    blocks: list[_ItemBlock] = []
    i = start
    while i < end:
        token = tokens[i]
        block_end = i + 1 if token.nesting == 0 else _skip_matching_block(tokens, i)
        if token.type == "paragraph_open":
            kind = _ItemBlockKind.PARAGRAPH
            inline_token = (
                tokens[i + 1]
                if i + 1 < block_end and tokens[i + 1].type == "inline"
                else None
            )
        else:
            kind = _ItemBlockKind.OTHER
            inline_token = None
        blocks.append(
            _ItemBlock(
                kind=kind,
                start=i,
                end=block_end,
                line=_token_line(token),
                token=token,
                inline_token=inline_token,
            )
        )
        i = block_end
    return blocks


def _skip_unexpected_item_block(
    block: _ItemBlock,
    current_date: str | None,
    problems: list[LogParseProblem],
) -> None:
    """Report one unexpected block-level construct found inside a list item.

    Covers any direct child of a list item other than its own paragraph(s)
    -- a nested bullet or ordered list, a fenced or indented code block, a
    thematic break, a blockquote, a heading, or raw HTML -- as a single
    ``LogParseProblem`` category, mirroring ``_skip_stray_block``'s role for
    stray top-level blocks one nesting level up. The block's span was
    already resolved by ``_partition_item_blocks``, so no further token walk
    is needed here; skipping it is just declining to fold it into the
    entry's text. A no-op when ``current_date`` is ``None`` -- in practice
    this is never reached with a ``None`` date, since a bullet list is only
    ever dispatched to ``_consume_bullet_list`` from an open, valid date
    section, but the guard mirrors that invariant defensively rather than
    asserting it.
    """
    if current_date is None:
        return
    problems.append(
        LogParseProblem(
            date=current_date,
            line=block.line,
            message=(
                "skipped unexpected nested block in log entry: an entry's "
                f"own text must be flat prose, not {_stray_block_descriptor(block.token)}"
            ),
        )
    )


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


def _skip_stray_block(
    block: TopLevelBlock,
    date_for_problem: str | None,
    problems: list[LogParseProblem],
) -> None:
    """Report one stray top-level block placed under a date heading.

    Covers any block-level construct other than the expected date/title
    headings and the entry bullet list -- a bare paragraph, fenced or
    indented code block, thematic break, sub-heading, an out-of-place
    ``h1``, raw HTML block, blockquote, and so on -- as a single
    ``LogParseProblem`` category, since none of them has a representation
    in the flat ``LogEntry`` model. ``date_for_problem`` is the open
    section's date when called from ``IN_SECTION``, or ``None`` when called
    from ``IN_SKIPPED_SECTION`` -- there is no valid date to attribute it
    to there, only the malformed heading that preceded it. The block's
    span was already resolved by ``_partition_top_level_blocks``, so no
    further token walk is needed here; skipping it is just moving on to
    the next block in the caller's loop.
    """
    problems.append(
        LogParseProblem(
            date=date_for_problem,
            line=block.line,
            message=(
                "skipped stray block under date heading: log entries must "
                f"be bullet-list items, not {_stray_block_descriptor(block.token)}"
            ),
        )
    )


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
    if token_type == "bullet_list_open":
        return "a nested bullet list"
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

"""Attribution consistency check: footnote labels vs. ``sources[].id``.

OKF spec §5.1 attributes individual claims via a Markdown footnote whose
label joins to a ``sources[].id`` frontmatter entry, e.g.::

    The `events_` table is sharded daily.[^ga4-schema]

    [^ga4-schema]: GA4 BigQuery Export schema

Design (raw-source scan, superseding a rule-disabling approach)
-----------------------------------------------------------------
``extract_footnote_occurrences`` scans the **raw** ``body`` string directly
with ``_FOOTNOTE_RE`` for ``[^label]``/``[^label]:`` patterns. ``markdown_it``
is used only to locate the source ranges of inline code spans
(``code_inline`` tokens) and fenced/indented code blocks (``fence``/
``code_block`` tokens); any regex match whose ``[`` falls inside one of
those ranges is excluded. Line numbers are recovered by counting literal
``"\\n"`` characters up to the match position -- no token-line bookkeeping
needed, since a raw string naturally carries every line break, including
soft breaks that only exist as token boundaries during tokenization.

This replaces an earlier design that walked ``markdown_it``'s parsed
*inline* token children per paragraph, scanning only ``text``-type
children for the regex. That approach broke three times in succession,
each time because a CommonMark rule consumed or split the bracket
characters ``[^label]`` is built from before the per-child scan ever saw a
complete pattern:

1. A ``[^label]: <url>`` definition whose text happened to look like a link
   destination was consumed whole by the block ``reference`` rule, and every
   later ``[^label]`` in the document was then rewritten into a resolved
   link span (no ``text`` child left for either).
2. A ``[^label](dest)`` or ``![^label](url)`` was consumed by the inline
   ``link``/``image`` rules, independently of the ``reference`` rule's
   state.
3. Content *nested inside* the label -- e.g. ``[^lab<http://x.com>el]`` (an
   autolink) or `` [^lab`code`el] `` (a code span) -- triggered
   ``autolink``/``html_inline``/``backticks`` mid-label, splitting the
   surrounding text into two non-adjacent ``text`` children; neither half
   matched the full pattern, and the occurrence was silently lost.

Each fix disabled another CommonMark rule, but round 3 showed the strategy
doesn't converge: there is no fixed list of "rules that can consume ``[``/
``]``" to disable, because any rule that produces a non-``text`` child in
the middle of a label defeats a per-child scan, whether or not it touches
brackets itself. Scanning the raw source sidesteps the class of bug
entirely -- the tokenizer's choices about how to group characters into
child tokens are irrelevant when the scan never looks at child tokens for
extraction. ``markdown_it`` is kept, at its CommonMark defaults, purely as
an oracle for "is this byte range code" (spans and blocks), which is the
one thing a hand-rolled regex should not reimplement (backtick-string
matching has spec-defined edge cases around run length and adjacency that
the real parser already gets right).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from markdown_it import MarkdownIt

from okf_core.documents import ValidationFinding

_MARKDOWN = MarkdownIt("commonmark")

# A footnote label stops at the first "]" or whitespace; an immediately
# following ":" distinguishes a definition ("[^label]: text") from a
# reference ("[^label]" inline in prose). This is the accepted-input
# contract carried over unchanged from the token-scanning design.
_FOOTNOTE_RE = re.compile(r"\[\^([^\]\s]+)\](:)?")

# Block-level token types whose entire source line range is opaque to the
# footnote scan (fenced and indented code blocks).
_CODE_BLOCK_TOKEN_TYPES = ("fence", "code_block")


@dataclass(frozen=True)
class FootnoteOccurrence:
    """One ``[^label]`` occurrence in a concept body.

    ``is_definition`` is True for a ``[^label]:`` definition line, False for
    a bare ``[^label]`` reference in prose.
    """

    label: str
    line: int | None
    is_definition: bool


def extract_footnote_occurrences(body: str) -> tuple[FootnoteOccurrence, ...]:
    """Extract every ``[^label]`` reference and ``[^label]:`` definition in *body*.

    Matches whose ``[`` falls inside a code span or fenced/indented code
    block are excluded; every other match is reported regardless of what
    surrounds it (nested autolinks, code spans, or resolved reference
    links do not affect a raw-source scan).
    """
    line_starts = _line_starts(body)
    excluded = _excluded_ranges(body, _MARKDOWN.parse(body), line_starts)

    occurrences: list[FootnoteOccurrence] = []
    for match in _FOOTNOTE_RE.finditer(body):
        pos = match.start()
        if _position_excluded(pos, excluded):
            continue
        occurrences.append(
            FootnoteOccurrence(
                label=match.group(1),
                line=body.count("\n", 0, pos) + 1,
                is_definition=match.group(2) is not None,
            )
        )
    return tuple(occurrences)


def _line_starts(body: str) -> list[int]:
    """Return the char offset each source line (0-indexed) begins at.

    ``line_starts[i]`` is the offset of line *i*, matching ``markdown_it``
    token ``.map`` line numbering. A trailing sentinel equal to
    ``len(body)`` lets a token's ``end_line`` (exclusive) be used as a
    slice bound even when it addresses one line past the last real line.
    """
    starts: list[int] = []
    offset = 0
    for raw_line in body.splitlines(keepends=True):
        starts.append(offset)
        offset += len(raw_line)
    starts.append(offset)
    return starts


def _line_offset(line_starts: Sequence[int], line: int) -> int:
    """Char offset for 0-indexed *line*, clamped to the sentinel end offset."""
    return line_starts[min(line, len(line_starts) - 1)]


def _find_backtick_run(text: str, start: int, length: int) -> int | None:
    """Find the next maximal backtick run of exactly *length* at/after *start*.

    A CommonMark "backtick string" is a run of backticks bounded by a
    non-backtick character (or the string boundary) on both sides. Matching
    the literal substring alone is not enough -- ``"`"`` (length 1) must not
    match inside ``"``"`` (a run of two) -- so each candidate is checked for
    both neighbors before being accepted; a false candidate advances the
    search by one character rather than past the whole run, since the next
    real run could start one character later.
    """
    marker = "`" * length
    pos = start
    while True:
        idx = text.find(marker, pos)
        if idx == -1:
            return None
        before_ok = idx == 0 or text[idx - 1] != "`"
        after_idx = idx + length
        after_ok = after_idx >= len(text) or text[after_idx] != "`"
        if before_ok and after_ok:
            return idx
        pos = idx + 1


def _iter_code_inline_tokens(children: Sequence[Any]) -> Iterator[Any]:
    """Yield every ``code_inline`` token reachable from *children*, in raw-text order.

    ``token.children`` is not a flat list of every inline token in the
    paragraph -- a container-like token (e.g. ``image``) carries its own
    nested ``.children`` (the parsed alt-text tokens), which does not
    appear in the top-level list at all. A scan that only inspected
    top-level children silently skipped any ``code_inline`` parented that
    way, and -- worse -- misattributed a *later* top-level ``code_inline``
    to the skipped span's own backticks, because ``_code_inline_ranges``'s
    search cursor never advanced past them (issue #197 recurrence #4).

    Recursing into every child's ``.children`` (not just ``image``'s)
    keeps this general: any token type, present or future, that nests
    content that way is walked the same way, not special-cased. A
    ``code_inline`` token is always a leaf (markdown_it never gives it its
    own ``.children``), so checking its type first and only recursing into
    the ``else`` branch cannot skip a nested code span inside it.
    """
    for child in children:
        if getattr(child, "type", None) == "code_inline":
            yield child
        else:
            nested = getattr(child, "children", None)
            if nested:
                yield from _iter_code_inline_tokens(nested)


def _code_inline_ranges(
    para_text: str, children: Sequence[Any]
) -> list[tuple[int, int]]:
    """Return (start, end) char ranges of each ``code_inline`` descendant, in order.

    ``children`` is walked via ``_iter_code_inline_tokens`` rather than
    iterated directly, so a ``code_inline`` nested inside another token
    (e.g. inside an ``image``'s alt text) is found alongside top-level
    ones, in the same left-to-right order they appear in *para_text*. Each
    code span is located by re-finding its opening/closing backtick run
    directly in *para_text*, starting the search for span *n+1* only after
    span *n*'s close -- so two code spans using the same delimiter length
    in one paragraph resolve in document order rather than both matching
    the first run found. With every descendant visited in true document
    order, a span whose delimiters can't be relocated should not happen --
    markdown_it only emits ``code_inline`` for an actual backtick-delimited
    run in the source, and the monotonically-advancing cursor now walks
    every one of those runs in the order they occur -- so this is a
    defensive skip (never mis-locating a real footnote occurrence) rather
    than a known-reachable path.
    """
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for child in _iter_code_inline_tokens(children):
        markup = getattr(child, "markup", "") or "`"
        length = len(markup)
        open_pos = _find_backtick_run(para_text, cursor, length)
        if open_pos is None:
            continue
        close_pos = _find_backtick_run(para_text, open_pos + length, length)
        if close_pos is None:
            continue
        end = close_pos + length
        ranges.append((open_pos, end))
        cursor = end
    return ranges


def _excluded_ranges(
    body: str, tokens: Sequence[Any], line_starts: Sequence[int]
) -> list[tuple[int, int]]:
    """Collect every code-span and code-block char range to exclude from the scan."""
    ranges: list[tuple[int, int]] = []
    for token in tokens:
        if token.type in _CODE_BLOCK_TOKEN_TYPES and token.map:
            start_line, end_line = token.map
            ranges.append(
                (
                    _line_offset(line_starts, start_line),
                    _line_offset(line_starts, end_line),
                )
            )
        elif token.type == "inline" and token.children and token.map:
            start_line, end_line = token.map
            start_off = _line_offset(line_starts, start_line)
            para_text = body[start_off : _line_offset(line_starts, end_line)]
            ranges.extend(
                (start_off + rel_start, start_off + rel_end)
                for rel_start, rel_end in _code_inline_ranges(para_text, token.children)
            )
    return ranges


def _position_excluded(pos: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def _declared_source_ids(frontmatter: Mapping[str, Any]) -> tuple[str, ...]:
    """Return ``sources[].id`` values that are non-empty strings, dedup-first-seen.

    ``id`` is optional per spec §5.1 -- entries without one, or with a
    malformed ``sources`` list/entry, are simply not join candidates, not
    errors here; that permissiveness is validated elsewhere. ``frontmatter``
    may come straight from a freshly-parsed ``ConceptDocument`` (plain
    ``dict``/``list``) or from a scanned ``ConceptManifestEntry`` (frozen via
    ``MappingProxyType``/``tuple``), so both list-like and mapping-like
    variants are accepted rather than the concrete ``list``/``dict`` types.
    """
    sources = frontmatter.get("sources")
    if not isinstance(sources, (list, tuple)):
        return ()
    ids: list[str] = []
    for entry in sources:
        if not isinstance(entry, Mapping):
            continue
        source_id = entry.get("id")
        if isinstance(source_id, str) and source_id.strip():
            ids.append(source_id)
    return tuple(dict.fromkeys(ids))


def check_attribution_consistency(
    frontmatter: Mapping[str, Any], body: str
) -> tuple[ValidationFinding, ...]:
    """Join footnote labels in *body* against ``sources[].id`` in *frontmatter*.

    A footnote label (reference or definition) with no matching
    ``sources[].id`` is an ``"error"`` finding. A declared ``sources[].id``
    that no footnote (reference or definition) ever cites is a ``"warning"``
    finding -- an unreferenced source is legal, so this is advisory rather
    than an error. A document with neither footnotes nor sources reports
    nothing.
    """
    occurrences = extract_footnote_occurrences(body)
    source_ids = _declared_source_ids(frontmatter)
    if not occurrences and not source_ids:
        return ()

    referenced: set[str] = set()
    findings: list[ValidationFinding] = []
    for occurrence in occurrences:
        referenced.add(occurrence.label)
        if occurrence.label not in source_ids:
            findings.append(
                ValidationFinding(
                    severity="error",
                    message=(
                        f"Footnote label '{occurrence.label}' has no matching "
                        "sources[].id"
                    ),
                    field=occurrence.label,
                    line=occurrence.line,
                )
            )

    for source_id in source_ids:
        if source_id not in referenced:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    message=(
                        f"sources[].id '{source_id}' is not referenced by any footnote"
                    ),
                    field=source_id,
                )
            )

    return tuple(findings)

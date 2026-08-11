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

Locating ``code_inline`` spans by validated content match (round 5/6)
-----------------------------------------------------------------------
Extraction (above) was fixed for good in round 3 by never looking at
tokens at all. But *exclusion* still needs one fact tokens alone don't
carry: exactly which raw byte range each ``code_inline`` token occupies.
``markdown_it`` sets ``.map`` (line range) on block tokens but never on
inline tokens, and a ``code_inline``'s ``.content`` is normalized (line
endings collapsed to spaces, one layer of surrounding whitespace trimmed)
rather than a literal slice of source -- so its span has to be
*relocated* in the raw text, not read off the token.

Round 4 (``_iter_code_inline_tokens``) fixed relocation across nesting --
walking every ``code_inline`` reachable from a paragraph's inline children
in true document order, including ones parented under another token's own
``.children`` (e.g. an ``image``'s alt text), rather than only the
top-level list. What it did not anticipate: three more ways a *different*
raw backtick character, belonging to no code span at all, can sit between
one ``code_inline``'s search cursor and its real delimiters and get
mistaken for one:

4. An HTML comment (``html_inline``) containing a literal backtick.
5. An autolink (``<uri>``) whose URI contains a literal backtick (legal
   per CommonMark -- autolink content is not entity/escape-processed).
6. A link or image destination (``](url)``) containing a literal
   backtick -- and, independently, a backslash-escaped backtick
   (``\\```) in ordinary prose, which ``markdown_it`` resolves to a
   literal ``` ` ``` character in a ``text`` token's ``.content`` with no
   trace of the escape left for a raw-text scan to see.

Each of these looks like a new special case to enumerate and skip past --
exactly the pattern that took three rounds to escape in extraction. The
structural fix here is the same move made for extraction: stop trying to
recognize *what* produced a stray backtick (comment syntax, autolink
syntax, link-destination syntax, escape syntax, or anything not yet seen)
and instead validate *whether* a candidate pairing is the right one, using
a fact already on hand and already trusted -- the token's own
``.content``. ``_find_code_inline_span`` finds a candidate backtick pair
with ``_find_backtick_run`` (unchanged since round 1) as before, but only
accepts it once the candidate's raw inner text, normalized exactly the way
``markdown_it.rules_inline.backticks`` normalizes ``code_inline`` content
(``_normalize_code_span_content``), equals the token's ``.content``. A
candidate that fails resumes the search one character past its own
(wrong) opening backtick, not past its wrong closing pair, so the true
opening candidate -- wherever it is, including immediately adjacent to the
rejected one -- is never skipped over.

This closes the bug class rather than reopening it one construct at a
time: the fix does not parse, recognize, or even know the names of HTML
comments, autolinks, link destinations, or escape sequences. It has no
list of "constructs to skip" to keep growing. *Any* source of a raw
backtick that is not this token's own delimiters -- known now or
discovered in round 7 -- fails the content check the same way, because the
text between a false pairing's endpoints is, by construction, not this
span's content. The only way this validation could accept a wrong pairing
is a coincidental collision where unrelated raw text between two false
delimiters normalizes to the exact same string as the real span's content
-- vanishingly unlikely for real documents, and not a new failure mode
introduced by this fix (the pre-round-5 code had no defense against it
either, since it never checked content at all).
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


def _normalize_code_span_content(raw: str) -> str:
    """Reproduce ``markdown_it``'s own ``code_inline`` content normalization.

    Mirrors ``markdown_it.rules_inline.backticks.backtick`` exactly: line
    endings become a single space each, then one leading and one trailing
    space are stripped if the result has both and is not all spaces. This
    is not a place to be "close enough" -- it exists solely so a candidate
    raw slice can be compared byte-for-byte against ``token.content``, and
    a normalization that drifted from markdown_it's own would either reject
    genuine spans (false negatives) or, worse, accept a wrong pairing that
    happens to normalize to the same string under the wrong rule.
    """
    content = raw.replace("\n", " ")
    if content.startswith(" ") and content.endswith(" ") and len(content.strip()) > 0:
        content = content[1:-1]
    return content


def _find_code_inline_span(
    para_text: str, cursor: int, markup: str, expected_content: str
) -> tuple[int, int] | None:
    """Locate *this* ``code_inline`` token's own (start, end) span at/after *cursor*.

    Design (structural fix for issue #197 recurrence #5/#6 -- see module
    docstring): a candidate backtick pair found by ``_find_backtick_run`` is
    only accepted once its raw inner text, normalized the same way
    ``markdown_it`` normalizes ``code_inline`` content, equals *this*
    token's own ``expected_content``. A candidate that fails the check is
    rejected and the search resumes one character past its opening
    backtick -- not past its (wrong) closing backtick -- so the next
    candidate open position is still reachable, including one that starts
    immediately after the rejected one.

    This is what makes the fix construct-agnostic: it does not need to
    recognize an HTML comment, an autolink, a link/image destination, a
    backslash-escaped backtick, or any other source of a stray backtick
    character between here and the token's true delimiters. Whatever
    produced that backtick, the raw text it delimits cannot normalize to
    *this* token's own known content (short of an almost-impossible
    coincidental collision), so the validation step rejects it and the
    cursor keeps advancing until it reaches the pairing ``markdown_it``
    itself identified when it emitted this token in the first place.
    """
    length = len(markup)
    pos = cursor
    while True:
        open_pos = _find_backtick_run(para_text, pos, length)
        if open_pos is None:
            return None
        close_pos = _find_backtick_run(para_text, open_pos + length, length)
        if close_pos is None:
            return None
        raw_inner = para_text[open_pos + length : close_pos]
        if _normalize_code_span_content(raw_inner) == expected_content:
            return open_pos, close_pos + length
        pos = open_pos + 1


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
    span is located by ``_find_code_inline_span``, which validates a
    candidate backtick pair against the token's own known content before
    accepting it -- see that function's docstring and the module docstring
    for why this is what makes span-finding immune to stray backticks
    coming from *any* other inline construct, not just the three the
    fix was reproduced against. The search for span *n+1* starts only
    after span *n*'s accepted close, so two code spans using the same
    delimiter length in one paragraph resolve in document order rather
    than both matching the first run found. With every descendant visited
    in true document order, a span that can never validate should not
    happen -- markdown_it only emits ``code_inline`` for an actual
    backtick-delimited run in the source -- so a ``None`` result here is a
    defensive skip (never mis-locating a real footnote occurrence) rather
    than a known-reachable path.
    """
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for child in _iter_code_inline_tokens(children):
        markup = getattr(child, "markup", "") or "`"
        span = _find_code_inline_span(para_text, cursor, markup, child.content)
        if span is None:
            continue
        ranges.append(span)
        cursor = span[1]
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

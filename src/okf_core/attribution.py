"""Attribution consistency check: footnote labels vs. ``sources[].id``.

OKF spec §5.1 attributes individual claims via a Markdown footnote whose
label joins to a ``sources[].id`` frontmatter entry, e.g.::

    The `events_` table is sharded daily.[^ga4-schema]

    [^ga4-schema]: GA4 BigQuery Export schema

See ``docs/decisions/0003-attribution-footnote-label-charset.md`` for the
design history and rejected alternatives (earlier extraction designs that
each closed one CommonMark interaction while missing another). What
follows is the load-bearing soundness argument for the design actually
shipped: why the restricted label charset is safe, and why code spans are
excluded without any extra bookkeeping.

Label charset: ``[A-Za-z0-9-]+``
---------------------------------
``_FOOTNOTE_RE``'s label group matches only ``[A-Za-z0-9-]+`` -- letters,
digits, and hyphens. No brackets, backticks, angle brackets, parens,
colons, underscores, whitespace, or any other character with meaning to
any CommonMark rule. A ``[^label]``-shaped construct whose label contains a
character outside this set is not recognized as footnote syntax at all --
see "Narrowed contract" below.

**Why no label character can ever be a delimiter for an active rule:**

This module's ``MarkdownIt("commonmark")`` configuration disables the block
``reference`` rule (see ``_MARKDOWN.block.ruler.disable("reference")``
below); it is not part of the active rule set. Its own trigger characters
are still checked below, for defense in depth against a future change that
re-enables it. The active rule set -- confirmed empirically against the
installed ``markdown-it-py`` (``md.block.ruler.get_active_rules()`` /
``md.inline.ruler.get_active_rules()`` / ``md.inline.ruler2.get_active_rules()``
-- rather than assumed) is:

- Block: ``code``, ``fence``, ``blockquote``, ``hr``, ``list``,
  ``html_block``, ``heading``, ``lheading``, ``paragraph``.
- Inline (pass 1): ``text``, ``newline``, ``escape``, ``backticks``,
  ``emphasis``, ``link``, ``image``, ``autolink``, ``html_inline``,
  ``entity``.
- Inline (pass 2, post-processing over already-tagged delimiters):
  ``balance_pairs``, ``emphasis``, ``fragments_join``.

Every rule's own trigger/delimiter character set -- active or not, per the
defense-in-depth note above for ``reference`` -- is checked against
``{A-Z, a-z, 0-9, -}``:

- ``text`` has no delimiter set of its own -- it is the catch-all that
  consumes any character no other rule claims. Nothing to check.
- ``newline`` triggers on ``"\\n"`` (and trailing-space/backslash hard-break
  variants). Not in the label charset.
- ``escape`` triggers on ``"\\"``. Not in the label charset.
- ``backticks`` triggers on `` "`" ``. Not in the label charset.
- ``emphasis`` triggers on ``"*"`` or ``"_"`` only -- CommonMark's
  delimiter-run scan for emphasis never treats ``"-"`` as a delimiter
  character; hyphen and underscore are visually similar but distinct code
  points with distinct rule membership. Underscore is deliberately excluded
  from the label charset specifically to keep this check trivial (no need
  to reason about ``_..._`` intraword-flanking edge cases at all, since
  underscore never appears in a label in the first place). Asterisk is not
  in the label charset either.
- ``link`` / ``image`` trigger on ``"["``, ``"]"``, ``"("``, ``")"`` (and
  ``"!"`` for the image form). None are in the label charset -- a label's
  own characters can never independently open or close a link/image
  delimiter. Only the *surrounding* literal brackets (never part of the
  label) can do that; for ``link``, that is exactly the closed, enumerable
  case handled explicitly below (link reconstruction). ``image`` is out of
  scope entirely regardless of its trigger characters -- see "Images are out
  of scope" below.
- ``autolink`` / ``html_inline`` both require a literal ``"<"`` to trigger
  and (for autolink) a literal ``">"`` to close. Neither is in the label
  charset. Once triggered by those chars, content inside is opaque to
  further inline parsing regardless of what characters it contains --
  irrelevant here, since a label consisting only of ``{A-Z, a-z, 0-9, -}``
  can never *itself* supply the ``"<"``/``">"`` needed to open or close one.
- ``entity`` triggers on ``"&"``. Not in the label charset.
- ``reference`` (block; disabled, see above) triggers on a line starting
  with ``"["`` and later a `` "]:" `` shape. Neither is in the label
  charset, so by the same reasoning as ``link``/``image`` a label's own
  characters cannot trigger it even if the rule were re-enabled. It is
  disabled regardless -- not because the label charset requires it, but
  because *unrelated* bracket text elsewhere in the same document (a
  genuine ``[^label]: text`` definition) can register a reference and cause
  a *different* bracket span later in the document to be rewritten into a
  resolved link. That is a structural interaction between two separate
  constructs, orthogonal to what characters either one's label is made of
  -- disabling the rule removes the interaction entirely rather than
  reasoning about it.
- ``balance_pairs`` / ``fragments_join`` (pass 2) operate over delimiter
  runs and token adjacency already established by pass 1; they introduce no
  new trigger characters of their own.
- The remaining active block rules (``code``, ``fence``, ``blockquote``,
  ``hr``, ``list``, ``html_block``, ``heading``, ``lheading``,
  ``paragraph``) all key off the *start* of a line (indentation, ``>``,
  ``#``, a run of ``-``/``*``/``_``, list markers, etc.), never off
  characters embedded mid-line inside a ``[^...]`` construct. A line whose
  first character is the literal ``"["`` of a genuine footnote occurrence
  falls through all of them to the default ``paragraph`` rule, which is
  what every example in this module's test suite already relies on.

Because no label character can ever be a delimiter, opener, or closer for
any active rule, a slug-shaped label can never be split across a token
boundary except by the closed-set ``link`` shape above -- which is exactly
the shape handled explicitly, not reasoned about heuristically. ``image``
never reaches this reasoning at all: it is out of scope regardless of
whether its alt text is slug-shaped -- see "Images are out of scope" below.

**Extraction: scan ``text``-type inline children by token type, never by
raw position.** ``extract_footnote_occurrences`` parses once with
``markdown_it`` (``reference`` disabled, as above) and walks every
top-level ``inline`` token's ``.children`` in document order, regex-matching
``_FOOTNOTE_RE`` against each ``text``-type child's ``.content``. One
closed-set reconstruction covers the shape that swallows the surrounding
brackets:

- ``link_open`` immediately followed by one ``text`` child immediately
  followed by ``link_close``, whose content fully matches ``^label`` --
  recovers ``[^label](dest)``.

An ``image`` token is never reconstructed at all -- see "Images are out of
scope" below.

A ``code_inline`` token is never of type ``text``, so it is skipped by
construction -- no relocation of its raw byte range is needed, because
nothing needs to know *where* it is, only that it *is* one. The same holds
for fenced/indented code blocks: they are ``fence``/``code_block`` block
tokens with their own opaque ``.content`` and are never passed through the
inline tokenizer at all, so a walk that only visits ``inline`` tokens'
children never sees them -- no exclusion-range bookkeeping needed for code
blocks either. This is what makes mis-locating a code span's raw position
-- the failure mode of every earlier design, see the ADR referenced above
-- structurally unreachable: this design never computes a raw position for
a code span at all.

**Images are out of scope, by design (not a partial-match omission).**
markdown-it-py flattens a link's text directly into the parent token list
(the ``link_open``/``text``/``link_close`` triple reconstructed above), but
an image's alt text is different: even trivial alt text lives on a nested
``image.children`` list this module never walks -- ``image.content`` is only
a flattened rendering of that nested tree, not the tree itself. Matching
against ``image.content`` directly is also unsound on its own terms: it
would only catch alt text that is *exactly* ``^label``, so non-trivial alt
text like ``![Diagram [^label]](url.png)`` would be silently invisible
regardless. Any image-alt-text handling needs its own child-tree walk, and
none exists here: images are scoped out of this check entirely -- an
``image`` token is never inspected for footnote syntax, whether its alt
text matches a label exactly or merely contains one -- there is no
partial-match/exact-match distinction to reason about, because nothing
about an image's alt text is recognized. A ``sources[].id`` cited only from
image alt text will therefore always surface as the advisory "unreferenced
source" finding (see "Narrowed contract" below); that is expected, not a
bug.

Line numbers are recovered by walking each ``inline`` token's children
in order, starting from ``token.map[0] + 1`` (``markdown_it``'s block-level
line numbering, 1-indexed here), and incrementing on every ``softbreak`` /
``hardbreak`` child -- the same line-tracking a renderer would do, not a
raw-offset computation.

**Narrowed contract (intentional, documented behavior change).** A
``[^label]``-shaped construct whose label contains a character outside
``[A-Za-z0-9-]`` is no longer recognized as footnote syntax by this check
at all -- neither as an occurrence to report, nor as a definition. It is
simply invisible to the checker, the same as any other prose. This is an
interpretation of "stable key" scoped to *this validation tool only*, not a
broader library-wide restriction: ``sources[].id`` in frontmatter is read
as-is elsewhere in the codebase (e.g. ``plan_source_upsert``) with no such
restriction. One practical consequence: a ``sources[].id`` value that is
not slug-shaped can never be matched by a footnote reference under this
check, so it will always surface as the advisory "unreferenced source"
finding. That is expected given the restriction, not a bug.

A ``[^label]`` match additionally requires that the character immediately
following the closing ``]`` (when present and not a definition-marking
``":"``) not itself be a label-charset character -- e.g. ``[^label]abc``
is not recognized as citing ``label``. Without this, a value like
``label`` inside a longer run of identifier-like text adjacent to the
brackets would be silently truncated to a shorter, unintended label rather
than being either fully recognized or fully ignored; requiring a clean
boundary keeps recognition unambiguous rather than partial.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from markdown_it import MarkdownIt

from okf_core.documents import ValidationFinding

_MARKDOWN = MarkdownIt("commonmark")
# See the "Label charset" docstring section above: disabling this rule
# removes the structural interaction where a genuine `[^label]: text` definition
# elsewhere in the document causes an unrelated `[^label]` reference to be
# rewritten into a resolved link span. It is not required for label-shape
# safety on its own (the label charset already can't trigger this rule),
# but it keeps every occurrence -- reference and definition alike -- a
# plain, literal `text` child, so no third reconstruction case is needed.
_MARKDOWN.block.ruler.disable("reference")

# A footnote label is restricted to a safe slug shape: letters, digits, and
# hyphens only. No character in this set can ever be a delimiter, opener,
# or closer for any active CommonMark inline/block rule in this module's
# `MarkdownIt("commonmark")` configuration -- see the module docstring's
# "Label charset" section for the full per-rule derivation. The lookahead after
# the closing "]" rejects a match immediately followed by another
# label-charset character (e.g. `[^label]abc`), requiring a clean boundary
# rather than silently truncating to a shorter label.
_FOOTNOTE_RE = re.compile(r"\[\^([A-Za-z0-9-]+)\](?![A-Za-z0-9-])(:)?")

# Matches a token's `.content` that is *exactly* `^label` -- used to recover
# the label from the closed-set `link_open` shape whose surrounding brackets
# were consumed by the inline `link` rule rather than left as literal text.
# Anchored on both ends, so this can never match anything but the whole
# content. Not used for `image` tokens -- see the module docstring's "Images
# are out of scope" section.
_BARE_LABEL_RE = re.compile(r"\A\^([A-Za-z0-9-]+)\Z")


@dataclass(frozen=True)
class FootnoteOccurrence:
    """One ``[^label]`` occurrence in a concept body.

    ``is_definition`` is True for a ``[^label]:`` definition line, False for
    a bare ``[^label]`` reference in prose (including the ``[^label](dest)``
    reference shape, which can never be a definition -- see the module
    docstring). An ``![^label](url)`` image-style occurrence is never
    produced -- images are out of scope entirely, see the module docstring's
    "Images are out of scope" section.
    """

    label: str
    line: int | None
    is_definition: bool


def extract_footnote_occurrences(body: str) -> tuple[FootnoteOccurrence, ...]:
    """Extract every ``[^label]`` reference and ``[^label]:`` definition in *body*.

    A label is recognized only when it matches the restricted slug charset
    ``[A-Za-z0-9-]+`` with a clean boundary after the closing ``]`` -- see
    the module docstring's "Narrowed contract" section. Matches inside a
    code span or fenced/indented code block are never seen in the first
    place (this scan only visits ``text``-type inline children; a
    ``code_inline`` token is never one, and code blocks are never passed
    through the inline tokenizer at all), so no separate exclusion step is
    needed.
    """
    occurrences: list[FootnoteOccurrence] = []
    for token in _MARKDOWN.parse(body):
        if token.type == "inline" and token.children and token.map:
            occurrences.extend(_scan_inline_children(token.children, token.map[0] + 1))
    return tuple(occurrences)


def _scan_inline_children(
    children: Sequence[Any], start_line: int
) -> list[FootnoteOccurrence]:
    """Walk one paragraph's (or heading's, etc.) inline children in order.

    ``text``-type children are regex-scanned directly. ``link_open`` is
    checked against the closed-set ``[^label](dest)`` reconstruction shape
    (see module docstring). Every other token type -- notably
    ``code_inline``, which is not visited by any branch here, and ``image``,
    which is deliberately never inspected at all (see the module docstring's
    "Images are out of scope" section) -- is passed over without inspection;
    for ``code_inline`` that omission *is* the code-span exclusion, not a gap
    in one.
    """
    occurrences: list[FootnoteOccurrence] = []
    line = start_line
    for index, child in enumerate(children):
        child_type = getattr(child, "type", None)
        if child_type == "text":
            content = child.content or ""
            for match in _FOOTNOTE_RE.finditer(content):
                occurrences.append(
                    FootnoteOccurrence(
                        label=match.group(1),
                        line=line + content.count("\n", 0, match.start()),
                        is_definition=match.group(2) is not None,
                    )
                )
            line += content.count("\n")
        elif child_type in ("softbreak", "hardbreak"):
            line += 1
        elif child_type == "link_open":
            label = _bracket_reconstructed_label(children, index)
            if label is not None:
                occurrences.append(
                    FootnoteOccurrence(label=label, line=line, is_definition=False)
                )
        # `image` is deliberately not matched here -- see the module
        # docstring's "Images are out of scope" section. An image's alt text
        # is never inspected for footnote syntax, exact-match or otherwise.
    return occurrences


def _bracket_reconstructed_label(
    children: Sequence[Any], open_index: int
) -> str | None:
    """Recover a ``[^label](dest)``-shaped label from a ``link_open`` triple.

    Only accepts the shape ``link_open``, then exactly one ``text`` child
    whose content fully matches ``^label``, then ``link_close`` -- an
    ordinary link whose visible text is unrelated to a footnote label (the
    common case) never matches ``_BARE_LABEL_RE`` and is correctly left
    alone.
    """
    if open_index + 2 >= len(children):
        return None
    label_token = children[open_index + 1]
    close_token = children[open_index + 2]
    if getattr(label_token, "type", None) != "text":
        return None
    if getattr(close_token, "type", None) != "link_close":
        return None
    match = _BARE_LABEL_RE.match(getattr(label_token, "content", "") or "")
    return match.group(1) if match is not None else None


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

    # `source_ids` is a tuple to preserve declaration order for the
    # unreferenced-source warning loop below; `source_id_set` is a plain set
    # so the occurrences loop's membership check is O(1) instead of O(sources).
    source_id_set = set(source_ids)

    referenced: set[str] = set()
    findings: list[ValidationFinding] = []
    for occurrence in occurrences:
        referenced.add(occurrence.label)
        if occurrence.label not in source_id_set:
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

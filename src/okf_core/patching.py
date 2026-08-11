"""Format-specific document planning: Markdown sections, links, frontmatter."""

from __future__ import annotations

import io
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlsplit, urlunsplit

import yaml
from markdown_it import MarkdownIt
from ruamel.yaml import YAML
from ruamel.yaml import YAMLError as RuamelYAMLError
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from yaml.nodes import MappingNode

from okf_core.change_envelope import (
    DocumentChangeApplyError,
    DocumentChangeConflictError,
    DocumentChangeError,
    DocumentChangePlan,
    DocumentChangePlanningError,
    DocumentChangeResult,
    DocumentChangeSafetyError,
    FileMoveConflictError,
    FileMovePlan,
    FileMoveResult,
    _plan_document_change,
    apply_document_change,
    apply_file_move,
    plan_document_change,
    plan_document_change_from_reader,
    plan_file_move,
)
from okf_core.config import BundleConfig
from okf_core.documents import (
    DocumentParseError,
    _split_frontmatter,
    parse_concept_document,
)

if TYPE_CHECKING:
    from markdown_it.rules_core.state_core import StateCore
    from markdown_it.rules_inline.state_inline import StateInline
    from markdown_it.token import Token

__all__ = [
    "DocumentChangeApplyError",
    "DocumentChangeConflictError",
    "DocumentChangeError",
    "DocumentChangePlan",
    "DocumentChangePlanningError",
    "DocumentChangeResult",
    "DocumentChangeSafetyError",
    "FileMoveConflictError",
    "FileMovePlan",
    "FileMoveResult",
    "LinkRewrite",
    "apply_document_change",
    "apply_file_move",
    "link_target_for_new_location",
    "plan_document_change",
    "plan_document_change_from_reader",
    "plan_file_move",
    "plan_frontmatter_merge",
    "plan_markdown_link_rewrite",
    "plan_markdown_section_patch",
    "plan_source_upsert",
    "plan_stamp_generated",
    "plan_stamp_stale_after",
    "plan_stamp_status",
    "plan_stamp_verified",
    "source_upsert",
    "stamp_generated",
    "stamp_stale_after",
    "stamp_status",
    "stamp_verified",
]

_MARKDOWN = MarkdownIt("commonmark")

# `freezegun.freeze_time` finds every module-level attribute anywhere in
# `sys.modules` that is (by identity) the real `datetime.datetime` class and
# rebinds *that attribute* to its `FakeDatetime` subclass for the freeze's
# duration -- not just names literally called `datetime`, and not just this
# module. A plain `_REAL_DATETIME_TYPE = datetime` module global would be
# found and patched the same as the `datetime` import itself, defeating the
# point. Wrapping it in a 1-tuple hides it from that scan: freezegun checks
# `attribute is real_datetime`, and the tuple *object* is never `is` the
# class it merely contains, so `_REAL_DATETIME_TYPE[0]` stays the genuine
# class through any freeze -- confirmed empirically, not merely by reading
# freezegun's source. This is what lets `_validate_datetime_value` construct
# a real `datetime.datetime` (matching what `_SUPPORTED_FRONTMATTER_SCALAR_TYPES`,
# built the same way at the same time, actually contains) even when called
# from inside a freeze.
_REAL_DATETIME_TYPE = (datetime,)

# Same freezegun module-attribute scan as `_REAL_DATETIME_TYPE` above, and
# the same 1-tuple-hiding trick to survive it: `freeze_time` also finds and
# rebinds every module-level attribute that is (by identity) the real
# `datetime.date` class to `FakeDate` for the freeze's duration, so a plain
# `_REAL_DATE_TYPE = date` module global would be patched exactly like the
# `date` import itself. Confirmed empirically (not merely by reading
# freezegun's source) that `date.fromisoformat` returns a `FakeDate`
# instance while a freeze is active, and that `_REAL_DATE_TYPE[0]` stays the
# genuine class throughout. `datetime.datetime` is itself a `date`
# subclass, so `_REAL_DATETIME_TYPE[0]` also satisfies
# `isinstance(x, _REAL_DATE_TYPE[0])` for real or fake datetimes alike --
# callers that need to tell a datetime apart from a plain date must check
# `_REAL_DATETIME_TYPE[0]` first, as `_validate_date_value` does.
_REAL_DATE_TYPE = (date,)


def _as_real_datetime(value: datetime) -> datetime:
    """Rebuild ``value`` as a genuine ``datetime.datetime``, discarding any
    subclass-ness (notably freezegun's ``FakeDatetime``).

    Returns ``value`` itself, unchanged, when it is already the exact real
    type -- rebuilding is only needed to strip a subclass, and reuses the
    exact rebuild ``_validate_datetime_value`` has always performed inline;
    shared here so ``_yaml_values_equal``'s no-op comparison (AC3) can apply
    the identical normalization to its "current" operand, not just the
    "candidate" operand callers pass through ``_validate_datetime_value``.
    """
    if type(value) is _REAL_DATETIME_TYPE[0]:
        return value
    return _REAL_DATETIME_TYPE[0](
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        tzinfo=value.tzinfo,
    )


def _as_real_date(value: date) -> date:
    """``date`` counterpart of ``_as_real_datetime`` -- see its docstring."""
    if type(value) is _REAL_DATE_TYPE[0]:
        return value
    return _REAL_DATE_TYPE[0](value.year, value.month, value.day)


def _normalize_temporal_for_comparison(value: Any) -> Any:
    """Rebuild ``value`` as a genuine ``datetime``/``date`` if it is a
    subclass of either (notably freezegun's ``FakeDatetime``/``FakeDate``),
    otherwise return it unchanged.

    Used by ``_yaml_values_equal`` to normalize *both* operands of a no-op
    comparison the same way ``_validate_datetime_value``/
    ``_validate_date_value`` already normalize a stamping candidate --
    without this, a "current" value parsed from the document by PyYAML
    (whose ``construct_yaml_timestamp`` also calls ``datetime.datetime(...)``
    dynamically, so it is likewise a ``FakeDatetime`` under
    ``freeze_time``) would never compare exact-type-equal to an
    already-normalized candidate while a freeze is active, breaking AC3's
    no-op detection. Checks ``datetime`` before ``date`` since
    ``datetime.datetime`` is itself a ``date`` subclass.
    """
    if isinstance(value, _REAL_DATETIME_TYPE[0]):
        return _as_real_datetime(value)
    if isinstance(value, _REAL_DATE_TYPE[0]):
        return _as_real_date(value)
    return value


def _make_frontmatter_yaml() -> YAML:
    """Build a fresh round-trip YAML instance for one frontmatter operation.

    A new instance is constructed on every call rather than shared, because
    ``ruamel.yaml``'s ``YAML.dump``/``dump_all`` stash internal state on the
    instance for the duration of a call and only clear it on the success
    path: a failed dump on one document would otherwise leave a shared
    instance poisoned for the next, unrelated document's merge. Per-call
    construction cost is negligible for file-at-a-time frontmatter
    operations (see the #117 spike).

    Round-trip mode keeps comments, key order, and anchors/aliases on any
    key a merge does not touch; ADR-0002 documents the resulting canonical
    form. Per-node flow/block style hints the loader also preserves are
    deliberately cleared before dump (see `_normalize_container_style`) so
    an untouched flow-style collection still converges to block form on the
    document's first edit, rather than surviving indefinitely -- forcing
    `default_flow_style=False` here alone does not achieve this, since a
    loaded node's own preserved style hint takes priority over the
    dumper-level default (confirmed empirically). `preserve_quotes` keeps
    original quote style on untouched scalars; `width` is set high so long
    scalars are not hard-wrapped mid-value.
    """
    instance = YAML(typ="rt")
    instance.preserve_quotes = True
    instance.width = 10_000
    return instance


def plan_markdown_section_patch(
    bundle: BundleConfig,
    path: Path | str,
    heading: str,
    body: str,
    *,
    level: int = 1,
) -> DocumentChangePlan:
    """Plan replacement or insertion of one named CommonMark section.

    A section is identified by exact, case-sensitive parsed heading content and
    heading level. Existing ATX and Setext headings are supported. The heading
    itself is preserved when replacing a section; an absent section is appended
    using ATX syntax.
    """

    return _plan_document_change(
        bundle,
        Path(path),
        lambda resolved_path, original_content: _patch_markdown_section(
            resolved_path,
            original_content,
            heading,
            body,
            level,
        ),
    )


@dataclass(frozen=True)
class LinkRewrite:
    """A requested target/destination rewrite for inline Markdown links."""

    old_target: str
    new_target: str


def _normalize_target(target: str) -> str:
    """Normalize a caller-supplied target the same way markdown-it-py normalizes hrefs.

    Using the library's own normalizer (rather than a hand-rolled %20-only
    substitution) keeps comparisons consistent for every kind of escaping
    markdown-it-py applies to a real link destination: spaces, non-ASCII
    characters, and already-percent-encoded sequences all round-trip through
    the same function, so callers can pass the plain-text target regardless
    of how it happens to appear in the source file.
    """

    return _MARKDOWN.normalizeLink(target)


@dataclass(frozen=True)
class _LinkOccurrence:
    """The exact source span of one real inline link's destination."""

    dest_start: int
    dest_end: int
    href: str


_LINK_OCCURRENCES_ENV_KEY = "_okf_link_rewrite_occurrences"
_BLOCK_OFFSET_ENV_KEY = "_okf_link_rewrite_block_offset"


def _locate_link_destination(
    state: StateInline, label_start: int
) -> tuple[int, int] | None:
    """Re-derive a just-parsed link's destination span using the parser's own helpers.

    markdown-it-py's inline tokens carry no character offsets, so this replays
    the label/destination lookup with the same ``state.md.helpers`` functions
    the "link" rule itself used a moment earlier, instead of re-scanning the
    raw text with a regex. Returns None for reference-style links, which have
    no literal destination text in the body to rewrite.
    """

    label_end = state.md.helpers.parseLinkLabel(state, label_start, True)
    if label_end < 0:
        return None
    pos = label_end + 1
    maximum = state.posMax
    if pos >= maximum or state.src[pos] != "(":
        return None
    pos += 1
    while pos < maximum and state.src[pos] in ("\t", " ", "\n"):
        pos += 1
    dest_start = pos
    result = state.md.helpers.parseLinkDestination(state.src, pos, maximum)
    if result.ok:
        return dest_start, result.pos
    if pos < maximum and state.src[pos] == ")":
        # Empty destination, e.g. `[label]()`: parseLinkDestination reports
        # this as unmatched, but the "link" rule itself still treats it as a
        # real link with an empty href, so record a zero-length span here too.
        return dest_start, dest_start
    return None


def _find_block_offset(src: str, token: Token) -> int | None:
    """Locate one block-level "inline" token's content within the whole source.

    markdown-it-py parses each block's inline content as an independent
    string, so positions captured while parsing it are relative to that
    block, not the document. `.map` gives the block's original line range, so
    its content can be found unambiguously within that range and used as a
    baseline to translate block-relative offsets back to document offsets.
    """

    if token.map is None:
        return None
    line_offsets = _line_offsets(src)
    start_line, end_line = token.map
    if not (0 <= start_line < len(line_offsets)) or not (
        0 <= end_line < len(line_offsets)
    ):
        return None
    found = src.find(token.content, line_offsets[start_line], line_offsets[end_line])
    return found if found >= 0 else None


def _instrumented_core_inline_rule(state: StateCore) -> None:
    """Wrap the core "inline" rule to record each block's absolute start offset.

    This mirrors the library's own rule (see markdown_it.rules_core.inline)
    exactly, adding only the offset bookkeeping `_instrumented_link_rule`
    needs to translate its block-relative spans into document-relative ones.
    """

    for token in state.tokens:
        if token.type != "inline":
            continue
        if token.children is None:
            token.children = []
        state.env[_BLOCK_OFFSET_ENV_KEY] = _find_block_offset(state.src, token)
        state.md.inline.parse(token.content, state.md, state.env, token.children)
    state.env.pop(_BLOCK_OFFSET_ENV_KEY, None)


def _instrumented_link_rule(state: StateInline, silent: bool) -> bool:
    """Wrap the "link" rule to record each real link's destination span as it parses.

    This is a pure observer: it defers entirely to the library's own rule for
    matching and token creation, and only inspects the outcome afterward, so
    it cannot change what the parser recognizes as a link.
    """

    label_start = state.pos
    tokens_before = len(state.tokens)
    assert _link_inline_rule is not None
    matched = _link_inline_rule(state, silent)
    if matched and not silent and len(state.tokens) > tokens_before:
        # state.push() flushes any pending plain text into its own token first,
        # so the link_open this call produced isn't necessarily at tokens_before.
        token = next(
            (t for t in state.tokens[tokens_before:] if t.type == "link_open"), None
        )
        if token is not None:
            href = token.attrGet("href")
            location = _locate_link_destination(state, label_start)
            block_offset = state.env.get(_BLOCK_OFFSET_ENV_KEY)
            if (
                location is not None
                and isinstance(href, str)
                and isinstance(block_offset, int)
            ):
                dest_start, dest_end = location
                state.env.setdefault(_LINK_OCCURRENCES_ENV_KEY, []).append(
                    _LinkOccurrence(
                        dest_start=block_offset + dest_start,
                        dest_end=block_offset + dest_end,
                        href=href,
                    )
                )
    return matched


_link_inline_rule: Callable[[StateInline, bool], bool] | None = None
_LINK_REWRITE_MARKDOWN: MarkdownIt | None = None


def _get_link_rewrite_markdown(resolved_path: Path) -> MarkdownIt:
    """Build (once) the MarkdownIt instance instrumented for link-span capture.

    The instrumentation wraps markdown-it-py's own "link" inline rule and
    "inline" core rule -- undocumented implementation details, not public API.
    Importing them lazily, only when link rewriting is actually requested,
    keeps a hypothetical upstream internal refactor from breaking every
    consumer of this module instead of just this one primitive.
    """

    global _link_inline_rule, _LINK_REWRITE_MARKDOWN
    if _LINK_REWRITE_MARKDOWN is None:
        try:
            from markdown_it.rules_inline import link as link_rule
        except ImportError as exc:
            raise DocumentChangePlanningError(
                resolved_path,
                "plan_markdown_link_rewrite requires markdown-it-py internals "
                f"that are unavailable in this installed version: {exc}",
            ) from exc
        _link_inline_rule = link_rule
        md = MarkdownIt("commonmark")
        md.core.ruler.at("inline", _instrumented_core_inline_rule)
        md.inline.ruler.at("link", _instrumented_link_rule)
        _LINK_REWRITE_MARKDOWN = md
    return _LINK_REWRITE_MARKDOWN


def _format_destination(new_target: str, *, wrap: bool) -> str:
    if not wrap:
        return new_target
    escaped = new_target.replace("<", "\\<").replace(">", "\\>")
    return f"<{escaped}>"


def _validate_link_rewrites(resolved_path: Path, rewrites: Any) -> None:
    if not isinstance(rewrites, (list, tuple, Sequence)) or isinstance(
        rewrites, (str, bytes)
    ):
        raise DocumentChangePlanningError(
            resolved_path,
            "rewrites must be a sequence of LinkRewrite objects",
        )

    for i, r in enumerate(rewrites):
        if not isinstance(r, LinkRewrite):
            raise DocumentChangePlanningError(
                resolved_path,
                f"Element at index {i} in rewrites is not a LinkRewrite object: {r!r}",
            )
        if not isinstance(getattr(r, "old_target", None), str) or not isinstance(
            getattr(r, "new_target", None), str
        ):
            raise DocumentChangePlanningError(
                resolved_path,
                f"LinkRewrite at index {i} must have string old_target and new_target: {r!r}",
            )

    old_targets_normalized = [_normalize_target(r.old_target) for r in rewrites]
    if len(old_targets_normalized) != len(set(old_targets_normalized)):
        raise DocumentChangePlanningError(
            resolved_path,
            "Duplicate old_target values in rewrites list",
        )


def plan_markdown_link_rewrite(
    bundle: BundleConfig,
    path: Path | str,
    rewrites: Sequence[LinkRewrite],
) -> DocumentChangePlan:
    """Plan rewriting target destinations of one or more inline Markdown links.

    Rewrites match real inline links only: a target is located by parsing the
    document and comparing each link's resolved href against a caller-supplied
    target, normalized the same way markdown-it-py normalizes hrefs (so %20,
    other percent-encoding, and non-ASCII characters all compare consistently
    regardless of how they appear in the source). Text that merely looks like
    a link but isn't one to the parser - inside code spans or fenced code
    blocks, or in frontmatter - is never touched. Duplicate old_target entries
    (after normalization) are rejected. Reference-style links are not
    supported and cause planning to raise DocumentChangePlanningError.
    """
    resolved_path = Path(path)
    _validate_link_rewrites(resolved_path, rewrites)

    def rewrite_links(resolved_path: Path, original_content: str) -> str:
        if not rewrites:
            return original_content

        try:
            document = parse_concept_document(original_content)
        except DocumentParseError as exc:
            raise DocumentChangePlanningError(
                resolved_path,
                f"Could not parse document frontmatter: {exc}",
            ) from exc

        body = document.body
        body_offset = len(original_content) - len(body)
        frontmatter_content = original_content[:body_offset]

        # markdown-it-py normalizes CRLF/CR to LF internally before parsing, so
        # parser-derived offsets are only valid against an equally-normalized
        # copy of the body. The original line-ending style is restored below.
        line_ending = _first_line_ending(body)
        normalized_body = _normalize_line_endings(body)

        env: dict[str, Any] = {}
        _get_link_rewrite_markdown(resolved_path).parse(normalized_body, env)
        if env.get("references"):
            raise DocumentChangePlanningError(
                resolved_path,
                "Reference-style links are not supported by the link rewriter.",
            )

        occurrences_by_href: dict[str, list[_LinkOccurrence]] = {}
        for occurrence in env.get(_LINK_OCCURRENCES_ENV_KEY, []):
            occurrences_by_href.setdefault(occurrence.href, []).append(occurrence)

        all_matches: list[tuple[_LinkOccurrence, LinkRewrite]] = [
            (occurrence, r)
            for r in rewrites
            for occurrence in occurrences_by_href.get(
                _normalize_target(r.old_target), []
            )
        ]

        # Sort matches in reverse order of their start positions to prevent offset drift
        all_matches.sort(key=lambda item: item[0].dest_start, reverse=True)

        patched_body = normalized_body
        for occurrence, r in all_matches:
            wrap = patched_body[occurrence.dest_start] == "<" or (
                " " in r.new_target or "(" in r.new_target or ")" in r.new_target
            )
            new_target_str = _format_destination(r.new_target, wrap=wrap)
            patched_body = (
                patched_body[: occurrence.dest_start]
                + new_target_str
                + patched_body[occurrence.dest_end :]
            )

        if line_ending != "\n":
            patched_body = patched_body.replace("\n", line_ending)

        return frontmatter_content + patched_body

    return _plan_document_change(
        bundle,
        Path(path),
        rewrite_links,
    )


def link_target_for_new_location(
    bundle: BundleConfig,
    *,
    original_target: str,
    source_path: Path,
    new_target_path: Path,
) -> str:
    """Recompute a Markdown link href after its target concept file has moved.

    Preserves the '#fragment'/'?query' suffix and the absolute-vs-relative
    style of original_target exactly (a target already written in
    bundle-root-anchored '/...' form stays in that form; anything else is
    rewritten as a path relative to source_path's own directory, using POSIX
    '/' separators and '../' as needed for sibling/cousin directories). Only
    the path portion is recalculated. The recomputed path is percent-encoded
    (leaving '/' as a literal separator) so a target containing a space or
    other special character comes out in the same normalized form markdown-it
    already emits for such hrefs, rather than an unencoded path that CommonMark
    would require wrapping in '<...>'.
    """

    parsed = urlsplit(original_target)
    bundle_root = bundle.bundle_root.resolve(strict=False)

    if parsed.path.startswith("/"):
        new_path = "/" + new_target_path.relative_to(bundle_root).as_posix()
    else:
        relative = os.path.relpath(new_target_path, source_path.parent)
        new_path = PurePosixPath(relative.replace("\\", "/")).as_posix()

    return urlunsplit(
        ("", "", quote(new_path, safe="/"), parsed.query, parsed.fragment)
    )


def plan_frontmatter_merge(
    bundle: BundleConfig,
    path: Path | str,
    updates: Mapping[str, Any],
) -> DocumentChangePlan:
    """Plan a shallow merge of top-level frontmatter fields into canonical form.

    Frontmatter is parsed with a round-trip YAML loader, mutated in place
    (targeted keys are replaced, missing keys are appended in update order),
    and re-serialized in `okf-core`'s documented canonical form (ADR-0002):
    key order and comments on untouched keys are preserved, the *whole*
    document (including untouched sibling collections, not just targeted
    keys) is emitted in block style with LF line endings regardless of the
    source document's style, and quote style is not guaranteed to survive
    on a touched value. A non-canonical but conformant input converges to
    canonical form on its first edit; this may produce one-time formatting
    churn in that edit's diff, which is expected rather than a defect. A
    merge that resolves to a no-op never rewrites the file, so an untouched
    document's formatting is unaffected until an edit actually happens.

    Update values may contain plain YAML-oriented scalars, dates, datetimes,
    lists, and string-keyed dictionaries. An update whose value already
    equals the current one (by data model, not by source bytes) is a no-op.
    Untargeted YAML aliases are preserved; fields participating in alias
    relationships cannot be changed safely and are rejected.
    """

    return _plan_document_change(
        bundle,
        Path(path),
        lambda resolved_path, original_content: _merge_frontmatter(
            resolved_path,
            original_content,
            updates,
        ),
    )


def plan_source_upsert(
    bundle: BundleConfig,
    path: Path | str,
    source: Mapping[str, Any],
) -> DocumentChangePlan:
    """Plan adding one OKF v0.2 §5.1 ``sources`` provenance entry.

    ``source`` is validated up front -- before the target is even read --
    the same way each existing ``sources`` entry is: it must be a mapping
    with a non-empty string ``resource`` (§5.1's only required field), and
    an ``id``, when present, must be a non-empty string (it doubles as a
    Markdown footnote label, §5.1's "Per-claim attribution"). Every other
    key (``title`` and the credibility signals ``author``, ``usage_count``,
    ``last_modified``, plus a per-entry ``usage_window`` override) is
    optional and passed through untouched -- this operation does not
    interpret or validate them beyond the base mapping/``resource`` shape.

    The document's existing ``sources`` list (created fresh, an empty list,
    when the key is absent) is validated the same way: not a list, or any
    entry failing the mapping/``resource``/``id`` shape check, raises
    ``DocumentChangePlanningError`` before any write (AC7). Identity is
    ``id``, falling back to ``resource`` (R-B3): when an existing entry
    already carries ``source``'s identity, this plans a semantic no-op
    (AC5) -- the existing entry is left completely untouched, byte-for-byte,
    in its original position, rather than having fields merged into it,
    which is what AC4 ("existing entries retain their content and order")
    requires. A genuinely new identity is appended to the end of the list.
    Any other top-level frontmatter key, including the shared
    ``usage_window`` sibling described in §5.1, is left untouched (AC6);
    this operation never reads or writes it.

    The write itself -- only reached when an entry is actually appended --
    delegates to ``_merge_frontmatter``, the same canonical frontmatter
    engine ``plan_frontmatter_merge`` (#195) uses, applied to the *same*
    read this function performs via ``plan_document_change_from_reader``.
    Deriving the updated ``sources`` list from a separate, earlier read
    (rather than the content that read hashes as the plan's baseline) would
    let a concurrent edit between the two reads go undetected -- exactly
    the risk ``plan_document_change_from_reader`` itself documents -- so
    this function reads the document exactly once.
    """
    resolved_path = Path(path)
    validated_source = _validate_source_entry(
        resolved_path, source, context="Candidate", strip_none_id=True
    )

    def build_source_update(resolved_path: Path, original_content: str) -> str:
        try:
            document = parse_concept_document(original_content)
        except DocumentParseError as exc:
            raise DocumentChangePlanningError(
                resolved_path, f"Could not parse document frontmatter: {exc}"
            ) from exc
        current_sources = _validate_existing_sources(
            resolved_path, document.frontmatter.get("sources", _MISSING)
        )
        new_sources, changed = _upsert_source_entry(current_sources, validated_source)
        if not changed:
            return original_content
        return _merge_frontmatter(
            resolved_path, original_content, {"sources": new_sources}
        )

    return plan_document_change_from_reader(bundle, resolved_path, build_source_update)


def source_upsert(
    bundle: BundleConfig,
    path: Path | str,
    source: Mapping[str, Any],
) -> DocumentChangeResult:
    """Plan and apply one ``sources`` entry upsert in a single call.

    Mirrors ``log_append``'s relationship to ``plan_log_append``:
    ``plan_source_upsert`` alone is read-only and safe for a dry-run
    preview, while this convenience wrapper also applies it, retaining the
    same SHA-256 optimistic-concurrency protection.
    """
    plan = plan_source_upsert(bundle, path, source)
    return apply_document_change(bundle, plan)


def _validate_source_entry(
    path: Path, source: Any, *, context: str, strip_none_id: bool = False
) -> Mapping[str, Any]:
    """Validate one ``sources`` entry has the §5.1 required/optional shape.

    Shared between the caller-supplied candidate entry and each entry read
    back from a document's existing ``sources`` list -- ``context``
    distinguishes the two in the raised message. §5.1 requires ``resource``
    to be a non-empty string; ``id``, when present, must also be a
    non-empty string, since it doubles as a Markdown footnote label.

    ``strip_none_id`` controls what happens to an explicit ``id: None``:
    when ``True`` (the ``plan_source_upsert`` candidate path only), it is
    treated the same as ``id``'s absence and stripped from the returned
    mapping, so a caller never gets a literal ``id: null`` written into
    frontmatter for the entry it is actively upserting, matching §5.1's
    "present with a value, or fully omitted" shape for the optional field.
    When ``False`` (the default, used for every pre-existing entry read
    back via ``_validate_existing_sources``), an ``id: None`` already
    present in the document is returned untouched -- normalizing
    pre-existing document state is out of scope for an upsert operation,
    and stripping it here would silently mutate an entry the caller never
    targeted. Every other key is optional and returned as-is, unvalidated:
    per AGENTS.md, this operation deliberately does not enforce a schema
    on ``title`` or the credibility signals beyond this base shape.
    """
    if not isinstance(source, Mapping):
        raise DocumentChangePlanningError(
            path, f"{context} sources entry must be a mapping: {source!r}"
        )
    resource = source.get("resource")
    if not isinstance(resource, str) or not resource:
        raise DocumentChangePlanningError(
            path,
            f"{context} sources entry must have a non-empty string "
            f"'resource': {source!r}",
        )
    entry_id = source.get("id")
    if entry_id is not None and (not isinstance(entry_id, str) or not entry_id):
        raise DocumentChangePlanningError(
            path,
            f"{context} sources entry 'id' must be a non-empty string when "
            f"present: {source!r}",
        )
    if strip_none_id and "id" in source and entry_id is None:
        return {key: value for key, value in source.items() if key != "id"}
    return source


def _validate_existing_sources(path: Path, sources: Any) -> list[Mapping[str, Any]]:
    """Validate a document's existing ``sources`` frontmatter value (AC7).

    An absent ``sources`` key (represented by the ``_MISSING`` sentinel)
    is treated as a conformant empty list -- AC2's "create when absent".
    Anything else that is not a list, or any entry failing
    ``_validate_source_entry``, raises ``DocumentChangePlanningError``
    before any write.
    """
    if sources is _MISSING:
        return []
    if not isinstance(sources, list):
        raise DocumentChangePlanningError(
            path, f"Existing 'sources' frontmatter must be a list, got {sources!r}"
        )
    return [
        _validate_source_entry(path, entry, context="Existing") for entry in sources
    ]


def _source_identity(entry: Mapping[str, Any]) -> str:
    """Resolve one ``sources`` entry's stable identity: ``id``, else ``resource``.

    Both are already validated (by ``_validate_source_entry``) to be
    non-empty strings when used this way, so the result is always a
    non-empty string.
    """
    entry_id = entry.get("id")
    return cast(str, entry_id if entry_id else entry.get("resource"))


def _upsert_source_entry(
    current: Sequence[Mapping[str, Any]],
    candidate: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], bool]:
    """Insert ``candidate`` into ``current`` unless already represented.

    Pure per-item helper (AGENTS.md's collector-loop-delegates-per-item
    convention): given the current ``sources`` list and one candidate
    entry, decides whether an existing entry already represents the same
    source by stable identity (``id``, falling back to ``resource``). On a
    match, ``current`` is returned completely untouched -- same entries,
    same content, same order -- rather than merging fields into the match;
    this is what makes AC5 ("already-represented source is a semantic
    no-op") and AC4 ("existing entries retain their content and order")
    hold simultaneously. On no match, ``candidate`` is appended to the end.
    """
    candidate_identity = _source_identity(candidate)
    if any(_source_identity(entry) == candidate_identity for entry in current):
        return list(current), False
    return [*current, candidate], True


# ---------------------------------------------------------------------------
# Trust and lifecycle stamping (OKF v0.2 §5.2 "generated"/"verified", §5.4
# "status", §5.5 "stale_after"; #196)
# ---------------------------------------------------------------------------

_ACTOR_PATTERN = re.compile(r"(?:[^\s/:]+/[^\s/:]+)|(?:human:\S+)|(?:process:\S+)")

_VALID_STATUS_VALUES = ("draft", "stable", "deprecated")


def _validate_actor_string(path: Path, value: Any, *, field_name: str) -> str:
    """Validate ``value`` as one OKF v0.2 §7 actor string.

    Accepts exactly the three shapes §7 defines, matched against the whole
    string (no partial match): ``<producer>/<version>`` (agents and tools,
    e.g. ``reference_agent/gemini-2.5-pro``), ``human:<id>``, or
    ``process:<id>``. Each segment/id must be non-empty and free of
    whitespace; the ``<producer>/<version>`` shape additionally forbids a
    second ``/`` or a ``:`` in either segment, so it can never be confused
    with the ``human:``/``process:`` prefixed shapes. Anything else --
    wrong shape, an empty segment, embedded whitespace, or a non-``str``
    value entirely -- raises ``DocumentChangePlanningError`` before any
    write, naming ``field_name`` (e.g. ``"generated.by"``) so a caller with
    several actor-shaped fields in flight can tell which one failed.
    """
    if isinstance(value, str) and _ACTOR_PATTERN.fullmatch(value):
        return value
    raise DocumentChangePlanningError(
        path,
        f"{field_name} must be an actor string in the form "
        "'<producer>/<version>', 'human:<id>', or 'process:<id>' "
        f"(OKF v0.2 §7): {value!r}",
    )


def _parse_iso_datetime_string(text: str) -> datetime:
    """Parse an ISO 8601 datetime string, tolerating a trailing ``Z``.

    ``datetime.fromisoformat`` only accepts a bare ``Z`` UTC designator from
    Python 3.11 onward; okf-core supports runtime Python 3.10+ (AGENTS.md),
    and spec §5.2's own example (``2026-06-20T22:53:05Z``) uses exactly that
    form, so the ``Z`` is rewritten to the ``+00:00`` offset
    ``fromisoformat`` has always accepted before parsing. Raises
    ``ValueError`` -- the same as ``fromisoformat`` itself -- for anything
    that still isn't parseable after that rewrite.

    Parses via ``_REAL_DATETIME_TYPE`` rather than the module-global
    ``datetime`` name so the result is always a genuine ``datetime.datetime``
    even when called from inside a ``freezegun.freeze_time`` context, which
    rebinds that name to ``FakeDatetime`` -- see ``_REAL_DATETIME_TYPE``'s
    own comment.
    """
    candidate = text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text
    return _REAL_DATETIME_TYPE[0].fromisoformat(candidate)


def _validate_datetime_value(path: Path, value: Any, *, field_name: str) -> datetime:
    """Validate/coerce ``value`` into a timezone-aware ``datetime`` for a §5.2 ``at`` field.

    Accepts either a native ``datetime.datetime`` or an ISO 8601 string (see
    ``_parse_iso_datetime_string``); anything else -- including a bare
    ``datetime.date``, which is a different, day-only shape -- raises
    ``DocumentChangePlanningError`` before any write, as does a string that
    fails to parse.

    The result must carry a UTC offset. This is required, not merely
    preferred, for two reasons: spec §5.2's own examples always carry one
    (the trailing ``Z``), and an offset-naive value is never equal (by
    Python's own ``==``) to an offset-aware one representing the same wall
    clock time -- the shape every value validated here produces -- so
    without this requirement, a caller re-stamping with an equivalent naive
    value would never be recognized as a no-op (AC3). A naive value,
    whether passed directly or parsed from a string with no offset, is
    rejected here rather than silently assumed to be UTC.

    A ``datetime.datetime`` *subclass* instance (notably ``freezegun``'s
    ``FakeDatetime`` -- the documented way to test ``plan_stamp_generated``/
    ``plan_stamp_verified``'s "defaults to real UTC now" behavior
    deterministically) is rebuilt as a genuine ``datetime.datetime`` here,
    via ``_REAL_DATETIME_TYPE`` rather than the module-global ``datetime``
    name (which a ``freeze_time`` context rebinds to ``FakeDatetime`` for
    its duration, making a same-module ``type(value) is datetime`` check
    unreliable -- see ``_REAL_DATETIME_TYPE``'s own comment).
    ``_merge_frontmatter``'s value validator deliberately checks
    ``type(value)`` against an exact set of allowed types rather than
    ``isinstance`` -- that strictness is what lets it also tell ``bool``
    apart from ``int`` (``bool`` is an ``int`` subclass) -- so an
    unconverted subclass instance would otherwise be rejected downstream as
    an unsupported type, not because its value is wrong.
    """
    if isinstance(value, _REAL_DATETIME_TYPE[0]):
        candidate = _as_real_datetime(value)
    elif isinstance(value, str):
        try:
            candidate = _parse_iso_datetime_string(value)
        except ValueError as exc:
            raise DocumentChangePlanningError(
                path,
                f"{field_name} must be an ISO 8601 datetime: {value!r} ({exc})",
            ) from exc
    else:
        raise DocumentChangePlanningError(
            path,
            f"{field_name} must be a datetime.datetime or an ISO 8601 "
            f"datetime string: {value!r}",
        )
    if candidate.tzinfo is None:
        raise DocumentChangePlanningError(
            path,
            f"{field_name} must include a UTC offset (e.g. a trailing "
            f"'Z'): {value!r}",
        )
    return candidate


def _validate_date_value(path: Path, value: Any, *, field_name: str) -> date:
    """Validate/coerce ``value`` into a ``datetime.date`` for a §5.5 ``stale_after`` field.

    Accepts either a native ``datetime.date`` or an ISO 8601 ``YYYY-MM-DD``
    string; a ``datetime.datetime`` is rejected -- §5.5 defines
    ``stale_after`` as "an absolute date", not a timestamp, so accepting one
    would silently drop its time-of-day component. Anything else, or a
    string that fails to parse as a calendar date, raises
    ``DocumentChangePlanningError`` before any write.

    Checks and parses via ``_REAL_DATETIME_TYPE``/``_REAL_DATE_TYPE`` rather
    than the module-global ``datetime``/``date`` names, mirroring
    ``_validate_datetime_value`` (see ``_REAL_DATETIME_TYPE``'s own
    comment). This matters under a ``freeze_time`` context, which rebinds
    both module-global names to ``FakeDatetime``/``FakeDate`` for the
    freeze's duration: ``date.fromisoformat`` would hand back a
    ``FakeDate`` instance that fails ``_merge_frontmatter``'s exact-
    ``type()`` scalar check downstream -- confirmed empirically, not
    merely by reading freezegun's source. A ``datetime.date`` *subclass*
    instance (``FakeDate``) is rebuilt as a genuine ``datetime.date`` via
    ``_as_real_date``, the same way ``_validate_datetime_value`` rebuilds a
    ``datetime`` subclass via ``_as_real_datetime``, for the same reason:
    ``_merge_frontmatter``'s value validator checks ``type(value)``
    against an exact set of allowed types, not ``isinstance``.
    """
    if isinstance(value, _REAL_DATETIME_TYPE[0]):
        raise DocumentChangePlanningError(
            path,
            f"{field_name} must be a calendar date (YYYY-MM-DD), not a "
            f"datetime: {value!r}",
        )
    if isinstance(value, _REAL_DATE_TYPE[0]):
        return _as_real_date(value)
    if isinstance(value, str):
        try:
            return _REAL_DATE_TYPE[0].fromisoformat(value)
        except ValueError as exc:
            raise DocumentChangePlanningError(
                path,
                f"{field_name} must be an ISO 8601 date (YYYY-MM-DD): "
                f"{value!r} ({exc})",
            ) from exc
    raise DocumentChangePlanningError(
        path,
        f"{field_name} must be a datetime.date or an ISO 8601 date "
        f"string: {value!r}",
    )


def plan_stamp_generated(
    bundle: BundleConfig,
    path: Path | str,
    by: str,
    *,
    at: datetime | str | None = None,
) -> DocumentChangePlan:
    """Plan setting the top-level ``generated`` trust record (OKF v0.2 §5.2).

    ``by`` (an actor, §7) and ``at`` (an ISO 8601 datetime) are validated
    before any file is touched -- see ``_validate_actor_string`` and
    ``_validate_datetime_value``. ``at`` defaults to the real current UTC
    datetime when omitted; pass a fixed value for deterministic tests (the
    same pattern ``plan_log_append``'s ``date`` parameter uses).

    The write delegates entirely to ``plan_frontmatter_merge``: no-op
    semantics (AC3 -- setting ``generated`` to a value that already matches
    the document's current value writes nothing) come from that engine's
    existing value-equality check, not from any pre-read this function
    performs. A malformed pre-existing ``generated`` value in the document
    is not separately validated or rejected here; ``generated`` is fully
    replaced on every stamp (unlike ``verified``'s append-only history), so
    an existing malformed value is simply treated as unequal and overwritten
    with the freshly validated one, rather than blocking the operation.
    """
    resolved_path = Path(path)
    validated_by = _validate_actor_string(resolved_path, by, field_name="generated.by")
    validated_at = _validate_datetime_value(
        resolved_path,
        at if at is not None else datetime.now(timezone.utc),
        field_name="generated.at",
    )
    return plan_frontmatter_merge(
        bundle,
        resolved_path,
        {"generated": {"by": validated_by, "at": validated_at}},
    )


def stamp_generated(
    bundle: BundleConfig,
    path: Path | str,
    by: str,
    *,
    at: datetime | str | None = None,
) -> DocumentChangeResult:
    """Plan and apply one ``generated`` trust-record stamp in a single call.

    Mirrors ``source_upsert``'s relationship to ``plan_source_upsert``:
    ``plan_stamp_generated`` alone is read-only and safe for a dry-run
    preview, while this convenience wrapper also applies it, retaining the
    same SHA-256 optimistic-concurrency protection.
    """
    plan = plan_stamp_generated(bundle, path, by, at=at)
    return apply_document_change(bundle, plan)


def plan_stamp_status(
    bundle: BundleConfig,
    path: Path | str,
    status: str,
) -> DocumentChangePlan:
    """Plan setting the top-level ``status`` lifecycle field (OKF v0.2 §5.4).

    ``status`` must be one of ``draft``, ``stable``, or ``deprecated``;
    anything else raises ``DocumentChangePlanningError`` before any file is
    touched. The write delegates to ``plan_frontmatter_merge``, so setting
    ``status`` to its already-current value (AC3) is a no-op.
    """
    resolved_path = Path(path)
    if not isinstance(status, str) or status not in _VALID_STATUS_VALUES:
        raise DocumentChangePlanningError(
            resolved_path,
            "status must be one of 'draft', 'stable', 'deprecated' "
            f"(OKF v0.2 §5.4): {status!r}",
        )
    return plan_frontmatter_merge(bundle, resolved_path, {"status": status})


def stamp_status(
    bundle: BundleConfig,
    path: Path | str,
    status: str,
) -> DocumentChangeResult:
    """Plan and apply one ``status`` lifecycle stamp in a single call.

    Mirrors ``source_upsert``'s relationship to ``plan_source_upsert``:
    ``plan_stamp_status`` alone is read-only and safe for a dry-run preview,
    while this convenience wrapper also applies it, retaining the same
    SHA-256 optimistic-concurrency protection.
    """
    plan = plan_stamp_status(bundle, path, status)
    return apply_document_change(bundle, plan)


def plan_stamp_stale_after(
    bundle: BundleConfig,
    path: Path | str,
    stale_after: date | str,
) -> DocumentChangePlan:
    """Plan setting the top-level ``stale_after`` lifecycle field (OKF v0.2 §5.5).

    ``stale_after`` must be a ``datetime.date`` or an ISO 8601 ``YYYY-MM-DD``
    string; see ``_validate_date_value`` for the exact rejection cases
    (wrong type, a datetime instead of a date, or an unparseable string),
    all raised as ``DocumentChangePlanningError`` before any file is
    touched. The write delegates to ``plan_frontmatter_merge``, so setting
    ``stale_after`` to its already-current value (AC3) is a no-op.
    """
    resolved_path = Path(path)
    validated = _validate_date_value(
        resolved_path, stale_after, field_name="stale_after"
    )
    return plan_frontmatter_merge(bundle, resolved_path, {"stale_after": validated})


def stamp_stale_after(
    bundle: BundleConfig,
    path: Path | str,
    stale_after: date | str,
) -> DocumentChangeResult:
    """Plan and apply one ``stale_after`` lifecycle stamp in a single call.

    Mirrors ``source_upsert``'s relationship to ``plan_source_upsert``:
    ``plan_stamp_stale_after`` alone is read-only and safe for a dry-run
    preview, while this convenience wrapper also applies it, retaining the
    same SHA-256 optimistic-concurrency protection.
    """
    plan = plan_stamp_stale_after(bundle, path, stale_after)
    return apply_document_change(bundle, plan)


def plan_stamp_verified(
    bundle: BundleConfig,
    path: Path | str,
    by: str,
    *,
    at: datetime | str | None = None,
) -> DocumentChangePlan:
    """Plan appending one OKF v0.2 §5.2 ``verified`` trust event.

    ``by`` (an actor, §7) and ``at`` (an ISO 8601 datetime, defaulting to
    the real current UTC datetime when omitted, same as
    ``plan_stamp_generated``) are validated before any file is touched.

    The document's existing ``verified`` value is read and validated the
    same way each candidate event is (``_validate_existing_verified``): an
    absent key is treated as an empty list (AC -- creating a conformant
    list when absent); a bare ``{by, at}`` mapping -- spec §5.2's
    single-verifier shorthand -- is normalized to a one-element list,
    purely in memory; a list has every entry validated the same way.
    Unlike ``plan_stamp_generated``'s replace-on-write semantics,
    ``verified`` is append-only history, so a malformed pre-existing entry
    is not silently discarded or overwritten -- it raises
    ``DocumentChangePlanningError`` before any write, the same choice
    ``plan_source_upsert``'s ``_validate_existing_sources`` makes for
    ``sources``.

    Identity for the no-op decision (AC3) is the exact ``(by, at)`` pair,
    not just ``by``: spec §5.2 treats two checks by the same actor at
    different times as distinct events (a human sign-off plus a later
    nightly process re-check, or the same actor re-confirming after a
    change), unlike ``plan_source_upsert``'s single-field identity key. When
    an identical event is already present, the existing content is returned
    completely untouched -- including an existing bare-mapping shape, which
    is *not* rewritten into list form just to canonicalize it, since no-op
    means no-op, zero bytes changed. When the event is new, it is appended
    and the result is always written as a list (even when the result has
    only one element), per this operation's canonical-form decision: once
    ``plan_stamp_verified`` manages this field, its on-disk shape is always
    a list, never a bare mapping, even though a bare mapping remains valid
    input the next time this operation reads the document.

    The write itself, when actually needed, delegates to the same
    ``_merge_frontmatter`` engine ``plan_frontmatter_merge`` (#195) and
    ``plan_source_upsert`` (#113) use, applied to the exact read
    ``plan_document_change_from_reader`` performs -- never a separate,
    earlier read -- so a concurrent edit to ``verified`` between planning
    and applying is still reported as ``DocumentChangeConflictError``
    rather than silently discarded.
    """
    resolved_path = Path(path)
    validated_by = _validate_actor_string(resolved_path, by, field_name="verified.by")
    validated_at = _validate_datetime_value(
        resolved_path,
        at if at is not None else datetime.now(timezone.utc),
        field_name="verified.at",
    )
    candidate = {"by": validated_by, "at": validated_at}

    def build_verified_update(resolved_path: Path, original_content: str) -> str:
        try:
            document = parse_concept_document(original_content)
        except DocumentParseError as exc:
            raise DocumentChangePlanningError(
                resolved_path, f"Could not parse document frontmatter: {exc}"
            ) from exc
        current_events = _validate_existing_verified(
            resolved_path, document.frontmatter.get("verified", _MISSING)
        )
        new_events, changed = _append_verified_event(current_events, candidate)
        if not changed:
            return original_content
        return _merge_frontmatter(
            resolved_path, original_content, {"verified": new_events}
        )

    return plan_document_change_from_reader(
        bundle, resolved_path, build_verified_update
    )


def stamp_verified(
    bundle: BundleConfig,
    path: Path | str,
    by: str,
    *,
    at: datetime | str | None = None,
) -> DocumentChangeResult:
    """Plan and apply one ``verified`` trust-event append in a single call.

    Mirrors ``source_upsert``'s relationship to ``plan_source_upsert``:
    ``plan_stamp_verified`` alone is read-only and safe for a dry-run
    preview, while this convenience wrapper also applies it, retaining the
    same SHA-256 optimistic-concurrency protection.
    """
    plan = plan_stamp_verified(bundle, path, by, at=at)
    return apply_document_change(bundle, plan)


def _validate_verified_entry(path: Path, entry: Any, *, context: str) -> dict[str, Any]:
    """Validate one ``verified`` list entry has the §5.2 ``{by, at}`` shape.

    Shared between the caller-supplied candidate event and each entry read
    back from a document's existing ``verified`` value -- ``context``
    distinguishes the two in any raised message, mirroring
    ``_validate_source_entry``'s ``context`` parameter.
    """
    if not isinstance(entry, Mapping):
        raise DocumentChangePlanningError(
            path, f"{context} verified entry must be a mapping: {entry!r}"
        )
    validated_by = _validate_actor_string(
        path, entry.get("by"), field_name=f"{context} verified entry 'by'"
    )
    validated_at = _validate_datetime_value(
        path, entry.get("at"), field_name=f"{context} verified entry 'at'"
    )
    return {"by": validated_by, "at": validated_at}


def _validate_existing_verified(path: Path, verified: Any) -> list[dict[str, Any]]:
    """Validate/normalize a document's existing ``verified`` frontmatter value.

    An absent ``verified`` key (the ``_MISSING`` sentinel) is treated as a
    conformant empty list. A bare ``{by, at}`` mapping -- spec §5.2's
    single-verifier shorthand, which "Consumers MUST treat ... as a
    one-element list" -- is normalized to a one-element list, purely in
    memory; the on-disk shape is left untouched unless ``plan_stamp_verified``
    actually appends. A list has every entry validated the same way a
    candidate event is. Anything else raises ``DocumentChangePlanningError``
    before any write.
    """
    if verified is _MISSING:
        return []
    if isinstance(verified, Mapping):
        return [_validate_verified_entry(path, verified, context="Existing")]
    if isinstance(verified, list):
        return [
            _validate_verified_entry(path, entry, context="Existing")
            for entry in verified
        ]
    raise DocumentChangePlanningError(
        path,
        "Existing 'verified' frontmatter must be a mapping or list, got "
        f"{verified!r}",
    )


def _append_verified_event(
    current: Sequence[Mapping[str, Any]],
    candidate: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], bool]:
    """Append ``candidate`` to ``current`` unless an identical event is already present.

    Pure per-item helper (AGENTS.md's collector-loop-delegates-per-item
    convention), structurally parallel to ``_upsert_source_entry`` but with
    a different identity rule: spec §5.2 treats two checks by the same
    actor at different times as distinct events, so identity here is the
    exact ``(by, at)`` pair, not just ``by`` -- unlike ``_upsert_source_entry``'s
    single-field identity key (``id``, falling back to ``resource``). On a
    match, ``current`` is returned completely untouched -- same entries,
    same content, same order -- so a true no-op never rewrites even an
    existing bare-mapping value into list form. On no match, ``candidate``
    is appended to the end.
    """
    if any(
        entry["by"] == candidate["by"] and entry["at"] == candidate["at"]
        for entry in current
    ):
        return list(current), False
    return [*current, candidate], True


def _patch_markdown_section(
    path: Path,
    content: str,
    heading: str,
    body: str,
    level: int,
) -> str:
    _validate_section_request(path, heading, body, level)
    try:
        document = parse_concept_document(content)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not parse document frontmatter: {exc}"
        ) from exc

    document_body = document.body
    body_offset = len(content) - len(document_body)
    tokens = _MARKDOWN.parse(document_body)
    matches: list[tuple[int, int]] = []
    target_tag = f"h{level}"
    for index, token in enumerate(tokens):
        if (
            token.type != "heading_open"
            or token.tag != target_tag
            or token.map is None
            or index + 1 >= len(tokens)
        ):
            continue
        inline = tokens[index + 1]
        if inline.type == "inline" and inline.content == heading:
            matches.append((index, token.map[1]))

    if len(matches) > 1:
        raise DocumentChangePlanningError(
            path,
            f"Document contains multiple level-{level} headings named {heading!r}",
        )

    line_ending = _first_line_ending(content)
    normalized_body = _ensure_structural_line_ending(body, line_ending)
    if not matches:
        return _append_markdown_section(
            content,
            heading,
            normalized_body,
            level,
            line_ending,
        )

    token_index, section_start_line = matches[0]
    section_end_line = len(document_body.splitlines(keepends=True))
    for token in tokens[token_index + 1 :]:
        if token.type != "heading_open" or token.map is None:
            continue
        token_level = int(token.tag[1:])
        if token_level <= level:
            section_end_line = token.map[0]
            break

    offsets = _line_offsets(document_body)
    section_start = body_offset + offsets[section_start_line]
    section_end = body_offset + offsets[section_end_line]
    return f"{content[:section_start]}{normalized_body}{content[section_end:]}"


def _validate_section_request(
    path: Path,
    heading: str,
    body: str,
    level: int,
) -> None:
    if (
        not isinstance(heading, str)
        or not heading
        or heading != heading.strip()
        or "\n" in heading
        or "\r" in heading
    ):
        raise DocumentChangePlanningError(
            path,
            "Section heading must be a non-empty, single-line string "
            "without surrounding whitespace",
        )
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 6:
        raise DocumentChangePlanningError(
            path, "Section heading level must be an integer from 1 through 6"
        )
    generated_heading = _MARKDOWN.parse(f"{'#' * level} {heading}\n")
    if (
        len(generated_heading) < 2
        or generated_heading[0].type != "heading_open"
        or generated_heading[1].type != "inline"
        or generated_heading[1].content != heading
    ):
        raise DocumentChangePlanningError(
            path,
            "Section heading cannot be represented unambiguously in ATX syntax",
        )
    if not isinstance(body, str):
        raise DocumentChangePlanningError(path, "Section body must be a string")


def _append_markdown_section(
    content: str,
    heading: str,
    body: str,
    level: int,
    line_ending: str,
) -> str:
    trailing_line_endings = _count_trailing_line_endings(content)
    separator = line_ending * max(0, 2 - trailing_line_endings) if content else ""
    heading_line = f"{'#' * level} {heading}{line_ending}"
    return f"{content}{separator}{heading_line}{body}"


def _ensure_structural_line_ending(body: str, line_ending: str) -> str:
    if body and not body.endswith(("\n", "\r")):
        return f"{body}{line_ending}"
    return body


def _first_line_ending(content: str) -> str:
    for index, character in enumerate(content):
        if character == "\n":
            return "\n"
        if character == "\r":
            if index + 1 < len(content) and content[index + 1] == "\n":
                return "\r\n"
            return "\r"
    return "\n"


def _normalize_line_endings(text: str) -> str:
    """Collapse CRLF/CR line endings to LF, mirroring markdown-it-py's own normalization."""

    return text.replace("\r\n", "\n").replace("\r", "\n")


def _count_trailing_line_endings(content: str) -> int:
    count = 0
    position = len(content)
    while position > 0 and count < 2:
        if position >= 2 and content[position - 2 : position] == "\r\n":
            position -= 2
        elif content[position - 1] in "\r\n":
            position -= 1
        else:
            break
        count += 1
    return count


def _line_offsets(content: str) -> tuple[int, ...]:
    offsets = [0]
    position = 0
    for line in content.splitlines(keepends=True):
        position += len(line)
        offsets.append(position)
    return tuple(offsets)


def _merge_frontmatter(
    path: Path,
    content: str,
    updates: Mapping[str, Any],
) -> str:
    update_items = _validated_frontmatter_update_items(path, updates)
    if not update_items:
        return content

    try:
        document = parse_concept_document(content)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not parse document frontmatter: {exc}"
        ) from exc

    yaml_source, body = _split_frontmatter(content)
    data = _load_frontmatter(path, yaml_source or "")
    alias_linked_keys = _alias_linked_keys(
        _compose_frontmatter(path, yaml_source or "")
    )

    changed = False
    for key, value in update_items:
        current = document.frontmatter.get(key, _MISSING)
        if current is not _MISSING and _yaml_values_equal(current, value):
            continue
        if current is not _MISSING and key in alias_linked_keys:
            raise DocumentChangePlanningError(
                path,
                f"Frontmatter field {key!r} is a YAML alias and cannot be changed",
            )
        data[key] = value
        changed = True

    if not changed:
        return content

    proposed = f"---\n{_dump_frontmatter(path, data)}---\n{body}"
    _validate_merged_frontmatter(path, proposed)
    return proposed


def _validated_frontmatter_update_items(
    path: Path,
    updates: Mapping[str, Any],
) -> tuple[tuple[str, Any], ...]:
    if not isinstance(updates, Mapping):
        raise DocumentChangePlanningError(path, "Frontmatter updates must be a mapping")
    update_items = tuple(updates.items())
    seen_containers: set[int] = set()
    for key, value in update_items:
        if (
            not isinstance(key, str)
            or not key
            or key != key.strip()
            or "\n" in key
            or "\r" in key
        ):
            raise DocumentChangePlanningError(
                path,
                "Frontmatter update keys must be single-line strings without "
                "leading or trailing whitespace",
            )
        _validate_frontmatter_update_value(path, value, seen_containers)
        _validate_frontmatter_update_value_dumpable(path, key, value)
    return update_items


def _validate_frontmatter_update_value_dumpable(
    path: Path, key: str, value: Any
) -> None:
    """Fail fast if ``value`` cannot round-trip through the YAML dumper.

    ``_validate_frontmatter_update_value`` only checks that ``value`` uses a
    supported Python type; it cannot rule out every value ``ruamel.yaml``'s
    round-trip dumper will still refuse (a `RepresenterError` or similar).
    Probing with a real dump here catches a genuinely undumpable value
    before any write-path work proceeds -- restoring the guard the old
    span-splice engine's `_dump_yaml` pre-flight call used to provide --
    rather than surfacing it only from the final `_dump_frontmatter` call
    once ``key`` has already been merged into the target document's data.
    """

    probe = CommentedMap()
    probe[key] = value
    try:
        _make_frontmatter_yaml().dump(probe, io.StringIO())
    except (RuamelYAMLError, TypeError, ValueError) as exc:
        raise DocumentChangePlanningError(
            path,
            f"Frontmatter update value for {key!r} cannot be represented "
            f"as YAML: {exc}",
        ) from exc


def _load_frontmatter(path: Path, yaml_source: str) -> CommentedMap:
    """Load raw frontmatter YAML into a mutable round-trip ``CommentedMap``.

    An empty source (no frontmatter block, or an empty block) loads as an
    empty map so callers have one code path for "populate absent frontmatter"
    and "edit existing frontmatter" alike. Uses a fresh `YAML` instance (see
    `_make_frontmatter_yaml`) so this call can never be corrupted by, or
    corrupt, state from another document's load or dump.
    """

    try:
        data = _make_frontmatter_yaml().load(yaml_source)
    except RuamelYAMLError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not parse document frontmatter: {exc}"
        ) from exc
    return CommentedMap() if data is None else data


def _normalize_container_style(node: Any) -> None:
    """Recursively clear preserved flow-style hints so every mapping/sequence
    in *node* dumps in block form, regardless of the source document's style.

    ADR-0002's convergence framework (R-C2/R-C3) is per-document, on first
    touch: touching any part of a non-canonical document brings the *whole*
    document to canonical form, not just the keys an edit targets. ruamel's
    round-trip loader records each mapping/sequence's original flow-vs-block
    style as a per-node hint (`.fa`) that survives into the dumped output
    independently of the `YAML` instance's own `default_flow_style` setting
    -- empirically, setting `default_flow_style=False` on the dumper does
    *not* override a loaded node's own preserved style hint, so an untouched
    flow-style sibling would otherwise survive an edit unchanged forever.
    Explicitly clearing the hint on every mapping/sequence node (loaded or
    freshly assigned by an update) is the only way to make that happen.
    Only container *style* is touched here; comments, key order, and quote
    style on untouched scalars are unaffected.
    """

    if isinstance(node, CommentedMap):
        node.fa.set_block_style()
        for value in node.values():
            _normalize_container_style(value)
    elif isinstance(node, CommentedSeq):
        node.fa.set_block_style()
        for item in node:
            _normalize_container_style(item)
    elif isinstance(node, dict):
        for value in node.values():
            _normalize_container_style(value)
    elif isinstance(node, list):
        for item in node:
            _normalize_container_style(item)


def _dump_frontmatter(path: Path, data: CommentedMap) -> str:
    """Dump a frontmatter ``CommentedMap`` in `okf-core`'s canonical form.

    Block style and LF line endings are the round-trip dumper's own
    defaults for any node without its own preserved style hint;
    `_normalize_container_style` clears hints preserved from the source
    document so the *whole* document -- not just the keys this dump's
    caller touched -- converges to block form, per ADR-0002. No other
    post-processing is applied. Uses a fresh `YAML` instance (see
    `_make_frontmatter_yaml`) so a dump failure here raises cleanly instead
    of leaving a shared instance poisoned for the next, unrelated
    document's merge.
    """

    _normalize_container_style(data)
    buffer = io.StringIO()
    try:
        _make_frontmatter_yaml().dump(data, buffer)
    except (RuamelYAMLError, TypeError, ValueError) as exc:
        raise DocumentChangePlanningError(
            path, f"Could not represent merged frontmatter as YAML: {exc}"
        ) from exc
    return buffer.getvalue()


_MISSING = object()
_SUPPORTED_FRONTMATTER_SCALAR_TYPES = {
    str,
    bool,
    int,
    float,
    type(None),
    date,
    datetime,
}


def _validate_frontmatter_update_value(
    path: Path,
    value: Any,
    seen_containers: set[int],
) -> None:
    value_type = type(value)
    if value_type in _SUPPORTED_FRONTMATTER_SCALAR_TYPES:
        if value_type is float and not isfinite(value):
            raise DocumentChangePlanningError(
                path, "Frontmatter update values must use supported finite scalars"
            )
        return
    if value_type not in {list, dict}:
        raise DocumentChangePlanningError(
            path,
            "Frontmatter update values must use supported scalar, list, or dict types",
        )

    identity = id(value)
    if identity in seen_containers:
        raise DocumentChangePlanningError(
            path,
            "Frontmatter update values must not contain shared or cyclic containers",
        )
    seen_containers.add(identity)

    if value_type is list:
        for item in value:
            _validate_frontmatter_update_value(path, item, seen_containers)
        return

    for key, item in value.items():
        if type(key) is not str:
            raise DocumentChangePlanningError(
                path, "Frontmatter update dictionaries must use string keys"
            )
        _validate_frontmatter_update_value(path, item, seen_containers)


def _compose_frontmatter(path: Path, yaml_source: str) -> MappingNode | None:
    try:
        root = yaml.compose(yaml_source, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not compose document frontmatter: {exc}"
        ) from exc
    if root is None:
        return None
    if not isinstance(root, MappingNode):
        raise DocumentChangePlanningError(path, "YAML frontmatter must be a mapping")
    return root


def _alias_linked_keys(root: MappingNode | None) -> set[str]:
    """Top-level keys whose composed value node is shared with another key.

    PyYAML composes an anchor definition and each of its aliases to the same
    node object, so a shared node identity covers both the anchor-defining
    key and every key that aliases it.
    """
    if root is None:
        return set()
    node_counts: dict[int, int] = {}
    for _, value_node in root.value:
        node_counts[id(value_node)] = node_counts.get(id(value_node), 0) + 1
    return {
        key_node.value
        for key_node, value_node in root.value
        if node_counts[id(value_node)] > 1
    }


def _validate_merged_frontmatter(path: Path, proposed: str) -> None:
    try:
        document = parse_concept_document(proposed)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Merged frontmatter is invalid: {exc}"
        ) from exc
    yaml_source, _ = _split_frontmatter(proposed)
    if yaml_source is None:
        raise DocumentChangePlanningError(path, "Merged frontmatter is missing")
    if not isinstance(document.frontmatter, dict):
        raise DocumentChangePlanningError(path, "Merged frontmatter is not a mapping")


# `_yaml_values_equal` compares `document.frontmatter` (PyYAML's plain-dict
# parse, used for no-op detection in `_merge_frontmatter`) against an
# update value already constrained to plain builtin types by
# `_validate_frontmatter_update_value`. Neither side is ever a value loaded
# by the ruamel round-trip engine -- that engine's `CommentedMap`/`data` is
# mutated directly and never routed through this comparison -- so beyond the
# temporal normalization below, a plain `type(...) is not type(...)` check
# is sufficient here; there is no ruamel-specific formatting subclass (e.g.
# `SingleQuotedScalarString`, `ScalarInt`) for this comparison to ever see.
# Do not reintroduce *ruamel* subclass normalization without first tracing a
# real call path that passes a ruamel-loaded value here, per AGENTS.md's
# guidance against defensive handling for scenarios that cannot happen.
#
# The temporal normalization is a different, real scenario, not a
# precautionary one: PyYAML's `construct_yaml_timestamp` calls
# `datetime.datetime(...)` dynamically for every `left` operand this
# function ever sees for a date/datetime field, so under
# `freeze_time` `left` is a `FakeDatetime`/`FakeDate` exactly as often as a
# stamping candidate would be without `_validate_datetime_value`/
# `_validate_date_value`'s own rebuild -- but `right` (the candidate) is
# *always* pre-normalized by one of those two validators before reaching
# here, while `left` never was. `_normalize_temporal_for_comparison`
# closes that asymmetry on both operands (a no-op on `right`, since it is
# already real; the actual fix for `left`) before the exact-type check, so
# AC3's no-op detection holds under a freeze for every field routed through
# this comparison (`generated`, `status`, `stale_after`, `verified`), not
# only whichever case a given test happens to freeze.
def _yaml_values_equal(left: Any, right: Any) -> bool:
    left = _normalize_temporal_for_comparison(left)
    right = _normalize_temporal_for_comparison(right)
    left_type = type(left)
    if left_type is not type(right):
        return False
    if left_type is dict:
        if left.keys() != right.keys():
            return False
        return all(_yaml_values_equal(left[key], right[key]) for key in left)
    if left_type is list:
        return len(left) == len(right) and all(
            _yaml_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return bool(left == right)

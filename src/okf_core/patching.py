"""Format-specific document planning: Markdown sections, links, frontmatter."""

from __future__ import annotations

import io
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit, urlunsplit

import yaml
from markdown_it import MarkdownIt
from ruamel.yaml import YAML
from ruamel.yaml import YAMLError as RuamelYAMLError
from ruamel.yaml.comments import CommentedMap
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
]

_MARKDOWN = MarkdownIt("commonmark")


def _make_frontmatter_yaml() -> YAML:
    """Build a fresh round-trip YAML instance for one frontmatter operation.

    A new instance is constructed on every call rather than shared, because
    ``ruamel.yaml``'s ``YAML.dump``/``dump_all`` stash internal state on the
    instance for the duration of a call and only clear it on the success
    path: a failed dump on one document would otherwise leave a shared
    instance poisoned for the next, unrelated document's merge. Per-call
    construction cost is negligible for file-at-a-time frontmatter
    operations (see the #117 spike).

    Round-trip mode keeps comments, key order, anchors/aliases, and
    per-node flow/block style on any key a merge does not touch; ADR-0002
    documents the resulting canonical form. `preserve_quotes` keeps
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
    key order and comments on untouched keys are preserved, output uses
    block style and LF line endings regardless of the source document's
    style, and quote style is not guaranteed to survive on a touched value.
    A non-canonical but conformant input converges to canonical form on its
    first edit; this may produce one-time formatting churn in that edit's
    diff, which is expected rather than a defect.

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


def _dump_frontmatter(path: Path, data: CommentedMap) -> str:
    """Dump a frontmatter ``CommentedMap`` in `okf-core`'s canonical form.

    Block style and LF line endings are the round-trip dumper's own
    defaults; no post-processing is applied, per ADR-0002. Uses a fresh
    `YAML` instance (see `_make_frontmatter_yaml`) so a dump failure here
    raises cleanly instead of leaving a shared instance poisoned for the
    next, unrelated document's merge.
    """

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


# ruamel's round-trip loader returns str/int/float/bool *subclasses*
# (e.g. SingleQuotedScalarString, ScalarInt, ScalarBoolean) that carry
# source formatting alongside the plain value. `_yaml_values_equal` is a
# semantic (data-model) comparison, so these formatting subclasses must be
# normalized to their plain base type before comparing -- otherwise a
# round-tripped value would never compare equal to the plain Python literal
# an update supplies, and every no-op check on a quoted/formatted scalar
# would spuriously report "changed". Only ruamel's own formatting
# subclasses are normalized here; real semantic types (bool vs int, date vs
# str) keep their own distinct type and are unaffected. `bool` is checked
# before `int` because `bool` is itself an `int` subclass in Python.
def _normalize_yaml_scalar_type(value: Any) -> type:
    if isinstance(value, bool):
        return bool
    if isinstance(value, str):
        return str
    if isinstance(value, int):
        return int
    if isinstance(value, float):
        return float
    return type(value)


def _yaml_values_equal(left: Any, right: Any) -> bool:
    left_type = _normalize_yaml_scalar_type(left)
    if left_type is not _normalize_yaml_scalar_type(right):
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

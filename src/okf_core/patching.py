"""Inspectable, optimistic-concurrency-safe document changes."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit, urlunsplit

import yaml
from markdown_it import MarkdownIt
from yaml.nodes import MappingNode, Node
from yaml.tokens import AliasToken

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError, parse_concept_document
from okf_core.write_safety import check_bundle_write_safety

if TYPE_CHECKING:
    from markdown_it.rules_core.state_core import StateCore
    from markdown_it.rules_inline.state_inline import StateInline
    from markdown_it.token import Token

_MARKDOWN = MarkdownIt("commonmark")


class DocumentChangeError(Exception):
    """Base exception for document change planning and application failures."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


class DocumentChangePlanningError(DocumentChangeError):
    """Raised when a document change cannot be planned safely."""


class DocumentChangeSafetyError(DocumentChangeError):
    """Raised when metadata at ``path`` makes bundle writes unsafe."""


class DocumentChangeConflictError(DocumentChangeError):
    """Raised when a target no longer matches the content used by a plan."""

    def __init__(
        self,
        path: Path,
        expected_sha256: str,
        actual_sha256: str | None,
    ) -> None:
        actual = actual_sha256 if actual_sha256 is not None else "<unavailable>"
        super().__init__(
            path,
            (
                f"Document changed after planning: {path} "
                f"(expected SHA-256 {expected_sha256}, got {actual})"
            ),
        )
        self.expected_sha256 = expected_sha256
        self.actual_sha256 = actual_sha256


class DocumentChangeApplyError(DocumentChangeError):
    """Raised when a validated document change cannot be written."""


class FileMoveConflictError(DocumentChangeError):
    """Raised when a file move's source or destination no longer matches its plan."""


@dataclass(frozen=True)
class DocumentChangePlan:
    """An inspectable proposed replacement for one existing bundle document.

    ``original_exists`` is ``True`` for every plan built the traditional way,
    against a target that was already present when planned. It is ``False``
    only for a plan built with ``allow_missing=True`` against a target that
    did not yet exist at planning time -- in which case ``original_content``
    is ``""`` and ``apply_document_change`` creates the file fresh instead of
    replacing it, still guarding against a concurrent create the same way
    every other apply guards against a concurrent edit.
    """

    bundle_root: Path
    path: Path
    original_content: str
    proposed_content: str
    original_sha256: str
    proposed_sha256: str
    original_exists: bool = True

    @property
    def changed(self) -> bool:
        """Return whether applying this plan would change document bytes."""

        return self.original_sha256 != self.proposed_sha256


@dataclass(frozen=True)
class DocumentChangeResult:
    """The result of applying or confirming one document change plan."""

    path: Path
    original_sha256: str
    resulting_sha256: str
    changed: bool


@dataclass(frozen=True)
class FileMovePlan:
    """An inspectable proposed relocation of one existing bundle file.

    Planning reads and hashes the source but never moves it. Relative paths
    for both source and dest are interpreted from the configured bundle root,
    the same convention plan_document_change and its siblings use.
    """

    bundle_root: Path
    source_path: Path
    dest_path: Path
    source_sha256: str

    @property
    def noop(self) -> bool:
        """Return whether source and dest already resolve to the same path."""

        return self.source_path == self.dest_path


@dataclass(frozen=True)
class FileMoveResult:
    """The result of applying or confirming one file move plan."""

    source_path: Path
    dest_path: Path
    moved: bool


def plan_document_change(
    bundle: BundleConfig,
    path: Path | str,
    proposed_content: str,
    *,
    allow_missing: bool = False,
) -> DocumentChangePlan:
    """Prepare an inspectable change for a UTF-8 bundle document.

    Planning reads and hashes the target but never modifies it. Relative paths
    are interpreted from the configured bundle root. By default the target
    must already exist, matching every other planning primitive in this
    module; pass ``allow_missing=True`` to also accept a target that does not
    exist yet, treating its original content as empty (``DocumentChangePlan
    .original_exists`` records which case applied, and ``apply_document_change``
    creates the file fresh rather than replacing it).
    """

    def use_proposed_content(resolved_path: Path, _: str) -> str:
        if not isinstance(proposed_content, str):
            raise DocumentChangePlanningError(
                resolved_path, "Proposed document content must be a string"
            )
        return proposed_content

    return _plan_document_change(
        bundle,
        Path(path),
        use_proposed_content,
        allow_missing=allow_missing,
    )


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
    """Plan a shallow, byte-preserving merge of top-level frontmatter fields.

    Existing values are replaced at their YAML source spans and missing fields
    are appended in update order. Update values may contain plain YAML-oriented
    scalars, dates, datetimes, lists, and string-keyed dictionaries. Untargeted
    YAML aliases are preserved; fields participating in alias relationships
    cannot be changed safely and are rejected.
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


_DEFAULT_NEW_FILE_MODE = 0o644


def apply_document_change(
    bundle: BundleConfig,
    plan: DocumentChangePlan,
) -> DocumentChangeResult:
    """Apply a document change if the target still matches its planned hash.

    Changed content is prepared in the target directory and installed with
    ``os.replace``. This provides atomic replacement on supported local
    filesystems, but it is not a multi-file transaction or a filesystem lock.
    For a plan built with ``allow_missing=True`` where the target did not
    exist at planning time (``plan.original_exists is False``), "still
    matches its planned hash" instead means the target must still be
    missing -- a target that has since been created concurrently is a
    conflict, the same as one whose content has since changed. The file is
    then created fresh with ``_DEFAULT_NEW_FILE_MODE`` rather than
    ``tempfile.mkstemp``'s restrictive default.
    """

    bundle_root = bundle.bundle_root.resolve(strict=False)
    if bundle_root != plan.bundle_root:
        raise DocumentChangeApplyError(
            plan.path,
            f"Plan belongs to bundle root {plan.bundle_root}, not {bundle_root}",
        )

    _require_plan_target(bundle_root, plan.path)
    _require_bundle_write_safety(bundle)
    if plan.original_exists:
        _, current_mode = _read_for_apply(plan)
    else:
        _require_target_still_missing(plan)
        current_mode = _DEFAULT_NEW_FILE_MODE

    if not plan.changed:
        return DocumentChangeResult(
            path=plan.path,
            original_sha256=plan.original_sha256,
            resulting_sha256=plan.original_sha256,
            changed=False,
        )

    proposed_bytes = _encode_utf8(
        plan.path,
        plan.proposed_content,
        DocumentChangeApplyError,
    )
    if _sha256(proposed_bytes) != plan.proposed_sha256:
        raise DocumentChangeApplyError(
            plan.path, "Plan proposed content does not match its SHA-256 hash"
        )

    temp_path: Path | None = None
    try:
        temp_path = _write_temporary_file(plan.path, proposed_bytes, current_mode)
        if plan.original_exists:
            _require_current_hash(plan)
        else:
            _require_target_still_missing(plan)
        os.replace(temp_path, plan.path)
        temp_path = None
    except DocumentChangeError:
        raise
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.path, f"Could not apply document change: {exc}"
        ) from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    return DocumentChangeResult(
        path=plan.path,
        original_sha256=plan.original_sha256,
        resulting_sha256=plan.proposed_sha256,
        changed=True,
    )


def plan_file_move(
    bundle: BundleConfig,
    source: Path | str,
    dest: Path | str,
) -> FileMovePlan:
    """Prepare an inspectable relocation of one existing bundle file.

    Planning reads and hashes the source but never moves or creates anything.
    Relative paths for both source and dest are interpreted from the
    configured bundle root, the same convention plan_document_change uses. If
    source and dest resolve to the same path, the returned plan is idempotent
    (``.noop`` is True) and apply_file_move will not touch the filesystem for
    it.
    """

    try:
        resolved_source, bundle_root, _ = _resolve_existing_target(bundle, Path(source))
    except DocumentChangePlanningError as exc:
        # _resolve_existing_target's messages are phrased for the generic
        # "document change target" case; reword for a move's source-specific
        # meaning without duplicating its symlink/existence/shape checks.
        raise DocumentChangePlanningError(
            exc.path, str(exc).replace("Document change target", "Move source")
        ) from exc
    _require_bundle_write_safety(bundle)

    dest_path = Path(dest)
    candidate_dest = dest_path if dest_path.is_absolute() else bundle_root / dest_path
    # Checked before .resolve() and before the noop short-circuit below: a
    # symlinked DEST that happens to point at SOURCE must still be rejected
    # as a symlink argument, not silently accepted as a no-op move.
    if candidate_dest.is_symlink():
        raise DocumentChangePlanningError(
            candidate_dest.absolute(), "Move destination must not be a symbolic link"
        )
    resolved_dest = candidate_dest.resolve(strict=False)
    source_sha256 = _sha256(_read_for_planning(resolved_source))

    if resolved_dest == resolved_source:
        return FileMovePlan(
            bundle_root=bundle_root,
            source_path=resolved_source,
            dest_path=resolved_dest,
            source_sha256=source_sha256,
        )

    try:
        _require_plan_target(bundle_root, resolved_dest, planning=True)
    except DocumentChangePlanningError as exc:
        raise DocumentChangePlanningError(
            exc.path, str(exc).replace("Document change target", "Move destination")
        ) from exc
    if resolved_dest.exists():
        raise DocumentChangePlanningError(
            resolved_dest, "Move destination already exists"
        )

    return FileMovePlan(
        bundle_root=bundle_root,
        source_path=resolved_source,
        dest_path=resolved_dest,
        source_sha256=source_sha256,
    )


def apply_file_move(bundle: BundleConfig, plan: FileMovePlan) -> FileMoveResult:
    """Apply a file move if the source still matches its planned hash and the
    destination still does not exist.

    Uses a create-hard-link-then-unlink sequence rather than ``os.replace``,
    so a destination that appears concurrently after planning is never
    silently overwritten: ``os.link`` fails atomically with
    ``FileExistsError`` in that case. If the link succeeds but removing the
    source then fails, the document is left present at both paths rather than
    lost -- the resulting DocumentChangeApplyError explains the manual
    cleanup needed. This is a single-file optimistic-concurrency primitive,
    not a multi-file transaction. Requires source and dest to reside on the
    same filesystem.
    """

    bundle_root = bundle.bundle_root.resolve(strict=False)
    if bundle_root != plan.bundle_root:
        raise DocumentChangeApplyError(
            plan.dest_path,
            f"Plan belongs to bundle root {plan.bundle_root}, not {bundle_root}",
        )

    _require_plan_target(bundle_root, plan.source_path)
    _require_plan_target(bundle_root, plan.dest_path)
    _require_bundle_write_safety(bundle)

    current_hash = _current_regular_file_sha256(plan.source_path)
    if current_hash != plan.source_sha256:
        raise FileMoveConflictError(
            plan.source_path,
            f"Move source changed after planning: {plan.source_path} "
            f"(expected SHA-256 {plan.source_sha256}, "
            f"got {current_hash if current_hash is not None else '<unavailable>'})",
        )

    if plan.noop:
        return FileMoveResult(plan.source_path, plan.dest_path, moved=False)

    if plan.dest_path.exists() or plan.dest_path.is_symlink():
        raise FileMoveConflictError(
            plan.dest_path, f"Move destination now exists: {plan.dest_path}"
        )

    dest_parent = plan.dest_path.parent
    # Checked before mkdir(parents=True): if an ancestor was swapped for a
    # symlink after planning, resolve() already diverges from the lexical
    # path even though dest_parent itself doesn't exist yet, so this catches
    # it before mkdir ever creates anything through that symlink.
    _require_dest_parent_unchanged(plan.dest_path, dest_parent)

    try:
        dest_parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.dest_path, f"Could not create destination directory: {exc}"
        ) from exc

    # Re-checked immediately after mkdir: a narrower race where dest_parent
    # was swapped for a symlink between the check above and mkdir returning.
    _require_dest_parent_unchanged(plan.dest_path, dest_parent)

    try:
        os.link(plan.source_path, plan.dest_path)
    except FileExistsError as exc:
        raise FileMoveConflictError(
            plan.dest_path, f"Move destination now exists: {plan.dest_path}"
        ) from exc
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.dest_path, f"Could not create move destination: {exc}"
        ) from exc

    try:
        plan.source_path.unlink()
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.source_path,
            f"Created {plan.dest_path} but could not remove original "
            f"{plan.source_path}: {exc}. Both copies currently exist; remove "
            "the original manually once verified.",
        ) from exc

    return FileMoveResult(plan.source_path, plan.dest_path, moved=True)


def _plan_document_change(
    bundle: BundleConfig,
    path: Path,
    build_proposed_content: Callable[[Path, str], str],
    *,
    allow_missing: bool = False,
) -> DocumentChangePlan:
    resolved_path, bundle_root, original_exists = _resolve_existing_target(
        bundle, path, allow_missing=allow_missing
    )
    _require_bundle_write_safety(bundle)
    if original_exists:
        original_bytes = _read_for_planning(resolved_path)
        try:
            original_content = original_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentChangePlanningError(
                resolved_path,
                f"Could not decode document as UTF-8: {exc}",
            ) from exc
    else:
        original_bytes = b""
        original_content = ""

    proposed_content = build_proposed_content(resolved_path, original_content)
    proposed_bytes = _encode_utf8(
        resolved_path,
        proposed_content,
        DocumentChangePlanningError,
    )
    return DocumentChangePlan(
        bundle_root=bundle_root,
        path=resolved_path,
        original_content=original_content,
        proposed_content=proposed_content,
        original_sha256=_sha256(original_bytes),
        proposed_sha256=_sha256(proposed_bytes),
        original_exists=original_exists,
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
        _dump_yaml(path, value, flow_style=False)

    if not update_items:
        return content

    try:
        document = parse_concept_document(content)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not parse document frontmatter: {exc}"
        ) from exc

    bounds = _frontmatter_bounds(content)
    line_ending = _first_line_ending(content)
    if bounds is None:
        generated = _dump_yaml_mapping(path, update_items, line_ending)
        proposed = f"---{line_ending}{generated}---{line_ending}{content}"
        _validate_merged_frontmatter(path, proposed)
        return proposed

    yaml_start, yaml_end = bounds
    yaml_source = content[yaml_start:yaml_end]
    root = _compose_frontmatter(path, yaml_source)
    nodes = _top_level_nodes(root)
    alias_linked_keys = _alias_linked_keys(root)

    replacements: list[tuple[int, int, str]] = []
    additions: list[tuple[str, Any]] = []
    for key, value in update_items:
        current = document.frontmatter.get(key, _MISSING)
        if current is not _MISSING and _yaml_values_equal(current, value):
            continue
        value_node = nodes.get(key)
        if value_node is None:
            additions.append((key, value))
            continue
        if key in alias_linked_keys:
            raise DocumentChangePlanningError(
                path,
                f"Frontmatter field {key!r} is a YAML alias and cannot be changed",
            )
        key_line = _node_key_line(root, key)
        start = value_node.start_mark.index
        end = value_node.end_mark.index
        original_value_source = yaml_source[start:end]
        inline = value_node.start_mark.line == key_line
        replacement = _serialize_replacement_value(
            path,
            value,
            column=value_node.start_mark.column,
            inline=inline,
            preserve_final_line_ending=original_value_source.endswith(("\n", "\r")),
            line_ending=line_ending,
        )
        if start == end:
            replacement = f" {replacement}"
        replacements.append((start, end, replacement))

    merged_yaml = yaml_source
    for start, end, replacement in sorted(replacements, reverse=True):
        merged_yaml = f"{merged_yaml[:start]}{replacement}{merged_yaml[end:]}"
    if additions:
        merged_yaml += _dump_yaml_mapping(path, additions, line_ending)

    proposed = f"{content[:yaml_start]}{merged_yaml}{content[yaml_end:]}"
    _validate_merged_frontmatter(path, proposed)
    return proposed


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


def _frontmatter_bounds(content: str) -> tuple[int, int] | None:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    yaml_start = len(lines[0])
    position = yaml_start
    for line in lines[1:]:
        if line.rstrip("\r\n") == "---":
            return yaml_start, position
        position += len(line)
    return None


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


def _top_level_nodes(root: MappingNode | None) -> dict[str, Node]:
    if root is None:
        return {}
    return {key_node.value: value_node for key_node, value_node in root.value}


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


def _node_key_line(root: MappingNode | None, target_key: str) -> int:
    assert root is not None
    for key_node, _ in root.value:
        if key_node.value == target_key:
            return key_node.start_mark.line
    raise AssertionError(f"Missing composed frontmatter key: {target_key}")


def _serialize_replacement_value(
    path: Path,
    value: Any,
    *,
    column: int,
    inline: bool,
    preserve_final_line_ending: bool,
    line_ending: str,
) -> str:
    dumped = _dump_yaml(path, value, flow_style=inline)
    dumped = _strip_yaml_document_end(dumped)
    dumped = dumped.removesuffix("\n")
    dumped = dumped.replace("\n", f"\n{' ' * column}")
    dumped = dumped.replace("\n", line_ending)
    if preserve_final_line_ending:
        dumped += line_ending
    return dumped


def _dump_yaml(path: Path, value: Any, *, flow_style: bool) -> str:
    try:
        dumped = yaml.safe_dump(
            value,
            allow_unicode=True,
            default_flow_style=flow_style,
            sort_keys=False,
            width=10_000,
        )
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        raise DocumentChangePlanningError(
            path, f"Frontmatter value cannot be represented as safe YAML: {exc}"
        ) from exc
    _reject_generated_yaml_aliases(path, dumped)
    return dumped


def _dump_yaml_mapping(
    path: Path,
    items: Sequence[tuple[str, Any]],
    line_ending: str,
) -> str:
    if not items:
        return ""
    try:
        dumped = yaml.safe_dump(
            dict(items),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=10_000,
        )
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        raise DocumentChangePlanningError(
            path, f"Frontmatter updates cannot be represented as safe YAML: {exc}"
        ) from exc
    _reject_generated_yaml_aliases(path, dumped)
    return dumped.replace("\n", line_ending)


def _strip_yaml_document_end(dumped: str) -> str:
    if dumped.endswith("\n...\n"):
        return dumped[:-4]
    return dumped


def _reject_generated_yaml_aliases(path: Path, yaml_source: str) -> None:
    try:
        has_alias = any(
            isinstance(token, AliasToken)
            for token in yaml.scan(yaml_source, Loader=yaml.SafeLoader)
        )
    except yaml.YAMLError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not scan document frontmatter: {exc}"
        ) from exc
    if has_alias:
        raise DocumentChangePlanningError(
            path, "Generated frontmatter updates must not contain YAML aliases"
        )


def _validate_merged_frontmatter(path: Path, proposed: str) -> None:
    try:
        document = parse_concept_document(proposed)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Merged frontmatter is invalid: {exc}"
        ) from exc
    bounds = _frontmatter_bounds(proposed)
    if bounds is None:
        raise DocumentChangePlanningError(path, "Merged frontmatter is missing")
    if not isinstance(document.frontmatter, dict):
        raise DocumentChangePlanningError(path, "Merged frontmatter is not a mapping")


def _yaml_values_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        if left.keys() != right.keys():
            return False
        return all(_yaml_values_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(
            _yaml_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return left == right


def _resolve_existing_target(
    bundle: BundleConfig, path: Path, *, allow_missing: bool = False
) -> tuple[Path, Path, bool]:
    """Resolve and validate a planning target, returning whether it exists.

    By default a missing target is a planning error, matching every
    pre-#136 caller. With ``allow_missing=True`` a missing target is
    accepted instead (returned as ``exists=False``) so a caller like
    ``plan_log_concept_move`` can plan against a ``log.md`` that has never
    been written yet.
    """
    bundle_root = bundle.bundle_root.resolve(strict=False)
    candidate = path if path.is_absolute() else bundle_root / path
    if candidate.is_symlink():
        raise DocumentChangePlanningError(
            candidate.absolute(), "Document change target must not be a symbolic link"
        )

    resolved_path = candidate.resolve(strict=False)
    _require_plan_target(bundle_root, resolved_path, planning=True)
    if not resolved_path.exists():
        if allow_missing:
            return resolved_path, bundle_root, False
        raise DocumentChangePlanningError(
            resolved_path, "Document change target does not exist"
        )
    if not resolved_path.is_file():
        raise DocumentChangePlanningError(
            resolved_path, "Document change target must be a regular file"
        )
    return resolved_path, bundle_root, True


def _require_plan_target(
    bundle_root: Path,
    path: Path,
    *,
    planning: bool = False,
) -> None:
    error_type = DocumentChangePlanningError if planning else DocumentChangeApplyError
    if planning and path.is_symlink():
        raise error_type(path, "Document change target must not be a symbolic link")
    try:
        path.relative_to(bundle_root)
    except ValueError as exc:
        raise error_type(
            path,
            f"Document change target is outside bundle root {bundle_root}",
        ) from exc


def _require_bundle_write_safety(bundle: BundleConfig) -> None:
    problem = check_bundle_write_safety(bundle)
    if problem is not None:
        raise DocumentChangeSafetyError(problem.path, problem.message)


def _encode_utf8(
    path: Path,
    content: str,
    error_type: type[DocumentChangeError],
) -> bytes:
    try:
        return content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise error_type(
            path,
            f"Could not encode proposed document content as UTF-8: {exc}",
        ) from exc


def _read_for_planning(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not read document for planning: {exc}"
        ) from exc


def _read_for_apply(plan: DocumentChangePlan) -> tuple[bytes, int]:
    try:
        if (
            plan.path.is_symlink()
            or plan.path.resolve(strict=False) != plan.path
            or not plan.path.is_file()
        ):
            raise DocumentChangeConflictError(plan.path, plan.original_sha256, None)
        current_bytes = plan.path.read_bytes()
        mode = stat.S_IMODE(plan.path.stat().st_mode)
    except DocumentChangeConflictError:
        raise
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.path, f"Could not read document before applying change: {exc}"
        ) from exc

    actual_sha256 = _sha256(current_bytes)
    if actual_sha256 != plan.original_sha256:
        raise DocumentChangeConflictError(
            plan.path,
            plan.original_sha256,
            actual_sha256,
        )
    return current_bytes, mode


def _require_current_hash(plan: DocumentChangePlan) -> None:
    try:
        if (
            plan.path.is_symlink()
            or plan.path.resolve(strict=False) != plan.path
            or not plan.path.is_file()
        ):
            raise DocumentChangeConflictError(plan.path, plan.original_sha256, None)
        actual_sha256 = _sha256(plan.path.read_bytes())
    except DocumentChangeConflictError:
        raise
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.path, f"Could not recheck document before replacement: {exc}"
        ) from exc

    if actual_sha256 != plan.original_sha256:
        raise DocumentChangeConflictError(
            plan.path,
            plan.original_sha256,
            actual_sha256,
        )


def _require_target_still_missing(plan: DocumentChangePlan) -> None:
    """Raise if a target planned as missing (``original_exists=False``) now exists.

    A target that appears concurrently between planning and apply -- created
    by another process, or left over as a symlink -- is a conflict just like
    a target whose content changed underneath an existing-target plan, even
    though there is no "current hash" to compare against; ``actual_sha256``
    reports the new content's hash (or ``None`` if it can't be read) so the
    conflict message still says what showed up.
    """
    if plan.path.exists() or plan.path.is_symlink():
        raise DocumentChangeConflictError(
            plan.path,
            plan.original_sha256,
            _current_regular_file_sha256(plan.path),
        )


def _require_dest_parent_unchanged(dest_path: Path, dest_parent: Path) -> None:
    """Raise if dest_parent is now a symlink, or an ancestor of it is."""

    if dest_parent.is_symlink() or dest_parent.resolve(strict=False) != dest_parent:
        raise FileMoveConflictError(
            dest_path,
            f"Move destination directory changed after planning: {dest_parent}",
        )


def _current_regular_file_sha256(path: Path) -> str | None:
    """Return path's current SHA-256, or None if it's missing/symlink/non-regular."""

    try:
        if (
            path.is_symlink()
            or path.resolve(strict=False) != path
            or not path.is_file()
        ):
            return None
        return _sha256(path.read_bytes())
    except OSError:
        return None


def _write_temporary_file(path: Path, content: bytes, mode: int) -> Path:
    fd = -1
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=".okf-",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written == 0:
                raise OSError("temporary file write made no progress")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.chmod(temp_path, mode)
        return temp_path
    except OSError:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _sha256(content: bytes) -> str:
    return sha256(content).hexdigest()

"""Index file parsing and generation for OKF bundles."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from markdown_it import MarkdownIt

from okf_core._markdown_inline import (
    inline_token_source,
    render_linked_span,
    token_line,
)
from okf_core.documents import (
    ConceptDocument,
    parse_concept_document,
    serialize_concept_document,
    validate_concept_document,
    validate_concept_document_with_profile,
)
from okf_core.manifest import BundleManifest, ConceptManifestEntry
from okf_core.versions import normalize_okf_version_declaration

if TYPE_CHECKING:
    from okf_core.config import BundleConfig, ProfileConfig, TaxonomyConfig

_MARKDOWN = MarkdownIt("commonmark")

_DESC_SEP = re.compile(r"^\s+-\s+")


@dataclass(frozen=True)
class IndexProblem:
    """A non-fatal problem encountered while generating an index."""

    concept_id: str
    path: Path
    message: str


@dataclass(frozen=True)
class GeneratedIndex:
    """Result of a ``generate_index()`` call.

    ``body`` is the rendered ``index.md`` content string.  ``problems`` is a
    tuple of non-fatal issues encountered during generation (skipped entries,
    out-of-scope paths, etc.).
    """

    body: str
    problems: tuple[IndexProblem, ...] = ()


@dataclass(frozen=True)
class IndexEntry:
    """A single entry in an index file section."""

    title: str
    link: str
    description: str | None = None


@dataclass(frozen=True)
class IndexSection:
    """A heading and its entries in a parsed index file."""

    heading: str
    entries: tuple[IndexEntry, ...]


@dataclass(frozen=True)
class IndexParseProblem:
    """A non-fatal problem encountered while parsing an index file."""

    heading: str | None
    line: int | None
    message: str


@dataclass(frozen=True)
class ParsedIndex:
    """Structured representation of a parsed index.md file."""

    sections: tuple[IndexSection, ...]
    problems: tuple[IndexParseProblem, ...] = ()


def render_index_document(body: str, okf_version: str | None = None) -> str:
    """Render complete ``index.md`` content with optional root version metadata."""

    if okf_version is None:
        return body
    return serialize_concept_document(
        ConceptDocument(frontmatter={"okf_version": okf_version}, body=body)
    )


def declared_okf_version(content: str) -> str | None:
    """Return an ``index.md`` frontmatter ``okf_version`` declaration, if present."""

    document = parse_concept_document(content)
    if "okf_version" not in document.frontmatter:
        return None
    return normalize_okf_version_declaration(document.frontmatter["okf_version"])


def okf_version_for_index_write(
    bundle: BundleConfig, target_dir: Path, force: bool = False
) -> str | None:
    """Return the ``okf_version`` to declare when (re)writing ``target_dir``'s index.md.

    Only the bundle root's index.md carries an ``okf_version`` declaration;
    every other directory's index.md gets ``None`` (no declaration). For the
    root, an explicitly configured ``bundle.okf_version`` wins; otherwise an
    existing supported declaration in the current root index.md is preserved
    unless ``force`` is set, in which case it is dropped.
    """

    if target_dir.resolve() != bundle.bundle_root.resolve():
        return None
    if bundle.okf_version is not None:
        return bundle.okf_version
    if force:
        return None

    index_path = bundle.bundle_root / "index.md"
    if not index_path.is_file():
        return None
    return declared_okf_version(index_path.read_text(encoding="utf-8"))


def entries_for_directory(
    directory: Path, manifest: BundleManifest
) -> tuple[list[ConceptManifestEntry], list[Path]]:
    """Return (direct_entries, subdirectories) for generate_index(directory, ...).

    ``direct_entries`` are concepts whose immediate parent is ``directory``.
    ``subdirectories`` are ``directory``'s immediate child directories that
    contain a concept, directly or at any depth beneath them -- exactly what
    generate_index expects for its own ``subdirectories`` argument.
    """

    resolved_dir = directory.resolve()
    direct_entries: list[ConceptManifestEntry] = []
    subdirs: set[Path] = set()
    for entry in manifest.concepts:
        try:
            rel = entry.path.resolve().relative_to(resolved_dir)
        except ValueError:
            continue
        if len(rel.parts) == 1:
            direct_entries.append(entry)
        else:
            subdirs.add(resolved_dir / rel.parts[0])
    return direct_entries, sorted(subdirs)


def parse_index(content: str) -> ParsedIndex:
    """Parse an index.md body into structured sections and entries.

    Only entries under a ``# Heading`` are captured; list items that appear
    before the first heading are ignored.  Malformed list items are skipped and
    reported as parse problems.
    """
    tokens = _MARKDOWN.parse(content)
    sections: list[IndexSection] = []
    problems: list[IndexParseProblem] = []
    current_heading: str | None = None
    current_entries: list[IndexEntry] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open" and token.tag == "h1":
            if current_heading is not None:
                sections.append(
                    IndexSection(
                        heading=current_heading, entries=tuple(current_entries)
                    )
                )
            i += 1
            if i < len(tokens) and tokens[i].type == "inline":
                current_heading = tokens[i].content
            current_entries = []
        elif token.type == "bullet_list_open":
            list_level = token.level
            item_captured = False
            i += 1
            while i < len(tokens):
                t = tokens[i]
                if t.level == list_level and t.nesting == -1:
                    break  # any list close at this level; outer i += 1 advances past it
                if t.level == list_level + 1 and t.type == "list_item_open":
                    item_captured = False
                elif (
                    t.type == "inline"
                    and list_level < t.level <= list_level + 3
                    and not item_captured
                    and current_heading is not None
                ):
                    entry, message = _entry_from_inline_token(t)
                    if entry is not None:
                        current_entries.append(entry)
                    elif message is not None:
                        problems.append(
                            IndexParseProblem(
                                heading=current_heading,
                                line=_token_line(t),
                                message=message,
                            )
                        )
                    item_captured = True
                i += 1
        i += 1

    if current_heading is not None:
        sections.append(
            IndexSection(heading=current_heading, entries=tuple(current_entries))
        )

    return ParsedIndex(sections=tuple(sections), problems=tuple(problems))


def generate_index(
    directory: Path,
    entries: Sequence[ConceptManifestEntry],
    subdirectories: Sequence[Path] = (),
    *,
    describe_directory: Callable[[Path], str | None] | None = None,
    directory_metadata_file: str = "_directory.yml",
    profile: ProfileConfig | None = None,
    project_taxonomy: TaxonomyConfig | None = None,
) -> GeneratedIndex:
    """Generate an index.md body from manifest entries scoped to a directory.

    ``directory`` is resolved to an absolute path before any comparison so
    relative, absolute, and symlink-containing inputs all behave consistently.

    Entries are grouped by their ``type`` frontmatter field and sorted
    alphabetically within each group.  Unknown but valid string ``type`` values
    are tolerated and grouped normally per OKF spec §9.  Subdirectories are
    listed in a trailing section.

    The following inputs are skipped and reported as ``IndexProblem`` objects
    in the ``.problems`` field of the returned ``GeneratedIndex`` rather than
    raising:

    - Entries whose ``type`` is not a non-empty, non-whitespace string
      (missing or non-string ``type`` is a spec §4.1 violation).
    - Entries or subdirectories whose resolved path does not fall under the
      resolved ``directory``.

    ``title`` is taken from frontmatter: the raw value is converted to a string,
    internal newlines collapsed to spaces, and stripped; if absent, ``None``, or
    empty/whitespace-only after normalisation, the file stem is used as the
    fallback so that every entry has a non-empty title.  Falsy-but-non-empty
    values such as ``0`` or ``false`` are preserved as their string
    representation.  ``description`` follows the same normalisation: internal
    newlines collapsed and stripped; if absent, ``None``, or empty/whitespace-only
    after normalisation, the entry suffix is omitted.  Falsy-but-non-empty values
    such as ``0`` or ``false`` are preserved as their string representation.
    ``describe_directory`` callback return values are normalised the same way:
    internal newlines collapsed and stripped; empty/whitespace-only results are
    treated as ``None``.

    ``describe_directory`` is a hook for callers (e.g. workflow agents) to
    supply directory-level descriptions without ``okf-core`` owning any model
    access.  It always receives the resolved absolute subdirectory path and
    should return a description string or ``None``.

    """
    resolved_dir = directory.resolve()
    problems: list[IndexProblem] = []

    # Validate directory_metadata_file is a simple filename
    meta_file_path = Path(directory_metadata_file)
    effective_meta_file = directory_metadata_file
    if meta_file_path.name != directory_metadata_file or meta_file_path.is_absolute():
        problems.append(
            IndexProblem(
                concept_id="",
                path=directory,
                message=f"invalid configuration: directory_metadata_file {directory_metadata_file!r} must be a simple filename, not a path",
            )
        )
        effective_meta_file = (
            meta_file_path.name if meta_file_path.name else "_directory.yml"
        )

    groups, entry_problems = _group_entries(entries, resolved_dir)
    problems.extend(entry_problems)

    lines: list[str] = []

    for group_key in sorted(groups):
        heading = group_key.title()
        sorted_entries = sorted(groups[group_key], key=lambda e: e.title.lower())
        lines.append(f"# {heading}")
        lines.append("")
        for e in sorted_entries:
            lines.append(_render_entry(e))
        lines.append("")

    if subdirectories:
        subdir_entries, subdir_problems = _subdirectory_entries(
            subdirectories,
            resolved_dir,
            effective_meta_file,
            describe_directory,
            profile,
            project_taxonomy,
        )
        problems.extend(subdir_problems)

        if subdir_entries:
            lines.append("# Subdirectories")
            lines.append("")
            for e in subdir_entries:
                lines.append(_render_entry(e))
            lines.append("")

    return GeneratedIndex(body="\n".join(lines), problems=tuple(problems))


def _group_entries(
    entries: Sequence[ConceptManifestEntry], resolved_dir: Path
) -> tuple[dict[str, list[IndexEntry]], list[IndexProblem]]:
    """Validate and group manifest entries by ``type``, scoped to ``resolved_dir``.

    Entries with a missing/invalid ``type`` or a path outside ``resolved_dir``
    are skipped and reported as problems rather than raising.
    """
    groups: dict[str, list[IndexEntry]] = {}
    problems: list[IndexProblem] = []

    for entry in entries:
        type_key = entry.frontmatter.get("type")
        if not isinstance(type_key, str) or not type_key.strip():
            problems.append(
                IndexProblem(
                    concept_id=entry.concept_id,
                    path=entry.path,
                    message=f"skipped: 'type' frontmatter must be a non-empty string, got {type_key!r}",
                )
            )
            continue

        try:
            rel = entry.path.resolve().relative_to(resolved_dir)
        except ValueError:
            problems.append(
                IndexProblem(
                    concept_id=entry.concept_id,
                    path=entry.path,
                    message=f"skipped: path is not under directory {resolved_dir}",
                )
            )
            continue

        title_raw = entry.frontmatter.get("title")
        title_str = _normalize_inline(str(title_raw)) if title_raw is not None else ""
        title = title_str if title_str else entry.path.stem
        description_raw = entry.frontmatter.get("description")
        description_str = (
            _normalize_inline(str(description_raw))
            if description_raw is not None
            else ""
        )
        description = description_str if description_str else None
        link = rel.as_posix()

        groups.setdefault(type_key.strip(), []).append(
            IndexEntry(title=title, link=link, description=description)
        )

    return groups, problems


def _load_directory_metadata(
    meta_path: Path,
    profile: ProfileConfig | None,
    project_taxonomy: TaxonomyConfig | None,
) -> tuple[dict[str, Any], list[IndexProblem]]:
    """Load and validate a subdirectory's metadata file, if present.

    Returns the parsed metadata mapping (``{}`` if the file is absent, empty,
    or rejected) alongside any problems encountered. Validation only runs once
    the loaded YAML has been accepted as a string-keyed mapping.
    """
    meta_data: dict[str, Any] = {}
    problems: list[IndexProblem] = []

    if not meta_path.is_file():
        return meta_data, problems

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        if loaded is None:
            meta_data = {}
        elif not isinstance(loaded, dict):
            problems.append(
                IndexProblem(
                    concept_id="",
                    path=meta_path,
                    message=f"invalid metadata file {meta_path.name}: content must be a YAML mapping",
                )
            )
        elif not all(isinstance(k, str) for k in loaded):
            problems.append(
                IndexProblem(
                    concept_id="",
                    path=meta_path,
                    message=f"invalid metadata file {meta_path.name}: YAML frontmatter keys must be strings",
                )
            )
        else:
            meta_data = loaded
            doc = ConceptDocument(frontmatter=meta_data, body="")
            if profile is not None:
                findings = validate_concept_document_with_profile(
                    doc,
                    profile,
                    project_taxonomy,
                    is_directory_meta=True,
                )
            else:
                findings = validate_concept_document(doc)
            for finding in findings:
                problems.append(
                    IndexProblem(
                        concept_id="",
                        path=meta_path,
                        message=(
                            f"validation {finding.severity}: [{finding.field}] {finding.message}"
                            if finding.field
                            else f"validation {finding.severity}: {finding.message}"
                        ),
                    )
                )
    except (OSError, yaml.YAMLError) as exc:
        problems.append(
            IndexProblem(
                concept_id="",
                path=meta_path,
                message=f"failed to parse metadata file {meta_path.name}: {exc}",
            )
        )

    return meta_data, problems


def _subdirectory_entries(
    subdirectories: Sequence[Path],
    resolved_dir: Path,
    effective_meta_file: str,
    describe_directory: Callable[[Path], str | None] | None,
    profile: ProfileConfig | None,
    project_taxonomy: TaxonomyConfig | None,
) -> tuple[list[IndexEntry], list[IndexProblem]]:
    """Build the trailing "Subdirectories" entries, scoped to ``resolved_dir``.

    Subdirectories that are the index directory itself, or fall outside
    ``resolved_dir``, are skipped and reported as problems. Each remaining
    subdirectory's title/description are derived from its metadata file (see
    ``_load_directory_metadata``), falling back to the relative path and the
    ``describe_directory`` callback respectively.
    """
    subdir_entries: list[IndexEntry] = []
    problems: list[IndexProblem] = []

    for subdir in sorted(subdirectories, key=lambda p: str(p.resolve()).lower()):
        resolved_subdir = subdir.resolve()
        if resolved_subdir == resolved_dir:
            problems.append(
                IndexProblem(
                    concept_id="",
                    path=subdir,
                    message=f"skipped: subdirectory is the index directory itself {resolved_dir}",
                )
            )
            continue
        try:
            rel_path = resolved_subdir.relative_to(resolved_dir).as_posix()
        except ValueError:
            problems.append(
                IndexProblem(
                    concept_id="",
                    path=subdir,
                    message=f"skipped: subdirectory is not under directory {resolved_dir}",
                )
            )
            continue

        meta_path = resolved_subdir / effective_meta_file
        meta_data, meta_problems = _load_directory_metadata(
            meta_path, profile, project_taxonomy
        )
        problems.extend(meta_problems)

        title = rel_path
        if "title" in meta_data:
            title_raw = meta_data["title"]
            if title_raw is not None:
                normalized_title = _normalize_inline(str(title_raw))
                if normalized_title:
                    title = normalized_title

        desc: str | None = None
        if "description" in meta_data:
            desc_raw = meta_data["description"]
            if desc_raw is not None:
                normalized_desc = _normalize_inline(str(desc_raw))
                desc = normalized_desc if normalized_desc else None

        if desc is None and describe_directory is not None:
            desc_raw = describe_directory(resolved_subdir)
            if desc_raw is not None:
                normalized = _normalize_inline(desc_raw)
                desc = normalized if normalized else None

        subdir_entries.append(
            IndexEntry(title=title, link=rel_path + "/", description=desc)
        )

    return subdir_entries, problems


def _normalize_inline(s: str) -> str:
    """Collapse internal newlines/CRs to spaces and strip, keeping output single-line."""
    return re.sub(r"[\r\n]+", " ", s).strip()


def _md_escape(s: str) -> str:
    """Escape backslash then markdown link delimiters so output round-trips."""
    return (
        s.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(")", "\\)")
    )


_inline_content = inline_token_source
"""Alias kept for call sites within this module; see ``_markdown_inline``."""

_render_suffix_span = render_linked_span
"""Reconstitute the Markdown source of the tokens following the title link.

Inner link pairs become ``[text](href)``; every other token passes through
``_inline_content``. Shared with ``logs.py``'s entry-prose renderer via
``_markdown_inline.render_linked_span`` -- see that module for the
implementation.
"""


def _render_span(children: list[Any]) -> str:
    """Render a run of inline children with no link tokens back to Markdown source."""
    return "".join(
        c for c in (_inline_content(child) for child in children) if c is not None
    )


def _title_link_bounds(children: list[Any]) -> tuple[int, int] | str:
    """Locate the title link's open/close indices, or return an error message.

    Enforces the position rules that the old single pass raised while walking:
    the entry must contain a link, that link must be the first rendered content,
    and it must not itself contain a nested link.
    """
    open_idx = next(
        (i for i, child in enumerate(children) if child.type == "link_open"), None
    )
    if open_idx is None:
        return "skipped malformed index entry: missing link target"
    if _render_span(children[:open_idx]).strip():
        return "skipped malformed index entry: entry link must be the first content"
    for i in range(open_idx + 1, len(children)):
        if children[i].type == "link_open":
            return "skipped malformed index entry: nested links are not supported"
        if children[i].type == "link_close":
            return open_idx, i
    return open_idx, len(children)


def _description_from_suffix(children: list[Any]) -> tuple[str | None, str | None]:
    """Validate the post-title span and extract the optional description.

    Returns ``(description, error)``. A second link or any trailing text is only
    valid inside a ``" - description"`` suffix (``_DESC_SEP``).
    """
    has_link = any(child.type == "link_open" for child in children)
    suffix = _render_suffix_span(children)
    m = _DESC_SEP.match(suffix)
    if has_link and not m:
        return (
            None,
            "skipped malformed index entry: additional links must be in a description",
        )
    if not m and suffix.strip():
        return (
            None,
            "skipped malformed index entry: trailing text must be in a description",
        )
    return (suffix[m.end() :].rstrip() if m else None), None


def _entry_from_inline_token(
    token: object,
) -> tuple[IndexEntry | None, str | None]:
    """Extract title, href, and optional description from a list-item inline token."""
    children = getattr(token, "children", None) or []

    bounds = _title_link_bounds(children)
    if isinstance(bounds, str):
        return None, bounds
    open_idx, close_idx = bounds

    href = children[open_idx].attrGet("href") or ""
    if not href:
        return None, "skipped malformed index entry: missing link target"

    title = _render_span(children[open_idx + 1 : close_idx])
    if not title:
        return None, "skipped malformed index entry: missing link title"

    description, error = _description_from_suffix(children[close_idx + 1 :])
    if error is not None:
        return None, error
    return IndexEntry(title=title, link=href, description=description), None


_token_line = token_line


def _render_entry(entry: IndexEntry) -> str:
    base = f"* [{_md_escape(entry.title)}]({_md_escape(entry.link)})"
    if entry.description:
        return f"{base} - {entry.description}"
    return base

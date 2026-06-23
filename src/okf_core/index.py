"""Index file parsing and generation for OKF bundles."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt

from okf_core.manifest import ConceptManifestEntry

_MARKDOWN = MarkdownIt("commonmark")


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
class ParsedIndex:
    """Structured representation of a parsed index.md file."""

    sections: tuple[IndexSection, ...]


def parse_index(content: str) -> ParsedIndex:
    """Parse an index.md body into structured sections and entries.

    Only entries under a ``# Heading`` are captured; list items that appear
    before the first heading are ignored.  Non-link list items are skipped.
    """
    tokens = _MARKDOWN.parse(content)
    sections: list[IndexSection] = []
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
            list_depth = 1
            item_captured = False
            i += 1
            while i < len(tokens) and list_depth > 0:
                if tokens[i].type == "bullet_list_open":
                    list_depth += 1
                elif tokens[i].type == "bullet_list_close":
                    list_depth -= 1
                    if list_depth == 0:
                        break  # leave i on bullet_list_close; outer i += 1 advances past it
                elif list_depth == 1:
                    if tokens[i].type == "list_item_open":
                        item_captured = False
                    elif (
                        tokens[i].type == "inline"
                        and not item_captured
                        and current_heading is not None
                    ):
                        entry = _entry_from_inline_token(tokens[i])
                        if entry is not None:
                            current_entries.append(entry)
                        item_captured = True
                i += 1
        i += 1

    if current_heading is not None:
        sections.append(
            IndexSection(heading=current_heading, entries=tuple(current_entries))
        )

    return ParsedIndex(sections=tuple(sections))


def generate_index(
    directory: Path,
    entries: Sequence[ConceptManifestEntry],
    subdirectories: Sequence[Path] = (),
    *,
    describe_directory: Callable[[Path], str | None] | None = None,
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
        subdir_entries: list[IndexEntry] = []
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
            desc: str | None = None
            if describe_directory is not None:
                desc_raw = describe_directory(resolved_subdir)
                if desc_raw is not None:
                    normalized = _normalize_inline(desc_raw)
                    desc = normalized if normalized else None
            subdir_entries.append(
                IndexEntry(title=rel_path, link=rel_path + "/", description=desc)
            )
        if subdir_entries:
            lines.append("# Subdirectories")
            lines.append("")
            for e in subdir_entries:
                lines.append(_render_entry(e))
            lines.append("")

    return GeneratedIndex(body="\n".join(lines), problems=tuple(problems))


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


def _entry_from_inline_token(token: object) -> IndexEntry | None:
    """Extract title, href, and optional description from a list-item inline token."""
    children = getattr(token, "children", None) or []
    title_parts: list[str] = []
    after_link: list[str] = []
    href: str | None = None
    in_link = False
    link_count = 0

    for child in children:
        if child.type == "link_open":
            link_count += 1
            if link_count > 1:
                return None  # multiple links: ambiguous, skip
            href = child.attrGet("href") or ""
            in_link = True
        elif child.type == "link_close":
            in_link = False
        elif in_link and child.type in ("text", "code_inline"):
            title_parts.append(
                f"`{child.content}`" if child.type == "code_inline" else child.content
            )
        elif not in_link and href is not None and child.type in ("text", "code_inline"):
            after_link.append(
                f"`{child.content}`" if child.type == "code_inline" else child.content
            )

    if not href or not title_parts:
        return None

    suffix = "".join(after_link)
    description = suffix[3:].rstrip() if suffix.startswith(" - ") else None
    return IndexEntry(title="".join(title_parts), link=href, description=description)


def _render_entry(entry: IndexEntry) -> str:
    base = f"* [{_md_escape(entry.title)}]({_md_escape(entry.link)})"
    if entry.description:
        return f"{base} - {entry.description}"
    return base

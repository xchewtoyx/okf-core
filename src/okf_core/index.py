"""Index file parsing and generation for OKF bundles."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from okf_core.manifest import ConceptManifestEntry

_ENTRY_RE = re.compile(
    r"^\* \[(?P<title>[^\]]+)\]\((?P<link>[^)]+)\)(?:\s+-\s+(?P<desc>.+))?$"
)


@dataclass(frozen=True)
class IndexProblem:
    """A non-fatal problem encountered while generating an index."""

    concept_id: str
    path: Path
    message: str


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

    Only lines under a ``# Heading`` are captured as entries; list items that
    appear before the first heading are ignored.  Lines that are not headings
    or well-formed ``* [title](link)`` entries are also ignored.
    """
    sections: list[IndexSection] = []
    current_heading: str | None = None
    current_entries: list[IndexEntry] = []

    for line in content.splitlines():
        if line.startswith("# "):
            if current_heading is not None:
                sections.append(
                    IndexSection(
                        heading=current_heading,
                        entries=tuple(current_entries),
                    )
                )
            current_heading = line[2:].strip()
            current_entries = []
        elif line.startswith("* "):
            m = _ENTRY_RE.match(line)
            if m:
                current_entries.append(
                    IndexEntry(
                        title=m.group("title"),
                        link=m.group("link"),
                        description=m.group("desc"),
                    )
                )

    if current_heading is not None:
        sections.append(
            IndexSection(
                heading=current_heading,
                entries=tuple(current_entries),
            )
        )

    return ParsedIndex(sections=tuple(sections))


def generate_index(
    directory: Path,
    entries: Sequence[ConceptManifestEntry],
    subdirectories: Sequence[Path] = (),
    *,
    describe_directory: Callable[[Path], str | None] | None = None,
) -> tuple[str, tuple[IndexProblem, ...]]:
    """Generate an index.md body from manifest entries scoped to a directory.

    ``directory`` is resolved to an absolute path before any comparison so
    relative, absolute, and symlink-containing inputs all behave consistently.

    Entries are grouped by their ``type`` frontmatter field and sorted
    alphabetically within each group.  Unknown but valid string ``type`` values
    are tolerated and grouped normally per OKF spec §9.  Subdirectories are
    listed in a trailing section.

    The following inputs are skipped and reported as ``IndexProblem`` objects
    in the second return value rather than raising:

    - Entries whose ``type`` is not a non-empty, non-whitespace string
      (missing or non-string ``type`` is a spec §4.1 violation).
    - Entries or subdirectories whose resolved path does not fall under the
      resolved ``directory``.

    ``title`` and ``description`` are extracted with explicit ``None`` checks so
    that falsy-but-valid YAML values (e.g. ``0``, ``false``) are preserved.

    ``describe_directory`` is a hook for callers (e.g. workflow agents) to
    supply directory-level descriptions without ``okf-core`` owning any model
    access.  It always receives the resolved absolute subdirectory path and
    should return a description string or ``None``.

    Assumption: entry titles, frontmatter descriptions, and relative path
    strings are free of markdown link metacharacters (``]``, ``)``,
    newlines).  OKF concept ID constraints enforced by ``concept_id_to_path``
    and ``scan_bundle`` already prevent these characters from appearing in
    valid bundle entries and subdirectory paths.  Callers supplying synthetic
    entries that violate this assumption will produce unparseable markdown.
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
        title = str(title_raw) if title_raw is not None else entry.path.stem
        description_raw = entry.frontmatter.get("description")
        description = str(description_raw) if description_raw is not None else None
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
        for subdir in sorted(subdirectories, key=lambda p: p.name.lower()):
            resolved_subdir = subdir.resolve()
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
                desc = describe_directory(resolved_subdir)
            subdir_entries.append(
                IndexEntry(title=rel_path, link=rel_path + "/", description=desc)
            )
        if subdir_entries:
            lines.append("# Subdirectories")
            lines.append("")
            for e in subdir_entries:
                lines.append(_render_entry(e))
            lines.append("")

    return "\n".join(lines), tuple(problems)


def _render_entry(entry: IndexEntry) -> str:
    base = f"* [{entry.title}]({entry.link})"
    if entry.description:
        return f"{base} - {entry.description}"
    return base

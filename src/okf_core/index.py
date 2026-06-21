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

    Lines that are not headings or well-formed list entries are ignored.
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

    Entries are grouped by their ``type`` frontmatter field.  Within each group
    entries are sorted alphabetically by resolved title.  Subdirectories are
    listed in a trailing section.

    Entries whose ``type`` value is not a non-empty string are skipped — a
    missing or non-string ``type`` is a spec §4.1 violation — and reported as
    ``IndexProblem`` objects in the second return value.  Entries or
    subdirectories whose path does not fall under ``directory`` are likewise
    skipped and reported.

    Note: unknown but valid string ``type`` values are tolerated and grouped
    normally per the OKF spec (§9 consumers MUST tolerate unknown types).

    ``describe_directory`` is a hook for callers (e.g. workflow agents) to
    supply directory-level descriptions without ``okf-core`` owning any model
    access.  It receives the absolute subdirectory path and should return a
    description string or ``None``.
    """
    groups: dict[str, list[IndexEntry]] = {}
    problems: list[IndexProblem] = []

    for entry in entries:
        type_key = entry.frontmatter.get("type")
        if not isinstance(type_key, str) or not type_key:
            problems.append(
                IndexProblem(
                    concept_id=entry.concept_id,
                    path=entry.path,
                    message=f"skipped: 'type' frontmatter must be a non-empty string, got {type_key!r}",
                )
            )
            continue

        try:
            rel = entry.path.relative_to(directory)
        except ValueError:
            problems.append(
                IndexProblem(
                    concept_id=entry.concept_id,
                    path=entry.path,
                    message=f"skipped: path is not under directory {directory}",
                )
            )
            continue

        title = str(entry.frontmatter.get("title") or entry.path.stem)
        description_raw = entry.frontmatter.get("description")
        description = str(description_raw) if description_raw else None
        link = rel.as_posix()

        groups.setdefault(type_key, []).append(
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
            try:
                rel_path = subdir.relative_to(directory).as_posix()
            except ValueError:
                problems.append(
                    IndexProblem(
                        concept_id="",
                        path=subdir,
                        message=f"skipped: subdirectory is not under directory {directory}",
                    )
                )
                continue
            desc: str | None = None
            if describe_directory is not None:
                desc = describe_directory(subdir)
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

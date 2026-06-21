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
    fallback_group: str = "Other",
    describe_directory: Callable[[Path], str | None] | None = None,
) -> str:
    """Generate an index.md body from manifest entries scoped to a directory.

    Entries are grouped by their ``type`` frontmatter field.  Entries without a
    type value fall into ``fallback_group``.  Within each group entries are
    sorted alphabetically by resolved title.  Subdirectories are listed in a
    trailing section.

    ``describe_directory`` is a hook for callers (e.g. workflow agents) to
    supply directory-level descriptions without ``okf-core`` owning any model
    access.  It receives the absolute subdirectory path and should return a
    description string or ``None``.
    """
    groups: dict[str, list[IndexEntry]] = {}

    for entry in entries:
        type_key = entry.frontmatter.get("type") or None
        title = str(entry.frontmatter.get("title") or entry.path.stem)
        description_raw = entry.frontmatter.get("description")
        description = str(description_raw) if description_raw else None
        rel = entry.path.relative_to(directory)
        link = rel.as_posix()

        index_entry = IndexEntry(title=title, link=link, description=description)
        group = type_key if type_key is not None else None
        groups.setdefault(group, []).append(index_entry)  # type: ignore[arg-type]

    fallback_entries = groups.pop(None, [])  # type: ignore[call-overload]

    lines: list[str] = []

    for group_key in sorted(groups):
        heading = str(group_key).title()
        sorted_entries = sorted(groups[group_key], key=lambda e: e.title.lower())
        lines.append(f"# {heading}")
        lines.append("")
        for e in sorted_entries:
            lines.append(_render_entry(e))
        lines.append("")

    if fallback_entries:
        sorted_fallback = sorted(fallback_entries, key=lambda e: e.title.lower())
        lines.append(f"# {fallback_group}")
        lines.append("")
        for e in sorted_fallback:
            lines.append(_render_entry(e))
        lines.append("")

    if subdirectories:
        lines.append("# Subdirectories")
        lines.append("")
        for subdir in sorted(subdirectories, key=lambda p: p.name.lower()):
            desc: str | None = None
            if describe_directory is not None:
                desc = describe_directory(subdir)
            rel_link = subdir.name + "/"
            lines.append(
                _render_entry(
                    IndexEntry(title=subdir.name, link=rel_link, description=desc)
                )
            )
        lines.append("")

    return "\n".join(lines)


def _render_entry(entry: IndexEntry) -> str:
    base = f"* [{entry.title}]({entry.link})"
    if entry.description:
        return f"{base} - {entry.description}"
    return base

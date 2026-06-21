"""Tests for index file parsing and generation."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from okf_core.index import (
    IndexEntry,
    IndexSection,
    ParsedIndex,
    generate_index,
    parse_index,
)
from okf_core.manifest import ConceptManifestEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    path: Path,
    bundle_root: Path,
    *,
    concept_id: str = "stub",
    type: str | None = "concept",
    title: str | None = None,
    description: str | None = None,
) -> ConceptManifestEntry:
    fm: dict = {}
    if type is not None:
        fm["type"] = type
    if title is not None:
        fm["title"] = title
    if description is not None:
        fm["description"] = description
    return ConceptManifestEntry(
        concept_id=concept_id,
        path=path,
        bundle_root=bundle_root,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType(fm),
    )


# ---------------------------------------------------------------------------
# generate_index tests
# ---------------------------------------------------------------------------


def test_generate_flat_typed_concepts(tmp_path: Path) -> None:
    directory = tmp_path
    a = _entry(tmp_path / "alpha.md", tmp_path, concept_id="alpha", title="Alpha")
    b = _entry(tmp_path / "beta.md", tmp_path, concept_id="beta", title="Beta")
    result = generate_index(directory, [a, b])
    assert "# Concept" in result
    assert "* [Alpha](alpha.md)" in result
    assert "* [Beta](beta.md)" in result


def test_generate_mixed_types_sort_order(tmp_path: Path) -> None:
    directory = tmp_path
    a = _entry(tmp_path / "a.md", tmp_path, type="zebra", title="Z Entry")
    b = _entry(tmp_path / "b.md", tmp_path, type="apple", title="A Entry")
    result = generate_index(directory, [a, b])
    apple_pos = result.index("# Apple")
    zebra_pos = result.index("# Zebra")
    assert apple_pos < zebra_pos


def test_generate_entries_alphabetical_within_group(tmp_path: Path) -> None:
    directory = tmp_path
    a = _entry(tmp_path / "z.md", tmp_path, title="Zulu")
    b = _entry(tmp_path / "a.md", tmp_path, title="Alpha")
    result = generate_index(directory, [a, b])
    alpha_pos = result.index("Alpha")
    zulu_pos = result.index("Zulu")
    assert alpha_pos < zulu_pos


def test_generate_with_subdirectories(tmp_path: Path) -> None:
    subdir = tmp_path / "subtopic"
    result = generate_index(tmp_path, [], subdirectories=[subdir])
    assert "# Subdirectories" in result
    assert "* [subtopic](subtopic/)" in result


def test_generate_missing_title_falls_back_to_stem(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "my-file.md", tmp_path, title=None)
    result = generate_index(directory, [e])
    assert "* [my-file](my-file.md)" in result


def test_generate_missing_description_omits_suffix(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, title="Alpha", description=None)
    result = generate_index(directory, [e])
    assert " - " not in result


def test_generate_with_description_included(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, title="Alpha", description="A short desc")
    result = generate_index(directory, [e])
    assert "* [Alpha](a.md) - A short desc" in result


def test_generate_unknown_type_goes_to_fallback_group(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, type=None, title="Orphan")
    result = generate_index(directory, [e])
    assert "# Other" in result
    assert "* [Orphan](a.md)" in result


def test_generate_custom_fallback_group(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, type=None, title="X")
    result = generate_index(directory, [e], fallback_group="Uncategorised")
    assert "# Uncategorised" in result


def test_generate_describe_directory_hook(tmp_path: Path) -> None:
    subdir = tmp_path / "sub"

    def describe(path: Path) -> str | None:
        return "A subdirectory"

    result = generate_index(
        tmp_path, [], subdirectories=[subdir], describe_directory=describe
    )
    assert "* [sub](sub/) - A subdirectory" in result


def test_generate_describe_directory_none_return(tmp_path: Path) -> None:
    subdir = tmp_path / "sub"

    def describe(path: Path) -> str | None:
        return None

    result = generate_index(
        tmp_path, [], subdirectories=[subdir], describe_directory=describe
    )
    assert "* [sub](sub/)" in result
    assert " - " not in result


def test_generate_empty_produces_empty_string(tmp_path: Path) -> None:
    result = generate_index(tmp_path, [])
    assert result == ""


# ---------------------------------------------------------------------------
# parse_index tests
# ---------------------------------------------------------------------------


def test_parse_conformant_index() -> None:
    content = "# Concepts\n\n* [Alpha](alpha.md) - First\n* [Beta](beta.md)\n"
    parsed = parse_index(content)
    assert len(parsed.sections) == 1
    section = parsed.sections[0]
    assert section.heading == "Concepts"
    assert len(section.entries) == 2
    assert section.entries[0] == IndexEntry(
        title="Alpha", link="alpha.md", description="First"
    )
    assert section.entries[1] == IndexEntry(
        title="Beta", link="beta.md", description=None
    )


def test_parse_index_with_no_description_entries() -> None:
    content = "# Things\n\n* [X](x.md)\n* [Y](y.md)\n"
    parsed = parse_index(content)
    for entry in parsed.sections[0].entries:
        assert entry.description is None


def test_parse_empty_content_returns_empty_sections() -> None:
    parsed = parse_index("")
    assert parsed == ParsedIndex(sections=())


def test_parse_multiple_sections() -> None:
    content = "# A\n\n* [One](one.md)\n\n# B\n\n* [Two](two.md)\n"
    parsed = parse_index(content)
    assert len(parsed.sections) == 2
    assert parsed.sections[0].heading == "A"
    assert parsed.sections[1].heading == "B"


def test_parse_ignores_non_entry_lines() -> None:
    content = "Some preamble\n\n# Section\n\nsome body text\n* [X](x.md)\n"
    parsed = parse_index(content)
    assert len(parsed.sections) == 1
    assert len(parsed.sections[0].entries) == 1


def test_parse_subdirectory_link() -> None:
    content = "# Subdirectories\n\n* [sub](sub/) - desc\n"
    parsed = parse_index(content)
    assert parsed.sections[0].entries[0].link == "sub/"


# ---------------------------------------------------------------------------
# Round-trip test
# ---------------------------------------------------------------------------


def test_round_trip(tmp_path: Path) -> None:
    directory = tmp_path
    entries = [
        _entry(
            tmp_path / "z.md",
            tmp_path,
            type="concept",
            title="Zulu",
            description="Last",
        ),
        _entry(tmp_path / "a.md", tmp_path, type="concept", title="Alpha"),
        _entry(
            tmp_path / "b.md",
            tmp_path,
            type="guide",
            title="Bravo",
            description="A guide",
        ),
    ]
    subdir = tmp_path / "sub"

    first = generate_index(directory, entries, subdirectories=[subdir])
    parsed = parse_index(first)

    # Re-render from parsed index
    lines: list[str] = []
    for section in parsed.sections:
        lines.append(f"# {section.heading}")
        lines.append("")
        for e in section.entries:
            if e.description:
                lines.append(f"* [{e.title}]({e.link}) - {e.description}")
            else:
                lines.append(f"* [{e.title}]({e.link})")
        lines.append("")
    second = "\n".join(lines)

    assert first == second

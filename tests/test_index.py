"""Tests for index file parsing and generation."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from okf_core.index import (
    IndexEntry,
    IndexProblem,
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
    type: object = "concept",
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
    body, problems = generate_index(directory, [a, b])
    assert problems == ()
    assert "# Concept" in body
    assert "* [Alpha](alpha.md)" in body
    assert "* [Beta](beta.md)" in body


def test_generate_mixed_types_sort_order(tmp_path: Path) -> None:
    directory = tmp_path
    a = _entry(tmp_path / "a.md", tmp_path, type="zebra", title="Z Entry")
    b = _entry(tmp_path / "b.md", tmp_path, type="apple", title="A Entry")
    body, problems = generate_index(directory, [a, b])
    assert problems == ()
    apple_pos = body.index("# Apple")
    zebra_pos = body.index("# Zebra")
    assert apple_pos < zebra_pos


def test_generate_entries_alphabetical_within_group(tmp_path: Path) -> None:
    directory = tmp_path
    a = _entry(tmp_path / "z.md", tmp_path, title="Zulu")
    b = _entry(tmp_path / "a.md", tmp_path, title="Alpha")
    body, problems = generate_index(directory, [a, b])
    assert problems == ()
    alpha_pos = body.index("Alpha")
    zulu_pos = body.index("Zulu")
    assert alpha_pos < zulu_pos


def test_generate_with_subdirectories(tmp_path: Path) -> None:
    subdir = tmp_path / "subtopic"
    body, problems = generate_index(tmp_path, [], subdirectories=[subdir])
    assert problems == ()
    assert "# Subdirectories" in body
    assert "* [subtopic](subtopic/)" in body


def test_generate_missing_title_falls_back_to_stem(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "my-file.md", tmp_path, title=None)
    body, problems = generate_index(directory, [e])
    assert problems == ()
    assert "* [my-file](my-file.md)" in body


def test_generate_missing_description_omits_suffix(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, title="Alpha", description=None)
    body, problems = generate_index(directory, [e])
    assert problems == ()
    assert " - " not in body


def test_generate_with_description_included(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, title="Alpha", description="A short desc")
    body, problems = generate_index(directory, [e])
    assert problems == ()
    assert "* [Alpha](a.md) - A short desc" in body


def test_generate_nonstring_type_skipped_and_reported(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(
        tmp_path / "bad.md",
        tmp_path,
        concept_id="bad",
        type=["concept"],
        title="Bad",
    )
    body, problems = generate_index(directory, [e])
    assert "bad.md" not in body
    assert "Bad" not in body
    assert len(problems) == 1
    assert problems[0].concept_id == "bad"
    assert problems[0].path == tmp_path / "bad.md"


def test_generate_missing_type_skipped_and_reported(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, concept_id="a", type=None, title="Orphan")
    body, problems = generate_index(directory, [e])
    assert "Orphan" not in body
    assert len(problems) == 1
    assert problems[0].concept_id == "a"


def test_generate_entry_path_outside_directory_skipped_and_reported(
    tmp_path: Path,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    directory = tmp_path / "docs"
    directory.mkdir()
    e = _entry(other / "a.md", other, concept_id="a", title="Outside")
    body, problems = generate_index(directory, [e])
    assert "Outside" not in body
    assert len(problems) == 1
    assert problems[0].concept_id == "a"


def test_generate_subdirectory_outside_directory_skipped_and_reported(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "docs"
    directory.mkdir()
    outside = tmp_path / "other"
    body, problems = generate_index(directory, [], subdirectories=[outside])
    assert "# Subdirectories" not in body
    assert len(problems) == 1
    assert problems[0].path == outside


def test_generate_describe_directory_hook(tmp_path: Path) -> None:
    subdir = tmp_path / "sub"

    def describe(path: Path) -> str | None:
        return "A subdirectory"

    body, problems = generate_index(
        tmp_path, [], subdirectories=[subdir], describe_directory=describe
    )
    assert problems == ()
    assert "* [sub](sub/) - A subdirectory" in body


def test_generate_describe_directory_none_return(tmp_path: Path) -> None:
    subdir = tmp_path / "sub"

    def describe(path: Path) -> str | None:
        return None

    body, problems = generate_index(
        tmp_path, [], subdirectories=[subdir], describe_directory=describe
    )
    assert problems == ()
    assert "* [sub](sub/)" in body
    assert " - " not in body


def test_generate_nested_subdirectory_link(tmp_path: Path) -> None:
    subdir = tmp_path / "foo" / "bar"
    body, problems = generate_index(tmp_path, [], subdirectories=[subdir])
    assert problems == ()
    assert "* [foo/bar](foo/bar/)" in body
    assert "* [bar](bar/)" not in body


def test_generate_whitespace_only_type_skipped_and_reported(tmp_path: Path) -> None:
    e = _entry(tmp_path / "a.md", tmp_path, concept_id="a", type="   ", title="X")
    body, problems = generate_index(tmp_path, [e])
    assert "X" not in body
    assert len(problems) == 1
    assert problems[0].concept_id == "a"


def test_generate_falsy_title_value_used_not_stem(tmp_path: Path) -> None:
    # title: 0 is falsy but valid YAML — must not fall back to filename stem
    e2 = ConceptManifestEntry(
        concept_id="zero",
        path=tmp_path / "zero.md",
        bundle_root=tmp_path,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType({"type": "concept", "title": 0}),
    )
    body, problems = generate_index(tmp_path, [e2])
    assert problems == ()
    assert "* [0](zero.md)" in body


def test_generate_relative_directory_resolves_correctly(tmp_path: Path) -> None:
    # entries from scan_bundle use absolute paths; directory may be relative
    e = _entry(tmp_path / "a.md", tmp_path, title="Alpha")
    import os

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path.parent)
        rel_dir = Path(tmp_path.name)
        body, problems = generate_index(rel_dir, [e])
    finally:
        os.chdir(cwd)
    assert problems == ()
    assert "* [Alpha](a.md)" in body


def test_generate_empty_string_title_falls_back_to_stem(tmp_path: Path) -> None:
    # title: "" is not None but produces no usable text — fall back to stem
    e = ConceptManifestEntry(
        concept_id="my-doc",
        path=tmp_path / "my-doc.md",
        bundle_root=tmp_path,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType({"type": "concept", "title": ""}),
    )
    body, problems = generate_index(tmp_path, [e])
    assert problems == ()
    assert "* [my-doc](my-doc.md)" in body


def test_generate_title_with_closing_bracket_is_escaped(tmp_path: Path) -> None:
    # ] terminates the markdown link title; must be escaped
    e = _entry(tmp_path / "a.md", tmp_path, title="Foo [Bar]", description=None)
    body, problems = generate_index(tmp_path, [e])
    assert problems == ()
    # [ does not need escaping (only ] terminates the title group)
    assert "* [Foo [Bar\\]](a.md)" in body


def test_generate_link_with_closing_paren_is_escaped(tmp_path: Path) -> None:
    # ) terminates the markdown link target; must be escaped
    e = ConceptManifestEntry(
        concept_id="foo(bar)",
        path=tmp_path / "foo(bar).md",
        bundle_root=tmp_path,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType({"type": "concept", "title": "Foo Bar"}),
    )
    body, problems = generate_index(tmp_path, [e])
    assert problems == ()
    # ( does not need escaping (only ) terminates the link group)
    assert "* [Foo Bar](foo(bar\\).md)" in body


def test_round_trip_metacharacters(tmp_path: Path) -> None:
    e = _entry(tmp_path / "a.md", tmp_path, title="Has ]bracket[", description="desc")
    body, problems = generate_index(tmp_path, [e])
    assert problems == ()
    parsed = parse_index(body)
    assert parsed.sections[0].entries[0].title == "Has ]bracket["
    assert parsed.sections[0].entries[0].link == "a.md"


def test_generate_empty_produces_empty_string(tmp_path: Path) -> None:
    body, problems = generate_index(tmp_path, [])
    assert body == ""
    assert problems == ()


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

    first, problems = generate_index(directory, entries, subdirectories=[subdir])
    assert problems == ()
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

"""Tests for index file parsing and generation."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from okf_core.index import (
    GeneratedIndex,
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
    result = generate_index(directory, [a, b])
    assert result.problems == ()
    assert "# Concept" in result.body
    assert "* [Alpha](alpha.md)" in result.body
    assert "* [Beta](beta.md)" in result.body


def test_generate_returns_generated_index_dataclass(tmp_path: Path) -> None:
    result = generate_index(tmp_path, [])
    assert isinstance(result, GeneratedIndex)


def test_generate_mixed_types_sort_order(tmp_path: Path) -> None:
    directory = tmp_path
    a = _entry(tmp_path / "a.md", tmp_path, type="zebra", title="Z Entry")
    b = _entry(tmp_path / "b.md", tmp_path, type="apple", title="A Entry")
    result = generate_index(directory, [a, b])
    assert result.problems == ()
    apple_pos = result.body.index("# Apple")
    zebra_pos = result.body.index("# Zebra")
    assert apple_pos < zebra_pos


def test_generate_entries_alphabetical_within_group(tmp_path: Path) -> None:
    directory = tmp_path
    a = _entry(tmp_path / "z.md", tmp_path, title="Zulu")
    b = _entry(tmp_path / "a.md", tmp_path, title="Alpha")
    result = generate_index(directory, [a, b])
    assert result.problems == ()
    alpha_pos = result.body.index("Alpha")
    zulu_pos = result.body.index("Zulu")
    assert alpha_pos < zulu_pos


def test_generate_with_subdirectories(tmp_path: Path) -> None:
    subdir = tmp_path / "subtopic"
    result = generate_index(tmp_path, [], subdirectories=[subdir])
    assert result.problems == ()
    assert "# Subdirectories" in result.body
    assert "* [subtopic](subtopic/)" in result.body


def test_generate_missing_title_falls_back_to_stem(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "my-file.md", tmp_path, title=None)
    result = generate_index(directory, [e])
    assert result.problems == ()
    assert "* [my-file](my-file.md)" in result.body


def test_generate_missing_description_omits_suffix(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, title="Alpha", description=None)
    result = generate_index(directory, [e])
    assert result.problems == ()
    assert " - " not in result.body


@pytest.mark.parametrize("desc_value", ["", "   ", "\t"])
def test_generate_empty_description_omits_suffix(
    tmp_path: Path, desc_value: str
) -> None:
    # empty/whitespace-only description is treated as absent — no suffix emitted
    e = ConceptManifestEntry(
        concept_id="a",
        path=tmp_path / "a.md",
        bundle_root=tmp_path,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType({"type": "concept", "description": desc_value}),
    )
    result = generate_index(tmp_path, [e])
    assert result.problems == ()
    assert " - " not in result.body


def test_generate_with_description_included(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, title="Alpha", description="A short desc")
    result = generate_index(directory, [e])
    assert result.problems == ()
    assert "* [Alpha](a.md) - A short desc" in result.body


def test_generate_nonstring_type_skipped_and_reported(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(
        tmp_path / "bad.md",
        tmp_path,
        concept_id="bad",
        type=["concept"],
        title="Bad",
    )
    result = generate_index(directory, [e])
    assert "bad.md" not in result.body
    assert "Bad" not in result.body
    assert len(result.problems) == 1
    assert result.problems[0].concept_id == "bad"
    assert result.problems[0].path == tmp_path / "bad.md"


def test_generate_missing_type_skipped_and_reported(tmp_path: Path) -> None:
    directory = tmp_path
    e = _entry(tmp_path / "a.md", tmp_path, concept_id="a", type=None, title="Orphan")
    result = generate_index(directory, [e])
    assert "Orphan" not in result.body
    assert len(result.problems) == 1
    assert result.problems[0].concept_id == "a"


def test_generate_entry_path_outside_directory_skipped_and_reported(
    tmp_path: Path,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    directory = tmp_path / "docs"
    directory.mkdir()
    e = _entry(other / "a.md", other, concept_id="a", title="Outside")
    result = generate_index(directory, [e])
    assert "Outside" not in result.body
    assert len(result.problems) == 1
    assert result.problems[0].concept_id == "a"


def test_generate_subdirectory_is_directory_itself_skipped_and_reported(
    tmp_path: Path,
) -> None:
    result = generate_index(tmp_path, [], subdirectories=[tmp_path])
    assert "# Subdirectories" not in result.body
    assert len(result.problems) == 1
    assert result.problems[0].path == tmp_path
    assert "index directory itself" in result.problems[0].message


def test_generate_subdirectory_outside_directory_skipped_and_reported(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "docs"
    directory.mkdir()
    outside = tmp_path / "other"
    result = generate_index(directory, [], subdirectories=[outside])
    assert "# Subdirectories" not in result.body
    assert len(result.problems) == 1
    assert result.problems[0].path == outside


def test_generate_describe_directory_hook(tmp_path: Path) -> None:
    subdir = tmp_path / "sub"

    def describe(path: Path) -> str | None:
        return "A subdirectory"

    result = generate_index(
        tmp_path, [], subdirectories=[subdir], describe_directory=describe
    )
    assert result.problems == ()
    assert "* [sub](sub/) - A subdirectory" in result.body


def test_generate_describe_directory_none_return(tmp_path: Path) -> None:
    subdir = tmp_path / "sub"

    def describe(path: Path) -> str | None:
        return None

    result = generate_index(
        tmp_path, [], subdirectories=[subdir], describe_directory=describe
    )
    assert result.problems == ()
    assert "* [sub](sub/)" in result.body
    assert " - " not in result.body


def test_generate_nested_subdirectory_link(tmp_path: Path) -> None:
    subdir = tmp_path / "foo" / "bar"
    result = generate_index(tmp_path, [], subdirectories=[subdir])
    assert result.problems == ()
    assert "* [foo/bar](foo/bar/)" in result.body
    assert "* [bar](bar/)" not in result.body


def test_generate_whitespace_only_type_skipped_and_reported(tmp_path: Path) -> None:
    e = _entry(tmp_path / "a.md", tmp_path, concept_id="a", type="   ", title="X")
    result = generate_index(tmp_path, [e])
    assert "X" not in result.body
    assert len(result.problems) == 1
    assert result.problems[0].concept_id == "a"


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
    result = generate_index(tmp_path, [e2])
    assert result.problems == ()
    assert "* [0](zero.md)" in result.body


def test_generate_relative_directory_resolves_correctly(tmp_path: Path) -> None:
    # entries from scan_bundle use absolute paths; directory may be relative
    e = _entry(tmp_path / "a.md", tmp_path, title="Alpha")
    import os

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path.parent)
        rel_dir = Path(tmp_path.name)
        result = generate_index(rel_dir, [e])
    finally:
        os.chdir(cwd)
    assert result.problems == ()
    assert "* [Alpha](a.md)" in result.body


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
    result = generate_index(tmp_path, [e])
    assert result.problems == ()
    assert "* [my-doc](my-doc.md)" in result.body


def test_generate_title_with_closing_bracket_is_escaped(tmp_path: Path) -> None:
    # Both [ and ] are escaped so the generated markdown is valid CommonMark
    e = _entry(tmp_path / "a.md", tmp_path, title="Foo [Bar]", description=None)
    result = generate_index(tmp_path, [e])
    assert result.problems == ()
    assert "* [Foo \\[Bar\\]](a.md)" in result.body


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
    result = generate_index(tmp_path, [e])
    assert result.problems == ()
    # ( does not need escaping (only ) terminates the link group)
    assert "* [Foo Bar](foo(bar\\).md)" in result.body


def test_round_trip_metacharacters(tmp_path: Path) -> None:
    e = _entry(tmp_path / "a.md", tmp_path, title="Has ]bracket[", description="desc")
    result = generate_index(tmp_path, [e])
    assert result.problems == ()
    parsed = parse_index(result.body)
    assert parsed.sections[0].entries[0].title == "Has ]bracket["
    assert parsed.sections[0].entries[0].link == "a.md"


def test_round_trip_backslash_before_bracket(tmp_path: Path) -> None:
    # backslash must be escaped before ] so \\] is unambiguous on parse
    e = _entry(tmp_path / "a.md", tmp_path, title="foo\\]bar", description=None)
    result = generate_index(tmp_path, [e])
    assert result.problems == ()
    parsed = parse_index(result.body)
    assert parsed.sections[0].entries[0].title == "foo\\]bar"


def test_generate_multiline_title_normalized(tmp_path: Path) -> None:
    # YAML multiline strings can embed \n; title must stay single-line
    e = ConceptManifestEntry(
        concept_id="ml",
        path=tmp_path / "ml.md",
        bundle_root=tmp_path,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType(
            {"type": "concept", "title": "line one\nline two"}
        ),
    )
    result = generate_index(tmp_path, [e])
    assert result.problems == ()
    assert "line one line two" in result.body
    assert "line one\nline two" not in result.body


def test_generate_multiline_description_normalized(tmp_path: Path) -> None:
    # description with embedded \r\n must be collapsed to a single space-joined line
    e = ConceptManifestEntry(
        concept_id="ml",
        path=tmp_path / "ml.md",
        bundle_root=tmp_path,
        mtime_ns=0,
        size=0,
        sha256="",
        frontmatter=MappingProxyType(
            {"type": "concept", "title": "T", "description": "part one\r\npart two"}
        ),
    )
    result = generate_index(tmp_path, [e])
    assert result.problems == ()
    assert "part one part two" in result.body


def test_generate_describe_directory_multiline_normalized(tmp_path: Path) -> None:
    # describe_directory callback returning a multiline string must be normalized
    subdir = tmp_path / "sub"

    def describe(path: Path) -> str | None:
        return "first line\nsecond line"

    result = generate_index(
        tmp_path, [], subdirectories=[subdir], describe_directory=describe
    )
    assert result.problems == ()
    assert "first line second line" in result.body


def test_generate_empty_produces_empty_string(tmp_path: Path) -> None:
    result = generate_index(tmp_path, [])
    assert result.body == ""
    assert result.problems == ()


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


def test_parse_entry_with_inline_code_title_round_trips() -> None:
    content = "# Section\n\n* [`foo`](a.md)\n"
    parsed = parse_index(content)
    assert parsed.sections[0].entries[0].title == "`foo`"
    assert parsed.sections[0].entries[0].link == "a.md"


def test_parse_entry_with_inline_code_description_round_trips() -> None:
    content = "# Section\n\n* [A](a.md) - use `foo`\n"
    parsed = parse_index(content)
    assert parsed.sections[0].entries[0].description == "use `foo`"


def test_parse_nested_ordered_list_items_not_captured() -> None:
    content = "# Section\n\n* [A](a.md)\n  1. [Nested](n.md)\n* [B](b.md)\n"
    parsed = parse_index(content)
    links = [e.link for e in parsed.sections[0].entries]
    assert "a.md" in links
    assert "b.md" in links
    assert "n.md" not in links


def test_parse_list_item_with_multiple_links_is_skipped() -> None:
    content = "# Section\n\n* [A](a.md) and [B](b.md)\n* [C](c.md)\n"
    parsed = parse_index(content)
    # multi-link item is rejected; only the clean single-link item survives
    assert len(parsed.sections[0].entries) == 1
    assert parsed.sections[0].entries[0].link == "c.md"


def test_parse_list_item_with_link_in_description_is_captured() -> None:
    content = "# Section\n\n* [A](a.md) - see [B](b.md)\n"
    parsed = parse_index(content)
    assert len(parsed.sections[0].entries) == 1
    entry = parsed.sections[0].entries[0]
    assert entry.link == "a.md"
    assert entry.description == "see [B](b.md)"


def test_parse_only_first_inline_per_list_item_used() -> None:
    # A list item with two paragraphs produces two inline tokens; only the first matters
    content = "# Section\n\n* [A](a.md)\n\n  [B](b.md)\n\n* [C](c.md)\n"
    parsed = parse_index(content)
    ids = [e.link for e in parsed.sections[0].entries]
    assert "a.md" in ids
    assert "b.md" not in ids
    assert "c.md" in ids


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

    result = generate_index(directory, entries, subdirectories=[subdir])
    assert result.problems == ()
    parsed = parse_index(result.body)

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

    assert result.body == second

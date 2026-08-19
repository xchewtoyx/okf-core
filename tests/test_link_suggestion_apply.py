"""Tests for applying `find_unlinked_mentions` suggestions (#61).

Covers `link_suggestion_href`, `select_link_suggestions`, and the
`plan_link_suggestions_apply`/`apply_link_suggestions` plan/apply pair that
writes selected suggestions into their source concept's body. See
`test_unlinked_mentions.py` for discovery-only coverage of
`find_unlinked_mentions` itself, and `test_section_patching.py` for the
lower-level `plan_markdown_section_append` primitive these functions are
built on.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import assume, example, given
from hypothesis import strategies as st

from okf_core import (
    BundleConfig,
    LinkSuggestion,
    apply_link_suggestions,
    extract_markdown_links,
    find_unlinked_mentions,
    link_suggestion_href,
    plan_link_suggestions_apply,
    select_link_suggestions,
)
from okf_core.graph import _link_suggestion_line


def _bundle(root: Path, *, okf_cache_dir: Path | None = None) -> BundleConfig:
    return BundleConfig(
        name="docs",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
        okf_cache_dir=okf_cache_dir,
    )


def _write_concept(path: Path, *, title: str, body: str = "Body.\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: concept\ntitle: {title}\n---\n{body}",
        encoding="utf-8",
    )


def _suggestion(
    source_path: Path,
    target_path: Path,
    *,
    source_id: str = "source",
    target_id: str = "target",
    title: str = "Target",
) -> LinkSuggestion:
    return LinkSuggestion(
        source_concept_id=source_id,
        source_path=source_path,
        target_concept_id=target_id,
        target_path=target_path,
        matched_text=title,
        target_title=title,
    )


# ---------------------------------------------------------------------------
# link_suggestion_href
# ---------------------------------------------------------------------------


def test_link_suggestion_href_sibling_file(tmp_path: Path) -> None:
    suggestion = _suggestion(tmp_path / "source.md", tmp_path / "alpha.md")

    assert link_suggestion_href(suggestion) == "alpha.md"


def test_link_suggestion_href_subdirectory(tmp_path: Path) -> None:
    suggestion = _suggestion(tmp_path / "source.md", tmp_path / "topics" / "alpha.md")

    assert link_suggestion_href(suggestion) == "topics/alpha.md"


def test_link_suggestion_href_parent_directory(tmp_path: Path) -> None:
    suggestion = _suggestion(tmp_path / "topics" / "source.md", tmp_path / "alpha.md")

    assert link_suggestion_href(suggestion) == "../alpha.md"


def test_link_suggestion_href_percent_encodes_special_characters(
    tmp_path: Path,
) -> None:
    suggestion = _suggestion(tmp_path / "source.md", tmp_path / "alpha beta.md")

    assert link_suggestion_href(suggestion) == "alpha%20beta.md"


# ---------------------------------------------------------------------------
# target_title on LinkSuggestion / find_unlinked_mentions
# ---------------------------------------------------------------------------


def test_find_unlinked_mentions_populates_target_title(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "source.md", title="Source", body="See Alpha for details.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")

    result = find_unlinked_mentions(bundle)

    assert len(result.suggestions) == 1
    assert result.suggestions[0].target_title == "Alpha"


# ---------------------------------------------------------------------------
# select_link_suggestions
# ---------------------------------------------------------------------------


def test_select_link_suggestions_none_selects_everything(tmp_path: Path) -> None:
    alpha = _suggestion(tmp_path / "s.md", tmp_path / "a.md", target_id="alpha")
    beta = _suggestion(tmp_path / "s.md", tmp_path / "b.md", target_id="beta")

    selection = select_link_suggestions([alpha, beta], None)

    assert selection.selected == (alpha, beta)
    assert selection.unmatched_pairs == ()


def test_select_link_suggestions_filters_to_requested_pairs(tmp_path: Path) -> None:
    alpha = _suggestion(tmp_path / "s.md", tmp_path / "a.md", target_id="alpha")
    beta = _suggestion(tmp_path / "s.md", tmp_path / "b.md", target_id="beta")

    selection = select_link_suggestions([alpha, beta], [("source", "beta")])

    assert selection.selected == (beta,)
    assert selection.unmatched_pairs == ()


def test_select_link_suggestions_reports_unmatched_pair(tmp_path: Path) -> None:
    alpha = _suggestion(tmp_path / "s.md", tmp_path / "a.md", target_id="alpha")

    selection = select_link_suggestions([alpha], [("source", "nonexistent")])

    assert selection.selected == ()
    assert selection.unmatched_pairs == (("source", "nonexistent"),)


def test_select_link_suggestions_dedupes_repeated_pair(tmp_path: Path) -> None:
    alpha = _suggestion(tmp_path / "s.md", tmp_path / "a.md", target_id="alpha")

    selection = select_link_suggestions(
        [alpha], [("source", "alpha"), ("source", "alpha")]
    )

    assert selection.selected == (alpha,)


# ---------------------------------------------------------------------------
# plan_link_suggestions_apply / apply_link_suggestions
# ---------------------------------------------------------------------------


def test_apply_link_suggestions_writes_selected_as_inline_links(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "source.md", title="Source", body="See Alpha for details.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")
    mentions = find_unlinked_mentions(bundle)

    result = apply_link_suggestions(bundle, mentions.suggestions)

    assert result.updated_files == (root / "source.md",)
    written = (root / "source.md").read_text(encoding="utf-8")
    assert "## See also" in written
    assert "- [Alpha](alpha.md)" in written


def test_apply_link_suggestions_only_writes_selected_suggestions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "beta.md", title="Beta")
    _write_concept(
        root / "source.md",
        title="Source",
        body="See Alpha and Beta for details.\n",
    )
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")
    mentions = find_unlinked_mentions(bundle)
    selection = select_link_suggestions(mentions.suggestions, [("source", "alpha")])

    apply_link_suggestions(bundle, selection.selected)

    written = (root / "source.md").read_text(encoding="utf-8")
    assert "[Alpha](alpha.md)" in written
    assert "beta.md" not in written


def test_apply_link_suggestions_preserves_existing_content_and_sections(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(
        root / "source.md",
        title="Source",
        body=(
            "Human-written introduction.\n"
            "\n"
            "See Alpha for details.\n"
            "\n"
            "## Unrelated\n"
            "\n"
            "Untouched paragraph.\n"
        ),
    )
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")
    mentions = find_unlinked_mentions(bundle)

    apply_link_suggestions(bundle, mentions.suggestions)

    written = (root / "source.md").read_text(encoding="utf-8")
    assert "Human-written introduction." in written
    assert "## Unrelated" in written
    assert "Untouched paragraph." in written


def test_apply_link_suggestions_groups_same_file_into_one_write(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "beta.md", title="Beta")
    _write_concept(
        root / "source.md",
        title="Source",
        body="See Alpha and Beta for details.\n",
    )
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")
    mentions = find_unlinked_mentions(bundle)
    assert len(mentions.suggestions) == 2

    prep = plan_link_suggestions_apply(bundle, mentions.suggestions)

    assert len(prep.section_plans) == 1
    (only_plan,) = prep.section_plans.values()
    assert "- [Alpha](alpha.md)" in only_plan.proposed_content
    assert "- [Beta](beta.md)" in only_plan.proposed_content


def test_apply_link_suggestions_is_idempotent_on_repeat(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "source.md", title="Source", body="See Alpha for details.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")
    mentions = find_unlinked_mentions(bundle)

    first = apply_link_suggestions(bundle, mentions.suggestions)
    assert first.updated_files == (root / "source.md",)

    second = apply_link_suggestions(bundle, mentions.suggestions)

    assert second.updated_files == ()
    written = (root / "source.md").read_text(encoding="utf-8")
    assert written.count("[Alpha](alpha.md)") == 1


def test_plan_link_suggestions_apply_never_writes(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "source.md", title="Source", body="See Alpha for details.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")
    mentions = find_unlinked_mentions(bundle)
    original = (root / "source.md").read_text(encoding="utf-8")

    plan_link_suggestions_apply(bundle, mentions.suggestions)

    assert (root / "source.md").read_text(encoding="utf-8") == original


def test_apply_link_suggestions_uses_custom_heading_and_level(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write_concept(root / "alpha.md", title="Alpha")
    _write_concept(root / "source.md", title="Source", body="See Alpha for details.\n")
    bundle = _bundle(root, okf_cache_dir=tmp_path / "cache")
    mentions = find_unlinked_mentions(bundle)

    apply_link_suggestions(bundle, mentions.suggestions, heading="Related", level=3)

    written = (root / "source.md").read_text(encoding="utf-8")
    assert "### Related" in written
    assert "## See also" not in written


# ---------------------------------------------------------------------------
# Hypothesis round-trip: `_link_suggestion_line` interpolates a target
# concept's (arbitrary, human-authored) `title` into Markdown link *text*
# (`- [title](href)`) via the shared `markdown_engine` module's
# `link_children`/`render_inline_children` primitives (#199). Per AGENTS.md's
# "Round-Trip Property Tests for Markdown Interpolation" rule, this must
# recover the same title value on reparse across markdown-significant
# characters, not just hand-picked examples -- see test_index_span.py's
# `_TEXT_ALPHABET` for the same rationale applied to a sibling link-text
# renderer: `[`, `]`, and `\` are the characters unescaped link text cannot
# safely carry (an unescaped bracket breaks the enclosing link's own
# delimiter matching); quotes and parens are inert in text position but
# included for parity with that established alphabet. A run of two or more
# consecutive spaces in `title` is excluded, same as test_index_span.py's
# `test_render_span_link_round_trips`: the shared engine's canonical
# renderer collapses an internal whitespace run in literal text content to
# a single space when reconstructing the link (the same "canonical, not
# byte-identical" convergence ADR-0002 documents elsewhere) -- a deliberate
# normalization, not the identity function this test otherwise checks for
# every other character in the alphabet.
# ---------------------------------------------------------------------------

_TITLE_ALPHABET = 'ab01 .-"()[]\\'
# Bare, non-existent paths -- link_suggestion_href only computes a relative
# path (os.path.relpath) and never touches the filesystem, so no tmp_path
# fixture is needed (Hypothesis's own guidance is against pairing @given
# with a function-scoped fixture; see test_markdown_canonical_roundtrip.py).
_SOURCE_PATH = Path("source.md")
_TARGET_PATH = Path("alpha.md")


@given(title=st.text(alphabet=_TITLE_ALPHABET, max_size=16))
@example(title="Alpha [Beta] Gamma")  # balanced brackets
@example(title="Alpha [ Gamma")  # unbalanced bracket
@example(title="Alpha \\ Beta")  # bare backslash
def test_link_suggestion_line_round_trips_title(title: str) -> None:
    assume("  " not in title)
    suggestion = _suggestion(_SOURCE_PATH, _TARGET_PATH, title=title)

    line = _link_suggestion_line(suggestion)
    (link,) = extract_markdown_links(line)

    assert link.text == title
    assert link.target == "alpha.md"

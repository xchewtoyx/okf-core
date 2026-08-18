"""Hypothesis round-trip property tests for the #198 Markdown token-tree engine.

Per AGENTS.md's "Round-Trip Property Tests for Markdown Interpolation" rule
and ADR-0002's canonical-serialization framework: R-C1 requires
serialize-then-parse to recover the same *value* (a data-model comparison,
not byte-for-byte) and serialize itself to be idempotent
(``serialize(parse(serialize(x))) == serialize(x)``). Hand-picked examples in
``test_link_rewrite_patching.py``/``test_section_patching.py`` cover specific
syntax shapes; these tests fuzz markdown-significant characters to reach the
adjacency effects (e.g. a trailing backslash landing next to a link's closing
paren) that hand-picked cases miss.

These exercise the module's private engine functions directly
(``_patch_markdown_section``, ``_rewrite_markdown_links``) rather than the
file-based public API (``plan_markdown_section_patch``/
``plan_markdown_link_rewrite``), which need a real file via ``tmp_path``.
Hypothesis's own guidance is against pairing ``@given`` with a
function-scoped fixture like ``tmp_path`` (it is resolved once per test
*function*, not once per generated example), so this file works directly
against the pure ``content: str -> str`` functions those public entry points
delegate to -- ``path`` is only ever used for error messages there, so a
bare, non-existent ``Path`` is fine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from markdown_it import MarkdownIt

from okf_core import DocumentChangePlanningError
from okf_core.patching import (
    LinkRewrite,
    _patch_markdown_section,
    _rewrite_markdown_links,
)

_PATH = Path("topic.md")
_NORMALIZE = MarkdownIt("commonmark").normalizeLink
_LINK_MARKDOWN = MarkdownIt("commonmark")


def _link_hrefs(content: str) -> list[str]:
    """Every real inline link's href in `content`, in document order."""
    hrefs: list[str] = []
    for token in _LINK_MARKDOWN.parse(content):
        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type != "link_open":
                continue
            href = child.attrGet("href")
            assert isinstance(href, str)
            hrefs.append(href)
    return hrefs


# Markdown-significant characters in link destinations, per AGENTS.md's
# Markdown-interpolation rule: brackets, parens (Hypothesis's generation
# naturally produces unbalanced combinations from this alphabet), the
# backslash escape character, and double quotes.
_TARGET_ALPHABET = 'ab01 .[]()\\"><#?'


@given(new_target=st.text(alphabet=_TARGET_ALPHABET, max_size=20))
def test_link_rewrite_recovers_new_target_value(new_target: str) -> None:
    """serialize(x) round-trips to the same *value* on reparse (R-C1).

    The recovered href is compared after the same normalization
    ``_normalize_target``/markdown-it-py itself applies to any destination
    -- a semantic comparison, not a byte-for-byte one (a literal space and
    its ``%20`` encoding are the same value).
    """
    content = _rewrite_markdown_links(
        _PATH, "[link](old)\n", [LinkRewrite("old", new_target)]
    )

    assert _link_hrefs(content) == [_NORMALIZE(new_target)]


@given(new_target=st.text(alphabet=_TARGET_ALPHABET, max_size=20))
def test_link_rewrite_output_is_idempotent(new_target: str) -> None:
    """serialize(parse(serialize(x))) == serialize(x) (R-C1).

    Re-running the identical rewrite request against the first pass's own
    output must change nothing: by the time of the second call the
    normalized "old" href is no longer present in the document (the first
    pass already rewrote it), so a passing assertion here proves the first
    pass's canonical rendering was already a fixed point -- not merely
    close enough to look unchanged at a glance.
    """
    once = _rewrite_markdown_links(
        _PATH, "[link](old)\n", [LinkRewrite("old", new_target)]
    )
    twice = _rewrite_markdown_links(_PATH, once, [LinkRewrite("old", new_target)])

    assert twice == once


# Alphabet for a replacement section body: the link-destination-significant
# characters above, plus emphasis/code-span/heading-adjacent markers and
# newlines, so generated bodies can span multiple lines and blocks.
_BODY_ALPHABET = 'ab01 .[]()\\"><#?*_`\n'


@given(body=st.text(alphabet=_BODY_ALPHABET, max_size=30))
def test_section_patch_output_is_idempotent(body: str) -> None:
    """serialize(parse(serialize(x))) == serialize(x) for section bodies.

    Requesting the exact same heading/body twice must change nothing the
    second time (R-C3's "no-op never rewrites", the same guarantee
    ``plan_frontmatter_merge`` documents for frontmatter): the second call's
    "existing section" tokens come from *reparsing* the first call's
    rendered output, not from the original raw `body` string, so this
    exercises reparse-of-own-output fidelity, not just parser determinism.

    A body that itself parses to a heading at or above the target level is
    deliberately unrepresentable (`_reject_body_heading_at_or_above_level`
    -- discovered by this exact property test generating ``body="#"``: such
    a body is indistinguishable, after reparse, from the section's own
    boundary, breaking idempotency); that planning error is a documented
    limitation; not the property under test here.
    """
    try:
        once = _patch_markdown_section(_PATH, "", "Target", body, 2)
    except DocumentChangePlanningError:
        return
    twice = _patch_markdown_section(_PATH, once, "Target", body, 2)

    assert twice == once


def test_section_patch_rejects_body_containing_heading_at_or_above_level() -> None:
    with pytest.raises(DocumentChangePlanningError, match="cannot contain a heading"):
        _patch_markdown_section(_PATH, "", "Target", "#", 2)

    # A heading *below* the target level is fine -- it's a genuine
    # subsection, unambiguously part of Target's body on any later reparse.
    result = _patch_markdown_section(_PATH, "", "Target", "### Sub\nBody.", 2)
    assert "### Sub" in result

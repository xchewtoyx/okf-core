from __future__ import annotations

from pathlib import Path
import pytest

from okf_core import (
    BundleConfig,
    DocumentChangePlanningError,
    LinkRewrite,
    plan_markdown_link_rewrite,
)


def _bundle(root: Path) -> BundleConfig:
    return BundleConfig(
        name="test",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
    )


def test_plan_link_rewrite_success(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = (
        "---\n"
        "type: concept\n"
        "---\n"
        'Here is [link1](old_dest1) and [link2](old_dest2 "with title").\n'
        "And [link1 duplicated](old_dest1).\n"
    )
    path.write_text(original, encoding="utf-8")

    rewrites = [
        LinkRewrite("old_dest1", "new_dest1"),
        LinkRewrite("old_dest2", "new_dest2"),
    ]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is True
    assert plan.proposed_content == (
        "---\n"
        "type: concept\n"
        "---\n"
        'Here is [link1](new_dest1) and [link2](new_dest2 "with title").\n'
        "And [link1 duplicated](new_dest1).\n"
    )


def test_plan_link_rewrite_no_matching_targets(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "[link](dest)\n"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("non_existent", "new_dest")]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is False
    assert plan.proposed_content == original


def test_plan_link_rewrite_duplicate_old_targets(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("[link](dest)\n", encoding="utf-8")

    rewrites = [
        LinkRewrite("dest", "new_dest1"),
        LinkRewrite("dest", "new_dest2"),
    ]
    with pytest.raises(DocumentChangePlanningError, match="Duplicate old_target"):
        plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    # Duplicates with space and %20 should also be rejected
    rewrites2 = [
        LinkRewrite("a b", "new1"),
        LinkRewrite("a%20b", "new2"),
    ]
    with pytest.raises(DocumentChangePlanningError, match="Duplicate old_target"):
        plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites2)


def test_plan_link_rewrite_invalid_inputs(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    path.write_text("[link](dest)\n", encoding="utf-8")

    # Not a sequence
    with pytest.raises(
        DocumentChangePlanningError, match="must be a sequence of LinkRewrite objects"
    ):
        plan_markdown_link_rewrite(_bundle(tmp_path), path, "not a sequence")  # type: ignore

    # None in sequence
    with pytest.raises(
        DocumentChangePlanningError,
        match="Element at index 0 in rewrites is not a LinkRewrite object",
    ):
        plan_markdown_link_rewrite(_bundle(tmp_path), path, [None])  # type: ignore

    # Non-LinkRewrite element
    with pytest.raises(
        DocumentChangePlanningError,
        match="Element at index 0 in rewrites is not a LinkRewrite object",
    ):
        plan_markdown_link_rewrite(
            _bundle(tmp_path), path, [{"old_target": "a", "new_target": "b"}]  # type: ignore
        )

    # Non-string targets
    with pytest.raises(
        DocumentChangePlanningError, match="must have string old_target and new_target"
    ):
        plan_markdown_link_rewrite(_bundle(tmp_path), path, [LinkRewrite(123, "new_dest")])  # type: ignore


@pytest.mark.parametrize(
    ("original", "old_target", "new_target", "expected"),
    [
        pytest.param("[link](old)", "old", "new", "[link](new)", id="standard"),
        pytest.param(
            "[some link text](old)",
            "old",
            "new",
            "[some link text](new)",
            id="spaces_in_link_text",
        ),
        pytest.param(
            "[link\ntext](old)",
            "old",
            "new",
            "[link\ntext](new)",
            id="newline_in_link_text",
        ),
        pytest.param(
            "[a\\]b](old)",
            "old",
            "new",
            "[a\\]b](new)",
            id="escaped_brackets_in_link_text",
        ),
        pytest.param(
            "[link](<old>)", "old", "new", "[link](<new>)", id="bracketed_destination"
        ),
        pytest.param(
            '[link](old "title")',
            "old",
            "new",
            '[link](new "title")',
            id="double_quoted_title",
        ),
        pytest.param(
            "[link](old 'title')",
            "old",
            "new",
            "[link](new 'title')",
            id="single_quoted_title",
        ),
        pytest.param(
            "[link](old (title))",
            "old",
            "new",
            "[link](new (title))",
            id="parenthesized_title",
        ),
        pytest.param(
            "[link](<old target>)",
            "old target",
            "new target",
            "[link](<new target>)",
            id="spaces_in_bracketed_destination",
        ),
        pytest.param(
            "[link](old\\)target)",
            "old)target",
            "new)target",
            "[link](<new)target>)",
            id="escaped_parenthesis_in_unbracketed_destination",
        ),
        pytest.param(
            "[link](old#sec)",
            "old#sec",
            "new#sec",
            "[link](new#sec)",
            id="fragment_destination",
        ),
        pytest.param(
            "[link](foo%2Fbar)",
            "foo%2Fbar",
            "foo%2Fnew",
            "[link](foo%2Fnew)",
            id="percent_encoded_destination",
        ),
        pytest.param(
            "[link](<a b>)",
            "a%20b",
            "new b",
            "[link](<new b>)",
            id="percent_encoded_old_target_matches_bracketed_space",
        ),
        pytest.param(
            "[intro](café.md)",
            "café.md",
            "intro.md",
            "[intro](intro.md)",
            id="non_ascii_percent_encoded_href",
        ),
        pytest.param(
            "\\![label](old)",
            "old",
            "new",
            "\\![label](new)",
            id="escaped_image_marker_is_a_real_link",
        ),
        pytest.param(
            "[link](<old>)",
            "old",
            "new>target",
            "[link](<new\\>target>)",
            id="angle_bracket_in_new_target_is_escaped",
        ),
        pytest.param(
            "[a [b] c](old)",
            "old",
            "new",
            "[a [b] c](new)",
            id="nested_brackets_in_label",
        ),
        pytest.param(
            "Code link: `[link](old)` and real [link](old)\n",
            "old",
            "new",
            "Code link: `[link](old)` and real [link](new)\n",
            id="mixed_inline_code",
        ),
        pytest.param(
            "Here is a real [link](old)\n```markdown\n[link](old)\n```\n",
            "old",
            "new",
            "Here is a real [link](new)\n```markdown\n[link](old)\n```\n",
            id="mixed_fenced_code",
        ),
        pytest.param(
            "`[fake](old)` and [a [b] c](old)",
            "old",
            "new",
            "`[fake](old)` and [a [b] c](new)",
            id="fake_code_span_and_nested_bracket_label_disambiguated",
        ),
    ],
)
def test_plan_link_rewrite_valid_syntax_variations(
    tmp_path: Path,
    original: str,
    old_target: str,
    new_target: str,
    expected: str,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite(old_target, new_target)]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is True
    assert plan.proposed_content == expected


@pytest.mark.parametrize(
    ("original", "old_target", "new_target"),
    [
        pytest.param("![alt](old)", "old", "new", id="image_link"),
        pytest.param(
            '---\ntype: concept\ndescription: "[guide](old)"\n---\nBody content.\n',
            "old",
            "new",
            id="frontmatter_link_shape",
        ),
        pytest.param("\\[link](old)", "old", "new", id="escaped_brackets_prefix"),
        pytest.param("Code link: `[link](old)`", "old", "new", id="inline_code_link"),
        pytest.param(
            "``[fake](old) ` more``",
            "old",
            "new",
            id="inline_code_span_double_backtick_fence",
        ),
        pytest.param(
            "```markdown\n[link](old)\n```\n", "old", "new", id="fenced_code_link"
        ),
        pytest.param(
            "````markdown\n[link](old)\n````\n",
            "old",
            "new",
            id="longer_backtick_fence",
        ),
        pytest.param("~~~markdown\n[link](old)\n~~~\n", "old", "new", id="tilde_fence"),
    ],
)
def test_plan_link_rewrite_ignored_syntax_variations(
    tmp_path: Path,
    original: str,
    old_target: str,
    new_target: str,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite(old_target, new_target)]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is False
    assert plan.proposed_content == original


@pytest.mark.parametrize(
    ("original", "old_target", "new_target"),
    [
        pytest.param(
            "[link][ref]\n\n[ref]: old\n", "old", "new", id="reference_style_link"
        ),
        pytest.param(
            "[ref]\n\n[ref]: old\n", "old", "new", id="shortcut_reference_link"
        ),
        pytest.param(
            "[link][ref] and `[fake](old)`\n\n[ref]: old\n",
            "old",
            "new",
            id="mixed_reference_and_code_fake_link",
        ),
    ],
)
def test_plan_link_rewrite_mismatch_rejections(
    tmp_path: Path,
    original: str,
    old_target: str,
    new_target: str,
) -> None:
    path = tmp_path / "topic.md"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite(old_target, new_target)]
    with pytest.raises(
        DocumentChangePlanningError,
        match="Link target mismatch|Reference-style links",
    ):
        plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

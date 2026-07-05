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


def test_plan_link_rewrite_ignores_inline_code_links(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "Code link: `[link](dest)`\n"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("dest", "new_dest")]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is False
    assert plan.proposed_content == original


def test_plan_link_rewrite_ignores_fenced_code_block_links(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "```markdown\n[link](dest)\n```\n"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("dest", "new_dest")]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is False
    assert plan.proposed_content == original


def test_plan_link_rewrite_rejects_reference_style_links(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "Here is a [link][ref].\n" "\n" "[ref]: dest\n"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("dest", "new_dest")]
    with pytest.raises(DocumentChangePlanningError, match="Link target mismatch"):
        plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)


def test_plan_link_rewrite_ignores_image_links(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "![alt](dest)\n"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("dest", "new_dest")]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is False
    assert plan.proposed_content == original


def test_plan_link_rewrite_ignores_frontmatter_link_shapes(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = (
        "---\n"
        "type: concept\n"
        'description: "[guide](dest)"\n'
        "---\n"
        "Body content.\n"
    )
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("dest", "new_dest")]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    # Frontmatter shouldn't be touched by the body link rewrite
    assert plan.changed is False
    assert plan.proposed_content == original


def test_plan_link_rewrite_handles_escaped_brackets_in_link_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    original = "Here is [a\\]b](dest).\n"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("dest", "new_dest")]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is True
    assert plan.proposed_content == "Here is [a\\]b](new_dest).\n"


def test_plan_link_rewrite_handles_escaped_parentheses_in_target(
    tmp_path: Path,
) -> None:
    path = tmp_path / "topic.md"
    original = "Here is [link](old\\)target).\n"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("old)target", "new)target")]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is True
    # If the new target contains spaces or parentheses, it gets wrapped in angle brackets
    assert plan.proposed_content == "Here is [link](<new)target>).\n"


def test_plan_link_rewrite_handles_bracketed_target(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    original = "Here is [link](<old target>).\n"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("old target", "new target")]
    plan = plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

    assert plan.changed is True
    assert plan.proposed_content == "Here is [link](<new target>).\n"


def test_plan_link_rewrite_detects_count_cancellation(tmp_path: Path) -> None:
    path = tmp_path / "topic.md"
    # AST shows 1 dest (reference link)
    # Literal count not in code spans shows 0 dest (no inline link target dest)
    # Fake link is in a code block so it is ignored by literal count
    original = "Here is `[fake](dest)` and [real][ref].\n" "\n" "[ref]: dest\n"
    path.write_text(original, encoding="utf-8")

    rewrites = [LinkRewrite("dest", "new_dest")]
    with pytest.raises(DocumentChangePlanningError, match="Link target mismatch"):
        plan_markdown_link_rewrite(_bundle(tmp_path), path, rewrites)

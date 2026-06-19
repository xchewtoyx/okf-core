from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import (
    ConceptPathError,
    concept_path_bundle_root,
    concept_id_to_path,
    is_reserved_concept_path,
    load_config,
    path_to_concept_id,
)
from okf_core.paths import _path_to_concept_id_in_root
from okf_core.config import BundleConfig


def test_nested_concept_id_resolves_under_first_bundle_root(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge", tmp_path / "notes")

    assert concept_id_to_path("topics/example", bundle) == (
        tmp_path / "knowledge" / "topics" / "example.md"
    )


def test_concept_id_can_target_explicit_configured_root(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge", tmp_path / "notes")

    assert concept_id_to_path("topics/example", bundle, bundle_root=tmp_path / "notes") == (
        tmp_path / "notes" / "topics" / "example.md"
    )


def test_explicit_bundle_root_must_be_configured(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge")

    with pytest.raises(ConceptPathError, match="not configured"):
        concept_id_to_path("topics/example", bundle, bundle_root=tmp_path / "other")


@pytest.mark.parametrize(
    "concept_id",
    [
        "",
        "   ",
        "./topic",
        "../outside",
        "topics/../outside",
        "topics/./example",
        "topics//example",
        "topics/",
        "topics/.",
        "/absolute",
        "C:/absolute-on-windows",
        "topics:name/example",
        "topics/example.md",
        r"topics\example",
    ],
)
def test_unsafe_concept_ids_are_rejected(
    tmp_path: Path,
    concept_id: str,
) -> None:
    bundle = _bundle(tmp_path / "knowledge")

    with pytest.raises(ConceptPathError):
        concept_id_to_path(concept_id, bundle)


@pytest.mark.parametrize("concept_id", ["index", "Index", "topics/log", "topics/LOG"])
def test_reserved_filenames_are_not_normal_concepts(
    tmp_path: Path,
    concept_id: str,
) -> None:
    bundle = _bundle(tmp_path / "knowledge")

    with pytest.raises(ConceptPathError, match="Reserved filename"):
        concept_id_to_path(concept_id, bundle)


def test_markdown_path_round_trips_to_concept_id(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge")
    path = concept_id_to_path("topics/example", bundle)

    assert path_to_concept_id(path, bundle) == "topics/example"


def test_path_to_concept_id_resolves_matching_bundle_root(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge", tmp_path / "notes")

    assert path_to_concept_id(tmp_path / "notes" / "topic.md", bundle) == "topic"


def test_path_to_concept_id_prefers_deepest_matching_root(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, tmp_path / "knowledge")

    assert path_to_concept_id(tmp_path / "knowledge" / "topic.md", bundle) == "topic"


def test_known_root_concept_id_helper_matches_public_resolution(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "knowledge")
    path = tmp_path / "knowledge" / "topic.md"
    owning_root = concept_path_bundle_root(path, bundle)

    assert _path_to_concept_id_in_root(path, owning_root, bundle) == (
        path_to_concept_id(path, bundle)
    )


def test_concept_path_bundle_root_prefers_deepest_matching_root(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "knowledge")

    assert concept_path_bundle_root(tmp_path / "knowledge" / "topic.md", bundle) == (
        tmp_path / "knowledge"
    )


def test_concept_path_bundle_root_requires_configured_roots() -> None:
    bundle = _bundle()

    with pytest.raises(ConceptPathError, match="Bundle has no roots"):
        concept_path_bundle_root("topic.md", bundle)


def test_path_outside_bundle_roots_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge")

    with pytest.raises(ConceptPathError, match="outside configured bundle roots"):
        path_to_concept_id(tmp_path / "other" / "topic.md", bundle)


def test_non_markdown_path_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge")

    with pytest.raises(ConceptPathError, match="Markdown file"):
        path_to_concept_id(tmp_path / "knowledge" / "topic.txt", bundle)


def test_path_to_concept_id_rejects_reserved_markdown_path(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge")

    with pytest.raises(ConceptPathError, match="Reserved filename"):
        path_to_concept_id(tmp_path / "knowledge" / "index.md", bundle)


def test_path_to_concept_id_rejects_reserved_markdown_path_case_insensitively(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path / "knowledge")

    with pytest.raises(ConceptPathError, match="Reserved filename"):
        path_to_concept_id(tmp_path / "knowledge" / "Index.md", bundle)


def test_reserved_paths_can_be_detected_without_raising(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge")

    assert is_reserved_concept_path(tmp_path / "knowledge" / "index.md", bundle)
    assert is_reserved_concept_path(tmp_path / "knowledge" / "Index.md", bundle)
    assert not is_reserved_concept_path(tmp_path / "knowledge" / "topic.md", bundle)


def test_configured_relative_path_strategy_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_roots = ["docs", "notes"]
reserved_filenames = ["README.md"]
""".strip(),
        encoding="utf-8",
    )
    bundle = load_config(config_path=config_path).bundles["default"]

    path = concept_id_to_path("nested/topic", bundle, bundle_root=tmp_path / "notes")

    assert path == tmp_path / "notes" / "nested" / "topic.md"
    assert path_to_concept_id(path, bundle) == "nested/topic"


def test_unsupported_path_strategy_fails_clearly(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "knowledge", concept_path_strategy="slug")

    with pytest.raises(ConceptPathError, match="Unsupported concept path strategy"):
        concept_id_to_path("topic", bundle)


def _bundle(
    *roots: Path,
    concept_path_strategy: str = "relative-path",
) -> BundleConfig:
    return BundleConfig(
        name="test",
        bundle_roots=tuple(root.resolve(strict=False) for root in roots),
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy=concept_path_strategy,
        index_cache=Path(".okf-cache"),
    )

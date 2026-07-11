"""Direct unit tests for ``okf_core.index._load_directory_metadata``.

``generate_index`` exercises this helper indirectly (see
``tests/test_index.py``); these tests pin down its return contract on its
own, including the profile-aware validation path.
"""

from __future__ import annotations

from pathlib import Path

from okf_core import ProfileConfig, TaxonomyConfig
from okf_core.index import _load_directory_metadata


def test_missing_file_returns_empty_with_no_problems(tmp_path: Path) -> None:
    meta_path = tmp_path / "_directory.yml"

    meta_data, problems = _load_directory_metadata(meta_path, None, None)

    assert meta_data == {}
    assert problems == []


def test_empty_yaml_file_returns_empty_with_no_problems(tmp_path: Path) -> None:
    meta_path = tmp_path / "_directory.yml"
    meta_path.write_text("", encoding="utf-8")

    meta_data, problems = _load_directory_metadata(meta_path, None, None)

    assert meta_data == {}
    assert problems == []


def test_non_mapping_yaml_reports_problem_with_empty_metadata(tmp_path: Path) -> None:
    meta_path = tmp_path / "_directory.yml"
    meta_path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    meta_data, problems = _load_directory_metadata(meta_path, None, None)

    assert meta_data == {}
    assert len(problems) == 1
    assert problems[0].path == meta_path
    assert (
        "invalid metadata file _directory.yml: content must be a YAML mapping"
        in problems[0].message
    )


def test_non_string_keys_reports_problem_with_empty_metadata(tmp_path: Path) -> None:
    meta_path = tmp_path / "_directory.yml"
    meta_path.write_text("type: _directory\n123: Numeric Key\n", encoding="utf-8")

    meta_data, problems = _load_directory_metadata(meta_path, None, None)

    assert meta_data == {}
    assert len(problems) == 1
    assert problems[0].path == meta_path
    assert "YAML frontmatter keys must be strings" in problems[0].message


def test_invalid_yaml_syntax_reports_parse_problem(tmp_path: Path) -> None:
    meta_path = tmp_path / "_directory.yml"
    meta_path.write_text("{invalid yaml: [\n", encoding="utf-8")

    meta_data, problems = _load_directory_metadata(meta_path, None, None)

    assert meta_data == {}
    assert len(problems) == 1
    assert problems[0].path == meta_path
    assert "failed to parse metadata file _directory.yml" in problems[0].message


def test_validation_findings_surfaced_as_problems_with_profile(
    tmp_path: Path,
) -> None:
    meta_path = tmp_path / "_directory.yml"
    meta_path.write_text("type: concept\n", encoding="utf-8")

    profile = ProfileConfig(taxonomy=TaxonomyConfig(allowed_types=("decision",)))

    meta_data, problems = _load_directory_metadata(meta_path, profile, None)

    assert meta_data == {"type": "concept"}
    assert len(problems) == 1
    assert problems[0].path == meta_path
    assert (
        "validation error: [type] Concept type 'concept' is not allowed by this profile"
        in problems[0].message
    )

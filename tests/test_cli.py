"""Tests for the okf-core CLI."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from okf_core.cli import cli


def _runner() -> CliRunner:
    return CliRunner()


def _write_concept(path: Path, *, title: str, type_: str = "concept") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {type_}\ntitle: {title}\n---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------


def test_help_exits_zero_and_lists_commands() -> None:
    result = _runner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.stdout
    assert "validate" in result.stdout
    assert "index" in result.stdout


def test_scan_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["scan", "--help"]).exit_code == 0


def test_validate_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["validate", "--help"]).exit_code == 0


def test_index_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["index", "--help"]).exit_code == 0


# ---------------------------------------------------------------------------
# okf scan
# ---------------------------------------------------------------------------


def test_scan_emits_json_manifest(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["scan", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["bundle"] == "default"
    assert len(data["concepts"]) == 1
    assert data["concepts"][0]["concept_id"] == "example"
    assert data["concepts"][0]["frontmatter"]["title"] == "Example"
    assert data["problems"] == []


def test_scan_empty_bundle_exits_zero(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )

    result = _runner().invoke(cli, ["scan", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["concepts"] == []
    assert data["problems"] == []


def test_scan_reports_malformed_documents_in_problems(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "broken.md").write_text(
        "---\ntype: [invalid\n---\nBody\n", encoding="utf-8"
    )
    _write_concept(tmp_path / "good.md", title="Good")

    result = _runner().invoke(cli, ["scan", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data["concepts"]) == 1
    assert len(data["problems"]) == 1
    assert "broken.md" in data["problems"][0]["path"]


def test_scan_date_frontmatter_serializes_to_json(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    # PyYAML parses bare YYYY-MM-DD values as datetime.date, which json.dumps
    # cannot serialize without help from _to_serializable.
    (tmp_path / "dated.md").write_text(
        "---\ntype: concept\ntitle: Dated\ntimestamp: 2024-01-01\n---\nBody\n",
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["scan", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data["concepts"]) == 1
    assert data["concepts"][0]["frontmatter"]["timestamp"] == "2024-01-01"


def test_scan_bundle_option_selects_named_bundle(tmp_path: Path) -> None:
    alt_root = tmp_path / "alt"
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[bundles.alt]\nbundle_root = "{alt_root}"\n', encoding="utf-8"
    )
    _write_concept(alt_root / "concept.md", title="Alt")

    result = _runner().invoke(
        cli, ["scan", "--config", str(config_path), "--bundle", "alt"]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["bundle"] == "alt"
    assert len(data["concepts"]) == 1


def test_scan_unknown_bundle_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text("[defaults]\n", encoding="utf-8")

    result = _runner().invoke(
        cli, ["scan", "--config", str(config_path), "--bundle", "missing"]
    )

    assert result.exit_code == 2


def test_scan_config_error_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("[defaults]\nunknown_key = true\n", encoding="utf-8")

    result = _runner().invoke(cli, ["scan", "--config", str(config_path)])

    assert result.exit_code == 2


def test_scan_no_config_uses_defaults_no_error() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["scan"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "concepts" in data


# ---------------------------------------------------------------------------
# okf validate
# ---------------------------------------------------------------------------


def test_validate_no_findings_exits_zero(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "valid.md", title="Valid")

    result = _runner().invoke(cli, ["validate", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["findings"] == {}


def test_validate_errors_exit_1(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    bad = tmp_path / "no_type.md"
    bad.write_text("---\ntitle: Missing Type\n---\nBody\n", encoding="utf-8")

    result = _runner().invoke(cli, ["validate", "--config", str(config_path)])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert str(bad) in data["findings"]
    assert data["findings"][str(bad)][0]["severity"] == "error"


def test_validate_warnings_only_exit_zero(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    # Taxonomy warnings only fire when a profile is configured on the bundle.
    # type: note is not in known_types → warning, not error.
    config_path.write_text(
        f"""
[taxonomy]
known_types = ["concept"]

[profiles.typed]
optional_frontmatter = []

[bundles.default]
bundle_root = "{tmp_path}"
profile = "typed"
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "other.md").write_text(
        "---\ntype: note\ntitle: A\n---\nBody\n", encoding="utf-8"
    )

    result = _runner().invoke(cli, ["validate", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    path_key = str(tmp_path / "other.md")
    assert path_key in data["findings"]
    assert all(f["severity"] == "warning" for f in data["findings"][path_key])


def test_validate_with_profile_checks_required_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[defaults]
bundle_root = "{tmp_path}"

[profiles.strict]
required_frontmatter = ["type", "title", "status"]

[bundles.default]
bundle_root = "{tmp_path}"
profile = "strict"
""".strip(),
        encoding="utf-8",
    )
    _write_concept(tmp_path / "missing_status.md", title="No Status")

    result = _runner().invoke(cli, ["validate", "--config", str(config_path)])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    path_key = str(tmp_path / "missing_status.md")
    assert path_key in data["findings"]
    assert any("status" in f["message"] for f in data["findings"][path_key])


def test_validate_config_error_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("[defaults]\nunknown = true\n", encoding="utf-8")

    result = _runner().invoke(cli, ["validate", "--config", str(config_path)])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# okf index
# ---------------------------------------------------------------------------


def test_index_writes_index_md(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 0
    index_path = tmp_path / "index.md"
    assert index_path.exists()
    assert "Example" in index_path.read_text(encoding="utf-8")


def test_index_emits_json_with_path_and_entry_count(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="A")
    _write_concept(tmp_path / "b.md", title="B")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["path"] == str(tmp_path / "index.md")
    assert data["entries"] == 2
    assert data["problems"] == []


def test_index_skipped_entries_exit_1(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    # No type field — generate_index will skip and report as a problem
    (tmp_path / "no_type.md").write_text(
        "---\ntitle: No Type\n---\nBody\n", encoding="utf-8"
    )

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert len(data["problems"]) == 1


def test_index_entries_count_excludes_skipped(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "valid.md", title="Valid")
    (tmp_path / "no_type.md").write_text(
        "---\ntitle: No Type\n---\nBody\n", encoding="utf-8"
    )

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["entries"] == 1
    assert len(data["problems"]) == 1


def test_index_scan_problems_exit_1(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "valid.md", title="Valid")
    (tmp_path / "broken.md").write_text(
        "---\ntype: [invalid\n---\nBody\n", encoding="utf-8"
    )

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["entries"] == 1
    assert data["problems"] == []
    assert len(data["scan_problems"]) == 1
    assert "broken.md" in data["scan_problems"][0]["path"]


def test_index_directory_option_generates_for_subdirectory(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    subdir = tmp_path / "topics"
    _write_concept(subdir / "a.md", title="A")
    _write_concept(subdir / "b.md", title="B")
    _write_concept(tmp_path / "root.md", title="Root")

    result = _runner().invoke(
        cli, ["index", "--config", str(config_path), "--directory", str(subdir)]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["entries"] == 2
    assert (subdir / "index.md").exists()
    assert not (tmp_path / "index.md").exists()


def test_index_directory_outside_bundle_exits_2(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{bundle_root}"\n', encoding="utf-8"
    )
    outside = tmp_path / "other"

    result = _runner().invoke(
        cli, ["index", "--config", str(config_path), "--directory", str(outside)]
    )

    assert result.exit_code == 2


def test_index_config_error_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("[defaults]\nunknown = true\n", encoding="utf-8")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 2

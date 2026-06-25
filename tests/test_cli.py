"""Tests for the okf-core CLI."""

from __future__ import annotations

import json
import inspect
from pathlib import Path

from click.testing import CliRunner

from okf_core.cli import cli
import pytest
from typing import Any


@pytest.fixture(autouse=True)
def _patch_toml_write(monkeypatch: pytest.MonkeyPatch) -> None:
    original_write_text = Path.write_text

    def new_write_text(self: Path, data: str, *args: Any, **kwargs: Any) -> Any:
        if self.suffix == ".toml":
            data = data.replace("\\", "/")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", new_write_text)


def _runner() -> CliRunner:
    if "mix_stderr" in inspect.signature(CliRunner).parameters:
        kwargs: dict[str, Any] = {"mix_stderr": False}
        return CliRunner(**kwargs)
    return CliRunner()


def _write_concept(path: Path, *, title: str, type_: str = "concept") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {type_}\ntitle: {title}\n---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_future_root_index(root: Path) -> None:
    (root / "index.md").write_text(
        "---\nokf_version: '0.2'\n---\n# Future Bundle\n",
        encoding="utf-8",
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
    assert "graph" in result.stdout
    assert "list-concepts" in result.stdout
    assert "context" in result.stdout
    assert "list-bundles" in result.stdout


def test_scan_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["scan", "--help"]).exit_code == 0


def test_validate_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["validate", "--help"]).exit_code == 0


def test_list_concepts_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["list-concepts", "--help"]).exit_code == 0


def test_index_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["index", "--help"]).exit_code == 0


def test_graph_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["graph", "--help"]).exit_code == 0


def test_context_help_documents_core_options() -> None:
    result = _runner().invoke(cli, ["context", "--help"])
    assert result.exit_code == 0
    assert "--seed" in result.stdout
    assert "--depth" in result.stdout
    assert "--direction" in result.stdout
    assert "--budget-chars" in result.stdout


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
    # PyYAML parses bare YYYY-MM-DD values as datetime.date;
    # _freeze_value converts them to ISO strings, verified here end-to-end.
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
    runner = _runner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["scan"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "concepts" in data


def test_scan_consumes_future_version_bundle_best_effort(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_future_root_index(tmp_path)
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["scan", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["concepts"][0]["concept_id"] == "example"
    assert data["problems"] == []


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
    # A type of "note" is not in known_types → warning, not error.
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


@pytest.mark.parametrize("quiet_flag", ["--quiet", "-q"])
def test_validate_quiet_success(tmp_path: Path, quiet_flag: str) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "valid.md", title="Valid")

    result = _runner().invoke(
        cli, ["validate", "--config", str(config_path), quiet_flag]
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    if hasattr(result, "stderr") and result.stderr is not None:
        assert result.stderr == ""


@pytest.mark.parametrize("quiet_flag", ["--quiet", "-q"])
def test_validate_quiet_errors(tmp_path: Path, quiet_flag: str) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    bad = tmp_path / "no_type.md"
    bad.write_text("---\ntitle: Missing Type\n---\nBody\n", encoding="utf-8")

    result = _runner().invoke(
        cli, ["validate", "--config", str(config_path), quiet_flag]
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    if hasattr(result, "stderr") and result.stderr is not None:
        assert result.stderr == ""


@pytest.mark.parametrize("quiet_flag", ["--quiet", "-q"])
def test_validate_quiet_config_error(tmp_path: Path, quiet_flag: str) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text("[defaults]\n", encoding="utf-8")

    result = _runner().invoke(
        cli,
        ["validate", "--config", str(config_path), quiet_flag, "--bundle", "missing"],
    )

    assert result.exit_code == 2
    mixed_output = result.stdout + (getattr(result, "stderr", "") or "")
    assert "not found" in mixed_output


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
# okf list-concepts
# ---------------------------------------------------------------------------


def test_list_concepts_emits_seed_discovery_json(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[defaults]
bundle_root = "{tmp_path}"
listing_fields = ["activity"]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "triage.md").write_text(
        "---\ntype: Playbook\ntitle: Triage\nactivity: [debug, repair]\n---\nBody\n",
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["list-concepts", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["bundle"] == "default"
    assert data["concepts"][0]["concept_id"] == "triage"
    assert data["concepts"][0]["type"] == "Playbook"
    assert data["concepts"][0]["fields"] == {"activity": ["debug", "repair"]}
    assert data["concepts"][0]["frontmatter"]["activity"] == ["debug", "repair"]
    assert data["concepts"][0]["outbound_link_count"] is None
    assert data["problems"] == []


def test_list_concepts_reports_invalid_types_without_failing(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "bad.md").write_text("---\ntitle: Bad\n---\nBody\n", encoding="utf-8")

    result = _runner().invoke(cli, ["list-concepts", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["concepts"] == []
    assert data["problems"][0]["concept_id"] == "bad"
    assert data["problems"][0]["kind"] == "missing-type"


def test_list_concepts_with_graph_counts(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [B](b.md).\n",
        encoding="utf-8",
    )
    _write_concept(tmp_path / "b.md", title="B")

    result = _runner().invoke(
        cli, ["list-concepts", "--config", str(config_path), "--with-graph-counts"]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    by_id = {concept["concept_id"]: concept for concept in data["concepts"]}
    assert by_id["a"]["outbound_link_count"] == 1
    assert by_id["a"]["inbound_link_count"] == 0
    assert by_id["b"]["outbound_link_count"] == 0
    assert by_id["b"]["inbound_link_count"] == 1


def test_list_concepts_config_error_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("[defaults]\nunknown = true\n", encoding="utf-8")

    result = _runner().invoke(cli, ["list-concepts", "--config", str(config_path)])

    assert result.exit_code == 2


def test_list_concepts_consumes_future_version_bundle_best_effort(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_future_root_index(tmp_path)
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["list-concepts", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["concepts"][0]["concept_id"] == "example"
    assert data["problems"] == []


def test_list_concepts_with_content_flag(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: Alpha\n---\nHello World\n",
        encoding="utf-8",
    )

    result = _runner().invoke(
        cli, ["list-concepts", "--config", str(config_path), "--with-content"]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["concepts"][0]["concept_id"] == "a"
    assert data["concepts"][0]["content"] == "Hello World\n"
    assert data["problems"] == []


def test_list_concepts_content_is_null_without_flag(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: Alpha\n---\nHello World\n",
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["list-concepts", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["concepts"][0]["concept_id"] == "a"
    assert data["concepts"][0]["content"] is None
    assert data["problems"] == []


# ---------------------------------------------------------------------------
# okf context
# ---------------------------------------------------------------------------


def test_context_seed_only_emits_json_pack(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "a.md"
    concept_path.write_text(
        "---\ntype: concept\ntitle: A\n---\nBody A\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _runner().invoke(
        cli,
        ["context", "--config", str(config_path), "--seed", "a", "--depth", "0"],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["bundle"] == "default"
    assert data["seeds"] == ["a"]
    assert data["omitted_concept_ids"] == []
    assert data["problems"] == []
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["concept_id"] == "a"
    assert entry["path"] == str(concept_path)
    assert entry["title"] == "A"
    assert entry["selection_reason"] == "seed"
    assert entry["graph_distance"] == 0
    assert entry["content"] == concept_path.read_text(encoding="utf-8")
    assert entry["char_count"] == len(entry["content"])


def test_context_outbound_expansion(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [B](b.md).\n",
        encoding="utf-8",
    )
    _write_concept(tmp_path / "b.md", title="B")

    result = _runner().invoke(
        cli,
        [
            "context",
            "--config",
            str(config_path),
            "--seed",
            "a",
            "--direction",
            "outbound",
            "--depth",
            "1",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert [entry["concept_id"] for entry in data["entries"]] == ["a", "b"]
    assert data["entries"][1]["selection_reason"] == "outbound-link"
    assert data["entries"][1]["graph_distance"] == 1


def test_context_inbound_expansion(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [B](b.md).\n",
        encoding="utf-8",
    )
    _write_concept(tmp_path / "b.md", title="B")

    result = _runner().invoke(
        cli,
        [
            "context",
            "--config",
            str(config_path),
            "--seed",
            "b",
            "--direction",
            "inbound",
            "--depth",
            "1",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert [entry["concept_id"] for entry in data["entries"]] == ["b", "a"]
    assert data["entries"][1]["selection_reason"] == "backlink"
    assert data["entries"][1]["graph_distance"] == 1


def test_context_both_direction_expands_outbound_and_inbound(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [B](b.md).\n",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text(
        "---\ntype: concept\ntitle: B\n---\nSee [C](c.md).\n",
        encoding="utf-8",
    )
    _write_concept(tmp_path / "c.md", title="C")

    result = _runner().invoke(
        cli,
        [
            "context",
            "--config",
            str(config_path),
            "--seed",
            "b",
            "--direction",
            "both",
            "--depth",
            "1",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert [entry["concept_id"] for entry in data["entries"]] == ["b", "a", "c"]
    by_id = {entry["concept_id"]: entry for entry in data["entries"]}
    assert by_id["a"]["selection_reason"] == "backlink"
    assert by_id["a"]["graph_distance"] == 1
    assert by_id["c"]["selection_reason"] == "outbound-link"
    assert by_id["c"]["graph_distance"] == 1


def test_context_budget_omits_entries_without_failing(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    a_content = "---\ntype: concept\ntitle: A\n---\nSee [B](b.md).\n"
    (tmp_path / "a.md").write_text(a_content, encoding="utf-8")
    _write_concept(tmp_path / "b.md", title="B")

    result = _runner().invoke(
        cli,
        [
            "context",
            "--config",
            str(config_path),
            "--seed",
            "a",
            "--direction",
            "outbound",
            "--depth",
            "1",
            "--budget-chars",
            str(len(a_content)),
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert [entry["concept_id"] for entry in data["entries"]] == ["a"]
    assert data["omitted_concept_ids"] == ["b"]
    assert data["problems"] == []


def test_context_unknown_seed_exits_1(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="A")

    result = _runner().invoke(
        cli, ["context", "--config", str(config_path), "--seed", "missing"]
    )

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["entries"] == []
    assert data["seeds"] == []
    assert data["problems"][0]["kind"] == "unknown-seed"
    assert data["problems"][0]["concept_id"] == "missing"


@pytest.mark.parametrize(
    "args",
    [
        ["--seed", "a", "--depth", "-1"],
        ["--seed", "a", "--direction", "sideways"],
        ["--seed", "a", "--budget-chars", "-1"],
    ],
)
def test_context_invalid_options_exit_2(tmp_path: Path, args: list[str]) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="A")

    result = _runner().invoke(cli, ["context", "--config", str(config_path), *args])

    assert result.exit_code == 2


def test_context_config_error_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("[defaults]\nunknown = true\n", encoding="utf-8")

    result = _runner().invoke(
        cli, ["context", "--config", str(config_path), "--seed", "a"]
    )

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# okf graph
# ---------------------------------------------------------------------------


def test_graph_emits_full_graph_json(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="A")
    _write_concept(tmp_path / "b.md", title="B")
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [B](b.md).\n",
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["graph", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["bundle"] == "default"
    assert data["concepts"] == ["a", "b"]
    assert data["links"][0]["source_concept_id"] == "a"
    assert data["links"][0]["target_concept_id"] == "b"
    assert data["broken_links"] == []


def test_graph_link_title_serialized_in_json(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="A")
    _write_concept(tmp_path / "b.md", title="B")
    (tmp_path / "a.md").write_text(
        '---\ntype: concept\ntitle: A\n---\nSee [B](b.md "related").\n',
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["graph", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["links"][0]["title"] == "related"


def test_graph_link_title_null_when_absent(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="A")
    _write_concept(tmp_path / "b.md", title="B")
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [B](b.md).\n",
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["graph", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["links"][0]["title"] is None


def test_graph_concept_output_includes_backlinks_and_neighborhood(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [B](b.md).\n",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text(
        "---\ntype: concept\ntitle: B\n---\nSee [C](c.md).\n",
        encoding="utf-8",
    )
    _write_concept(tmp_path / "c.md", title="C")

    result = _runner().invoke(
        cli, ["graph", "--config", str(config_path), "--concept", "b", "--depth", "1"]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["concept_id"] == "b"
    assert [link["target_concept_id"] for link in data["outbound_links"]] == ["c"]
    assert [link["source_concept_id"] for link in data["backlinks"]] == ["a"]
    assert data["neighborhood"] == ["a", "b", "c"]


def test_graph_broken_only_output(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [missing](missing.md).\n",
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["graph", "--config", str(config_path), "--broken"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data["broken_links"]) == 1
    assert data["broken_links"][0]["target_concept_id"] == "missing"


def test_graph_unknown_concept_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="A")

    result = _runner().invoke(
        cli, ["graph", "--config", str(config_path), "--concept", "missing"]
    )

    assert result.exit_code == 2


def test_graph_scan_problems_appear_in_json(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "valid.md", title="Valid")
    (tmp_path / "broken.md").write_text(
        "---\ntype: [invalid\n---\nBody\n", encoding="utf-8"
    )

    result = _runner().invoke(cli, ["graph", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data["problems"]) == 1
    assert data["problems"][0]["kind"] == "parse-error"


def test_graph_consumes_future_version_bundle_best_effort(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_future_root_index(tmp_path)
    _write_concept(tmp_path / "a.md", title="A")
    _write_concept(tmp_path / "b.md", title="B")
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [B](b.md).\n",
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["graph", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["concepts"] == ["a", "b"]
    assert data["links"][0]["target_concept_id"] == "b"


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


def test_index_writes_root_okf_version_when_configured(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\nokf_version = "0.1"\n',
        encoding="utf-8",
    )
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 0
    content = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert content.startswith("---\nokf_version: '0.1'\n---\n")
    assert "Example" in content


def test_index_preserves_existing_root_okf_version_when_config_unset(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    (tmp_path / "index.md").write_text(
        "---\nokf_version: '0.1'\n---\n# Old\n", encoding="utf-8"
    )
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 0
    content = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert content.startswith("---\nokf_version: '0.1'\n---\n")
    assert "Example" in content


def test_index_force_drops_existing_root_okf_version_when_config_unset(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    (tmp_path / "index.md").write_text(
        "---\nokf_version: '0.1'\n---\n# Old\n", encoding="utf-8"
    )
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path), "--force"])

    assert result.exit_code == 0
    content = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert not content.startswith("---")
    assert "Example" in content


def test_index_force_does_not_bypass_unsupported_root_version(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    original = "---\nokf_version: '0.2'\n---\n# Future\n"
    (tmp_path / "index.md").write_text(original, encoding="utf-8")
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path), "--force"])

    assert result.exit_code == 1
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == original
    assert "unsupported bundle root okf_version" in result.stdout


def test_index_does_not_write_version_frontmatter_for_subdirectory(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\nokf_version = "0.1"\n',
        encoding="utf-8",
    )
    subdir = tmp_path / "topics"
    _write_concept(subdir / "example.md", title="Example")

    result = _runner().invoke(
        cli, ["index", "--config", str(config_path), "--directory", str(subdir)]
    )

    assert result.exit_code == 0
    content = (subdir / "index.md").read_text(encoding="utf-8")
    assert not content.startswith("---")
    assert "Example" in content


def test_index_leaves_newer_version_bundle_root_alone(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    original = "---\nokf_version: '0.2'\n---\n# Future\n"
    (tmp_path / "index.md").write_text(original, encoding="utf-8")
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == original
    assert "unsupported bundle root okf_version" in result.stdout


def test_index_rejects_unquoted_numeric_root_okf_version(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    original = "---\nokf_version: 0.2\n---\n# Future\n"
    (tmp_path / "index.md").write_text(original, encoding="utf-8")
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == original
    assert "invalid bundle root okf_version" in result.stdout


def test_index_rejects_non_scalar_root_okf_version(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    original = "---\nokf_version: [0, 2]\n---\n# Future\n"
    (tmp_path / "index.md").write_text(original, encoding="utf-8")
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == original
    assert "invalid bundle root okf_version" in result.stdout


def test_index_rejects_malformed_root_index_frontmatter(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    original = "---\nokf_version: [invalid\n---\n# Future\n"
    (tmp_path / "index.md").write_text(original, encoding="utf-8")
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == original
    assert "could not parse bundle root index.md frontmatter" in result.stdout


def test_index_rejects_subdirectory_write_when_root_version_is_newer(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    (tmp_path / "index.md").write_text(
        "---\nokf_version: '0.2'\n---\n# Future\n",
        encoding="utf-8",
    )
    subdir = tmp_path / "topics"
    _write_concept(subdir / "example.md", title="Example")

    result = _runner().invoke(
        cli, ["index", "--config", str(config_path), "--directory", str(subdir)]
    )

    assert result.exit_code == 1
    assert not (subdir / "index.md").exists()
    assert "unsupported bundle root okf_version" in result.stdout


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


def test_index_picks_up_directory_metadata(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    subdir = tmp_path / "sub"
    subdir.mkdir()
    _write_concept(subdir / "a.md", title="Alpha")
    (subdir / "_directory.yml").write_text(
        """
type: _directory
title: Custom CLI Subdir
description: Custom CLI Description
""".strip(),
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 0
    index_path = tmp_path / "index.md"
    assert index_path.exists()
    content = index_path.read_text(encoding="utf-8")
    assert "* [Custom CLI Subdir](sub/) - Custom CLI Description" in content


def test_index_malformed_directory_metadata_exits_1(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    subdir = tmp_path / "sub"
    subdir.mkdir()
    _write_concept(tmp_path / "a.md", title="Alpha")
    _write_concept(subdir / "b.md", title="Beta")
    (subdir / "_directory.yml").write_text(
        """
{invalid yaml
""".strip(),
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert len(data["problems"]) == 1
    assert (
        "failed to parse metadata file _directory.yml" in data["problems"][0]["message"]
    )


# ---------------------------------------------------------------------------
# okf list-bundles
# ---------------------------------------------------------------------------


def test_list_bundles_help_exits_zero() -> None:
    result = _runner().invoke(cli, ["list-bundles", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.stdout


def test_list_bundles_emits_json_with_default_bundle(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\nokf_version = "0.1"\n',
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["list-bundles", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert str(config_path) == data["config_path"]
    assert len(data["bundles"]) == 1
    bundle = data["bundles"][0]
    assert bundle["name"] == "default"
    assert bundle["bundle_root"] == str(tmp_path)
    assert bundle["profile"] is None
    assert bundle["okf_version"] == "0.1"


def test_list_bundles_emits_all_named_bundles(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[bundles.beta]\nbundle_root = "{root_b}"\nprofile = "default"\n'
        f'[bundles.alpha]\nbundle_root = "{root_a}"\n'
        f"[profiles.default]\n",
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["list-bundles", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    # bundles are sorted by name regardless of TOML definition order
    assert [b["name"] for b in data["bundles"]] == ["alpha", "beta"]
    assert data["bundles"][1]["profile"] == "default"


def test_list_bundles_missing_config_file_exits_2(tmp_path: Path) -> None:
    result = _runner().invoke(
        cli, ["list-bundles", "--config", str(tmp_path / "nonexistent.toml")]
    )
    assert result.exit_code == 2
    assert "Configuration error" in result.stderr


def test_list_bundles_stderr_summary_reports_count(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[bundles.x]\nbundle_root = "{tmp_path}"\n'
        f'[bundles.y]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )

    result = _runner().invoke(cli, ["list-bundles", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Found 2 bundle(s)" in result.stderr

"""Tests for the okf-core CLI."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import okf_core.cli as cli_module
from okf_core.cli import cli


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
        "---\nokf_version: '0.3'\n---\n# Future Bundle\n",
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
    assert "search" in result.stdout
    assert "context" in result.stdout
    assert "list-bundles" in result.stdout
    assert "orient" in result.stdout
    assert "move" in result.stdout
    assert "graph-repair" in result.stdout
    assert "graph-report" in result.stdout
    assert "log-append" in result.stdout
    assert "source-add" in result.stdout
    assert "stamp-generated" in result.stdout
    assert "stamp-verified" in result.stdout
    assert "stamp-status" in result.stdout
    assert "stamp-stale-after" in result.stdout


def test_scan_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["scan", "--help"]).exit_code == 0


def test_validate_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["validate", "--help"]).exit_code == 0


def test_list_concepts_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["list-concepts", "--help"]).exit_code == 0


def test_search_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["search", "--help"]).exit_code == 0


def test_index_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["index", "--help"]).exit_code == 0


def test_graph_help_exits_zero() -> None:
    assert _runner().invoke(cli, ["graph", "--help"]).exit_code == 0


def test_graph_report_help_lists_flags() -> None:
    result = _runner().invoke(cli, ["graph-report", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.stdout
    assert "--bundle" in result.stdout
    assert "--output" in result.stdout
    assert "--json" in result.stdout
    assert "wiki-graph-out" in result.stdout
    assert "SUMMARY.md" in result.stdout
    assert "GRAPH_REPORT.md" in result.stdout
    assert "graph.json" in result.stdout
    assert "diagnostics, not quotas" in result.stdout
    assert "Never writes wiki notes and never merges bundles" in result.stdout


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


def test_scan_and_validate_accept_declared_v02_bundle(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\nokf_version = "0.2"\n',
        encoding="utf-8",
    )
    _write_concept(tmp_path / "example.md", title="Example")

    index_result = _runner().invoke(cli, ["index", "--config", str(config_path)])
    assert index_result.exit_code == 0
    root_index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert root_index.startswith("---\nokf_version: '0.2'\n---\n")

    scan_result = _runner().invoke(cli, ["scan", "--config", str(config_path)])
    assert scan_result.exit_code == 0
    assert json.loads(scan_result.stdout)["problems"] == []

    validate_result = _runner().invoke(cli, ["validate", "--config", str(config_path)])
    assert validate_result.exit_code == 0
    assert json.loads(validate_result.stdout)["findings"] == {}


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


def test_validate_reports_index_drift_and_stays_exit_zero(tmp_path: Path) -> None:
    """Seeded index drift (#200): a committed index.md that predates a newly
    added concept file surfaces in `okf validate`'s JSON findings, counts
    toward the warning summary, and -- being advisory-only -- does not affect
    the exit code."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "beta.md", title="Beta")
    # Committed before beta.md existed -- beta.md is missing from it.
    (tmp_path / "index.md").write_text(
        "# Concept\n\n* [Alpha](alpha.md)\n", encoding="utf-8"
    )

    result = _runner().invoke(cli, ["validate", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    index_key = str(tmp_path / "index.md")
    assert index_key in data["findings"]
    drift_findings = data["findings"][index_key]
    assert len(drift_findings) == 1
    assert drift_findings[0]["severity"] == "warning"
    assert drift_findings[0]["field"] == "beta.md"
    assert "beta.md" in drift_findings[0]["message"]
    assert "1 warnings" in result.stderr


def test_validate_index_drift_warning_does_not_mask_coexisting_error(
    tmp_path: Path,
) -> None:
    """An error-severity finding elsewhere in the bundle still fails the
    command even though the index-drift findings alongside it are only
    warnings."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "alpha.md", title="Alpha")
    bad = tmp_path / "no_type.md"
    bad.write_text("---\ntitle: Missing Type\n---\nBody\n", encoding="utf-8")
    # Committed index.md predates both files -- pure advisory drift.
    (tmp_path / "index.md").write_text("# Concept\n\n", encoding="utf-8")

    result = _runner().invoke(cli, ["validate", "--config", str(config_path)])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["findings"][str(bad)][0]["severity"] == "error"
    index_key = str(tmp_path / "index.md")
    assert index_key in data["findings"]
    assert all(f["severity"] == "warning" for f in data["findings"][index_key])


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
    assert data["concepts"][0]["pagerank"] is None
    assert data["orphans"] == []
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
    # pagerank: both concepts should have a positive normalised score
    assert isinstance(by_id["a"]["pagerank"], float)
    assert isinstance(by_id["b"]["pagerank"], float)
    assert abs(by_id["a"]["pagerank"] + by_id["b"]["pagerank"] - 1.0) < 1e-4
    # orphans: b has no outbound links but has an inbound link — neither is an orphan
    assert data["orphans"] == []


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
        newline="\n",
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
# okf search
# ---------------------------------------------------------------------------


def test_search_emits_json_results(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[bundles.default]
bundle_root = "{tmp_path}"
okf_cache_dir = ".okf-cache"
""".strip(),
        encoding="utf-8",
    )
    _write_concept(tmp_path / "alpha.md", title="Alpha")

    result = _runner().invoke(cli, ["search", "Alpha", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["bundle"] == "default"
    assert data["query"] == "Alpha"
    assert data["results"][0]["concept_id"] == "alpha"
    assert data["results"][0]["path"] == str(tmp_path / "alpha.md")
    assert data["results"][0]["snippets"]
    assert data["problems"] == []


def test_search_limit_option(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[bundles.default]
bundle_root = "{tmp_path}"
okf_cache_dir = ".okf-cache"
""".strip(),
        encoding="utf-8",
    )
    _write_concept(tmp_path / "a.md", title="Same")
    _write_concept(tmp_path / "b.md", title="Same")

    result = _runner().invoke(
        cli, ["search", "Same", "--config", str(config_path), "--limit", "1"]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data["results"]) == 1


def test_search_no_refresh_option_uses_existing_index(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[bundles.default]
bundle_root = "{tmp_path}"
okf_cache_dir = ".okf-cache"
""".strip(),
        encoding="utf-8",
    )
    _write_concept(tmp_path / "alpha.md", title="Alpha")

    stale = _runner().invoke(
        cli,
        ["search", "Alpha", "--config", str(config_path), "--no-refresh"],
    )
    fresh = _runner().invoke(cli, ["search", "Alpha", "--config", str(config_path)])

    assert stale.exit_code == 0
    assert json.loads(stale.stdout)["results"] == []
    assert fresh.exit_code == 0
    assert json.loads(fresh.stdout)["results"][0]["concept_id"] == "alpha"


def test_search_missing_okf_cache_dir_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "alpha.md", title="Alpha")

    result = _runner().invoke(cli, ["search", "Alpha", "--config", str(config_path)])

    assert result.exit_code == 2
    assert "okf_cache_dir" in result.stderr


# ---------------------------------------------------------------------------
# okf unlinked-mentions
# ---------------------------------------------------------------------------


def _write_unlinked_mentions_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[bundles.default]
bundle_root = "{tmp_path.as_posix()}"
okf_cache_dir = ".okf-cache"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _write_unlinked_mention_pair(tmp_path: Path) -> None:
    _write_concept(tmp_path / "alpha.md", title="Alpha")
    (tmp_path / "source.md").write_text(
        "---\ntype: concept\ntitle: Source\n---\nSee Alpha for details.\n",
        encoding="utf-8",
    )


def test_unlinked_mentions_emits_structured_suggestions(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_unlinked_mention_pair(tmp_path)

    result = _runner().invoke(cli, ["unlinked-mentions", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["bundle"] == "default"
    assert data["suggestions"] == [
        {
            "source_concept_id": "source",
            "source_path": str(tmp_path / "source.md"),
            "target_concept_id": "alpha",
            "target_path": str(tmp_path / "alpha.md"),
            "target_title": "Alpha",
            "target_href": "alpha.md",
            "matched_text": "See [Alpha] for details.\n",
        }
    ]
    assert data["problems"] == []
    assert "1 unlinked mention suggestion(s)" in result.stderr


def test_unlinked_mentions_no_refresh_uses_existing_index(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_unlinked_mention_pair(tmp_path)

    stale = _runner().invoke(
        cli,
        ["unlinked-mentions", "--config", str(config_path), "--no-refresh"],
    )
    fresh = _runner().invoke(cli, ["unlinked-mentions", "--config", str(config_path)])

    assert stale.exit_code == 0
    assert json.loads(stale.stdout)["suggestions"] == []
    assert fresh.exit_code == 0
    assert len(json.loads(fresh.stdout)["suggestions"]) == 1


def test_unlinked_mentions_missing_cache_dir_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path.as_posix()}"\n',
        encoding="utf-8",
    )
    _write_concept(tmp_path / "alpha.md", title="Alpha")

    result = _runner().invoke(cli, ["unlinked-mentions", "--config", str(config_path)])

    assert result.exit_code == 2
    assert "okf_cache_dir" in result.stderr


def test_unlinked_mentions_reports_non_fatal_problems(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_unlinked_mention_pair(tmp_path)
    initial = _runner().invoke(cli, ["unlinked-mentions", "--config", str(config_path)])
    assert initial.exit_code == 0
    (tmp_path / "alpha.md").unlink()

    result = _runner().invoke(
        cli,
        ["unlinked-mentions", "--config", str(config_path), "--no-refresh"],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert any(
        problem["concept_id"] == "alpha" and problem["kind"] == "read-error"
        for problem in data["problems"]
    )


def test_unlinked_mentions_empty_result_succeeds(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_concept(tmp_path / "alpha.md", title="Alpha")

    result = _runner().invoke(cli, ["unlinked-mentions", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["suggestions"] == []
    assert data["problems"] == []


def test_unlinked_mentions_apply_writes_suggestion_as_link(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_unlinked_mention_pair(tmp_path)

    result = _runner().invoke(
        cli, ["unlinked-mentions", "--config", str(config_path), "--apply"]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["bundle"] == "default"
    assert data["updated_files"] == [str(tmp_path / "source.md")]
    assert len(data["applied_suggestions"]) == 1
    assert data["applied_suggestions"][0]["target_concept_id"] == "alpha"
    assert "1 link suggestion(s)" in result.stderr

    written = (tmp_path / "source.md").read_text(encoding="utf-8")
    assert "## See also" in written
    assert "- [Alpha](alpha.md)" in written


def test_unlinked_mentions_apply_with_select_writes_only_selected(
    tmp_path: Path,
) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_concept(tmp_path / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "beta.md", title="Beta")
    (tmp_path / "source.md").write_text(
        "---\ntype: concept\ntitle: Source\n---\nSee Alpha and Beta for details.\n",
        encoding="utf-8",
    )

    result = _runner().invoke(
        cli,
        [
            "unlinked-mentions",
            "--config",
            str(config_path),
            "--apply",
            "--select",
            "source:alpha",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert [s["target_concept_id"] for s in data["applied_suggestions"]] == ["alpha"]
    written = (tmp_path / "source.md").read_text(encoding="utf-8")
    assert "[Alpha](alpha.md)" in written
    assert "beta.md" not in written


def test_unlinked_mentions_apply_rejects_malformed_select(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_unlinked_mention_pair(tmp_path)

    result = _runner().invoke(
        cli,
        [
            "unlinked-mentions",
            "--config",
            str(config_path),
            "--apply",
            "--select",
            "no-colon-here",
        ],
    )

    assert result.exit_code == 2
    assert "Invalid --select value" in result.stderr


def test_unlinked_mentions_apply_rejects_unmatched_select(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_unlinked_mention_pair(tmp_path)

    result = _runner().invoke(
        cli,
        [
            "unlinked-mentions",
            "--config",
            str(config_path),
            "--apply",
            "--select",
            "source:nonexistent",
        ],
    )

    assert result.exit_code == 2
    assert "source:nonexistent" in result.stderr
    assert (tmp_path / "source.md").read_text(encoding="utf-8") == (
        "---\ntype: concept\ntitle: Source\n---\nSee Alpha for details.\n"
    )


def test_unlinked_mentions_apply_is_idempotent(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_unlinked_mention_pair(tmp_path)
    args = ["unlinked-mentions", "--config", str(config_path), "--apply"]

    first = _runner().invoke(cli, args)
    second = _runner().invoke(cli, args)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(first.stdout)["updated_files"] == [str(tmp_path / "source.md")]
    assert json.loads(second.stdout)["updated_files"] == []
    written = (tmp_path / "source.md").read_text(encoding="utf-8")
    assert written.count("[Alpha](alpha.md)") == 1

    # Copilot review (PR #217 round 1, Finding 4): the stderr summary used to
    # lead with "Applied N link suggestion(s)", wording that implies a write
    # happened whenever N suggestions were selected/processed -- misleading
    # for any caller of the underlying `apply_link_suggestions` (its own
    # docstring: "re-running with the same ... suggestion set only
    # re-touches files whose plan is not already a no-op", so N and the
    # number of files actually touched are not the same count and can
    # diverge). The reworded summary states the two counts separately
    # ("N link suggestion(s) selected" / "M file(s) updated") instead of
    # using "Applied" to describe the suggestion count. This CLI's own
    # re-scan on every invocation happens to move both counts to 0 together
    # on this exact re-run (the mention is no longer "unlinked" once
    # linked), so both figures below are 0 -- still exercising the exact
    # reworded phrasing for the no-write case, distinct from first's
    # nonzero/changed case above.
    assert "1 link suggestion(s) selected" in first.stderr
    assert "1 file(s) updated" in first.stderr
    assert "0 link suggestion(s) selected" in second.stderr
    assert "0 file(s) updated" in second.stderr


def test_unlinked_mentions_apply_custom_heading(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_unlinked_mention_pair(tmp_path)

    result = _runner().invoke(
        cli,
        [
            "unlinked-mentions",
            "--config",
            str(config_path),
            "--apply",
            "--heading",
            "Related",
            "--heading-level",
            "3",
        ],
    )

    assert result.exit_code == 0
    written = (tmp_path / "source.md").read_text(encoding="utf-8")
    assert "### Related" in written
    assert "## See also" not in written


def test_unlinked_mentions_apply_rejects_invalid_heading_level(tmp_path: Path) -> None:
    config_path = _write_unlinked_mentions_config(tmp_path)
    _write_unlinked_mention_pair(tmp_path)

    result = _runner().invoke(
        cli,
        [
            "unlinked-mentions",
            "--config",
            str(config_path),
            "--apply",
            "--heading-level",
            "7",
        ],
    )

    assert result.exit_code == 1
    assert "level" in result.stderr
    assert (tmp_path / "source.md").read_text(encoding="utf-8") == (
        "---\ntype: concept\ntitle: Source\n---\nSee Alpha for details.\n"
    )


def test_unlinked_mentions_help_documents_apply_options() -> None:
    result = _runner().invoke(cli, ["unlinked-mentions", "--help"])

    assert result.exit_code == 0
    assert "--apply" in result.stdout
    assert "--select" in result.stdout
    assert "--heading" in result.stdout


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
    (tmp_path / "a.md").write_text(a_content, encoding="utf-8", newline="\n")
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
# okf graph-report
# ---------------------------------------------------------------------------


def _write_two_bundle_graph_report_project(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    notes = tmp_path / "notes"
    _write_concept(docs / "alpha.md", title="Alpha")
    _write_concept(notes / "beta.md", title="Beta")
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        '[bundles.docs]\nbundle_root = "docs"\n\n'
        '[bundles.notes]\nbundle_root = "notes"\n',
        encoding="utf-8",
    )
    return config_path


def test_graph_report_bundle_flag_is_repeatable(tmp_path: Path) -> None:
    config_path = _write_two_bundle_graph_report_project(tmp_path)
    output = tmp_path / "out"

    result = _runner().invoke(
        cli,
        [
            "graph-report",
            "--config",
            str(config_path),
            "--bundle",
            "notes",
            "--bundle",
            "docs",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert (output / "docs" / "GRAPH_REPORT.md").is_file()
    assert (output / "notes" / "GRAPH_REPORT.md").is_file()
    assert (output / "SUMMARY.md").is_file()
    assert "2 bundle(s)" in result.stderr


def test_graph_report_unknown_bundle_exits_2(tmp_path: Path) -> None:
    config_path = _write_two_bundle_graph_report_project(tmp_path)

    result = _runner().invoke(
        cli,
        [
            "graph-report",
            "--config",
            str(config_path),
            "--bundle",
            "missing",
            "--output",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 2
    assert "Unknown bundle" in result.stderr


def test_graph_report_json_emits_run_result_not_graph_envelope(
    tmp_path: Path,
) -> None:
    config_path = _write_two_bundle_graph_report_project(tmp_path)
    output = tmp_path / "out"

    result = _runner().invoke(
        cli,
        [
            "graph-report",
            "--config",
            str(config_path),
            "--bundle",
            "docs",
            "--output",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["is_subset"] is True
    assert data["selected_bundle_names"] == ["docs"]
    assert data["rows"][0]["bundle"] == "docs"
    assert "written_paths" in data
    assert "normalized_graph" not in data
    assert "schema_version" not in data


def test_graph_report_invalid_config_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text("this is not toml {", encoding="utf-8")

    result = _runner().invoke(cli, ["graph-report", "--config", str(config_path)])

    assert result.exit_code == 2
    assert "Configuration error" in result.stderr


def test_graph_report_output_at_bundle_dir_exits_2(tmp_path: Path) -> None:
    config_path = _write_two_bundle_graph_report_project(tmp_path)

    result = _runner().invoke(
        cli,
        [
            "graph-report",
            "--config",
            str(config_path),
            "--output",
            str(tmp_path / "docs"),
        ],
    )

    assert result.exit_code == 2
    assert "forbidden" in result.stderr


def test_graph_report_output_at_project_root_exits_2(tmp_path: Path) -> None:
    config_path = _write_two_bundle_graph_report_project(tmp_path)

    result = _runner().invoke(
        cli,
        [
            "graph-report",
            "--config",
            str(config_path),
            "--output",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "forbidden" in result.stderr
    assert not (tmp_path / "docs" / "GRAPH_REPORT.md").exists()


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


def test_index_reports_write_conflict_and_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`okf index` must not overwrite index.md content it never planned against."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "example.md", title="Example")
    (tmp_path / "index.md").write_text("stale placeholder\n", encoding="utf-8")

    real_plan_document_change = cli_module.plan_document_change

    def racing_plan_document_change(
        bundle: Any, path: Path, proposed_content: str, **kwargs: Any
    ):
        plan = real_plan_document_change(bundle, path, proposed_content, **kwargs)
        # Simulate a concurrent edit landing between planning and apply.
        Path(path).write_text("concurrently modified\n", encoding="utf-8")
        return plan

    monkeypatch.setattr(cli_module, "plan_document_change", racing_plan_document_change)

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    index_path = tmp_path / "index.md"
    assert index_path.read_text(encoding="utf-8") == "concurrently modified\n"
    payload = json.loads(result.stdout)
    assert payload["write_conflict"] is not None
    assert "changed after planning" in payload["write_conflict"]
    # The generated body had one candidate entry ("example"), but it was
    # never written -- entries must not claim that discarded count.
    assert payload["entries"] == 0
    assert "not written" in result.stderr
    assert "Wrote index.md" not in result.stderr


def test_index_reports_clean_error_when_symlink_swapped_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`okf index` must exit(1) cleanly, not crash, when index.md is replaced
    by a symlink between the earlier informational scan and the real
    planning call -- this raises DocumentChangePlanningError rather than
    DocumentChangeConflictError, and both must be caught the same way."""
    if not _can_symlink():
        pytest.skip("System does not support symlinks or requires elevated privileges")

    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "example.md", title="Example")
    index_path = tmp_path / "index.md"
    index_path.write_text("stale placeholder\n", encoding="utf-8")
    other_path = tmp_path / "other.md"
    other_path.write_text("Other\n", encoding="utf-8", newline="\n")

    real_plan_document_change = cli_module.plan_document_change

    def racing_plan_document_change(
        bundle: Any, path: Path, proposed_content: str, **kwargs: Any
    ):
        # Simulate the target being replaced by a symlink between the
        # earlier informational scan and this, the real planning call --
        # this makes plan_document_change itself raise
        # DocumentChangePlanningError (from _resolve_existing_target's own
        # symlink check), not DocumentChangeConflictError.
        Path(path).unlink()
        Path(path).symlink_to(other_path)
        return real_plan_document_change(bundle, path, proposed_content, **kwargs)

    monkeypatch.setattr(cli_module, "plan_document_change", racing_plan_document_change)

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["write_conflict"] is not None
    assert "symbolic link" in payload["write_conflict"]
    assert payload["entries"] == 0
    assert index_path.is_symlink()
    assert index_path.resolve() == other_path.resolve()
    assert other_path.read_text(encoding="utf-8") == "Other\n"
    assert "not written" in result.stderr
    assert "Wrote index.md" not in result.stderr


@pytest.mark.parametrize("version", ["0.1", "0.2"])
def test_index_writes_root_okf_version_when_configured(
    tmp_path: Path, version: str
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\nokf_version = "{version}"\n',
        encoding="utf-8",
    )
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 0
    content = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert content.startswith(f"---\nokf_version: '{version}'\n---\n")
    assert "Example" in content


@pytest.mark.parametrize("version", ["0.1", "0.2"])
def test_index_preserves_existing_root_okf_version_when_config_unset(
    tmp_path: Path, version: str
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    (tmp_path / "index.md").write_text(
        f"---\nokf_version: '{version}'\n---\n# Old\n", encoding="utf-8"
    )
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 0
    content = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert content.startswith(f"---\nokf_version: '{version}'\n---\n")
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
    original = "---\nokf_version: '0.3'\n---\n# Future\n"
    (tmp_path / "index.md").write_text(original, encoding="utf-8")
    _write_concept(tmp_path / "example.md", title="Example")

    result = _runner().invoke(cli, ["index", "--config", str(config_path), "--force"])

    assert result.exit_code == 1
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == original
    assert "unsupported bundle root okf_version" in result.stdout
    assert json.loads(result.stdout)["excluded_reserved_files"] == []


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
    original = "---\nokf_version: '0.3'\n---\n# Future\n"
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
        "---\nokf_version: '0.3'\n---\n# Future\n",
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
    assert data["excluded_reserved_files"] == []


def test_index_reports_reserved_files_when_zero_entries_written(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[defaults]
bundle_root = "{tmp_path}"
reserved_filenames = ["index.md", "MEMORY.md", "navigation-guide.md", "CHANGELOG.md"]
""",
        encoding="utf-8",
    )
    for filename in ("index.md", "MEMORY.md", "navigation-guide.md", "CHANGELOG.md"):
        (tmp_path / filename).write_text("# Reserved\n", encoding="utf-8")
    _write_concept(tmp_path / "topics" / "valid.md", title="Nested")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["entries"] == 0
    assert data["problems"] == []
    assert data["scan_problems"] == []
    assert [item["filename"] for item in data["excluded_reserved_files"]] == [
        "CHANGELOG.md",
        "index.md",
        "MEMORY.md",
        "navigation-guide.md",
    ]
    assert "excluded by reserved_filenames" in result.stderr
    assert "No index entries were written" in result.stderr


def test_index_reports_reserved_files_alongside_valid_entries(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[defaults]
bundle_root = "{tmp_path}"
reserved_filenames = ["index.md", "MEMORY.md"]
""",
        encoding="utf-8",
    )
    (tmp_path / "MEMORY.md").write_text("# Reserved\n", encoding="utf-8")
    _write_concept(tmp_path / "valid.md", title="Valid")

    result = _runner().invoke(cli, ["index", "--config", str(config_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["entries"] == 1
    assert data["excluded_reserved_files"] == [
        {"path": str(tmp_path / "MEMORY.md"), "filename": "MEMORY.md"}
    ]
    assert "No index entries were written" not in result.stderr


def test_index_reserved_file_diagnostics_are_scoped_to_directory(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[defaults]
bundle_root = "{tmp_path}"
reserved_filenames = ["index.md", "MEMORY.md"]
""",
        encoding="utf-8",
    )
    (tmp_path / "MEMORY.md").write_text("# Root reserved\n", encoding="utf-8")
    subdir = tmp_path / "topics"
    subdir.mkdir()
    (subdir / "MEMORY.md").write_text("# Nested reserved\n", encoding="utf-8")
    _write_concept(subdir / "valid.md", title="Valid")

    result = _runner().invoke(
        cli, ["index", "--config", str(config_path), "--directory", str(subdir)]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["entries"] == 1
    assert data["excluded_reserved_files"] == [
        {"path": str(subdir / "MEMORY.md"), "filename": "MEMORY.md"}
    ]


def test_index_quiet_suppresses_reserved_file_diagnostics(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[defaults]
bundle_root = "{tmp_path}"
reserved_filenames = ["MEMORY.md"]
""",
        encoding="utf-8",
    )
    (tmp_path / "MEMORY.md").write_text("# Reserved\n", encoding="utf-8")

    result = _runner().invoke(cli, ["index", "--config", str(config_path), "--quiet"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""


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


def test_version_option() -> None:
    from okf_core import __version__

    result = _runner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_scan_quiet(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8", newline="\n"
    )
    _write_concept(tmp_path / "a.md", title="Alpha")

    # Success case
    result = _runner().invoke(cli, ["scan", "--config", str(config_path), "-q"])
    assert result.exit_code == 0
    assert result.stdout == ""
    if hasattr(result, "stderr") and result.stderr is not None:
        assert result.stderr == ""

    # Failure case: Unterminated YAML frontmatter
    (tmp_path / "b.md").write_text(
        "---\ntype: concept\ntitle: Beta\n", encoding="utf-8", newline="\n"
    )
    result = _runner().invoke(cli, ["scan", "--config", str(config_path), "--quiet"])
    assert result.exit_code == 1
    assert result.stdout == ""
    if hasattr(result, "stderr") and result.stderr is not None:
        assert result.stderr == ""


def test_index_quiet(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8", newline="\n"
    )
    _write_concept(tmp_path / "a.md", title="Alpha")

    # Success case
    result = _runner().invoke(cli, ["index", "--config", str(config_path), "-q"])
    assert result.exit_code == 0
    assert result.stdout == ""
    if hasattr(result, "stderr") and result.stderr is not None:
        assert result.stderr == ""

    # Failure case: write safety problem (index.md has newer unsupported version)
    (tmp_path / "index.md").write_text(
        "---\nokf_version: '0.3'\n---\n# Index\n", encoding="utf-8", newline="\n"
    )
    result = _runner().invoke(cli, ["index", "--config", str(config_path), "-q"])
    assert result.exit_code == 1
    assert result.stdout == ""
    if hasattr(result, "stderr") and result.stderr is not None:
        assert result.stderr == ""


def test_quiet_option_config_error_does_not_suppress_stderr(tmp_path: Path) -> None:
    # Fed with a bad/missing configuration, command should still output error on stderr and exit with code 2
    result = _runner().invoke(
        cli, ["scan", "--config", str(tmp_path / "nonexistent.toml"), "-q"]
    )
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Configuration error" in result.stderr


def test_cli_stable_id_disconnected() -> None:
    # 1. Pure generation
    result = _runner().invoke(cli, ["stable-id"])
    assert result.exit_code == 0
    import uuid

    # Verify stdout is a valid UUID
    val = result.stdout.strip()
    uuid.UUID(val)

    # 2. Invalid options without CONCEPT_ID
    result = _runner().invoke(cli, ["stable-id", "--write"])
    assert result.exit_code == 2
    assert "Cannot specify --write without a CONCEPT_ID" in result.stderr

    result = _runner().invoke(cli, ["stable-id", "--force"])
    assert result.exit_code == 2
    assert "Cannot specify --force without a CONCEPT_ID" in result.stderr


def test_cli_stable_id_with_concept(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[bundles.default]\nbundle_root = "{tmp_path}"\nstable_id_field = "id"\n',
        encoding="utf-8",
        newline="\n",
    )

    # 1. No concept exists yet
    result = _runner().invoke(cli, ["stable-id", "a", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "Concept file not found" in result.stderr

    # Concept ID escapes bundle root
    result = _runner().invoke(
        cli, ["stable-id", "../outside", "--config", str(config_path)]
    )
    assert result.exit_code == 2
    assert (
        "Concept ID escapes bundle root" in result.stderr
        or "Concept path is outside" in result.stderr
        or "Unsafe concept ID" in result.stderr
    )

    # Create the concept file
    _write_concept(tmp_path / "a.md", title="Alpha")

    # 2. Get stable ID when missing in frontmatter (generates a new one in memory but doesn't write)
    result = _runner().invoke(cli, ["stable-id", "a", "--config", str(config_path)])
    assert result.exit_code == 0
    val1 = result.stdout.strip()
    import uuid

    uuid.UUID(val1)

    # Since it wasn't written, running it again generates a different one
    result2 = _runner().invoke(cli, ["stable-id", "a", "--config", str(config_path)])
    assert result2.exit_code == 0
    val2 = result2.stdout.strip()
    assert val1 != val2

    # 3. Write option
    result = _runner().invoke(
        cli, ["stable-id", "a", "--config", str(config_path), "--write"]
    )
    assert result.exit_code == 0
    written_id = result.stdout.strip()
    uuid.UUID(written_id)
    assert "Wrote stable ID" in result.stderr

    # Read file to verify
    from okf_core import parse_concept_document

    doc = parse_concept_document((tmp_path / "a.md").read_text(encoding="utf-8"))
    assert doc.frontmatter.get("id") == written_id

    # 4. Running it again now returns the same ID since it was written
    result = _runner().invoke(cli, ["stable-id", "a", "--config", str(config_path)])
    assert result.exit_code == 0
    assert result.stdout.strip() == written_id

    # 5. Using --force generates a new one
    result = _runner().invoke(
        cli, ["stable-id", "a", "--config", str(config_path), "--force"]
    )
    assert result.exit_code == 0
    forced_id = result.stdout.strip()
    assert forced_id != written_id

    # Since we didn't specify --write, the file still has written_id
    doc = parse_concept_document((tmp_path / "a.md").read_text(encoding="utf-8"))
    assert doc.frontmatter.get("id") == written_id

    # 6. Force and write
    result = _runner().invoke(
        cli, ["stable-id", "a", "--config", str(config_path), "--force", "--write"]
    )
    assert result.exit_code == 0
    forced_written_id = result.stdout.strip()
    assert forced_written_id != written_id
    assert "Wrote stable ID" in result.stderr

    doc = parse_concept_document((tmp_path / "a.md").read_text(encoding="utf-8"))
    assert doc.frontmatter.get("id") == forced_written_id


def test_cli_stable_id_not_configured(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[bundles.default]\nbundle_root = "{tmp_path}"\n',
        encoding="utf-8",
        newline="\n",
    )
    _write_concept(tmp_path / "a.md", title="Alpha")

    result = _runner().invoke(cli, ["stable-id", "a", "--config", str(config_path)])
    assert result.exit_code == 2
    assert "stable_id_field is not configured" in result.stderr


def test_cli_stable_id_write_safety_refused(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[bundles.default]\nbundle_root = "{tmp_path}"\nstable_id_field = "id"\n',
        encoding="utf-8",
        newline="\n",
    )
    _write_concept(tmp_path / "a.md", title="Alpha")
    (tmp_path / "index.md").write_text(
        "---\nokf_version: '0.3'\n---\n# Index\n", encoding="utf-8", newline="\n"
    )

    result = _runner().invoke(
        cli, ["stable-id", "a", "--config", str(config_path), "--write"]
    )
    assert result.exit_code == 1
    assert "Refusing to write" in result.stderr


def _can_symlink() -> bool:
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            target = p / "target"
            target.touch()
            link = p / "link"
            link.symlink_to(target)
            return True
    except (OSError, NotImplementedError):
        return False


def test_cli_stable_id_write_refuses_stale_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`okf stable-id --write` must not overwrite content it never planned against."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[bundles.default]\nbundle_root = "{tmp_path}"\nstable_id_field = "id"\n',
        encoding="utf-8",
        newline="\n",
    )
    concept_path = tmp_path / "a.md"
    _write_concept(concept_path, title="Alpha")

    real_plan_from_reader = cli_module.plan_document_change_from_reader

    def racing_plan_from_reader(bundle: Any, path: Path, build_proposed_content: Any):
        plan = real_plan_from_reader(bundle, path, build_proposed_content)
        # Simulate a concurrent edit landing between planning and apply.
        path.write_text(
            "---\ntype: concept\ntitle: Alpha\n---\nConcurrently edited body\n",
            encoding="utf-8",
            newline="\n",
        )
        return plan

    monkeypatch.setattr(
        cli_module, "plan_document_change_from_reader", racing_plan_from_reader
    )

    result = _runner().invoke(
        cli, ["stable-id", "a", "--config", str(config_path), "--write"]
    )

    assert result.exit_code == 1
    assert "changed after planning" in result.stderr
    assert (
        concept_path.read_text(encoding="utf-8")
        == "---\ntype: concept\ntitle: Alpha\n---\nConcurrently edited body\n"
    )


def test_cli_stable_id_write_refuses_symlinked_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`okf stable-id --write` must not write through a target swapped for a symlink."""
    if not _can_symlink():
        pytest.skip("System does not support symlinks or requires elevated privileges")

    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[bundles.default]\nbundle_root = "{tmp_path}"\nstable_id_field = "id"\n',
        encoding="utf-8",
        newline="\n",
    )
    concept_path = tmp_path / "a.md"
    _write_concept(concept_path, title="Alpha")
    other_path = tmp_path / "other.md"
    other_path.write_text("Other\n", encoding="utf-8", newline="\n")

    real_plan_from_reader = cli_module.plan_document_change_from_reader

    def racing_plan_from_reader(bundle: Any, path: Path, build_proposed_content: Any):
        plan = real_plan_from_reader(bundle, path, build_proposed_content)
        # Simulate the target being replaced by a symlink between planning
        # and apply -- e.g. a TOCTOU attack swapping in a link to a file the
        # caller has no business overwriting.
        path.unlink()
        path.symlink_to(other_path)
        return plan

    monkeypatch.setattr(
        cli_module, "plan_document_change_from_reader", racing_plan_from_reader
    )

    result = _runner().invoke(
        cli, ["stable-id", "a", "--config", str(config_path), "--write"]
    )

    assert result.exit_code == 1
    assert "changed after planning" in result.stderr
    assert concept_path.is_symlink()
    assert concept_path.resolve() == other_path.resolve()
    assert other_path.read_text(encoding="utf-8") == "Other\n"


def test_cli_stable_id_write_reports_clean_error_when_symlink_swapped_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`okf stable-id --write` must exit(1) cleanly, not crash, when the
    target is replaced by a symlink between the informational read at the
    top of the command and the real planning call -- this raises
    DocumentChangePlanningError rather than DocumentChangeConflictError, and
    both must be caught the same way."""
    if not _can_symlink():
        pytest.skip("System does not support symlinks or requires elevated privileges")

    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[bundles.default]\nbundle_root = "{tmp_path}"\nstable_id_field = "id"\n',
        encoding="utf-8",
        newline="\n",
    )
    concept_path = tmp_path / "a.md"
    _write_concept(concept_path, title="Alpha")
    other_path = tmp_path / "other.md"
    other_path.write_text("Other\n", encoding="utf-8", newline="\n")

    real_plan_from_reader = cli_module.plan_document_change_from_reader

    def racing_plan_from_reader(bundle: Any, path: Path, build_proposed_content: Any):
        # Simulate the target being replaced by a symlink between the
        # informational read/parse at the top of stable_id_cmd and this, the
        # real planning call -- this makes plan_document_change_from_reader
        # itself raise DocumentChangePlanningError (from
        # _resolve_existing_target's own symlink check), not
        # DocumentChangeConflictError.
        path.unlink()
        path.symlink_to(other_path)
        return real_plan_from_reader(bundle, path, build_proposed_content)

    monkeypatch.setattr(
        cli_module, "plan_document_change_from_reader", racing_plan_from_reader
    )

    result = _runner().invoke(
        cli, ["stable-id", "a", "--config", str(config_path), "--write"]
    )

    assert result.exit_code == 1
    assert "symbolic link" in result.stderr
    assert concept_path.is_symlink()
    assert concept_path.resolve() == other_path.resolve()
    assert other_path.read_text(encoding="utf-8") == "Other\n"


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


def test_move_help_exits_zero() -> None:
    result = _runner().invoke(cli, ["move", "--help"])
    assert result.exit_code == 0
    assert "SOURCE" in result.stdout
    assert "DEST" in result.stdout


def test_cli_move_success_updates_referring_file(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "old.md", title="Old")
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [link](old.md).\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _runner().invoke(
        cli, ["move", "old.md", "new.md", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["moved"] is True
    assert payload["updated_files"] == [str(tmp_path / "a.md")]
    assert not (tmp_path / "old.md").exists()
    assert (tmp_path / "new.md").exists()
    assert "Moved" in result.stderr
    assert "See [link](new.md)." in (tmp_path / "a.md").read_text(encoding="utf-8")


def test_cli_move_reports_regenerated_indexes(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "sub1").mkdir()
    _write_concept(tmp_path / "sub1" / "old.md", title="Old")
    (tmp_path / "sub1" / "index.md").write_text("stale\n", encoding="utf-8")

    result = _runner().invoke(
        cli,
        [
            "move",
            str(tmp_path / "sub1" / "old.md"),
            str(tmp_path / "sub1" / "new.md"),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["regenerated_indexes"] == [str(tmp_path / "sub1" / "index.md")]
    assert "regenerated 1 index" in result.stderr


def test_cli_move_idempotent_same_source_and_dest(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="Alpha")

    result = _runner().invoke(
        cli, ["move", "a.md", "a.md", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["moved"] is False
    assert payload["updated_files"] == []


def test_cli_move_dry_run_does_not_write(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "old.md", title="Old")
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [link](old.md).\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _runner().invoke(
        cli,
        ["move", "old.md", "new.md", "--config", str(config_path), "--dry-run"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_move"] is True
    assert payload["would_update_files"] == [str(tmp_path / "a.md")]
    assert (tmp_path / "old.md").exists()
    assert not (tmp_path / "new.md").exists()
    assert "See [link](old.md)." in (tmp_path / "a.md").read_text(encoding="utf-8")
    assert "Dry run" in result.stderr


def test_cli_move_write_safety_refused(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "old.md", title="Old")
    (tmp_path / "index.md").write_text(
        "---\nokf_version: '0.3'\n---\n# Index\n", encoding="utf-8", newline="\n"
    )

    result = _runner().invoke(
        cli, ["move", "old.md", "new.md", "--config", str(config_path)]
    )

    assert result.exit_code == 1
    assert "Refusing to write" in result.stderr
    assert (tmp_path / "old.md").exists()
    assert not (tmp_path / "new.md").exists()


def test_cli_move_source_not_found(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )

    result = _runner().invoke(
        cli, ["move", "missing.md", "new.md", "--config", str(config_path)]
    )

    assert result.exit_code == 1
    assert "does not exist" in result.stderr


def test_cli_move_dest_already_exists(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "old.md", title="Old")
    _write_concept(tmp_path / "new.md", title="New")

    result = _runner().invoke(
        cli, ["move", "old.md", "new.md", "--config", str(config_path)]
    )

    assert result.exit_code == 1
    assert "already exists" in result.stderr


def test_cli_move_dest_outside_bundle_root(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{bundle_root}"\n', encoding="utf-8"
    )
    _write_concept(bundle_root / "old.md", title="Old")

    result = _runner().invoke(
        cli,
        [
            "move",
            "old.md",
            str(tmp_path / "outside.md"),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 2


def test_cli_move_source_escapes_bundle_root(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{bundle_root}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "outside.md", title="Outside")

    result = _runner().invoke(
        cli,
        ["move", "../outside.md", "new.md", "--config", str(config_path)],
    )

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# log-append
# ---------------------------------------------------------------------------


def test_log_append_help_exits_zero() -> None:
    result = _runner().invoke(cli, ["log-append", "--help"])
    assert result.exit_code == 0
    assert "CONTENT" in result.stdout
    assert "--date" in result.stdout
    assert "--kind" in result.stdout


def test_cli_log_append_creates_log_and_writes_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )

    result = _runner().invoke(
        cli,
        [
            "log-append",
            "Did a thing.",
            "--date",
            "2026-07-25",
            "--kind",
            "Update",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    assert payload["path"] == str(tmp_path / "log.md")
    assert "Appended entry" in result.stderr
    log_text = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert "## 2026-07-25" in log_text
    assert "* **Update**: Did a thing." in log_text


def test_cli_log_append_default_date_and_kind(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )

    result = _runner().invoke(
        cli,
        ["log-append", "Untimed entry.", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    log_text = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert "* Untimed entry." in log_text


def test_cli_log_append_dry_run_does_not_write(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )

    result = _runner().invoke(
        cli,
        [
            "log-append",
            "Would-be entry.",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is True
    assert not (tmp_path / "log.md").exists()
    assert "Dry run" in result.stderr


def test_cli_log_append_invalid_date_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )

    result = _runner().invoke(
        cli,
        [
            "log-append",
            "Some content.",
            "--date",
            "not-a-date",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 2
    assert "Invalid --date value" in result.stderr
    assert not (tmp_path / "log.md").exists()


def test_cli_log_append_rejects_unrepresentable_content(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )

    result = _runner().invoke(
        cli,
        [
            "log-append",
            "First paragraph.\n\nSecond paragraph.",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert not (tmp_path / "log.md").exists()


def test_cli_log_append_stale_conflict_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`okf log-append` must not overwrite a log.md it never planned against.

    Mirrors test_cli_stable_id_write_refuses_stale_content's technique:
    log_append_cmd calls the library's log_append, not
    plan_document_change_from_reader directly, so the race is injected by
    patching that name inside okf_core.logs (where plan_log_append actually
    calls it), not inside cli_module.
    """
    import okf_core.logs as logs_module

    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    log_path = tmp_path / "log.md"
    log_path.write_text(
        "## 2020-01-01\n* **Update**: Original.\n", encoding="utf-8", newline="\n"
    )

    real_plan_from_reader = logs_module.plan_document_change_from_reader

    def racing_plan_from_reader(
        bundle: Any, path: Path, build_proposed_content: Any, **kwargs: Any
    ):
        plan = real_plan_from_reader(bundle, path, build_proposed_content, **kwargs)
        # Simulate a concurrent edit landing between planning and apply.
        Path(path).write_text(
            "## 2020-01-01\n* **Update**: Concurrent edit.\n",
            encoding="utf-8",
            newline="\n",
        )
        return plan

    monkeypatch.setattr(
        logs_module, "plan_document_change_from_reader", racing_plan_from_reader
    )

    result = _runner().invoke(
        cli,
        ["log-append", "New entry.", "--config", str(config_path)],
    )

    assert result.exit_code == 1
    assert "changed after planning" in result.stderr
    assert log_path.read_text(encoding="utf-8") == (
        "## 2020-01-01\n* **Update**: Concurrent edit.\n"
    )


# ---------------------------------------------------------------------------
# source-add
# ---------------------------------------------------------------------------


def test_source_add_help_exits_zero() -> None:
    result = _runner().invoke(cli, ["source-add", "--help"])
    assert result.exit_code == 0
    assert "PATH" in result.stdout
    assert "--resource" in result.stdout
    assert "--id" in result.stdout
    assert "--title" in result.stdout
    assert "--author" in result.stdout
    assert "--usage-count" in result.stdout
    assert "--last-modified" in result.stdout


def test_cli_source_add_creates_sources_list_and_writes_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    _write_concept(concept_path, title="Topic")

    result = _runner().invoke(
        cli,
        [
            "source-add",
            "topic.md",
            "--resource",
            "https://example.com/a",
            "--id",
            "src-a",
            "--title",
            "Source A",
            "--author",
            "human:alice",
            "--usage-count",
            "42",
            "--last-modified",
            "2026-05-30",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    assert payload["path"] == str(concept_path)
    assert "Added sources entry" in result.stderr
    text = concept_path.read_text(encoding="utf-8")
    assert "resource: https://example.com/a" in text
    assert "id: src-a" in text
    assert "title: Source A" in text
    assert "author: human:alice" in text
    assert "usage_count: 42" in text
    assert "last_modified: 2026-05-30" in text


def test_cli_source_add_only_requires_resource(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    concept_path.write_text(
        "---\ntype: concept\n---\nBody\n", encoding="utf-8", newline="\n"
    )

    result = _runner().invoke(
        cli,
        [
            "source-add",
            "topic.md",
            "--resource",
            "https://example.com/a",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    text = concept_path.read_text(encoding="utf-8")
    assert "resource: https://example.com/a" in text
    assert "id:" not in text
    assert "title:" not in text


def test_cli_source_add_dry_run_does_not_write(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "source-add",
            "topic.md",
            "--resource",
            "https://example.com/a",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is True
    assert concept_path.read_text(encoding="utf-8") == original
    assert "Dry run" in result.stderr


def test_cli_source_add_already_represented_is_noop(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    concept_path.write_text(
        "---\ntype: concept\nsources:\n  - resource: https://example.com/a\n"
        "    id: src-a\n---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _runner().invoke(
        cli,
        [
            "source-add",
            "topic.md",
            "--resource",
            "https://example.com/a",
            "--id",
            "src-a",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert "already represented" in result.stderr


def test_cli_source_add_invalid_last_modified_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "source-add",
            "topic.md",
            "--resource",
            "https://example.com/a",
            "--last-modified",
            "not-a-date",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 2
    assert "Invalid --last-modified value" in result.stderr
    assert concept_path.read_text(encoding="utf-8") == original


def test_cli_source_add_missing_resource_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    _write_concept(concept_path, title="Topic")

    result = _runner().invoke(
        cli,
        ["source-add", "topic.md", "--config", str(config_path)],
    )

    assert result.exit_code == 2


def test_cli_source_add_stale_conflict_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`okf source-add` must not overwrite a document it never planned against.

    Mirrors test_cli_log_append_stale_conflict_exits_1's technique:
    source_add_cmd calls the library's source_upsert, which calls
    plan_source_upsert, which calls plan_document_change_from_reader inside
    okf_core.patching -- so the race is injected by patching that name
    there, not inside cli_module.
    """
    import okf_core.patching as patching_module

    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    concept_path.write_text(
        "---\ntype: concept\nsources:\n  - resource: https://example.com/original\n"
        "---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )

    real_plan_from_reader = patching_module.plan_document_change_from_reader

    def racing_plan_from_reader(
        bundle: Any, path: Path, build_proposed_content: Any, **kwargs: Any
    ):
        plan = real_plan_from_reader(bundle, path, build_proposed_content, **kwargs)
        # Simulate a concurrent edit landing between planning and apply. `path`
        # here is the CLI's own bundle-root-relative argument, not yet resolved
        # against bundle_root -- write to the resolved concept_path instead.
        concept_path.write_text(
            "---\ntype: concept\nsources:\n"
            "  - resource: https://example.com/concurrent\n---\nBody\n",
            encoding="utf-8",
            newline="\n",
        )
        return plan

    monkeypatch.setattr(
        patching_module, "plan_document_change_from_reader", racing_plan_from_reader
    )

    result = _runner().invoke(
        cli,
        [
            "source-add",
            "topic.md",
            "--resource",
            "https://example.com/new",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert "changed after planning" in result.stderr
    assert "https://example.com/concurrent" in concept_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# stamp-generated / stamp-verified / stamp-status / stamp-stale-after (#196)
# ---------------------------------------------------------------------------


def test_stamp_generated_help_exits_zero() -> None:
    result = _runner().invoke(cli, ["stamp-generated", "--help"])
    assert result.exit_code == 0
    assert "PATH" in result.stdout
    assert "--by" in result.stdout
    assert "--at" in result.stdout


def test_stamp_verified_help_exits_zero() -> None:
    result = _runner().invoke(cli, ["stamp-verified", "--help"])
    assert result.exit_code == 0
    assert "PATH" in result.stdout
    assert "--by" in result.stdout
    assert "--at" in result.stdout


def test_stamp_status_help_exits_zero() -> None:
    result = _runner().invoke(cli, ["stamp-status", "--help"])
    assert result.exit_code == 0
    assert "PATH" in result.stdout
    assert "--status" in result.stdout


def test_stamp_stale_after_help_exits_zero() -> None:
    result = _runner().invoke(cli, ["stamp-stale-after", "--help"])
    assert result.exit_code == 0
    assert "PATH" in result.stdout
    assert "--stale-after" in result.stdout


def test_cli_stamp_generated_writes_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    _write_concept(concept_path, title="Topic")

    result = _runner().invoke(
        cli,
        [
            "stamp-generated",
            "topic.md",
            "--by",
            "human:alice",
            "--at",
            "2026-06-20T22:53:05Z",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    assert payload["path"] == str(concept_path)
    assert "Stamped generated" in result.stderr
    text = concept_path.read_text(encoding="utf-8")
    assert "by: human:alice" in text
    assert "at: 2026-06-20" in text


def test_cli_stamp_generated_dry_run_does_not_write(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-generated",
            "topic.md",
            "--by",
            "human:alice",
            "--at",
            "2026-06-20T22:53:05Z",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is True
    assert concept_path.read_text(encoding="utf-8") == original
    assert "Dry run" in result.stderr


def test_cli_stamp_generated_dry_run_already_up_to_date(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = (
        "---\ntype: concept\ngenerated: { by: human:alice, at: "
        "2026-06-20T22:53:05Z }\n---\nBody\n"
    )
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-generated",
            "topic.md",
            "--by",
            "human:alice",
            "--at",
            "2026-06-20T22:53:05Z",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is False
    assert concept_path.read_text(encoding="utf-8") == original
    assert "nothing to do" in result.stderr


def test_cli_stamp_generated_same_value_is_noop(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    concept_path.write_text(
        "---\ntype: concept\ngenerated: { by: human:alice, at: "
        "2026-06-20T22:53:05Z }\n---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _runner().invoke(
        cli,
        [
            "stamp-generated",
            "topic.md",
            "--by",
            "human:alice",
            "--at",
            "2026-06-20T22:53:05Z",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert "nothing to do" in result.stderr


def test_cli_stamp_generated_invalid_actor_exits_1(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-generated",
            "topic.md",
            "--by",
            "not-an-actor",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert "actor string" in result.stderr
    assert concept_path.read_text(encoding="utf-8") == original


def test_cli_stamp_generated_invalid_datetime_exits_1(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-generated",
            "topic.md",
            "--by",
            "human:alice",
            "--at",
            "not-a-date",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert "ISO 8601 datetime" in result.stderr
    assert concept_path.read_text(encoding="utf-8") == original


def test_cli_stamp_verified_appends_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    _write_concept(concept_path, title="Topic")

    result = _runner().invoke(
        cli,
        [
            "stamp-verified",
            "topic.md",
            "--by",
            "human:alice",
            "--at",
            "2026-06-20T22:53:05Z",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    assert "Appended verified event" in result.stderr
    text = concept_path.read_text(encoding="utf-8")
    assert "verified:" in text
    assert "by: human:alice" in text


def test_cli_stamp_verified_identical_event_is_noop(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    concept_path.write_text(
        "---\ntype: concept\nverified:\n  - { by: human:alice, at: "
        "2026-06-20T22:53:05Z }\n---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _runner().invoke(
        cli,
        [
            "stamp-verified",
            "topic.md",
            "--by",
            "human:alice",
            "--at",
            "2026-06-20T22:53:05Z",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert "already represented" in result.stderr


def test_cli_stamp_verified_dry_run_would_append(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-verified",
            "topic.md",
            "--by",
            "human:alice",
            "--at",
            "2026-06-20T22:53:05Z",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is True
    assert concept_path.read_text(encoding="utf-8") == original
    assert "Dry run" in result.stderr


def test_cli_stamp_verified_dry_run_already_represented(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = (
        "---\ntype: concept\nverified:\n  - { by: human:alice, at: "
        "2026-06-20T22:53:05Z }\n---\nBody\n"
    )
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-verified",
            "topic.md",
            "--by",
            "human:alice",
            "--at",
            "2026-06-20T22:53:05Z",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is False
    assert concept_path.read_text(encoding="utf-8") == original
    assert "nothing to do" in result.stderr


def test_cli_stamp_status_writes_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    _write_concept(concept_path, title="Topic")

    result = _runner().invoke(
        cli,
        [
            "stamp-status",
            "topic.md",
            "--status",
            "draft",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    assert "Stamped status" in result.stderr
    assert "status: draft" in concept_path.read_text(encoding="utf-8")


def test_cli_stamp_status_invalid_choice_exits_2(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-status",
            "topic.md",
            "--status",
            "bogus",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 2
    assert concept_path.read_text(encoding="utf-8") == original


def test_cli_stamp_status_dry_run_would_change(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-status",
            "topic.md",
            "--status",
            "draft",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is True
    assert concept_path.read_text(encoding="utf-8") == original
    assert "Dry run" in result.stderr


def test_cli_stamp_status_dry_run_already_up_to_date(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\nstatus: stable\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-status",
            "topic.md",
            "--status",
            "stable",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is False
    assert concept_path.read_text(encoding="utf-8") == original
    assert "nothing to do" in result.stderr


def test_cli_stamp_status_same_value_is_noop(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\nstatus: stable\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-status",
            "topic.md",
            "--status",
            "stable",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert "nothing to do" in result.stderr
    assert concept_path.read_text(encoding="utf-8") == original


def test_cli_stamp_status_stale_conflict_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`okf stamp-status` must not overwrite a document it never planned
    against. Mirrors test_cli_source_add_stale_conflict_exits_1's technique:
    stamp_status_cmd calls the library's stamp_status, which calls
    plan_stamp_status -> plan_frontmatter_merge -> _plan_document_change
    inside okf_core.patching -- so the race is injected by patching
    `_plan_document_change` there, not inside cli_module.
    """
    import okf_core.patching as patching_module

    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    concept_path.write_text(
        "---\ntype: concept\nstatus: draft\n---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )

    real_plan_document_change = patching_module._plan_document_change

    def racing_plan_document_change(bundle: Any, path: Path, *args: Any, **kwargs: Any):
        plan = real_plan_document_change(bundle, path, *args, **kwargs)
        concept_path.write_text(
            "---\ntype: concept\nstatus: deprecated\n---\nBody\n",
            encoding="utf-8",
            newline="\n",
        )
        return plan

    monkeypatch.setattr(
        patching_module, "_plan_document_change", racing_plan_document_change
    )

    result = _runner().invoke(
        cli,
        [
            "stamp-status",
            "topic.md",
            "--status",
            "stable",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert "changed after planning" in result.stderr
    assert "status: deprecated" in concept_path.read_text(encoding="utf-8")


def test_cli_stamp_stale_after_writes_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    _write_concept(concept_path, title="Topic")

    result = _runner().invoke(
        cli,
        [
            "stamp-stale-after",
            "topic.md",
            "--stale-after",
            "2026-09-23",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    assert "Stamped stale_after" in result.stderr
    assert "stale_after: 2026-09-23" in concept_path.read_text(encoding="utf-8")


def test_cli_stamp_stale_after_invalid_date_exits_1(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-stale-after",
            "topic.md",
            "--stale-after",
            "not-a-date",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert concept_path.read_text(encoding="utf-8") == original


def test_cli_stamp_stale_after_dry_run_would_change(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\ntitle: Topic\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-stale-after",
            "topic.md",
            "--stale-after",
            "2026-09-23",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is True
    assert concept_path.read_text(encoding="utf-8") == original
    assert "Dry run" in result.stderr


def test_cli_stamp_stale_after_dry_run_already_up_to_date(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\nstale_after: 2026-09-23\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-stale-after",
            "topic.md",
            "--stale-after",
            "2026-09-23",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_change"] is False
    assert concept_path.read_text(encoding="utf-8") == original
    assert "nothing to do" in result.stderr


def test_cli_stamp_stale_after_same_value_is_noop(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    original = "---\ntype: concept\nstale_after: 2026-09-23\n---\nBody\n"
    concept_path.write_text(original, encoding="utf-8", newline="\n")

    result = _runner().invoke(
        cli,
        [
            "stamp-stale-after",
            "topic.md",
            "--stale-after",
            "2026-09-23",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert "nothing to do" in result.stderr
    assert concept_path.read_text(encoding="utf-8") == original


def test_cli_stamp_verified_stale_conflict_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`okf stamp-verified` must not overwrite a document it never planned
    against. Mirrors test_cli_source_add_stale_conflict_exits_1's technique:
    the race is injected by patching `plan_document_change_from_reader`
    inside `okf_core.patching`, which `plan_stamp_verified` calls directly.
    """
    import okf_core.patching as patching_module

    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    concept_path = tmp_path / "topic.md"
    concept_path.write_text(
        "---\ntype: concept\nverified:\n  - { by: human:original, at: "
        "2026-06-20T22:53:05Z }\n---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )

    real_plan_from_reader = patching_module.plan_document_change_from_reader

    def racing_plan_from_reader(
        bundle: Any, path: Path, build_proposed_content: Any, **kwargs: Any
    ):
        plan = real_plan_from_reader(bundle, path, build_proposed_content, **kwargs)
        concept_path.write_text(
            "---\ntype: concept\nverified:\n  - { by: human:concurrent, at: "
            "2026-06-20T22:53:05Z }\n---\nBody\n",
            encoding="utf-8",
            newline="\n",
        )
        return plan

    monkeypatch.setattr(
        patching_module, "plan_document_change_from_reader", racing_plan_from_reader
    )

    result = _runner().invoke(
        cli,
        [
            "stamp-verified",
            "topic.md",
            "--by",
            "human:new",
            "--at",
            "2026-06-20T22:53:05Z",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert "changed after planning" in result.stderr
    assert "human:concurrent" in concept_path.read_text(encoding="utf-8")


def test_cli_graph_repair_dry_run_no_plugin_reports_unresolved(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [dead](dead.md).\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _runner().invoke(
        cli, ["graph-repair", "--dry-run", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["would_update_files"] == []
    assert payload["resolved_links"] == []
    assert len(payload["unresolved_links"]) == 1
    assert payload["unresolved_links"][0]["reason"] == "no-plugin-registered"
    assert "unresolved" in result.stderr


def test_cli_graph_repair_resolves_link_via_registered_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [dead](dead.md).\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_concept(tmp_path / "new.md", title="New")

    import okf_core.hooks as hooks_module
    from okf_core.hooks import hookimpl

    class _ResolvingPlugin:
        @hookimpl
        def okf_fetch_moved_concept_path(self, dead_concept_id):  # type: ignore[no-untyped-def]
            return tmp_path / "new.md" if dead_concept_id == "dead" else None

    original_get_hook_manager = hooks_module.get_hook_manager

    def patched(bundle):  # type: ignore[no-untyped-def]
        pm = original_get_hook_manager(bundle)
        pm.register(_ResolvingPlugin())
        return pm

    monkeypatch.setattr(hooks_module, "get_hook_manager", patched)

    result = _runner().invoke(cli, ["graph-repair", "--config", str(config_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["updated_files"] == [str(tmp_path / "a.md")]
    assert len(payload["resolved_links"]) == 1
    assert payload["unresolved_links"] == []
    assert "Resolved" in result.stderr
    assert "See [dead](new.md)." in (tmp_path / "a.md").read_text(encoding="utf-8")


def test_cli_graph_repair_scan_failure_exits_one(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nSee [dead](dead.md).\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "bad.md").write_text("---\ntype: concept\n", encoding="utf-8")

    result = _runner().invoke(cli, ["graph-repair", "--config", str(config_path)])

    assert result.exit_code == 1
    assert result.stderr.strip() != ""


def test_index_recurse_generates_nested_indexes(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="Alpha")

    subdir = tmp_path / "topics"
    _write_concept(subdir / "b.md", title="Beta")

    nested_subdir = subdir / "nested"
    _write_concept(nested_subdir / "c.md", title="Gamma")

    # An empty subdirectory (non-concept-bearing)
    empty_subdir = tmp_path / "empty"
    empty_subdir.mkdir()

    result = _runner().invoke(cli, ["index", "--config", str(config_path), "--recurse"])

    assert result.exit_code == 0

    # Check that index files exist where they should
    assert (tmp_path / "index.md").exists()
    assert (subdir / "index.md").exists()
    assert (nested_subdir / "index.md").exists()
    assert not (empty_subdir / "index.md").exists()

    # Verify contents
    root_index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "* [Alpha](a.md)" in root_index
    assert "* [topics](topics/)" in root_index

    subdir_index = (subdir / "index.md").read_text(encoding="utf-8")
    assert "* [Beta](b.md)" in subdir_index
    assert "* [nested](nested/)" in subdir_index

    nested_index = (nested_subdir / "index.md").read_text(encoding="utf-8")
    assert "* [Gamma](c.md)" in nested_index

    # Check JSON output format (should be a list of dictionaries)
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 3

    paths = [item["path"] for item in data]
    assert str(tmp_path / "index.md") in paths
    assert str(subdir / "index.md") in paths
    assert str(nested_subdir / "index.md") in paths

    for item in data:
        if (
            item["path"] == str(tmp_path / "index.md")
            or item["path"] == str(subdir / "index.md")
            or item["path"] == str(nested_subdir / "index.md")
        ):
            assert item["entries"] == 1


def test_index_recurse_quiet(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="Alpha")
    subdir = tmp_path / "sub"
    _write_concept(subdir / "b.md", title="Beta")

    result = _runner().invoke(
        cli, ["index", "--config", str(config_path), "--recurse", "-q"]
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    if hasattr(result, "stderr") and result.stderr is not None:
        assert result.stderr == ""

    assert (tmp_path / "index.md").exists()
    assert (subdir / "index.md").exists()


def test_index_recurse_handles_scan_problems(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="Alpha")

    # Write a broken file causing a scan problem
    (tmp_path / "broken.md").write_text(
        "---\ntype: [invalid\n---\nBody\n", encoding="utf-8"
    )

    result = _runner().invoke(cli, ["index", "--config", str(config_path), "--recurse"])
    assert result.exit_code == 1

    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["entries"] == 1
    assert len(data[0]["scan_problems"]) == 1
    assert "broken.md" in data[0]["scan_problems"][0]["path"]


def test_index_recurse_with_directory_option(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nbundle_root = "{tmp_path}"\n', encoding="utf-8"
    )
    _write_concept(tmp_path / "a.md", title="Alpha")

    subdir = tmp_path / "topics"
    _write_concept(subdir / "b.md", title="Beta")

    nested_subdir = subdir / "nested"
    _write_concept(nested_subdir / "c.md", title="Gamma")

    result = _runner().invoke(
        cli,
        [
            "index",
            "--config",
            str(config_path),
            "--recurse",
            "--directory",
            str(subdir),
        ],
    )

    assert result.exit_code == 0

    # It should generate index.md for 'topics' and 'topics/nested', but NOT the bundle root
    assert not (tmp_path / "index.md").exists()
    assert (subdir / "index.md").exists()
    assert (nested_subdir / "index.md").exists()

    # Check JSON output format (should be a list of 2 generated indexes)
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 2

    paths = [item["path"] for item in data]
    assert str(tmp_path / "index.md") not in paths
    assert str(subdir / "index.md") in paths
    assert str(nested_subdir / "index.md") in paths


def test_cli_orient_prints_guidance() -> None:
    result = _runner().invoke(cli, ["orient"])
    assert result.exit_code == 0
    assert "# OKF Bundle Agent & Developer Orientation" in result.stdout
    assert "okf scan" in result.stdout
    assert "okf-core.toml" in result.stdout
    assert "okf list-concepts" in result.stdout
    assert "okf --help" in result.stdout
    assert "okf unlinked-mentions --help" in result.stdout
    assert "--bundle" in result.stdout
    assert "--output" in result.stdout
    assert "--json" in result.stdout
    assert "wiki-graph-out" in result.stdout
    assert "SUMMARY.md" in result.stdout
    assert "GRAPH_REPORT.md" in result.stdout
    assert "graph.json" in result.stdout
    assert "generated diagnostics" in result.stdout
    assert "not wiki notes" in result.stdout
    assert "okf graph-report --help" in result.stdout

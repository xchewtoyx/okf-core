"""Tests for the graph-report orchestrator and output-tree safety."""

from __future__ import annotations

from pathlib import Path

import pytest
from freezegun import freeze_time

from okf_core import (
    GraphAnalysisError,
    GraphModelError,
    GraphReportError,
    GraphReportProvenance,
    acquire_normalized_graph,
    load_config,
    run_graph_report,
)
from okf_core.graph_report_run import (
    clean_stale_graph_report_artifacts,
    forbidden_graph_report_roots,
    validate_graph_report_output_dir,
)

_FROZEN_PROVENANCE = GraphReportProvenance(
    generated_at="2026-01-15T12:00:00Z",
    okf_version="0.2",
    git_revision=None,
)


def _write_concept(path: Path, *, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: concept\ntitle: {title}\n---\nBody\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_two_bundle_config(tmp_path: Path) -> Path:
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


def _write_root_bundle_config(tmp_path: Path) -> Path:
    _write_concept(tmp_path / "root-note.md", title="Root")
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        '[bundles.default]\nbundle_root = "."\n',
        encoding="utf-8",
    )
    return config_path


def test_run_graph_report_defaults_to_all_configured_bundles(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    output = tmp_path / "out"

    result = run_graph_report(config, output_dir=output, provenance=_FROZEN_PROVENANCE)

    assert result.selected_bundle_names == ("docs", "notes")
    assert result.is_subset is False
    assert [row.bundle for row in result.rows] == ["docs", "notes"]
    assert (output / "SUMMARY.md").is_file()
    assert (output / "docs" / "GRAPH_REPORT.md").is_file()
    assert (output / "notes" / "graph.json").is_file()


def test_run_graph_report_selected_bundle_is_a_subset(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    output = tmp_path / "out"

    result = run_graph_report(
        config,
        bundle_names=("notes",),
        output_dir=output,
        provenance=_FROZEN_PROVENANCE,
    )

    assert result.selected_bundle_names == ("notes",)
    assert result.is_subset is True
    assert [row.bundle for row in result.rows] == ["notes"]
    summary = (output / "SUMMARY.md").read_text(encoding="utf-8")
    assert "selected subset of configured bundles: notes" in summary
    assert "Configured bundles: docs, notes" in summary
    assert (output / "notes" / "GRAPH_REPORT.md").is_file()
    assert not (output / "docs" / "GRAPH_REPORT.md").exists()


def test_unknown_bundle_raises_graph_report_error(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))

    with pytest.raises(GraphReportError, match="Unknown bundle"):
        run_graph_report(
            config,
            bundle_names=("missing",),
            output_dir=tmp_path / "out",
            provenance=_FROZEN_PROVENANCE,
        )


def test_output_equal_to_a_bundle_root_is_rejected(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))

    with pytest.raises(GraphReportError, match="forbidden"):
        run_graph_report(
            config,
            output_dir=config.bundles["docs"].bundle_root,
            provenance=_FROZEN_PROVENANCE,
        )


def test_output_inside_a_bundle_root_is_rejected(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    inside = config.bundles["docs"].bundle_root / "reports"

    with pytest.raises(GraphReportError, match="forbidden"):
        run_graph_report(config, output_dir=inside, provenance=_FROZEN_PROVENANCE)


def test_output_at_project_root_is_rejected_when_slug_lands_in_a_bundle(
    tmp_path: Path,
) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    docs_report = tmp_path / "docs" / "GRAPH_REPORT.md"
    docs_json = tmp_path / "docs" / "graph.json"
    assert not docs_report.exists()
    assert not docs_json.exists()

    with pytest.raises(GraphReportError, match="forbidden"):
        run_graph_report(config, output_dir=tmp_path, provenance=_FROZEN_PROVENANCE)

    assert not docs_report.exists()
    assert not docs_json.exists()
    assert (tmp_path / "docs" / "alpha.md").is_file()


def test_output_inside_fleeting_is_rejected(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    fleeting = tmp_path / "fleeting" / "out"
    fleeting.mkdir(parents=True)

    with pytest.raises(GraphReportError, match="forbidden"):
        run_graph_report(config, output_dir=fleeting, provenance=_FROZEN_PROVENANCE)


def test_default_output_succeeds_when_bundles_are_subdirectories(
    tmp_path: Path,
) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))

    result = run_graph_report(config, provenance=_FROZEN_PROVENANCE)

    default = tmp_path / "wiki-graph-out"
    assert default.joinpath("SUMMARY.md").is_file()
    assert result.is_subset is False
    assert [row.bundle for row in result.rows] == ["docs", "notes"]


def test_default_output_refuses_when_bundle_root_is_project_root(
    tmp_path: Path,
) -> None:
    config = load_config(config_path=_write_root_bundle_config(tmp_path))

    with pytest.raises(GraphReportError, match="Pass --output"):
        run_graph_report(config, provenance=_FROZEN_PROVENANCE)


def test_clean_stale_graph_report_artifacts_is_filename_and_depth_limited(
    tmp_path: Path,
) -> None:
    output = tmp_path / "out"
    child = output / "docs"
    nested = output / "docs" / "nested"
    sibling_file = output / "keep.md"
    child.mkdir(parents=True)
    nested.mkdir()
    (output / "SUMMARY.md").write_text("old summary\n", encoding="utf-8")
    (child / "GRAPH_REPORT.md").write_text("old report\n", encoding="utf-8")
    (child / "graph.json").write_text("{}\n", encoding="utf-8")
    (child / "notes.txt").write_text("keep\n", encoding="utf-8")
    (nested / "GRAPH_REPORT.md").write_text("nested\n", encoding="utf-8")
    sibling_file.write_text("keep sibling\n", encoding="utf-8")

    clean_stale_graph_report_artifacts(output)

    assert not (output / "SUMMARY.md").exists()
    assert not (child / "GRAPH_REPORT.md").exists()
    assert not (child / "graph.json").exists()
    assert (child / "notes.txt").read_text(encoding="utf-8") == "keep\n"
    assert (nested / "GRAPH_REPORT.md").read_text(encoding="utf-8") == "nested\n"
    assert sibling_file.read_text(encoding="utf-8") == "keep sibling\n"
    assert child.is_dir()
    assert nested.is_dir()


def test_clean_stale_refuses_escaping_child_directory_symlink(
    tmp_path: Path,
) -> None:
    output = tmp_path / "out"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "GRAPH_REPORT.md").write_text("keep report\n", encoding="utf-8")
    (outside / "graph.json").write_text("keep json\n", encoding="utf-8")
    output.mkdir()
    (output / "docs").symlink_to(outside)

    with pytest.raises(GraphReportError, match="strict descendant"):
        clean_stale_graph_report_artifacts(output)

    assert (outside / "GRAPH_REPORT.md").read_text(encoding="utf-8") == "keep report\n"
    assert (outside / "graph.json").read_text(encoding="utf-8") == "keep json\n"
    assert (output / "docs").is_symlink()


def test_clean_stale_refuses_escaping_summary_symlink(tmp_path: Path) -> None:
    output = tmp_path / "out"
    outside = tmp_path / "elsewhere" / "keep.md"
    outside.parent.mkdir()
    outside.write_text("keep summary target\n", encoding="utf-8")
    output.mkdir()
    (output / "SUMMARY.md").symlink_to(outside)

    with pytest.raises(GraphReportError, match="strict descendant"):
        clean_stale_graph_report_artifacts(output)

    assert outside.read_text(encoding="utf-8") == "keep summary target\n"
    assert (output / "SUMMARY.md").is_symlink()


def test_clean_stale_refuses_escaping_file_symlink(tmp_path: Path) -> None:
    output = tmp_path / "out"
    outside = tmp_path / "elsewhere" / "keep.md"
    outside.parent.mkdir()
    outside.write_text("keep outside\n", encoding="utf-8")
    child = output / "docs"
    child.mkdir(parents=True)
    (child / "GRAPH_REPORT.md").symlink_to(outside)

    with pytest.raises(GraphReportError, match="strict descendant"):
        clean_stale_graph_report_artifacts(output)

    assert outside.read_text(encoding="utf-8") == "keep outside\n"
    assert (child / "GRAPH_REPORT.md").is_symlink()


def test_run_graph_report_refuses_leftover_summary_symlink_into_bundle(
    tmp_path: Path,
) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    bundle_file = tmp_path / "docs" / "alpha.md"
    original = bundle_file.read_text(encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    (output / "SUMMARY.md").symlink_to(bundle_file)

    with pytest.raises(GraphReportError, match="strict descendant|forbidden"):
        run_graph_report(config, output_dir=output, provenance=_FROZEN_PROVENANCE)

    assert bundle_file.read_text(encoding="utf-8") == original
    assert (output / "SUMMARY.md").is_symlink()


def test_run_graph_report_refuses_leftover_report_symlink_into_bundle(
    tmp_path: Path,
) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    bundle_file = tmp_path / "docs" / "alpha.md"
    original = bundle_file.read_text(encoding="utf-8")
    dest = tmp_path / "out" / "docs"
    dest.mkdir(parents=True)
    (dest / "GRAPH_REPORT.md").symlink_to(bundle_file)

    with pytest.raises(GraphReportError, match="strict descendant|forbidden"):
        run_graph_report(
            config, output_dir=tmp_path / "out", provenance=_FROZEN_PROVENANCE
        )

    assert bundle_file.read_text(encoding="utf-8") == original
    assert (dest / "GRAPH_REPORT.md").is_symlink()


@freeze_time("2026-08-28T12:34:56Z")
def test_default_generated_at_uses_frozen_utc_now(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    output = tmp_path / "out"

    run_graph_report(config, bundle_names=("docs",), output_dir=output)

    summary = (output / "SUMMARY.md").read_text(encoding="utf-8")
    assert "2026-08-28T12:34:56Z" in summary


@pytest.mark.parametrize(
    "output_dir",
    ["tmp", None, 1],
    ids=["string", "none", "int"],
)
def test_validate_output_dir_rejects_non_path(output_dir: object) -> None:
    with pytest.raises(GraphReportError, match="output_dir"):
        validate_graph_report_output_dir(output_dir, ())  # type: ignore[arg-type]


def test_forbidden_roots_include_every_bundle_and_fleeting(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    roots = forbidden_graph_report_roots(config)
    assert config.bundles["docs"].bundle_root in roots
    assert config.bundles["notes"].bundle_root in roots
    assert tmp_path / "fleeting" in roots


@pytest.mark.parametrize(
    "forbidden_roots",
    ["docs", None, ("docs", Path("/tmp"))],
    ids=["string", "none", "mixed"],
)
def test_validate_forbidden_roots_must_be_paths(forbidden_roots: object) -> None:
    with pytest.raises(GraphReportError, match="forbidden_roots"):
        validate_graph_report_output_dir(Path("/tmp/out"), forbidden_roots)  # type: ignore[arg-type]


def test_clean_stale_rejects_non_path_output_dir() -> None:
    with pytest.raises(GraphReportError, match="output_dir"):
        clean_stale_graph_report_artifacts("out")  # type: ignore[arg-type]


def test_run_graph_report_rejects_non_config(tmp_path: Path) -> None:
    with pytest.raises(GraphReportError, match="OkfConfig"):
        run_graph_report(object(), output_dir=tmp_path / "out")  # type: ignore[arg-type]


def test_run_graph_report_rejects_non_provenance(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    with pytest.raises(GraphReportError, match="GraphReportProvenance"):
        run_graph_report(
            config,
            output_dir=tmp_path / "out",
            provenance=object(),  # type: ignore[arg-type]
        )


def test_run_graph_report_rejects_string_bundle_names(tmp_path: Path) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    with pytest.raises(GraphReportError, match="bundle_names"):
        run_graph_report(
            config,
            bundle_names="docs",  # type: ignore[arg-type]
            output_dir=tmp_path / "out",
            provenance=_FROZEN_PROVENANCE,
        )


@pytest.mark.parametrize(
    ("target", "exc", "kind"),
    [
        (
            "okf_core.graph_report_run.acquire_normalized_graph",
            GraphModelError("normalized graph failed"),
            "model-error",
        ),
        (
            "okf_core.graph_report_run.analyze_normalized_graph",
            GraphAnalysisError("analysis failed"),
            "analysis-error",
        ),
        (
            "okf_core.graph_report_run.render_graph_report",
            GraphReportError("render failed"),
            "render-error",
        ),
    ],
    ids=["model", "analysis", "render"],
)
def test_per_bundle_failure_is_a_problem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    exc: Exception,
    kind: str,
) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(target, _boom)
    result = run_graph_report(
        config,
        bundle_names=("docs",),
        output_dir=tmp_path / "out",
        provenance=_FROZEN_PROVENANCE,
    )

    assert result.rows == ()
    assert len(result.problems) == 1
    assert result.problems[0].kind == kind
    assert str(exc) in result.problems[0].message


def test_all_bundles_run_with_one_failure_does_not_claim_a_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(config_path=_write_two_bundle_config(tmp_path))
    original = acquire_normalized_graph

    def _fail_notes(bundle: object, *, manifest: object = None) -> object:
        if getattr(bundle, "name", None) == "notes":
            raise GraphModelError("notes failed")
        return original(bundle, manifest=manifest)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "okf_core.graph_report_run.acquire_normalized_graph", _fail_notes
    )
    result = run_graph_report(
        config, output_dir=tmp_path / "out", provenance=_FROZEN_PROVENANCE
    )

    assert result.is_subset is False
    assert result.selected_bundle_names == ("docs", "notes")
    assert [row.bundle for row in result.rows] == ["docs"]
    summary = (tmp_path / "out" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "selected subset" not in summary
    assert "produced no row: notes" in summary

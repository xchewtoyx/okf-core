"""Library orchestrator for ``okf graph-report``.

Policy (also on :func:`run_graph_report`):

- This module does not grow :mod:`okf_core.graph_analysis` or
  :mod:`okf_core.graph_model`. It acquires, analyzes, renders, and writes
  through the existing public helpers.
- Output must not land in a configured bundle root or ``fleeting/``.
  Every write and unlink goes through
  :func:`~okf_core.graph_report.apply_graph_report_output_file`, which
  ``resolve()``s the final file path and refuses unless that location is
  a strict descendant of ``output_dir`` and not equal to or inside a
  forbidden root. Default output is ``<project_root>/wiki-graph-out``.
  When that default sits inside a forbidden root (typical
  ``bundle_root = "."``), the run refuses and asks for ``--output``
  outside authoring surfaces.
- Stale cleanup deletes only ``SUMMARY.md`` at the output root and
  ``GRAPH_REPORT.md`` / ``graph.json`` in immediate child directories,
  through that same helper. It does not recurse, delete other filenames,
  or ``rmdir``.
- ``SUMMARY.md`` covers selected bundles only. Default provenance does
  not run ``git``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from okf_core.config import BundleConfig, OkfConfig
from okf_core.graph_analysis import (
    BundleGraphAnalysis,
    GraphAnalysisError,
    analyze_normalized_graph,
)
from okf_core.graph_model import (
    GraphModelError,
    NormalizedBundleGraph,
    acquire_normalized_graph,
)
from okf_core.graph_report import (
    BundleGraphArtifactPaths,
    GraphReportError,
    GraphReportProvenance,
    GraphSummaryRow,
    apply_graph_report_output_file,
    render_graph_json,
    render_graph_report,
    render_graph_summary,
    resolved_graph_report_forbidden_roots,
    write_bundle_graph_artifacts,
)

_SUMMARY_FILENAME = "SUMMARY.md"
_DEFAULT_OUTPUT_DIRNAME = "wiki-graph-out"
_STALE_CHILD_FILENAMES = ("GRAPH_REPORT.md", "graph.json")


@dataclass(frozen=True)
class GraphReportProblem:
    """One selected bundle that could not be modeled, analyzed, or rendered."""

    bundle_name: str
    kind: str
    message: str


@dataclass(frozen=True)
class GraphReportRunResult:
    """Named return from :func:`run_graph_report`.

    ``rows`` and ``written_paths`` reflect selected bundles that succeeded.
    ``problems`` carries per-bundle model, analysis, or render failures so
    callers always see what was skipped.
    """

    selected_bundle_names: tuple[str, ...]
    is_subset: bool
    rows: tuple[GraphSummaryRow, ...]
    written_paths: tuple[Path, ...]
    problems: tuple[GraphReportProblem, ...]


def forbidden_graph_report_roots(config: OkfConfig) -> tuple[Path, ...]:
    """Return every configured ``bundle_root`` plus ``<project_root>/fleeting``."""

    typed = _require_config(config)
    return tuple(bundle.bundle_root for bundle in typed.bundles.values()) + (
        typed.project_root / "fleeting",
    )


def validate_graph_report_output_dir(
    output_dir: Path, forbidden_roots: Sequence[Path]
) -> Path:
    """Resolve ``output_dir`` or raise if it is equal to or inside a forbidden root."""

    if not isinstance(output_dir, Path):
        raise GraphReportError("output_dir must be a pathlib.Path")
    resolved = output_dir.expanduser().resolve()
    for forbidden in resolved_graph_report_forbidden_roots(forbidden_roots):
        if resolved == forbidden or resolved.is_relative_to(forbidden):
            raise GraphReportError(
                f"output_dir {resolved} is equal to or inside forbidden "
                f"path {forbidden}"
            )
    return resolved


def clean_stale_graph_report_artifacts(
    output_dir: Path, forbidden_roots: Sequence[Path] = ()
) -> None:
    """Delete known generated filenames from a previous run.

    Removes ``SUMMARY.md`` at ``output_dir`` and ``GRAPH_REPORT.md`` /
    ``graph.json`` in each immediate child directory, through
    :func:`apply_graph_report_output_file`. Does not recurse, delete other
    names, or remove directories.
    """

    if not isinstance(output_dir, Path):
        raise GraphReportError("output_dir must be a pathlib.Path")
    apply_graph_report_output_file(
        output_dir / _SUMMARY_FILENAME,
        output_dir=output_dir,
        forbidden_roots=forbidden_roots,
        unlink=True,
    )
    if not output_dir.is_dir():
        return
    for child in output_dir.iterdir():
        stale, _problem = _stale_child_artifacts(child)
        for path in stale:
            apply_graph_report_output_file(
                path,
                output_dir=output_dir,
                forbidden_roots=forbidden_roots,
                unlink=True,
            )


def run_graph_report(
    config: OkfConfig,
    *,
    bundle_names: Sequence[str] | None = None,
    output_dir: Path | None = None,
    provenance: GraphReportProvenance | None = None,
) -> GraphReportRunResult:
    """Generate per-bundle artifacts and a selected-only ``SUMMARY.md``.

    ``bundle_names is None`` selects every configured bundle, slug-asc.
    An unknown name raises :class:`GraphReportError`. Default
    ``output_dir`` is ``<project_root>/wiki-graph-out``. Default
    provenance uses UTC now (``YYYY-MM-DDTHH:MM:SSZ``),
    ``okf_core.__version__``, and ``git_revision=None`` — this function
    does not run ``git``.

    Guard the output directory, then clean and write through
    :func:`apply_graph_report_output_file` so a leftover symlink into a
    bundle cannot be followed. Render failures stay per-bundle problems;
    a path-safety failure aborts the run. ``SUMMARY.md`` is written from
    selected rows that succeeded.
    """

    typed = _require_config(config)
    selected, is_subset = _selected_bundle_names(typed, bundle_names)
    forbidden = forbidden_graph_report_roots(typed)
    resolved_output = _resolve_run_output_dir(typed, output_dir)
    typed_provenance = provenance if provenance is not None else _default_provenance()
    if provenance is not None:
        _require_run_provenance(provenance)
    clean_stale_graph_report_artifacts(resolved_output, forbidden)
    pending, problems = _collect_bundle_renders(typed, selected, typed_provenance)
    rows, written = _write_bundle_renders(resolved_output, pending, forbidden)
    summary_path = _write_summary(
        resolved_output,
        rows,
        typed_provenance,
        configured_bundle_names=tuple(typed.bundles),
        selected_bundle_names=selected,
        forbidden_roots=forbidden,
    )
    return GraphReportRunResult(
        selected_bundle_names=selected,
        is_subset=is_subset,
        rows=tuple(rows),
        written_paths=tuple(written) + (summary_path,),
        problems=tuple(problems),
    )


def _require_config(config: object) -> OkfConfig:
    if not isinstance(config, OkfConfig):
        raise GraphReportError("config must be an OkfConfig")
    return config


def _require_run_provenance(provenance: object) -> GraphReportProvenance:
    if not isinstance(provenance, GraphReportProvenance):
        raise GraphReportError("provenance must be a GraphReportProvenance")
    return provenance


def _default_provenance() -> GraphReportProvenance:
    from okf_core import __version__

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return GraphReportProvenance(
        generated_at=generated_at,
        okf_version=__version__,
        git_revision=None,
    )


def _selected_bundle_names(
    config: OkfConfig, bundle_names: Sequence[str] | None
) -> tuple[tuple[str, ...], bool]:
    configured = tuple(sorted(config.bundles))
    if bundle_names is None:
        return configured, False
    if isinstance(bundle_names, (str, bytes)) or not isinstance(bundle_names, Sequence):
        raise GraphReportError("bundle_names must be a sequence of str or None")
    unknown = tuple(
        sorted({name for name in bundle_names if name not in config.bundles})
    )
    if unknown:
        available = ", ".join(configured) or "(none)"
        raise GraphReportError(
            f"Unknown bundle(s): {', '.join(unknown)}. Available: {available}"
        )
    selected = tuple(sorted(set(bundle_names)))
    return selected, selected != configured


def _resolve_run_output_dir(config: OkfConfig, output_dir: Path | None) -> Path:
    if output_dir is None:
        target = config.project_root / _DEFAULT_OUTPUT_DIRNAME
        using_default = True
    else:
        target = output_dir
        using_default = False
    forbidden = forbidden_graph_report_roots(config)
    try:
        return validate_graph_report_output_dir(target, forbidden)
    except GraphReportError:
        if using_default:
            raise GraphReportError(
                "Default output directory "
                f"{target} is inside a configured bundle root or fleeting/. "
                "Pass --output outside authoring surfaces."
            ) from None
        raise


def _collect_bundle_renders(
    config: OkfConfig,
    selected: tuple[str, ...],
    provenance: GraphReportProvenance,
) -> tuple[list[tuple[GraphSummaryRow, str, str]], list[GraphReportProblem]]:
    pending: list[tuple[GraphSummaryRow, str, str]] = []
    problems: list[GraphReportProblem] = []
    for name in selected:
        result, problem = _render_one_bundle(config.bundles[name], provenance)
        if result is not None:
            pending.append(result)
        if problem is not None:
            problems.append(problem)
    return pending, problems


def _write_bundle_renders(
    output_dir: Path,
    pending: Sequence[tuple[GraphSummaryRow, str, str]],
    forbidden_roots: Sequence[Path],
) -> tuple[list[GraphSummaryRow], list[Path]]:
    rows: list[GraphSummaryRow] = []
    written: list[Path] = []
    for row, markdown, payload in pending:
        paths = write_bundle_graph_artifacts(
            output_dir,
            row.bundle,
            report_markdown=markdown,
            graph_json=payload,
            forbidden_roots=forbidden_roots,
        )
        rows.append(row)
        written.extend((paths.report_path, paths.graph_json_path))
    return rows, written


def _render_one_bundle(
    bundle: BundleConfig,
    provenance: GraphReportProvenance,
) -> tuple[tuple[GraphSummaryRow, str, str] | None, GraphReportProblem | None]:
    try:
        model = acquire_normalized_graph(bundle)
        analysis = analyze_normalized_graph(model)
        markdown = render_graph_report(model, analysis, provenance=provenance)
        payload = render_graph_json(model, analysis)
        return (_summary_row(model, analysis), markdown, payload), None
    except GraphModelError as exc:
        return None, GraphReportProblem(bundle.name, "model-error", str(exc))
    except GraphAnalysisError as exc:
        return None, GraphReportProblem(bundle.name, "analysis-error", str(exc))
    except GraphReportError as exc:
        return None, GraphReportProblem(bundle.name, "render-error", str(exc))


def _summary_row(
    model: NormalizedBundleGraph, analysis: BundleGraphAnalysis
) -> GraphSummaryRow:
    concept_count = analysis.overview.concept_count
    largest = analysis.components.sizes[0] if analysis.components.sizes else 0
    coverage = (largest / concept_count) if concept_count else 0.0
    zero_in = len(analysis.diagnostics.zero_inbound)
    zero_out = len(analysis.diagnostics.zero_outbound)
    top = analysis.top_by_pagerank[0].concept_id if analysis.top_by_pagerank else ""
    return GraphSummaryRow(
        bundle=analysis.bundle_name,
        concepts=concept_count,
        unique_links=analysis.overview.unique_directed_edge_count,
        components=analysis.components.count,
        largest_component_coverage=coverage,
        orphans=len(analysis.diagnostics.orphans),
        percent_zero_inbound=(
            (100.0 * zero_in / concept_count) if concept_count else 0.0
        ),
        percent_zero_outbound=(
            (100.0 * zero_out / concept_count) if concept_count else 0.0
        ),
        broken_links_and_problems=len(model.broken_links) + len(model.problems),
        top_central_concept=top,
        articulation_point_count=len(analysis.articulation_points),
    )


def _write_summary(
    output_dir: Path,
    rows: Sequence[GraphSummaryRow],
    provenance: GraphReportProvenance,
    *,
    configured_bundle_names: tuple[str, ...],
    selected_bundle_names: tuple[str, ...],
    forbidden_roots: Sequence[Path],
) -> Path:
    summary_path = output_dir / _SUMMARY_FILENAME
    apply_graph_report_output_file(
        summary_path,
        output_dir=output_dir,
        forbidden_roots=forbidden_roots,
        text=render_graph_summary(
            rows,
            provenance=provenance,
            configured_bundle_names=configured_bundle_names,
            selected_bundle_names=selected_bundle_names,
        ),
    )
    return summary_path


def _stale_child_artifacts(child: Path) -> tuple[tuple[Path, ...], None]:
    if not child.is_dir():
        return (), None
    stale = tuple(
        child / name
        for name in _STALE_CHILD_FILENAMES
        if (child / name).is_file() or (child / name).is_symlink()
    )
    return stale, None

"""Pure-string renderers for per-bundle graph-report artifacts.

Policy (also on :func:`render_graph_report` and :func:`render_graph_json`):

- This module does not grow :mod:`okf_core.graph_analysis` or
  :mod:`okf_core.graph_model`. It reads a ``NormalizedBundleGraph`` plus a
  ``BundleGraphAnalysis`` and returns text.
- Volatile fields (timestamp, git revision, source commands) are injected by
  the caller via :class:`GraphReportProvenance`. The library does not run
  ``git`` or the ``okf`` CLI.
- ``GRAPH_REPORT.md`` section order and headings are the canonical form
  documented on :func:`render_graph_report`. The Provenance section is the
  only volatile block; the rest of the body is byte-stable for a given
  model, analysis, and non-provenance inputs.
- Concept IDs and titles are literal inline Markdown
  (``render_inline_children(text_children(...))``). The report emits no
  Markdown links. Suggested inspections are fenced ``sh`` lines.
- Floats in the Markdown body always use ``format(x, ".6f")``.
- ``graph.json`` is a portable envelope of model + analysis only: no
  provenance, timestamps, ``output_dir``, or ``bundle_root``.
- :func:`write_bundle_graph_artifacts` only joins ``<output>/<slug>/`` and
  writes the two filenames. It does not guard bundle directories, fleeting
  paths, or gitignore (those are a later stage).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard

from okf_core.graph_analysis import (
    ArticulationPoint,
    BundleGraphAnalysis,
    RankedConcept,
)
from okf_core.graph_model import (
    GraphModelBrokenLink,
    GraphModelNode,
    GraphModelProblem,
    NormalizedBundleGraph,
)
from okf_core.markdown_engine import render_inline_children, text_children

_REPORT_FILENAME = "GRAPH_REPORT.md"
_GRAPH_JSON_FILENAME = "graph.json"
_EDGE_POLICY = (
    "directed unique-edge; self-links + unlisted-endpoint off unique edges; "
    "fragments not recovered; WCC/AP on undirected unique-edge projection"
)


class GraphReportError(Exception):
    """Raised when a graph report cannot be rendered or written fail-closed."""


@dataclass(frozen=True)
class GraphReportProvenance:
    """Caller-injected volatile fields for the Provenance section.

    ``generated_at`` is an ISO-8601 UTC timestamp string. ``git_revision`` is
    omitted from the report when ``None``. Empty ``source_commands`` are
    replaced at render time by the default ``okf graph`` /
    ``okf list-concepts --with-graph-counts`` pair for the bundle slug.
    """

    generated_at: str
    okf_version: str
    git_revision: str | None = None
    source_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class BundleGraphArtifactPaths:
    """Paths written by :func:`write_bundle_graph_artifacts`."""

    report_path: Path
    graph_json_path: Path


def render_graph_report(
    model: NormalizedBundleGraph,
    analysis: BundleGraphAnalysis,
    *,
    provenance: GraphReportProvenance,
) -> str:
    """Render the canonical ``GRAPH_REPORT.md`` body for one bundle.

    Section order and headings (the documented canonical form):

    1. ``# Graph report``
    2. ``## Provenance`` — bundle slug, OKF version, ``generated_at``,
       optional git revision, exact source commands, fixed
       edge-interpretation policy.
    3. ``## Graph overview`` — counts, density, reciprocity, degree stats,
       component count and sizes.
    4. ``## Graph health`` — OKF ``broken_links`` / ``problems``, orphans,
       zero-inbound, zero-outbound, ``other_memberships``. Largest-component
       membership is omitted. Lists are diagnostic signals, not quotas.
    5. ``## High-centrality concepts`` — top-N by PageRank and inbound
       degree, with a dual-reading note (foundational hub vs over-broad
       note).
    6. ``## Bridge concepts`` — each articulation point and its regions.
    7. ``## Suggested inspections`` — one condition-driven ``okf`` command
       per observed condition, or ``None observed.``
    8. ``## Communities`` — placeholder; analysis is deferred until CCP-260.

    Titles come from ``model.nodes``; a ``None`` title falls back to the
    concept id. IDs and titles are literal inline, never Markdown links.

    Raises :class:`GraphReportError` if ``model`` is not a
    :class:`NormalizedBundleGraph`, ``analysis`` is not a
    :class:`BundleGraphAnalysis`, ``provenance`` is not a
    :class:`GraphReportProvenance`, or ``model.bundle_name`` does not
    equal ``analysis.bundle_name``.
    """

    typed_model, typed_analysis, typed_provenance = _require_report_inputs(
        model, analysis, provenance
    )
    sections = (
        "# Graph report",
        _render_provenance(typed_model, typed_provenance),
        _render_overview(typed_analysis),
        _render_health(typed_model, typed_analysis),
        _render_centrality(typed_model, typed_analysis),
        _render_bridges(typed_model, typed_analysis),
        _render_inspections(typed_model, typed_analysis),
        _render_communities_placeholder(),
    )
    return "\n\n".join(sections) + "\n"


def graph_report_payload(
    model: NormalizedBundleGraph,
    analysis: BundleGraphAnalysis,
) -> dict[str, Any]:
    """Return the portable ``graph.json`` envelope for one bundle.

    The envelope is ``schema_version`` 1 plus the model and analysis
    ``to_portable_dict`` snapshots. It carries no provenance, timestamps,
    ``output_dir``, or ``bundle_root``.
    """

    typed_model, typed_analysis = _require_model_and_analysis(model, analysis)
    return {
        "schema_version": 1,
        "normalized_graph": typed_model.to_portable_dict(),
        "analysis": typed_analysis.to_portable_dict(),
    }


def render_graph_json(
    model: NormalizedBundleGraph,
    analysis: BundleGraphAnalysis,
) -> str:
    """Render the portable ``graph.json`` text for one bundle.

    Uses ``json.dumps(..., sort_keys=True, indent=2, ensure_ascii=True,
    allow_nan=False)`` and a trailing newline.
    """

    return (
        json.dumps(
            graph_report_payload(model, analysis),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )


def write_bundle_graph_artifacts(
    output_dir: Path,
    bundle_slug: str,
    *,
    report_markdown: str,
    graph_json: str,
) -> BundleGraphArtifactPaths:
    """Write ``GRAPH_REPORT.md`` and ``graph.json`` under ``<output>/<slug>/``.

    Creates parent directories. Writes UTF-8. Rejects an empty slug or a
    slug containing ``/`` or ``\\``. ``output_dir`` must be a
    :class:`~pathlib.Path`. This function does not interpret the strings
    and does not apply bundle-directory or gitignore guards.
    """

    root = _require_output_dir(output_dir)
    slug = _require_bundle_slug(bundle_slug)
    markdown = _require_artifact_text(report_markdown, "report_markdown")
    payload = _require_artifact_text(graph_json, "graph_json")
    dest = root / slug
    dest.mkdir(parents=True, exist_ok=True)
    report_path = dest / _REPORT_FILENAME
    graph_json_path = dest / _GRAPH_JSON_FILENAME
    report_path.write_text(markdown, encoding="utf-8")
    graph_json_path.write_text(payload, encoding="utf-8")
    return BundleGraphArtifactPaths(
        report_path=report_path, graph_json_path=graph_json_path
    )


def _require_report_inputs(
    model: object,
    analysis: object,
    provenance: object,
) -> tuple[NormalizedBundleGraph, BundleGraphAnalysis, GraphReportProvenance]:
    typed_model, typed_analysis = _require_model_and_analysis(model, analysis)
    return typed_model, typed_analysis, _require_provenance(provenance)


def _require_model_and_analysis(
    model: object, analysis: object
) -> tuple[NormalizedBundleGraph, BundleGraphAnalysis]:
    if not isinstance(model, NormalizedBundleGraph):
        raise GraphReportError("model must be a NormalizedBundleGraph")
    if not isinstance(analysis, BundleGraphAnalysis):
        raise GraphReportError("analysis must be a BundleGraphAnalysis")
    if model.bundle_name != analysis.bundle_name:
        raise GraphReportError(
            f"analysis.bundle_name {analysis.bundle_name!r} does not match "
            f"model.bundle_name {model.bundle_name!r}"
        )
    return model, analysis


def _require_provenance(provenance: object) -> GraphReportProvenance:
    if not isinstance(provenance, GraphReportProvenance):
        raise GraphReportError("provenance must be a GraphReportProvenance")
    if not _is_str(provenance.generated_at):
        raise GraphReportError("generated_at must be a str")
    if not _is_str(provenance.okf_version):
        raise GraphReportError("okf_version must be a str")
    if provenance.git_revision is not None and not _is_str(provenance.git_revision):
        raise GraphReportError("git_revision must be a str or None")
    if not isinstance(provenance.source_commands, tuple) or not all(
        _is_str(command) for command in provenance.source_commands
    ):
        raise GraphReportError("source_commands must be a tuple of str")
    return provenance


def _require_output_dir(output_dir: object) -> Path:
    if not isinstance(output_dir, Path):
        raise GraphReportError("output_dir must be a pathlib.Path")
    return output_dir


def _require_bundle_slug(bundle_slug: object) -> str:
    if not _is_str(bundle_slug) or not bundle_slug:
        raise GraphReportError("bundle_slug must be a non-empty string")
    if "/" in bundle_slug or "\\" in bundle_slug:
        raise GraphReportError("bundle_slug must not contain a path separator")
    return bundle_slug


def _require_artifact_text(value: object, name: str) -> str:
    if not _is_str(value):
        raise GraphReportError(f"{name} must be a str")
    return value


def _is_str(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and not isinstance(value, bool)


def _inline(text: str) -> str:
    return render_inline_children(text_children(text))


def _nodes_by_id(model: NormalizedBundleGraph) -> dict[str, GraphModelNode]:
    return {node.concept_id: node for node in model.nodes}


def _labeled(model: NormalizedBundleGraph, concept_id: str) -> str:
    node = _nodes_by_id(model).get(concept_id)
    title = concept_id if node is None or node.title is None else node.title
    rendered_title = _inline(title)
    rendered_id = _inline(concept_id)
    if title == concept_id:
        return rendered_id
    return f"{rendered_title} ({rendered_id})"


def _float(value: float) -> str:
    return format(value, ".6f")


def _default_source_commands(slug: str) -> tuple[str, ...]:
    return (
        f".venv/bin/okf graph --bundle {slug}",
        f".venv/bin/okf list-concepts --bundle {slug} --with-graph-counts",
    )


def _render_provenance(
    model: NormalizedBundleGraph, provenance: GraphReportProvenance
) -> str:
    commands = provenance.source_commands or _default_source_commands(model.bundle_name)
    lines = [
        "## Provenance",
        "",
        f"- Bundle: {_inline(model.bundle_name)}",
        f"- OKF version: {_inline(provenance.okf_version)}",
        f"- Generated at: {_inline(provenance.generated_at)}",
    ]
    if provenance.git_revision is not None:
        lines.append(f"- Git revision: {_inline(provenance.git_revision)}")
    lines.extend(
        [
            f"- Edge-interpretation policy: {_inline(_EDGE_POLICY)}",
            "",
            "### Source commands",
            "",
            "```sh",
            *commands,
            "```",
        ]
    )
    return "\n".join(lines)


def _render_overview(analysis: BundleGraphAnalysis) -> str:
    overview = analysis.overview
    components = analysis.components
    sizes = (
        ", ".join(str(size) for size in components.sizes)
        if components.sizes
        else "none"
    )
    return "\n".join(
        [
            "## Graph overview",
            "",
            f"- Concepts: {overview.concept_count}",
            f"- Link instances: {overview.link_instance_count}",
            f"- Unique directed edges: {overview.unique_directed_edge_count}",
            f"- Density: {_float(overview.density)}",
            f"- Reciprocal-edge ratio: {_float(overview.reciprocal_edge_ratio)}",
            f"- Mean in-degree: {_float(overview.mean_in_degree)}",
            f"- Median in-degree: {_float(overview.median_in_degree)}",
            f"- Mean out-degree: {_float(overview.mean_out_degree)}",
            f"- Median out-degree: {_float(overview.median_out_degree)}",
            f"- Weakly connected components: {components.count}",
            f"- Component sizes: {sizes}",
        ]
    )


def _render_health(model: NormalizedBundleGraph, analysis: BundleGraphAnalysis) -> str:
    return "\n\n".join(
        [
            "## Graph health",
            (
                "These lists are diagnostic signals, not quotas. Authoring "
                "rules permit notes with no outbound links."
            ),
            _render_broken_links(model),
            _render_problems(model),
            _render_id_list("Orphans", analysis.diagnostics.orphans, model),
            _render_id_list("Zero inbound", analysis.diagnostics.zero_inbound, model),
            _render_id_list("Zero outbound", analysis.diagnostics.zero_outbound, model),
            _render_other_memberships(model, analysis),
        ]
    )


def _render_broken_links(model: NormalizedBundleGraph) -> str:
    return _render_item_section("Broken links", model.broken_links, _broken_link_item)


def _render_problems(model: NormalizedBundleGraph) -> str:
    return _render_item_section("Problems", model.problems, _problem_item)


def _render_item_section(
    heading: str,
    items: tuple[Any, ...],
    item_helper: Callable[[Any], tuple[str, None]],
) -> str:
    lines = [f"### {heading}", ""]
    if not items:
        lines.append("None.")
        return "\n".join(lines)
    for item in items:
        rendered, _problem = item_helper(item)
        lines.append(rendered)
    return "\n".join(lines)


def _broken_link_item(link: GraphModelBrokenLink) -> tuple[str, None]:
    return (
        (
            f"- {_inline(link.source_id)} ({_inline(link.source_path)}); "
            f"target {_inline(link.target)}; path {_inline(link.target_path)}; "
            f"text {_inline(link.text)}"
        ),
        None,
    )


def _problem_item(problem: GraphModelProblem) -> tuple[str, None]:
    return (
        (
            f"- {_inline(problem.concept_id)} ({_inline(problem.path)}): "
            f"{_inline(problem.kind)}: {_inline(problem.message)}"
        ),
        None,
    )


def _render_id_list(
    heading: str, concept_ids: tuple[str, ...], model: NormalizedBundleGraph
) -> str:
    return _render_item_section(
        heading,
        concept_ids,
        lambda concept_id: _concept_list_item(concept_id, model),
    )


def _concept_list_item(
    concept_id: str, model: NormalizedBundleGraph
) -> tuple[str, None]:
    return f"- {_labeled(model, concept_id)}", None


def _render_other_memberships(
    model: NormalizedBundleGraph, analysis: BundleGraphAnalysis
) -> str:
    lines = [
        "### Other component memberships",
        "",
        "Largest-component membership is omitted.",
        "",
    ]
    if not analysis.components.other_memberships:
        lines.append("None.")
        return "\n".join(lines)
    for group in analysis.components.other_memberships:
        item, _problem = _membership_item(group, model)
        lines.append(item)
    return "\n".join(lines)


def _membership_item(
    group: tuple[str, ...], model: NormalizedBundleGraph
) -> tuple[str, None]:
    return "- " + ", ".join(_labeled(model, concept_id) for concept_id in group), None


def _render_centrality(
    model: NormalizedBundleGraph, analysis: BundleGraphAnalysis
) -> str:
    return "\n\n".join(
        [
            "## High-centrality concepts",
            (
                "A high-centrality concept may be a foundational hub or an "
                "over-broad note behaving like a forbidden hub. Rankings use "
                "verbatim OKF PageRank and inbound link count."
            ),
            _render_ranking("Top by PageRank", analysis.top_by_pagerank, model),
            _render_ranking("Top by inbound degree", analysis.top_by_inbound, model),
        ]
    )


def _render_ranking(
    heading: str, rows: tuple[RankedConcept, ...], model: NormalizedBundleGraph
) -> str:
    return _render_item_section(
        heading,
        tuple(enumerate(rows, start=1)),
        lambda item: _ranking_item(item[0], item[1], model),
    )


def _ranking_item(
    index: int, row: RankedConcept, model: NormalizedBundleGraph
) -> tuple[str, None]:
    return (
        f"{index}. {_labeled(model, row.concept_id)} — {_float(row.value)}",
        None,
    )


def _render_bridges(model: NormalizedBundleGraph, analysis: BundleGraphAnalysis) -> str:
    parts = [
        "## Bridge concepts",
        "",
        (
            "Articulation points on the undirected unique-edge projection, "
            "and the regions they connect."
        ),
    ]
    if not analysis.articulation_points:
        return "\n".join(parts + ["", "None observed."])
    for point in analysis.articulation_points:
        block, _problem = _bridge_item(point, model)
        parts.extend(["", block])
    return "\n".join(parts)


def _bridge_item(
    point: ArticulationPoint, model: NormalizedBundleGraph
) -> tuple[str, None]:
    lines = [f"### {_labeled(model, point.concept_id)}", ""]
    for region in point.regions:
        members = ", ".join(_labeled(model, concept_id) for concept_id in region)
        lines.append(f"- Region ({len(region)}): {members}")
    return "\n".join(lines), None


def _render_inspections(
    model: NormalizedBundleGraph, analysis: BundleGraphAnalysis
) -> str:
    header = (
        "## Suggested inspections\n\n"
        "Condition-driven follow-up commands. One command per observed condition."
    )
    blocks = _collect_inspections(model, analysis)
    if not blocks:
        return f"{header}\n\nNone observed."
    return header + "\n\n" + "\n\n".join(blocks)


def _collect_inspections(
    model: NormalizedBundleGraph, analysis: BundleGraphAnalysis
) -> tuple[str, ...]:
    blocks: list[str] = []
    for helper in (
        _inspection_articulation,
        _inspection_pagerank_zero_outbound,
        _inspection_orphans,
        _inspection_broken_link,
    ):
        command, _problem = helper(model, analysis)
        if command is not None:
            blocks.append(command)
    return tuple(blocks)


def _inspection_articulation(
    model: NormalizedBundleGraph, analysis: BundleGraphAnalysis
) -> tuple[str | None, None]:
    if not analysis.articulation_points:
        return None, None
    point = analysis.articulation_points[0]
    heading = (
        "Sole component bridge"
        if len(analysis.articulation_points) == 1
        else "Articulation point"
    )
    command = (
        f".venv/bin/okf graph --bundle {model.bundle_name} "
        f"--concept {point.concept_id} --depth 2"
    )
    return (
        _format_inspection(
            heading,
            f"Inspect the neighborhood of {_labeled(model, point.concept_id)}.",
            command,
        ),
        None,
    )


def _inspection_pagerank_zero_outbound(
    model: NormalizedBundleGraph, analysis: BundleGraphAnalysis
) -> tuple[str | None, None]:
    by_id = _nodes_by_id(model)
    for ranked in analysis.top_by_pagerank:
        node = by_id.get(ranked.concept_id)
        if node is None or node.outbound_link_count != 0:
            continue
        command = (
            f".venv/bin/okf context --bundle {model.bundle_name} "
            f"--seed {ranked.concept_id}"
        )
        return (
            _format_inspection(
                "High-PageRank concept with no outbound links",
                f"Inspect the content around {_labeled(model, ranked.concept_id)}.",
                command,
            ),
            None,
        )
    return None, None


def _inspection_orphans(
    model: NormalizedBundleGraph, analysis: BundleGraphAnalysis
) -> tuple[str | None, None]:
    orphans = analysis.diagnostics.orphans
    if not orphans:
        return None, None
    seed = orphans[0]
    command = f".venv/bin/okf context --bundle {model.bundle_name} --seed {seed}"
    return (
        _format_inspection(
            f"Orphans ({len(orphans)})",
            f"Inspect the first orphan, {_labeled(model, seed)}.",
            command,
        ),
        None,
    )


def _inspection_broken_link(
    model: NormalizedBundleGraph, analysis: BundleGraphAnalysis
) -> tuple[str | None, None]:
    del analysis
    if not model.broken_links:
        return None, None
    source_id = model.broken_links[0].source_id
    command = (
        f".venv/bin/okf graph --bundle {model.bundle_name} "
        f"--concept {source_id} --depth 2"
    )
    return (
        _format_inspection(
            "Broken link",
            f"Inspect the neighborhood of {_labeled(model, source_id)}.",
            command,
        ),
        None,
    )


def _format_inspection(heading: str, prose: str, command: str) -> str:
    return "\n".join(
        [
            f"### {heading}",
            "",
            prose,
            "",
            "```sh",
            command,
            "```",
        ]
    )


def _render_communities_placeholder() -> str:
    return "## Communities\n\nCommunity analysis is deferred until CCP-260."

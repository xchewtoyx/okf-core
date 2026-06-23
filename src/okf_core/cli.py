"""Command-line interface for okf-core."""

from __future__ import annotations

import datetime
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import click

from okf_core import (
    ConfigError,
    backlinks_to,
    build_bundle_graph,
    generate_index,
    list_concepts,
    links_from,
    load_config,
    neighborhood,
    scan_bundle,
    validate_bundle,
)


class _Encoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Mapping):
            return dict(obj)
        if isinstance(obj, (set, frozenset)):
            return sorted(obj, key=str)
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        return super().default(obj)


def _load(config_path: str | None, bundle_name: str) -> tuple[Any, Any]:
    try:
        cfg = load_config(config_path=config_path)
    except ConfigError as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(2)
    if bundle_name not in cfg.bundles:
        available = ", ".join(cfg.bundles) or "(none)"
        click.echo(
            f"Bundle {bundle_name!r} not found. Available: {available}",
            err=True,
        )
        sys.exit(2)
    return cfg, cfg.bundles[bundle_name]


@click.group()
def cli() -> None:
    """okf-core command-line tools for Open Knowledge Format bundles."""


@cli.command()
@click.option(
    "--config",
    "config_path",
    default=None,
    metavar="PATH",
    help="Path to okf-core.toml (default: search upward from cwd).",
)
@click.option(
    "--bundle",
    "bundle_name",
    default="default",
    show_default=True,
    metavar="NAME",
    help="Named bundle from config.",
)
def scan(config_path: str | None, bundle_name: str) -> None:
    """Scan a bundle and emit a JSON manifest."""
    _, bundle = _load(config_path, bundle_name)
    manifest = scan_bundle(bundle)
    result = {
        "bundle": bundle.name,
        "concepts": [
            {
                "concept_id": c.concept_id,
                "path": str(c.path),
                "size": c.size,
                "sha256": c.sha256,
                "frontmatter": c.frontmatter,
            }
            for c in manifest.concepts
        ],
        "problems": [
            {"path": str(p.path), "kind": p.kind, "message": p.message}
            for p in manifest.problems
        ],
    }
    click.echo(json.dumps(result, cls=_Encoder, indent=2))
    click.echo(
        f"Scanned bundle {bundle.name!r}: {len(manifest.concepts)} concepts, "
        f"{len(manifest.problems)} problems",
        err=True,
    )


@cli.command()
@click.option(
    "--config",
    "config_path",
    default=None,
    metavar="PATH",
    help="Path to okf-core.toml (default: search upward from cwd).",
)
@click.option(
    "--bundle",
    "bundle_name",
    default="default",
    show_default=True,
    metavar="NAME",
    help="Named bundle from config.",
)
def validate(config_path: str | None, bundle_name: str) -> None:
    """Validate a bundle and emit findings as JSON."""
    cfg, bundle = _load(config_path, bundle_name)
    findings = validate_bundle(bundle, cfg)
    error_count = 0
    warning_count = 0
    findings_dict: dict[str, list[dict[str, Any]]] = {}
    for path, path_findings in findings.items():
        findings_dict[str(path)] = [
            {"severity": f.severity, "message": f.message, "field": f.field}
            for f in path_findings
        ]
        for f in path_findings:
            if f.severity == "error":
                error_count += 1
            else:
                warning_count += 1
    result: dict[str, Any] = {"bundle": bundle.name, "findings": findings_dict}
    click.echo(json.dumps(result, cls=_Encoder, indent=2))
    click.echo(
        f"Validated bundle {bundle.name!r}: {error_count} errors, {warning_count} warnings",
        err=True,
    )
    if error_count:
        sys.exit(1)


@cli.command("list-concepts")
@click.option(
    "--config",
    "config_path",
    default=None,
    metavar="PATH",
    help="Path to okf-core.toml (default: search upward from cwd).",
)
@click.option(
    "--bundle",
    "bundle_name",
    default="default",
    show_default=True,
    metavar="NAME",
    help="Named bundle from config.",
)
@click.option(
    "--with-graph-counts",
    is_flag=True,
    help="Include resolved inbound/outbound concept-link counts.",
)
def list_concepts_cmd(
    config_path: str | None,
    bundle_name: str,
    with_graph_counts: bool,
) -> None:
    """List addressable concepts for seed discovery."""
    _, bundle = _load(config_path, bundle_name)
    manifest = scan_bundle(bundle)
    graph = build_bundle_graph(bundle, manifest=manifest) if with_graph_counts else None
    listing = list_concepts(bundle, manifest=manifest, graph=graph)

    result = {
        "bundle": listing.bundle_name,
        "concepts": [_concept_listing_dict(concept) for concept in listing.concepts],
        "problems": [_listing_problem_dict(problem) for problem in listing.problems],
    }
    click.echo(json.dumps(result, cls=_Encoder, indent=2))
    click.echo(
        f"Listed bundle {bundle.name!r}: {len(listing.concepts)} concepts, "
        f"{len(listing.problems)} problems",
        err=True,
    )


@cli.command("graph")
@click.option(
    "--config",
    "config_path",
    default=None,
    metavar="PATH",
    help="Path to okf-core.toml (default: search upward from cwd).",
)
@click.option(
    "--bundle",
    "bundle_name",
    default="default",
    show_default=True,
    metavar="NAME",
    help="Named bundle from config.",
)
@click.option(
    "--concept",
    "concept_id",
    default=None,
    metavar="CONCEPT_ID",
    help="Emit graph details for one concept.",
)
@click.option(
    "--depth",
    "depth",
    default=1,
    show_default=True,
    type=click.IntRange(min=0),
    metavar="N",
    help="Neighborhood depth when --concept is used.",
)
@click.option(
    "--broken",
    "broken_only",
    is_flag=True,
    help="Emit only broken internal concept links.",
)
def graph_cmd(
    config_path: str | None,
    bundle_name: str,
    concept_id: str | None,
    depth: int,
    broken_only: bool,
) -> None:
    """Inspect Markdown links and graph traversal for a bundle."""
    _, bundle = _load(config_path, bundle_name)
    graph = build_bundle_graph(bundle)
    concept_ids = {concept.concept_id for concept in graph.concepts}

    if concept_id is not None and concept_id not in concept_ids:
        click.echo(
            f"Concept {concept_id!r} not found in bundle {bundle.name!r}", err=True
        )
        sys.exit(2)

    if broken_only:
        result: dict[str, Any] = {
            "bundle": bundle.name,
            "broken_links": [_link_dict(link) for link in graph.broken_links],
            "problems": [_graph_problem_dict(problem) for problem in graph.problems],
        }
    elif concept_id is not None:
        result = {
            "bundle": bundle.name,
            "concept_id": concept_id,
            "outbound_links": [
                _link_dict(link) for link in links_from(graph, concept_id)
            ],
            "backlinks": [_link_dict(link) for link in backlinks_to(graph, concept_id)],
            "neighborhood": list(neighborhood(graph, concept_id, depth)),
            "broken_links": [
                _link_dict(link)
                for link in graph.broken_links
                if link.source_concept_id == concept_id
            ],
            "problems": [_graph_problem_dict(problem) for problem in graph.problems],
        }
    else:
        result = {
            "bundle": bundle.name,
            "concepts": [concept.concept_id for concept in graph.concepts],
            "links": [_link_dict(link) for link in graph.links],
            "broken_links": [_link_dict(link) for link in graph.broken_links],
            "problems": [_graph_problem_dict(problem) for problem in graph.problems],
        }

    click.echo(json.dumps(result, cls=_Encoder, indent=2))
    click.echo(
        f"Built graph for bundle {bundle.name!r}: {len(graph.concepts)} concepts, "
        f"{len(graph.links)} links, {len(graph.broken_links)} broken",
        err=True,
    )


@cli.command("index")
@click.option(
    "--config",
    "config_path",
    default=None,
    metavar="PATH",
    help="Path to okf-core.toml (default: search upward from cwd).",
)
@click.option(
    "--bundle",
    "bundle_name",
    default="default",
    show_default=True,
    metavar="NAME",
    help="Named bundle from config.",
)
@click.option(
    "--directory",
    "directory",
    default=None,
    metavar="PATH",
    help="Directory to generate index for (default: bundle root).",
)
def index_cmd(config_path: str | None, bundle_name: str, directory: str | None) -> None:
    """Generate index.md for a bundle directory."""
    _, bundle = _load(config_path, bundle_name)
    target_dir = (
        Path(directory).resolve() if directory is not None else bundle.bundle_root
    )

    try:
        target_dir.relative_to(bundle.bundle_root)
    except ValueError:
        click.echo(
            f"--directory {target_dir} is not under bundle root {bundle.bundle_root}",
            err=True,
        )
        sys.exit(2)

    manifest = scan_bundle(bundle)

    direct_entries = [c for c in manifest.concepts if c.path.parent == target_dir]

    subdirs: set[Path] = set()
    for c in manifest.concepts:
        try:
            rel = c.path.relative_to(target_dir)
            if len(rel.parts) > 1:
                subdirs.add(target_dir / rel.parts[0])
        except ValueError:
            pass

    scan_problems_in_dir = []
    for p in manifest.problems:
        try:
            p.path.relative_to(target_dir)
            scan_problems_in_dir.append(p)
        except ValueError:
            pass

    generated = generate_index(target_dir, direct_entries, sorted(subdirs))

    entries_written = len(direct_entries) - len(generated.problems)

    index_path = target_dir / "index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(generated.body, encoding="utf-8")

    result = {
        "path": str(index_path),
        "entries": entries_written,
        "problems": [
            {"concept_id": p.concept_id, "message": p.message}
            for p in generated.problems
        ],
        "scan_problems": [
            {"path": str(p.path), "kind": p.kind, "message": p.message}
            for p in scan_problems_in_dir
        ],
    }
    click.echo(json.dumps(result, cls=_Encoder, indent=2))
    click.echo(
        f"Wrote index.md for bundle {bundle.name!r}: "
        f"{entries_written} entries, {len(generated.problems)} skipped, "
        f"{len(scan_problems_in_dir)} scan errors",
        err=True,
    )
    if generated.problems or scan_problems_in_dir:
        sys.exit(1)


def _link_dict(link: Any) -> dict[str, Any]:
    return {
        "source_concept_id": link.source_concept_id,
        "source_path": str(link.source_path),
        "text": link.text,
        "target": link.target,
        "target_path": str(link.target_path),
        "target_concept_id": link.target_concept_id,
    }


def _graph_problem_dict(problem: Any) -> dict[str, Any]:
    return {
        "concept_id": problem.concept_id,
        "path": str(problem.path),
        "kind": problem.kind,
        "message": problem.message,
    }


def _concept_listing_dict(concept: Any) -> dict[str, Any]:
    return {
        "concept_id": concept.concept_id,
        "path": str(concept.path),
        "type": concept.type,
        "title": concept.title,
        "description": concept.description,
        "fields": concept.fields,
        "frontmatter": concept.frontmatter,
        "outbound_link_count": concept.outbound_link_count,
        "inbound_link_count": concept.inbound_link_count,
    }


def _listing_problem_dict(problem: Any) -> dict[str, Any]:
    return {
        "concept_id": problem.concept_id,
        "path": str(problem.path),
        "kind": problem.kind,
        "message": problem.message,
    }

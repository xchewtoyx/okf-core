"""Command-line interface for okf-core."""

from __future__ import annotations

import datetime
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

import click

from okf_core import (
    ConfigError,
    __version__,
    backlinks_to,
    build_context_pack,
    build_bundle_graph,
    declared_okf_version,
    generate_index,
    list_concepts,
    links_from,
    load_config,
    neighborhood,
    render_index_document,
    scan_bundle,
    search_concepts,
    SearchConfigError,
    validate_bundle,
    concept_id_to_path,
    ConceptPathError,
    parse_concept_document,
    serialize_concept_document,
)
from okf_core.write_safety import check_bundle_write_safety


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
@click.version_option(version=__version__)
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
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress command output and summary (does not suppress configuration/load errors).",
)
def scan(config_path: str | None, bundle_name: str, quiet: bool) -> None:
    """Scan a bundle and emit a JSON manifest."""
    _, bundle = _load(config_path, bundle_name)
    manifest = scan_bundle(bundle)
    if not quiet:
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
    if quiet and manifest.problems:
        sys.exit(1)


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
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress command output and summary (does not suppress configuration/load errors).",
)
def validate(config_path: str | None, bundle_name: str, quiet: bool) -> None:
    """Validate a bundle.

    Emits findings as JSON unless quiet is True.
    """
    cfg, bundle = _load(config_path, bundle_name)
    findings = validate_bundle(bundle, cfg)
    error_count = 0
    if quiet:
        for path_findings in findings.values():
            for f in path_findings:
                if f.severity == "error":
                    error_count += 1
    else:
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
@click.option(
    "--with-content",
    is_flag=True,
    help="Include raw Markdown body of valid concepts (with YAML frontmatter stripped).",
)
def list_concepts_cmd(
    config_path: str | None,
    bundle_name: str,
    with_graph_counts: bool,
    with_content: bool,
) -> None:
    """List addressable concepts for seed discovery."""
    _, bundle = _load(config_path, bundle_name)
    manifest = scan_bundle(bundle)
    graph = build_bundle_graph(bundle, manifest=manifest) if with_graph_counts else None
    listing = list_concepts(
        bundle, manifest=manifest, graph=graph, with_content=with_content
    )

    result = {
        "bundle": listing.bundle_name,
        "concepts": [_concept_listing_dict(concept) for concept in listing.concepts],
        "problems": [_listing_problem_dict(problem) for problem in listing.problems],
        "orphans": list(listing.orphans),
    }
    click.echo(json.dumps(result, cls=_Encoder, indent=2))
    orphan_info = f", {len(listing.orphans)} orphans" if with_graph_counts else ""
    click.echo(
        f"Listed bundle {bundle.name!r}: {len(listing.concepts)} concepts, "
        f"{len(listing.problems)} problems{orphan_info}",
        err=True,
    )


@cli.command("search")
@click.argument("query")
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
    "--limit",
    default=10,
    show_default=True,
    type=click.IntRange(min=0),
    metavar="N",
    help="Maximum number of results to return.",
)
@click.option(
    "--no-refresh",
    is_flag=True,
    help="Search the current FTS index without scanning and refreshing first.",
)
def search_cmd(
    query: str,
    config_path: str | None,
    bundle_name: str,
    limit: int,
    no_refresh: bool,
) -> None:
    """Search indexed bundle concepts with SQLite FTS5."""
    _, bundle = _load(config_path, bundle_name)
    try:
        search_results = search_concepts(
            bundle,
            query,
            limit=limit,
            refresh=not no_refresh,
        )
    except SearchConfigError as exc:
        click.echo(f"Search configuration error: {exc}", err=True)
        sys.exit(2)

    result = {
        "bundle": search_results.bundle_name,
        "query": search_results.query,
        "results": [
            _search_result_dict(search_result)
            for search_result in search_results.results
        ],
        "problems": [
            _listing_problem_dict(problem) for problem in search_results.problems
        ],
    }
    click.echo(json.dumps(result, cls=_Encoder, indent=2))
    click.echo(
        f"Searched bundle {bundle.name!r}: {len(search_results.results)} results, "
        f"{len(search_results.problems)} problems",
        err=True,
    )


@cli.command("context")
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
    "--seed",
    "seed_concept_ids",
    multiple=True,
    required=True,
    metavar="CONCEPT_ID",
    help="Seed concept ID. Repeat for multiple seeds.",
)
@click.option(
    "--depth",
    default=1,
    show_default=True,
    type=click.IntRange(min=0),
    metavar="N",
    help="Graph expansion depth.",
)
@click.option(
    "--direction",
    default="both",
    show_default=True,
    type=click.Choice(["outbound", "inbound", "both"]),
    help="Graph edge direction to follow.",
)
@click.option(
    "--budget-chars",
    default=None,
    type=click.IntRange(min=0),
    metavar="N",
    help="Approximate character budget for included content.",
)
def context_cmd(
    config_path: str | None,
    bundle_name: str,
    seed_concept_ids: tuple[str, ...],
    depth: int,
    direction: str,
    budget_chars: int | None,
) -> None:
    """Build a deterministic context pack from seed concept IDs."""
    _, bundle = _load(config_path, bundle_name)
    pack = build_context_pack(
        bundle,
        seed_concept_ids,
        depth=depth,
        direction=cast(Literal["outbound", "inbound", "both"], direction),
        budget_chars=budget_chars,
    )

    result = {
        "bundle": pack.bundle_name,
        "seeds": list(pack.seeds),
        "entries": [_context_entry_dict(entry) for entry in pack.entries],
        "omitted_concept_ids": list(pack.omitted_concept_ids),
        "problems": [_context_problem_dict(problem) for problem in pack.problems],
    }
    click.echo(json.dumps(result, cls=_Encoder, indent=2))
    click.echo(
        f"Built context pack for bundle {bundle.name!r}: "
        f"{len(pack.entries)} entries, {len(pack.omitted_concept_ids)} omitted, "
        f"{len(pack.problems)} problems",
        err=True,
    )
    if pack.problems:
        sys.exit(1)


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


@cli.command("stable-id")
@click.argument("concept_id", required=False, default=None)
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
    "--force",
    is_flag=True,
    help="Generate a new stable ID even if one already exists.",
)
@click.option(
    "--write",
    is_flag=True,
    help="Write the generated stable ID back to the document frontmatter.",
)
def stable_id_cmd(
    concept_id: str | None,
    config_path: str | None,
    bundle_name: str,
    force: bool,
    write: bool,
) -> None:
    """Retrieve, generate, or write a stable ID for a concept."""
    import uuid

    if concept_id is None:
        if write:
            raise click.UsageError("Cannot specify --write without a CONCEPT_ID")
        if force:
            raise click.UsageError("Cannot specify --force without a CONCEPT_ID")
        click.echo(str(uuid.uuid4()))
        return

    _, bundle = _load(config_path, bundle_name)

    if bundle.stable_id_field is None:
        click.echo(
            f"stable_id_field is not configured for bundle {bundle.name!r}",
            err=True,
        )
        sys.exit(2)

    try:
        path = concept_id_to_path(concept_id, bundle)
    except ConceptPathError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)

    if not path.is_file():
        click.echo(f"Concept file not found: {path}", err=True)
        sys.exit(1)

    try:
        content = path.read_bytes().decode("utf-8")
        document = parse_concept_document(content)
    except Exception as exc:
        click.echo(f"Error reading/parsing concept document: {exc}", err=True)
        sys.exit(1)

    existing_id = document.frontmatter.get(bundle.stable_id_field)
    has_valid_id = existing_id is not None and not (
        isinstance(existing_id, str) and not existing_id.strip()
    )

    if has_valid_id and not force:
        click.echo(str(existing_id).strip())
        return

    new_id = str(uuid.uuid4())

    if write:
        write_safety_problem = check_bundle_write_safety(bundle)
        if write_safety_problem is not None:
            click.echo(write_safety_problem.message, err=True)
            sys.exit(1)

        document.frontmatter[bundle.stable_id_field] = new_id
        try:
            serialized = serialize_concept_document(document)
            path.write_text(serialized, encoding="utf-8", newline="\n")
        except Exception as exc:
            click.echo(f"Error writing concept document: {exc}", err=True)
            sys.exit(1)
        click.echo(new_id)
        click.echo(f"Wrote stable ID {new_id} to {path}", err=True)
    else:
        click.echo(new_id)


@cli.command("list-bundles")
@click.option(
    "--config",
    "config_path",
    default=None,
    metavar="PATH",
    help="Path to okf-core.toml (default: search upward from cwd).",
)
def list_bundles_cmd(config_path: str | None) -> None:
    """List all configured bundles."""
    try:
        cfg = load_config(config_path=config_path)
    except ConfigError as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(2)

    bundles = [
        {
            "name": bundle.name,
            "bundle_root": str(bundle.bundle_root),
            "profile": bundle.profile,
            "okf_version": bundle.okf_version,
        }
        for bundle in sorted(cfg.bundles.values(), key=lambda b: b.name)
    ]
    result: dict[str, Any] = {
        "config_path": (str(cfg.config_path) if cfg.config_path is not None else None),
        "bundles": bundles,
    }
    click.echo(json.dumps(result, cls=_Encoder, indent=2))
    click.echo(f"Found {len(bundles)} bundle(s)", err=True)


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
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Overwrite root index.md without preserving an existing supported "
        "okf_version declaration when config omits okf_version."
    ),
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress command output and summary (does not suppress configuration/load errors).",
)
def index_cmd(
    config_path: str | None,
    bundle_name: str,
    directory: str | None,
    force: bool,
    quiet: bool,
) -> None:
    """Generate index.md for a bundle directory."""
    config, bundle = _load(config_path, bundle_name)
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

    write_safety_problem = check_bundle_write_safety(bundle)
    if write_safety_problem is not None:
        index_path = target_dir / "index.md"
        if not quiet:
            result = {
                "path": str(index_path),
                "entries": 0,
                "problems": [
                    {"concept_id": "", "message": write_safety_problem.message}
                ],
                "scan_problems": [],
            }
            click.echo(json.dumps(result, cls=_Encoder, indent=2))
            click.echo(write_safety_problem.message, err=True)
        sys.exit(1)

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

    profile_cfg = (
        config.profiles.get(bundle.profile) if bundle.profile is not None else None
    )
    project_taxonomy = config.taxonomy

    generated = generate_index(
        target_dir,
        direct_entries,
        sorted(subdirs),
        directory_metadata_file=bundle.directory_metadata_file,
        profile=profile_cfg,
        project_taxonomy=project_taxonomy,
    )

    skipped_entries = sum(1 for p in generated.problems if p.concept_id)
    entries_written = len(direct_entries) - skipped_entries

    index_path = target_dir / "index.md"
    body = render_index_document(
        generated.body,
        okf_version=_okf_version_for_index_write(bundle, target_dir, force),
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(body, encoding="utf-8", newline="\n")

    if not quiet:
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
            f"{entries_written} entries, {len(generated.problems)} problems, "
            f"{len(scan_problems_in_dir)} scan errors",
            err=True,
        )
    if generated.problems or scan_problems_in_dir:
        sys.exit(1)


def _okf_version_for_index_write(
    bundle: Any, target_dir: Path, force: bool
) -> str | None:
    if target_dir != bundle.bundle_root:
        return None
    if bundle.okf_version is not None:
        return bundle.okf_version
    if force:
        return None

    index_path = bundle.bundle_root / "index.md"
    if not index_path.is_file():
        return None
    return declared_okf_version(index_path.read_text(encoding="utf-8"))


def _link_dict(link: Any) -> dict[str, Any]:
    return {
        "source_concept_id": link.source_concept_id,
        "source_path": str(link.source_path),
        "text": link.text,
        "target": link.target,
        "title": link.title,
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
        "pagerank": concept.pagerank,
        "content": concept.content,
    }


def _listing_problem_dict(problem: Any) -> dict[str, Any]:
    return {
        "concept_id": problem.concept_id,
        "path": str(problem.path),
        "kind": problem.kind,
        "message": problem.message,
    }


def _search_result_dict(result: Any) -> dict[str, Any]:
    return {
        "concept_id": result.concept_id,
        "path": str(result.path),
        "title": result.title,
        "description": result.description,
        "score": result.score,
        "snippets": list(result.snippets),
    }


def _context_entry_dict(entry: Any) -> dict[str, Any]:
    return {
        "concept_id": entry.concept_id,
        "path": str(entry.path),
        "title": entry.title,
        "selection_reason": entry.selection_reason,
        "graph_distance": entry.graph_distance,
        "char_count": entry.char_count,
        "content": entry.content,
    }


def _context_problem_dict(problem: Any) -> dict[str, Any]:
    return {
        "kind": problem.kind,
        "message": problem.message,
        "concept_id": problem.concept_id,
        "path": str(problem.path) if problem.path is not None else None,
    }

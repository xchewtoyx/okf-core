"""Command-line interface for okf-core."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import click

from okf_core import (
    ConfigError,
    generate_index,
    load_config,
    scan_bundle,
    validate_bundle,
)


def _to_serializable(obj: Any) -> Any:
    """Recursively convert frozen manifest structures to JSON-serializable types."""
    if isinstance(obj, Mapping):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(v) for v in obj]
    return obj


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
                "frontmatter": _to_serializable(c.frontmatter),
            }
            for c in manifest.concepts
        ],
        "problems": [
            {"path": str(p.path), "message": p.message}
            for p in manifest.problems
        ],
    }
    click.echo(json.dumps(result, indent=2))
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
    click.echo(json.dumps(result, indent=2))
    click.echo(
        f"Validated bundle {bundle.name!r}: {error_count} errors, {warning_count} warnings",
        err=True,
    )
    if error_count:
        sys.exit(1)


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
def index_cmd(
    config_path: str | None, bundle_name: str, directory: str | None
) -> None:
    """Generate index.md for a bundle directory."""
    _, bundle = _load(config_path, bundle_name)
    target_dir = Path(directory).resolve() if directory is not None else bundle.bundle_root

    try:
        target_dir.relative_to(bundle.bundle_root)
    except ValueError:
        click.echo(
            f"--directory {target_dir} is not under bundle root {bundle.bundle_root}",
            err=True,
        )
        sys.exit(2)

    manifest = scan_bundle(bundle)

    direct_entries = [
        c for c in manifest.concepts if c.path.parent == target_dir
    ]

    subdirs: set[Path] = set()
    for c in manifest.concepts:
        try:
            rel = c.path.relative_to(target_dir)
            if len(rel.parts) > 1:
                subdirs.add(target_dir / rel.parts[0])
        except ValueError:
            pass

    generated = generate_index(target_dir, direct_entries, sorted(subdirs))

    index_path = target_dir / "index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(generated.body, encoding="utf-8")

    result = {
        "path": str(index_path),
        "entries": len(direct_entries),
        "problems": [
            {"concept_id": p.concept_id, "message": p.message}
            for p in generated.problems
        ],
    }
    click.echo(json.dumps(result, indent=2))
    click.echo(
        f"Wrote index.md for bundle {bundle.name!r}: "
        f"{len(direct_entries)} entries, {len(generated.problems)} skipped",
        err=True,
    )
    if generated.problems:
        sys.exit(1)

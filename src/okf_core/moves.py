"""Concept-aware relocation: move a bundle file while keeping inbound links intact."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from okf_core.config import BundleConfig
from okf_core.graph import backlinks_to, build_bundle_graph
from okf_core.manifest import scan_bundle
from okf_core.patching import (
    DocumentChangePlan,
    FileMovePlan,
    LinkRewrite,
    apply_document_change,
    apply_file_move,
    plan_file_move,
    plan_markdown_link_rewrite,
)
from okf_core.paths import path_to_concept_id


@dataclass(frozen=True)
class MovePreparation:
    """Read-only outputs of preparing a concept move; never applies anything."""

    file_move_plan: FileMovePlan
    link_rewrite_plans: Mapping[Path, DocumentChangePlan]


@dataclass(frozen=True)
class MoveResult:
    """The result of moving a concept and rewriting its inbound links."""

    source_path: Path
    dest_path: Path
    moved: bool
    updated_files: tuple[Path, ...]


def plan_move_concept(
    bundle: BundleConfig,
    source: Path | str,
    dest: Path | str,
) -> MovePreparation:
    """Read-only planning for a concept move.

    Validates source/dest, computes a file move plan, and (if the move is
    not a no-op) a link-rewrite plan for every other file that currently
    links to the concept at ``source``, against CURRENT file contents. Never
    writes anything -- safe to call repeatedly, e.g. for a dry-run preview.
    """

    bundle_root = bundle.bundle_root.resolve(strict=False)
    resolved_source = _resolve_bundle_path(bundle_root, source)
    resolved_dest = _resolve_bundle_path(bundle_root, dest)

    source_concept_id = path_to_concept_id(resolved_source, bundle)
    path_to_concept_id(resolved_dest, bundle)

    file_move_plan = plan_file_move(bundle, resolved_source, resolved_dest)

    link_rewrite_plans: dict[Path, DocumentChangePlan] = {}
    if not file_move_plan.noop:
        manifest = scan_bundle(bundle)
        graph = build_bundle_graph(bundle, manifest=manifest)

        rewrites_by_file: dict[Path, dict[str, LinkRewrite]] = {}
        for link in backlinks_to(graph, source_concept_id):
            new_target = _link_target_for_new_location(
                bundle,
                original_target=link.target,
                source_path=link.source_path,
                new_target_path=file_move_plan.dest_path,
            )
            rewrites_by_file.setdefault(link.source_path, {})[link.target] = (
                LinkRewrite(
                    old_target=link.target,
                    new_target=new_target,
                )
            )

        for path, rewrites in rewrites_by_file.items():
            link_rewrite_plans[path] = plan_markdown_link_rewrite(
                bundle, path, tuple(rewrites.values())
            )

    return MovePreparation(
        file_move_plan=file_move_plan, link_rewrite_plans=link_rewrite_plans
    )


def move_concept(
    bundle: BundleConfig,
    source: Path | str,
    dest: Path | str,
) -> MoveResult:
    """Relocate a concept file, rewriting every other file's inbound Markdown links.

    Fails fast: not a multi-file transaction, since none exists in this
    codebase. Every applied step is individually idempotent, so re-invoking
    move_concept with the same (source, dest) after a failure resumes and
    completes the operation: already-rewritten files have no remaining
    matches for the old link target (a no-op plan), not-yet-rewritten ones
    still match, and the move itself is attempted last, so a failure always
    leaves the concept file at a well-defined location -- either its
    original path, or (once every rewrite has succeeded) its new one.
    """

    prep = plan_move_concept(bundle, source, dest)
    if prep.file_move_plan.noop:
        return MoveResult(
            source_path=prep.file_move_plan.source_path,
            dest_path=prep.file_move_plan.dest_path,
            moved=False,
            updated_files=(),
        )

    updated: list[Path] = []
    for path, plan in prep.link_rewrite_plans.items():
        result = apply_document_change(bundle, plan)
        if result.changed:
            updated.append(path)

    # Re-derive the move plan now, after link rewrites (which may have
    # touched the source file itself via a self-referential link), so
    # source_sha256 reflects the file's true current content.
    final_plan = plan_file_move(
        bundle, prep.file_move_plan.source_path, prep.file_move_plan.dest_path
    )
    apply_file_move(bundle, final_plan)

    return MoveResult(
        source_path=prep.file_move_plan.source_path,
        dest_path=prep.file_move_plan.dest_path,
        moved=True,
        updated_files=tuple(sorted(updated)),
    )


def _resolve_bundle_path(bundle_root: Path, raw: Path | str) -> Path:
    """Resolve a SOURCE/DEST argument the same way patching.py's primitives do.

    A relative argument is joined to bundle_root; an absolute argument is
    used literally. Both are then fully resolved (symlinks, ``..`` segments).
    """

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = bundle_root / candidate
    return candidate.resolve(strict=False)


def _link_target_for_new_location(
    bundle: BundleConfig,
    *,
    original_target: str,
    source_path: Path,
    new_target_path: Path,
) -> str:
    """Recompute a Markdown link href after its target concept file has moved.

    Preserves the '#fragment'/'?query' suffix and the absolute-vs-relative
    style of original_target exactly (a target already written in
    bundle-root-anchored '/...' form stays in that form; anything else is
    rewritten as a path relative to source_path's own directory, using POSIX
    '/' separators and '../' as needed for sibling/cousin directories). Only
    the path portion is recalculated.
    """

    parsed = urlsplit(original_target)
    bundle_root = bundle.bundle_root.resolve(strict=False)

    if parsed.path.startswith("/"):
        new_path = "/" + new_target_path.relative_to(bundle_root).as_posix()
    else:
        relative = os.path.relpath(new_target_path, source_path.parent)
        new_path = PurePosixPath(relative.replace("\\", "/")).as_posix()

    return urlunsplit(("", "", new_path, parsed.query, parsed.fragment))

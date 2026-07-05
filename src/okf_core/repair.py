"""Self-healing link repair: rewrite broken links whose target concept moved."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from okf_core.config import BundleConfig
from okf_core.graph import SCAN_FAILURE_KINDS, ConceptLink, build_bundle_graph
from okf_core.manifest import scan_bundle
from okf_core.patching import (
    DocumentChangePlan,
    DocumentChangePlanningError,
    LinkRewrite,
    apply_document_change,
    link_target_for_new_location,
    plan_markdown_link_rewrite,
)
from okf_core.paths import ConceptPathError, path_to_concept_id


@dataclass(frozen=True)
class UnresolvedBrokenLink:
    """A broken link the repair hook could not (or was not asked to) resolve."""

    link: ConceptLink
    reason: str


@dataclass(frozen=True)
class RepairPreparation:
    """Read-only outputs of preparing a graph repair; never applies anything."""

    link_rewrite_plans: Mapping[Path, DocumentChangePlan]
    resolved_links: tuple[ConceptLink, ...]
    unresolved_links: tuple[UnresolvedBrokenLink, ...]


@dataclass(frozen=True)
class RepairResult:
    """The result of applying a graph repair."""

    updated_files: tuple[Path, ...]
    resolved_links: tuple[ConceptLink, ...]
    unresolved_links: tuple[UnresolvedBrokenLink, ...]


def _link_sort_key(link: ConceptLink) -> tuple[str, str]:
    return (str(link.source_path), link.target)


def _validate_hook_path(
    bundle: BundleConfig,
    raw_path: Path | None,
    invalid_reason_by_dead_id: dict[str, str],
    dead_id: str,
) -> Path | None:
    """Normalize and validate a hook-returned Path, or None if unusable.

    A plugin's answer is untrusted input: it may return a path relative to
    the current working directory rather than bundle_root (the natural
    frame of reference for a concept's location), a path that escapes
    bundle_root entirely, or a path to a concept that (per a stale cache or
    other plugin bug) doesn't actually exist on disk. Resolving relative to
    bundle_root, validating via path_to_concept_id (the same
    containment/shape check graph.py already applies to on-disk link
    targets), and confirming the file exists catches all three, recording a
    reason so the bad answer downgrades to unresolved instead of silently
    producing a wrong -- or newly broken -- href.
    """

    if raw_path is None:
        return None
    candidate = raw_path if raw_path.is_absolute() else bundle.bundle_root / raw_path
    candidate = candidate.resolve(strict=False)
    try:
        path_to_concept_id(candidate, bundle)
    except ConceptPathError as exc:
        invalid_reason_by_dead_id[dead_id] = f"invalid-resolved-path: {exc}"
        return None
    if not candidate.is_file():
        invalid_reason_by_dead_id[dead_id] = (
            f"invalid-resolved-path: resolved concept file does not exist: {candidate}"
        )
        return None
    return candidate


def plan_graph_repair(bundle: BundleConfig) -> RepairPreparation:
    """Read-only planning for a graph repair.

    Builds the bundle graph and, for every broken link, asks the
    ``okf_fetch_moved_concept_path`` hook (once per distinct dead concept ID)
    whether its former target moved. A link is left unresolved -- not an
    error -- for any of these reasons: it has no concept-id-shaped target
    (``"not-concept-shaped"``); no plugin implements the hook at all
    (``"no-plugin-registered"``); a plugin is registered but returned None
    for this dead concept ID (``"unresolved"``); the plugin's resolved path
    escapes bundle_root, isn't a Markdown file, doesn't exist on disk, or
    (for a bundle-root-anchored link specifically) can't be expressed in
    that style (``"invalid-resolved-path: ..."``); or planning the
    containing file's rewrite raises DocumentChangePlanningError, e.g. an unrelated
    reference-style link elsewhere in that file (``"planning-failed: ..."``,
    downgrading only that file's links -- other files' rewrites still
    proceed). Never writes anything -- safe to call repeatedly, e.g. for a
    dry-run preview.
    """

    from okf_core.hooks import get_hook_manager

    manifest = scan_bundle(bundle)
    graph = build_bundle_graph(bundle, manifest=manifest)

    scan_failures = [
        problem for problem in graph.problems if problem.kind in SCAN_FAILURE_KINDS
    ]
    if scan_failures:
        # A file that failed to scan or parse contributes no links to the
        # graph, so graph.broken_links cannot be trusted as a complete view
        # of every broken link in the bundle -- proceeding could silently
        # miss (or never even see) a link that needs repairing.
        problem_paths = ", ".join(
            str(problem.path)
            for problem in sorted(scan_failures, key=lambda p: str(p.path))
        )
        raise DocumentChangePlanningError(
            scan_failures[0].path,
            f"Cannot reliably find every broken link in the bundle: "
            f"{len(scan_failures)} file(s) failed to scan and may hide "
            f"broken links: {problem_paths}",
        )

    pm = get_hook_manager(bundle)
    hook_has_impls = bool(pm.hook.okf_fetch_moved_concept_path.get_hookimpls())
    resolved_paths_by_dead_id: dict[str, Path | None] = {}
    invalid_reason_by_dead_id: dict[str, str] = {}
    rewrites_by_file: dict[Path, dict[str, LinkRewrite]] = {}
    # Keyed by (path, href) like rewrites_by_file, but a list: a single href
    # can occur more than once in one file (e.g. two separate [text](dead.md)
    # occurrences), and every occurrence must still show up in
    # resolved_links/unresolved_links even though they share one LinkRewrite.
    links_by_file: dict[Path, dict[str, list[ConceptLink]]] = {}
    unresolved: list[UnresolvedBrokenLink] = []

    for link in graph.broken_links:
        dead_id = link.target_concept_id
        if dead_id is None:
            unresolved.append(UnresolvedBrokenLink(link, "not-concept-shaped"))
            continue

        if dead_id not in resolved_paths_by_dead_id:
            raw_path = pm.hook.okf_fetch_moved_concept_path(
                dead_concept_id=dead_id, bundle=bundle
            )
            resolved_paths_by_dead_id[dead_id] = _validate_hook_path(
                bundle, raw_path, invalid_reason_by_dead_id, dead_id
            )
        new_path = resolved_paths_by_dead_id[dead_id]

        if new_path is None:
            if dead_id in invalid_reason_by_dead_id:
                reason = invalid_reason_by_dead_id[dead_id]
            elif hook_has_impls:
                reason = "unresolved"
            else:
                reason = "no-plugin-registered"
            unresolved.append(UnresolvedBrokenLink(link, reason))
            continue

        try:
            new_target = link_target_for_new_location(
                bundle,
                original_target=link.target,
                source_path=link.source_path,
                new_target_path=new_path,
            )
        except ValueError as exc:
            # A plugin can return any Path it likes; a bundle-root-anchored
            # link whose resolved path falls outside bundle_root can't be
            # expressed in that style. Don't let one plugin's bad answer
            # abort every other link's repair.
            unresolved.append(
                UnresolvedBrokenLink(link, f"invalid-resolved-path: {exc}")
            )
            continue
        rewrites_by_file.setdefault(link.source_path, {})[link.target] = LinkRewrite(
            old_target=link.target, new_target=new_target
        )
        links_by_file.setdefault(link.source_path, {}).setdefault(
            link.target, []
        ).append(link)

    link_rewrite_plans: dict[Path, DocumentChangePlan] = {}
    resolved: list[ConceptLink] = []
    for path, rewrites in rewrites_by_file.items():
        try:
            link_rewrite_plans[path] = plan_markdown_link_rewrite(
                bundle, path, tuple(rewrites.values())
            )
        except DocumentChangePlanningError as exc:
            # Per-file catch-and-continue: a planning failure in one file
            # (e.g. an unrelated reference-style link definition elsewhere
            # in it) must not abort repairs to every other file.
            for referring_links in links_by_file[path].values():
                for referring_link in referring_links:
                    unresolved.append(
                        UnresolvedBrokenLink(referring_link, f"planning-failed: {exc}")
                    )
            continue
        for referring_links in links_by_file[path].values():
            resolved.extend(referring_links)

    return RepairPreparation(
        link_rewrite_plans=link_rewrite_plans,
        resolved_links=tuple(sorted(resolved, key=_link_sort_key)),
        unresolved_links=tuple(
            sorted(unresolved, key=lambda u: _link_sort_key(u.link))
        ),
    )


def repair_graph(bundle: BundleConfig) -> RepairResult:
    """Apply a graph repair: rewrite every broken link the hook could resolve.

    Plans the repair, then applies each planned file's link-rewrite plan.
    Idempotent: an already-repaired file's plan is a no-op, so re-running
    after a partial failure only re-touches files that still need it.
    """

    prep = plan_graph_repair(bundle)
    updated: list[Path] = []
    for path, plan in prep.link_rewrite_plans.items():
        result = apply_document_change(bundle, plan)
        if result.changed:
            updated.append(path)

    return RepairResult(
        updated_files=tuple(sorted(updated)),
        resolved_links=prep.resolved_links,
        unresolved_links=prep.unresolved_links,
    )

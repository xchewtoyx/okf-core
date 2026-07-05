"""Concept-aware relocation: move a bundle file while keeping inbound links intact."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit, urlunsplit

from okf_core.config import BundleConfig
from okf_core.graph import backlinks_to, build_bundle_graph, links_from
from okf_core.index import (
    entries_for_directory,
    generate_index,
    okf_version_for_index_write,
    render_index_document,
)
from okf_core.manifest import scan_bundle
from okf_core.patching import (
    DocumentChangePlan,
    DocumentChangePlanningError,
    FileMovePlan,
    LinkRewrite,
    apply_document_change,
    apply_file_move,
    plan_file_move,
    plan_markdown_link_rewrite,
)
from okf_core.paths import path_to_concept_id

# Manifest/graph problem kinds that mean a file's links could not be
# extracted at all -- as opposed to e.g. "stable-id-missing", a validation
# annotation on an entry whose body (and links) were still fully parsed.
_SCAN_FAILURE_KINDS = frozenset(
    {"path-error", "read-error", "decode-error", "parse-error"}
)


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
    regenerated_indexes: tuple[Path, ...]


def plan_move_concept(
    bundle: BundleConfig,
    source: Path | str,
    dest: Path | str,
) -> MovePreparation:
    """Read-only planning for a concept move.

    Validates source/dest, computes a file move plan, and (if the move is
    not a no-op) a link-rewrite plan for every file with a link that needs
    updating, against CURRENT file contents: every other file that currently
    links to the concept at ``source``, plus the moved file itself if it
    contains a self-link (rebased to its new location) or a relative
    outbound link to another concept (rebased if the move changes
    directory). Never writes anything -- safe to call repeatedly, e.g. for a
    dry-run preview.
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

        scan_failures = [
            problem for problem in graph.problems if problem.kind in _SCAN_FAILURE_KINDS
        ]
        if scan_failures:
            # A file that failed to scan or parse contributes no links to
            # the graph, so backlinks_to() cannot be trusted as a complete
            # set of referring files -- proceeding could silently leave a
            # stale link in a file we never got to look at. Other manifest
            # problems (e.g. a missing/duplicate stable ID) are validation
            # annotations on an otherwise fully link-extracted entry, and
            # don't affect backlink completeness, so they don't block a move.
            problem_paths = ", ".join(
                str(problem.path)
                for problem in sorted(scan_failures, key=lambda p: str(p.path))
            )
            raise DocumentChangePlanningError(
                scan_failures[0].path,
                f"Cannot reliably find every file referencing this concept: "
                f"{len(scan_failures)} file(s) failed to scan and may "
                f"contain undetected links: {problem_paths}",
            )

        rewrites_by_file: dict[Path, dict[str, LinkRewrite]] = {}
        for link in backlinks_to(graph, source_concept_id):
            # A self-referential link's relative text must be rebased from
            # the concept's NEW directory, not its old one: the file itself
            # is moving, so a link written inside it is interpreted relative
            # to wherever it ends up, not where it used to be.
            reference_dir = (
                file_move_plan.dest_path
                if link.source_path == file_move_plan.source_path
                else link.source_path
            )
            new_target = _link_target_for_new_location(
                bundle,
                original_target=link.target,
                source_path=reference_dir,
                new_target_path=file_move_plan.dest_path,
            )
            rewrites_by_file.setdefault(link.source_path, {})[link.target] = (
                LinkRewrite(
                    old_target=link.target,
                    new_target=new_target,
                )
            )

        # The moved file's own outbound links to OTHER concepts must also be
        # rebased when the move changes directory, since a relative href is
        # resolved from the file's own directory. Self-links are excluded
        # here (target_concept_id == source_concept_id): the loop above
        # already rewrites them using file_move_plan.dest_path as the new
        # TARGET location, whereas link.target_path for a self-link is the
        # file's OLD on-disk path, not where it is moving to.
        for link in links_from(graph, source_concept_id):
            if link.target_concept_id == source_concept_id:
                continue
            new_target = _link_target_for_new_location(
                bundle,
                original_target=link.target,
                source_path=file_move_plan.dest_path,
                new_target_path=link.target_path,
            )
            rewrites_by_file.setdefault(file_move_plan.source_path, {})[link.target] = (
                LinkRewrite(old_target=link.target, new_target=new_target)
            )

        failures: list[tuple[Path, DocumentChangePlanningError]] = []
        for path, rewrites in rewrites_by_file.items():
            try:
                link_rewrite_plans[path] = plan_markdown_link_rewrite(
                    bundle, path, tuple(rewrites.values())
                )
            except DocumentChangePlanningError as exc:
                failures.append((path, exc))

        if failures:
            details = "; ".join(
                f"{path}: {exc}"
                for path, exc in sorted(failures, key=lambda item: item[0])
            )
            raise DocumentChangePlanningError(
                failures[0][0],
                f"Cannot plan link rewrites for {len(failures)} referring "
                f"file(s): {details}",
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
            regenerated_indexes=(),
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

    regenerated = _regenerate_affected_indexes(
        bundle, {final_plan.source_path.parent, final_plan.dest_path.parent}
    )

    return MoveResult(
        source_path=prep.file_move_plan.source_path,
        dest_path=prep.file_move_plan.dest_path,
        moved=True,
        updated_files=tuple(sorted(updated)),
        regenerated_indexes=regenerated,
    )


def _regenerate_affected_indexes(
    bundle: BundleConfig, directories: set[Path]
) -> tuple[Path, ...]:
    """Refresh an existing index.md in each of the given directories.

    Only refreshes a directory that already has an index.md -- never creates
    one, since a move should not silently opt a directory into index
    generation it never had. Taxonomy/profile validation is not applied here
    (that's a separate ``okf validate``/``okf index --profile`` concern,
    orthogonal to keeping an existing index's concept listing in sync with a
    move); this only affects which non-fatal IndexProblem entries would be
    reported, not what gets written.
    """

    existing = sorted(d for d in directories if (d / "index.md").is_file())
    if not existing:
        return ()

    manifest = scan_bundle(bundle)
    regenerated = []
    for directory in existing:
        direct_entries, subdirs = entries_for_directory(directory, manifest)
        generated = generate_index(
            directory,
            direct_entries,
            subdirs,
            directory_metadata_file=bundle.directory_metadata_file,
        )
        body = render_index_document(
            generated.body,
            okf_version=okf_version_for_index_write(bundle, directory, force=False),
        )
        index_path = directory / "index.md"
        index_path.write_text(body, encoding="utf-8", newline="\n")
        regenerated.append(index_path)

    return tuple(regenerated)


def _resolve_bundle_path(bundle_root: Path, raw: Path | str) -> Path:
    """Join a SOURCE/DEST argument to bundle_root without following symlinks.

    A relative argument is joined to bundle_root; an absolute argument is
    used literally. Deliberately does not call ``.resolve()``: doing so here
    would silently follow a symlink in the argument itself before
    plan_file_move's own symlink checks ever see it, defeating them.
    path_to_concept_id also resolves its argument internally, but only to
    validate path shape and bundle-root containment -- the path it computes
    is discarded, never fed into a file operation, so its resolution has no
    bearing on this function's symlink-safety contract. plan_file_move (via
    ``_resolve_existing_target``) is what actually rejects a symlinked
    argument, and it runs after both path_to_concept_id calls in
    plan_move_concept.
    """

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = bundle_root / candidate
    return candidate


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
    the path portion is recalculated. The recomputed path is percent-encoded
    (leaving '/' as a literal separator) so a target containing a space or
    other special character comes out in the same normalized form markdown-it
    already emits for such hrefs, rather than an unencoded path that CommonMark
    would require wrapping in '<...>'.
    """

    parsed = urlsplit(original_target)
    bundle_root = bundle.bundle_root.resolve(strict=False)

    if parsed.path.startswith("/"):
        new_path = "/" + new_target_path.relative_to(bundle_root).as_posix()
    else:
        relative = os.path.relpath(new_target_path, source_path.parent)
        new_path = PurePosixPath(relative.replace("\\", "/")).as_posix()

    return urlunsplit(
        ("", "", quote(new_path, safe="/"), parsed.query, parsed.fragment)
    )

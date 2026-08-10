"""Content-agnostic plan/apply safety envelope for bundle writes.

This module owns the optimistic-concurrency primitives shared by every
format-specific writer in this package: hashing a target's current content
into an inspectable plan, then re-validating that hash (or the target's
continued absence) immediately before installing the change. Document-change
primitives (`plan_document_change`, `apply_document_change`, ...) and
file-move primitives (`plan_file_move`, `apply_file_move`) live together here
because `moves.py` already consumes both as a single unit and because they
share the same low-level guards (bundle-root containment, symlink rejection,
concurrent-mutation detection). Format-specific planning (Markdown section
patches, link rewrites, frontmatter merges) stays in `patching.py`, layered
on top of the primitives re-exported from here.
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from okf_core.config import BundleConfig
from okf_core.write_safety import check_bundle_write_safety


class DocumentChangeError(Exception):
    """Base exception for document change planning and application failures."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


class DocumentChangePlanningError(DocumentChangeError):
    """Raised when a document change cannot be planned safely."""


class DocumentChangeSafetyError(DocumentChangeError):
    """Raised when metadata at ``path`` makes bundle writes unsafe."""


class DocumentChangeConflictError(DocumentChangeError):
    """Raised when a target no longer matches the content used by a plan."""

    def __init__(
        self,
        path: Path,
        expected_sha256: str,
        actual_sha256: str | None,
    ) -> None:
        actual = actual_sha256 if actual_sha256 is not None else "<unavailable>"
        super().__init__(
            path,
            (
                f"Document changed after planning: {path} "
                f"(expected SHA-256 {expected_sha256}, got {actual})"
            ),
        )
        self.expected_sha256 = expected_sha256
        self.actual_sha256 = actual_sha256


class DocumentChangeApplyError(DocumentChangeError):
    """Raised when a validated document change cannot be written."""


class FileMoveConflictError(DocumentChangeError):
    """Raised when a file move's source or destination no longer matches its plan."""


@dataclass(frozen=True)
class DocumentChangePlan:
    """An inspectable proposed replacement for one bundle document.

    ``original_exists`` is ``True`` for every plan built the traditional way,
    against a target that was already present when planned. It is ``False``
    only for a plan built with ``allow_missing=True`` against a target that
    did not yet exist at planning time -- in which case ``original_content``
    is ``""`` and ``apply_document_change`` creates the file fresh instead of
    replacing it, still guarding against a concurrent create the same way
    every other apply guards against a concurrent edit.
    """

    bundle_root: Path
    path: Path
    original_content: str
    proposed_content: str
    original_sha256: str
    proposed_sha256: str
    original_exists: bool = True

    @property
    def changed(self) -> bool:
        """Return whether applying this plan would change document bytes."""

        return self.original_sha256 != self.proposed_sha256


@dataclass(frozen=True)
class DocumentChangeResult:
    """The result of applying or confirming one document change plan."""

    path: Path
    original_sha256: str
    resulting_sha256: str
    changed: bool


@dataclass(frozen=True)
class FileMovePlan:
    """An inspectable proposed relocation of one existing bundle file.

    Planning reads and hashes the source but never moves it. Relative paths
    for both source and dest are interpreted from the configured bundle root,
    the same convention plan_document_change and its siblings use.
    """

    bundle_root: Path
    source_path: Path
    dest_path: Path
    source_sha256: str

    @property
    def noop(self) -> bool:
        """Return whether source and dest already resolve to the same path."""

        return self.source_path == self.dest_path


@dataclass(frozen=True)
class FileMoveResult:
    """The result of applying or confirming one file move plan."""

    source_path: Path
    dest_path: Path
    moved: bool


def _require_string_content(resolved_path: Path, content: object) -> str:
    """Reject a non-``str`` proposed content value before it reaches ``_encode_utf8``.

    ``_encode_utf8`` calls ``.encode("utf-8")`` on whatever it's given; a
    non-``str`` value (``None`` from a callback that forgot a ``return``,
    accidentally-returned ``bytes``, etc.) would otherwise surface as a raw
    ``AttributeError``/``TypeError`` instead of the structured
    ``DocumentChangePlanningError`` every other planning failure raises.
    Shared by ``plan_document_change`` and ``plan_document_change_from_reader``
    so the check and its message stay in one place regardless of which one a
    caller's non-``str`` content came from.
    """
    if not isinstance(content, str):
        raise DocumentChangePlanningError(
            resolved_path, "Proposed document content must be a string"
        )
    return content


def plan_document_change(
    bundle: BundleConfig,
    path: Path | str,
    proposed_content: str,
    *,
    allow_missing: bool = False,
) -> DocumentChangePlan:
    """Prepare an inspectable change for a UTF-8 bundle document.

    Planning reads and hashes the target but never modifies it. Relative paths
    are interpreted from the configured bundle root. By default the target
    must already exist, matching every other planning primitive in this
    module; pass ``allow_missing=True`` to also accept a target that does not
    exist yet, treating its original content as empty (``DocumentChangePlan
    .original_exists`` records which case applied, and ``apply_document_change``
    creates the file fresh rather than replacing it). With ``allow_missing
    =True``, the target's parent directory must still exist as a directory --
    a missing parent raises ``DocumentChangePlanningError`` at plan time,
    since a plan against a parentless target could never be applied.
    """

    def use_proposed_content(resolved_path: Path, _: str) -> str:
        return _require_string_content(resolved_path, proposed_content)

    return _plan_document_change(
        bundle,
        Path(path),
        use_proposed_content,
        allow_missing=allow_missing,
    )


def plan_document_change_from_reader(
    bundle: BundleConfig,
    path: Path | str,
    build_proposed_content: Callable[[Path, str], str],
    *,
    allow_missing: bool = False,
) -> DocumentChangePlan:
    """Prepare an inspectable change whose content is derived from the document itself.

    Unlike ``plan_document_change`` (which takes an already-computed
    ``proposed_content`` string), this is for callers whose proposal must be
    *derived from* the document's current content -- e.g. parsing it,
    inserting something, and re-rendering. ``build_proposed_content`` is
    called with the resolved path and the exact ``original_content`` this
    plan reads once and hashes as its baseline. Deriving the proposal from
    any other read of the same file (a separate call before or after this
    one) would let a concurrent edit between the two reads go undetected: the
    plan's ``original_sha256`` would match whichever read happened last,
    while the proposed content would silently reflect the other one --
    passing the hash check at apply time while discarding the edit that
    happened in between.

    ``build_proposed_content``'s return value is type-checked the same way
    ``plan_document_change`` checks its own ``proposed_content`` argument
    (see ``_require_string_content``): a callback that returns something
    other than ``str`` raises ``DocumentChangePlanningError`` here rather
    than an unstructured ``AttributeError`` once the bad value reaches
    UTF-8 encoding.
    """

    def use_built_content(resolved_path: Path, original_content: str) -> str:
        return _require_string_content(
            resolved_path, build_proposed_content(resolved_path, original_content)
        )

    return _plan_document_change(
        bundle,
        Path(path),
        use_built_content,
        allow_missing=allow_missing,
    )


_DEFAULT_NEW_FILE_MODE = 0o644


def apply_document_change(
    bundle: BundleConfig,
    plan: DocumentChangePlan,
) -> DocumentChangeResult:
    """Apply a document change if the target still matches its planned hash.

    Changed content is prepared in the target directory and installed with
    ``os.replace``. This provides atomic replacement on supported local
    filesystems, but it is not a multi-file transaction or a filesystem lock.
    For a plan built with ``allow_missing=True`` where the target did not
    exist at planning time (``plan.original_exists is False``), "still
    matches its planned hash" instead means the target must still be
    missing -- a target that has since been created concurrently is a
    conflict, the same as one whose content has since changed. The file is
    then created fresh with ``_DEFAULT_NEW_FILE_MODE`` rather than
    ``tempfile.mkstemp``'s restrictive default.
    """

    bundle_root = bundle.bundle_root.resolve(strict=False)
    if bundle_root != plan.bundle_root:
        raise DocumentChangeApplyError(
            plan.path,
            f"Plan belongs to bundle root {plan.bundle_root}, not {bundle_root}",
        )

    _require_plan_target(bundle_root, plan.path)
    _require_bundle_write_safety(bundle)
    if plan.original_exists:
        _, current_mode = _read_for_apply(plan)
    else:
        _require_target_still_missing(plan)
        current_mode = _DEFAULT_NEW_FILE_MODE

    if not plan.changed:
        return DocumentChangeResult(
            path=plan.path,
            original_sha256=plan.original_sha256,
            resulting_sha256=plan.original_sha256,
            changed=False,
        )

    proposed_bytes = _encode_utf8(
        plan.path,
        plan.proposed_content,
        DocumentChangeApplyError,
    )
    if _sha256(proposed_bytes) != plan.proposed_sha256:
        raise DocumentChangeApplyError(
            plan.path, "Plan proposed content does not match its SHA-256 hash"
        )

    temp_path: Path | None = None
    try:
        temp_path = _write_temporary_file(plan.path, proposed_bytes, current_mode)
        if plan.original_exists:
            _require_current_hash(plan)
        else:
            _require_target_still_missing(plan)
        os.replace(temp_path, plan.path)
        temp_path = None
    except DocumentChangeError:
        raise
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.path, f"Could not apply document change: {exc}"
        ) from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    return DocumentChangeResult(
        path=plan.path,
        original_sha256=plan.original_sha256,
        resulting_sha256=plan.proposed_sha256,
        changed=True,
    )


def plan_file_move(
    bundle: BundleConfig,
    source: Path | str,
    dest: Path | str,
) -> FileMovePlan:
    """Prepare an inspectable relocation of one existing bundle file.

    Planning reads and hashes the source but never moves or creates anything.
    Relative paths for both source and dest are interpreted from the
    configured bundle root, the same convention plan_document_change uses. If
    source and dest resolve to the same path, the returned plan is idempotent
    (``.noop`` is True) and apply_file_move will not touch the filesystem for
    it.
    """

    try:
        resolved_source, bundle_root, _ = _resolve_existing_target(bundle, Path(source))
    except DocumentChangePlanningError as exc:
        # _resolve_existing_target's messages are phrased for the generic
        # "document change target" case; reword for a move's source-specific
        # meaning without duplicating its symlink/existence/shape checks.
        raise DocumentChangePlanningError(
            exc.path, str(exc).replace("Document change target", "Move source")
        ) from exc
    _require_bundle_write_safety(bundle)

    dest_path = Path(dest)
    candidate_dest = dest_path if dest_path.is_absolute() else bundle_root / dest_path
    # Checked before .resolve() and before the noop short-circuit below: a
    # symlinked DEST that happens to point at SOURCE must still be rejected
    # as a symlink argument, not silently accepted as a no-op move.
    if candidate_dest.is_symlink():
        raise DocumentChangePlanningError(
            candidate_dest.absolute(), "Move destination must not be a symbolic link"
        )
    resolved_dest = candidate_dest.resolve(strict=False)
    source_sha256 = _sha256(_read_for_planning(resolved_source))

    if resolved_dest == resolved_source:
        return FileMovePlan(
            bundle_root=bundle_root,
            source_path=resolved_source,
            dest_path=resolved_dest,
            source_sha256=source_sha256,
        )

    try:
        _require_plan_target(bundle_root, resolved_dest, planning=True)
    except DocumentChangePlanningError as exc:
        raise DocumentChangePlanningError(
            exc.path, str(exc).replace("Document change target", "Move destination")
        ) from exc
    if resolved_dest.exists():
        raise DocumentChangePlanningError(
            resolved_dest, "Move destination already exists"
        )

    return FileMovePlan(
        bundle_root=bundle_root,
        source_path=resolved_source,
        dest_path=resolved_dest,
        source_sha256=source_sha256,
    )


def apply_file_move(bundle: BundleConfig, plan: FileMovePlan) -> FileMoveResult:
    """Apply a file move if the source still matches its planned hash and the
    destination still does not exist.

    Uses a create-hard-link-then-unlink sequence rather than ``os.replace``,
    so a destination that appears concurrently after planning is never
    silently overwritten: ``os.link`` fails atomically with
    ``FileExistsError`` in that case. If the link succeeds but removing the
    source then fails, the document is left present at both paths rather than
    lost -- the resulting DocumentChangeApplyError explains the manual
    cleanup needed. This is a single-file optimistic-concurrency primitive,
    not a multi-file transaction. Requires source and dest to reside on the
    same filesystem.
    """

    bundle_root = bundle.bundle_root.resolve(strict=False)
    if bundle_root != plan.bundle_root:
        raise DocumentChangeApplyError(
            plan.dest_path,
            f"Plan belongs to bundle root {plan.bundle_root}, not {bundle_root}",
        )

    _require_plan_target(bundle_root, plan.source_path)
    _require_plan_target(bundle_root, plan.dest_path)
    _require_bundle_write_safety(bundle)

    current_hash = _current_regular_file_sha256(plan.source_path)
    if current_hash != plan.source_sha256:
        raise FileMoveConflictError(
            plan.source_path,
            f"Move source changed after planning: {plan.source_path} "
            f"(expected SHA-256 {plan.source_sha256}, "
            f"got {current_hash if current_hash is not None else '<unavailable>'})",
        )

    if plan.noop:
        return FileMoveResult(plan.source_path, plan.dest_path, moved=False)

    if plan.dest_path.exists() or plan.dest_path.is_symlink():
        raise FileMoveConflictError(
            plan.dest_path, f"Move destination now exists: {plan.dest_path}"
        )

    dest_parent = plan.dest_path.parent
    # Checked before mkdir(parents=True): if an ancestor was swapped for a
    # symlink after planning, resolve() already diverges from the lexical
    # path even though dest_parent itself doesn't exist yet, so this catches
    # it before mkdir ever creates anything through that symlink.
    _require_dest_parent_unchanged(plan.dest_path, dest_parent)

    try:
        dest_parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.dest_path, f"Could not create destination directory: {exc}"
        ) from exc

    # Re-checked immediately after mkdir: a narrower race where dest_parent
    # was swapped for a symlink between the check above and mkdir returning.
    _require_dest_parent_unchanged(plan.dest_path, dest_parent)

    try:
        os.link(plan.source_path, plan.dest_path)
    except FileExistsError as exc:
        raise FileMoveConflictError(
            plan.dest_path, f"Move destination now exists: {plan.dest_path}"
        ) from exc
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.dest_path, f"Could not create move destination: {exc}"
        ) from exc

    try:
        plan.source_path.unlink()
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.source_path,
            f"Created {plan.dest_path} but could not remove original "
            f"{plan.source_path}: {exc}. Both copies currently exist; remove "
            "the original manually once verified.",
        ) from exc

    return FileMoveResult(plan.source_path, plan.dest_path, moved=True)


def _plan_document_change(
    bundle: BundleConfig,
    path: Path,
    build_proposed_content: Callable[[Path, str], str],
    *,
    allow_missing: bool = False,
) -> DocumentChangePlan:
    resolved_path, bundle_root, original_exists = _resolve_existing_target(
        bundle, path, allow_missing=allow_missing
    )
    _require_bundle_write_safety(bundle)
    if original_exists:
        original_bytes = _read_for_planning(resolved_path)
        try:
            original_content = original_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentChangePlanningError(
                resolved_path,
                f"Could not decode document as UTF-8: {exc}",
            ) from exc
    else:
        original_bytes = b""
        original_content = ""

    proposed_content = build_proposed_content(resolved_path, original_content)
    proposed_bytes = _encode_utf8(
        resolved_path,
        proposed_content,
        DocumentChangePlanningError,
    )
    return DocumentChangePlan(
        bundle_root=bundle_root,
        path=resolved_path,
        original_content=original_content,
        proposed_content=proposed_content,
        original_sha256=_sha256(original_bytes),
        proposed_sha256=_sha256(proposed_bytes),
        original_exists=original_exists,
    )


def _resolve_existing_target(
    bundle: BundleConfig, path: Path, *, allow_missing: bool = False
) -> tuple[Path, Path, bool]:
    """Resolve and validate a planning target, returning whether it exists.

    By default a missing target is a planning error, matching every
    pre-#136 caller. With ``allow_missing=True`` a missing target is
    accepted instead (returned as ``exists=False``) so a caller like
    ``plan_log_concept_move`` can plan against a ``log.md`` that has never
    been written yet -- but only if the target's parent directory exists;
    see ``_require_missing_target_parent_exists``.
    """
    bundle_root = bundle.bundle_root.resolve(strict=False)
    candidate = path if path.is_absolute() else bundle_root / path
    if candidate.is_symlink():
        raise DocumentChangePlanningError(
            candidate.absolute(), "Document change target must not be a symbolic link"
        )

    resolved_path = candidate.resolve(strict=False)
    _require_plan_target(bundle_root, resolved_path, planning=True)
    if not resolved_path.exists():
        if allow_missing:
            _require_missing_target_parent_exists(resolved_path)
            return resolved_path, bundle_root, False
        raise DocumentChangePlanningError(
            resolved_path, "Document change target does not exist"
        )
    if not resolved_path.is_file():
        raise DocumentChangePlanningError(
            resolved_path, "Document change target must be a regular file"
        )
    return resolved_path, bundle_root, True


def _require_missing_target_parent_exists(resolved_path: Path) -> None:
    """Raise if a missing target's parent directory doesn't exist as a directory.

    A plan built with ``allow_missing=True`` records ``original_exists=False``
    and lets ``apply_document_change`` create the file fresh via
    ``_write_temporary_file``'s ``tempfile.mkstemp(dir=path.parent)`` call.
    ``mkstemp`` requires that directory to already exist; a plan built
    against a target whose parent is missing (or is a file rather than a
    directory) can therefore never be applied. Catching this at plan time
    gives callers a structured ``DocumentChangePlanningError`` instead of a
    confusing ``OSError`` surfacing only once they try to apply the plan.
    ``resolved_path`` has already been through ``.resolve(strict=False)``,
    so ``.parent`` reflects any symlinked ancestors as their real target --
    no separate symlink check is needed here.
    """
    parent = resolved_path.parent
    if not parent.is_dir():
        raise DocumentChangePlanningError(
            resolved_path,
            f"Document change target's parent directory does not exist: {parent}",
        )


def _require_plan_target(
    bundle_root: Path,
    path: Path,
    *,
    planning: bool = False,
) -> None:
    error_type = DocumentChangePlanningError if planning else DocumentChangeApplyError
    if planning and path.is_symlink():
        raise error_type(path, "Document change target must not be a symbolic link")
    try:
        path.relative_to(bundle_root)
    except ValueError as exc:
        raise error_type(
            path,
            f"Document change target is outside bundle root {bundle_root}",
        ) from exc


def _require_bundle_write_safety(bundle: BundleConfig) -> None:
    problem = check_bundle_write_safety(bundle)
    if problem is not None:
        raise DocumentChangeSafetyError(problem.path, problem.message)


def _encode_utf8(
    path: Path,
    content: str,
    error_type: type[DocumentChangeError],
) -> bytes:
    try:
        return content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise error_type(
            path,
            f"Could not encode proposed document content as UTF-8: {exc}",
        ) from exc


def _read_for_planning(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not read document for planning: {exc}"
        ) from exc


def _read_for_apply(plan: DocumentChangePlan) -> tuple[bytes, int]:
    try:
        if (
            plan.path.is_symlink()
            or plan.path.resolve(strict=False) != plan.path
            or not plan.path.is_file()
        ):
            raise DocumentChangeConflictError(plan.path, plan.original_sha256, None)
        current_bytes = plan.path.read_bytes()
        mode = stat.S_IMODE(plan.path.stat().st_mode)
    except DocumentChangeConflictError:
        raise
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.path, f"Could not read document before applying change: {exc}"
        ) from exc

    actual_sha256 = _sha256(current_bytes)
    if actual_sha256 != plan.original_sha256:
        raise DocumentChangeConflictError(
            plan.path,
            plan.original_sha256,
            actual_sha256,
        )
    return current_bytes, mode


def _require_current_hash(plan: DocumentChangePlan) -> None:
    try:
        if (
            plan.path.is_symlink()
            or plan.path.resolve(strict=False) != plan.path
            or not plan.path.is_file()
        ):
            raise DocumentChangeConflictError(plan.path, plan.original_sha256, None)
        actual_sha256 = _sha256(plan.path.read_bytes())
    except DocumentChangeConflictError:
        raise
    except OSError as exc:
        raise DocumentChangeApplyError(
            plan.path, f"Could not recheck document before replacement: {exc}"
        ) from exc

    if actual_sha256 != plan.original_sha256:
        raise DocumentChangeConflictError(
            plan.path,
            plan.original_sha256,
            actual_sha256,
        )


def _require_target_still_missing(plan: DocumentChangePlan) -> None:
    """Raise if a target planned as missing (``original_exists=False``) now exists.

    A target that appears concurrently between planning and apply -- created
    by another process, or left over as a symlink -- is a conflict just like
    a target whose content changed underneath an existing-target plan, even
    though there is no "current hash" to compare against; ``actual_sha256``
    reports the new content's hash (or ``None`` if it can't be read) so the
    conflict message still says what showed up. The ``resolve(strict=False)
    != plan.path`` check mirrors ``_read_for_apply``/``_require_current_hash``'s
    equivalent check for the existing-target path: it catches an *ancestor*
    directory that was swapped for a symlink after planning (escaping the
    bundle root) even though ``plan.path`` itself is still not a symlink and
    nothing exists at the escaped location yet -- ``.exists()``/
    ``.is_symlink()`` alone would miss that case and let
    ``apply_document_change`` go on to create the file outside the bundle
    root through the swapped ancestor.
    """
    if (
        plan.path.exists()
        or plan.path.is_symlink()
        or plan.path.resolve(strict=False) != plan.path
    ):
        raise DocumentChangeConflictError(
            plan.path,
            plan.original_sha256,
            _current_regular_file_sha256(plan.path),
        )


def _require_dest_parent_unchanged(dest_path: Path, dest_parent: Path) -> None:
    """Raise if dest_parent is now a symlink, or an ancestor of it is."""

    if dest_parent.is_symlink() or dest_parent.resolve(strict=False) != dest_parent:
        raise FileMoveConflictError(
            dest_path,
            f"Move destination directory changed after planning: {dest_parent}",
        )


def _current_regular_file_sha256(path: Path) -> str | None:
    """Return path's current SHA-256, or None if it's missing/symlink/non-regular."""

    try:
        if (
            path.is_symlink()
            or path.resolve(strict=False) != path
            or not path.is_file()
        ):
            return None
        return _sha256(path.read_bytes())
    except OSError:
        return None


def _write_temporary_file(path: Path, content: bytes, mode: int) -> Path:
    fd = -1
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=".okf-",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written == 0:
                raise OSError("temporary file write made no progress")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.chmod(temp_path, mode)
        return temp_path
    except OSError:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _sha256(content: bytes) -> str:
    return sha256(content).hexdigest()

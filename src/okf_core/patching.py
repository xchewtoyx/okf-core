"""Inspectable, optimistic-concurrency-safe document changes."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from markdown_it import MarkdownIt

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError, parse_concept_document
from okf_core.write_safety import check_bundle_write_safety

_MARKDOWN = MarkdownIt("commonmark")


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


@dataclass(frozen=True)
class DocumentChangePlan:
    """An inspectable proposed replacement for one existing bundle document."""

    bundle_root: Path
    path: Path
    original_content: str
    proposed_content: str
    original_sha256: str
    proposed_sha256: str

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


def plan_document_change(
    bundle: BundleConfig,
    path: Path | str,
    proposed_content: str,
) -> DocumentChangePlan:
    """Prepare an inspectable change for an existing UTF-8 bundle document.

    Planning reads and hashes the target but never modifies it. Relative paths
    are interpreted from the configured bundle root.
    """

    def use_proposed_content(resolved_path: Path, _: str) -> str:
        if not isinstance(proposed_content, str):
            raise DocumentChangePlanningError(
                resolved_path, "Proposed document content must be a string"
            )
        return proposed_content

    return _plan_document_change(
        bundle,
        Path(path),
        use_proposed_content,
    )


def plan_markdown_section_patch(
    bundle: BundleConfig,
    path: Path | str,
    heading: str,
    body: str,
    *,
    level: int = 1,
) -> DocumentChangePlan:
    """Plan replacement or insertion of one named CommonMark section.

    A section is identified by exact, case-sensitive parsed heading content and
    heading level. Existing ATX and Setext headings are supported. The heading
    itself is preserved when replacing a section; an absent section is appended
    using ATX syntax.
    """

    return _plan_document_change(
        bundle,
        Path(path),
        lambda resolved_path, original_content: _patch_markdown_section(
            resolved_path,
            original_content,
            heading,
            body,
            level,
        ),
    )


def apply_document_change(
    bundle: BundleConfig,
    plan: DocumentChangePlan,
) -> DocumentChangeResult:
    """Apply a document change if the target still matches its planned hash.

    Changed content is prepared in the target directory and installed with
    ``os.replace``. This provides atomic replacement on supported local
    filesystems, but it is not a multi-file transaction or a filesystem lock.
    """

    bundle_root = bundle.bundle_root.resolve(strict=False)
    if bundle_root != plan.bundle_root:
        raise DocumentChangeApplyError(
            plan.path,
            f"Plan belongs to bundle root {plan.bundle_root}, not {bundle_root}",
        )

    _require_plan_target(bundle_root, plan.path)
    _require_bundle_write_safety(bundle)
    _, current_mode = _read_for_apply(plan)
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
        _require_current_hash(plan)
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


def _plan_document_change(
    bundle: BundleConfig,
    path: Path,
    build_proposed_content: Callable[[Path, str], str],
) -> DocumentChangePlan:
    resolved_path, bundle_root = _resolve_existing_target(bundle, path)
    _require_bundle_write_safety(bundle)
    original_bytes = _read_for_planning(resolved_path)
    try:
        original_content = original_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentChangePlanningError(
            resolved_path,
            f"Could not decode document as UTF-8: {exc}",
        ) from exc

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
    )


def _patch_markdown_section(
    path: Path,
    content: str,
    heading: str,
    body: str,
    level: int,
) -> str:
    _validate_section_request(path, heading, body, level)
    try:
        document = parse_concept_document(content)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not parse document frontmatter: {exc}"
        ) from exc

    document_body = document.body
    body_offset = len(content) - len(document_body)
    tokens = _MARKDOWN.parse(document_body)
    matches: list[tuple[int, int]] = []
    target_tag = f"h{level}"
    for index, token in enumerate(tokens):
        if (
            token.type != "heading_open"
            or token.tag != target_tag
            or token.map is None
            or index + 1 >= len(tokens)
        ):
            continue
        inline = tokens[index + 1]
        if inline.type == "inline" and inline.content == heading:
            matches.append((index, token.map[1]))

    if len(matches) > 1:
        raise DocumentChangePlanningError(
            path,
            f"Document contains multiple level-{level} headings named {heading!r}",
        )

    line_ending = _first_line_ending(content)
    normalized_body = _ensure_structural_line_ending(body, line_ending)
    if not matches:
        return _append_markdown_section(
            content,
            heading,
            normalized_body,
            level,
            line_ending,
        )

    token_index, section_start_line = matches[0]
    section_end_line = len(document_body.splitlines(keepends=True))
    for token in tokens[token_index + 1 :]:
        if token.type != "heading_open" or token.map is None:
            continue
        token_level = int(token.tag[1:])
        if token_level <= level:
            section_end_line = token.map[0]
            break

    offsets = _line_offsets(document_body)
    section_start = body_offset + offsets[section_start_line]
    section_end = body_offset + offsets[section_end_line]
    return f"{content[:section_start]}{normalized_body}{content[section_end:]}"


def _validate_section_request(
    path: Path,
    heading: str,
    body: str,
    level: int,
) -> None:
    if (
        not isinstance(heading, str)
        or not heading
        or heading != heading.strip()
        or "\n" in heading
        or "\r" in heading
    ):
        raise DocumentChangePlanningError(
            path,
            "Section heading must be a non-empty, single-line string "
            "without surrounding whitespace",
        )
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 6:
        raise DocumentChangePlanningError(
            path, "Section heading level must be an integer from 1 through 6"
        )
    if not isinstance(body, str):
        raise DocumentChangePlanningError(path, "Section body must be a string")


def _append_markdown_section(
    content: str,
    heading: str,
    body: str,
    level: int,
    line_ending: str,
) -> str:
    trailing_line_endings = _count_trailing_line_endings(content)
    separator = line_ending * max(0, 2 - trailing_line_endings) if content else ""
    heading_line = f"{'#' * level} {heading}{line_ending}"
    return f"{content}{separator}{heading_line}{body}"


def _ensure_structural_line_ending(body: str, line_ending: str) -> str:
    if body and not body.endswith(("\n", "\r")):
        return f"{body}{line_ending}"
    return body


def _first_line_ending(content: str) -> str:
    for index, character in enumerate(content):
        if character == "\n":
            return "\n"
        if character == "\r":
            if index + 1 < len(content) and content[index + 1] == "\n":
                return "\r\n"
            return "\r"
    return "\n"


def _count_trailing_line_endings(content: str) -> int:
    count = 0
    position = len(content)
    while position > 0 and count < 2:
        if position >= 2 and content[position - 2 : position] == "\r\n":
            position -= 2
        elif content[position - 1] in "\r\n":
            position -= 1
        else:
            break
        count += 1
    return count


def _line_offsets(content: str) -> tuple[int, ...]:
    offsets = [0]
    position = 0
    for line in content.splitlines(keepends=True):
        position += len(line)
        offsets.append(position)
    return tuple(offsets)


def _resolve_existing_target(bundle: BundleConfig, path: Path) -> tuple[Path, Path]:
    bundle_root = bundle.bundle_root.resolve(strict=False)
    candidate = path if path.is_absolute() else bundle_root / path
    if candidate.is_symlink():
        raise DocumentChangePlanningError(
            candidate.absolute(), "Document change target must not be a symbolic link"
        )

    resolved_path = candidate.resolve(strict=False)
    _require_plan_target(bundle_root, resolved_path, planning=True)
    if not resolved_path.exists():
        raise DocumentChangePlanningError(
            resolved_path, "Document change target does not exist"
        )
    if not resolved_path.is_file():
        raise DocumentChangePlanningError(
            resolved_path, "Document change target must be a regular file"
        )
    return resolved_path, bundle_root


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

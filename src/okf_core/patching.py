"""Inspectable, optimistic-concurrency-safe document changes."""

from __future__ import annotations

import os
import stat
import tempfile
from math import isnan
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt
from yaml.nodes import MappingNode, Node
from yaml.tokens import AliasToken

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


def plan_frontmatter_merge(
    bundle: BundleConfig,
    path: Path | str,
    updates: Mapping[str, Any],
) -> DocumentChangePlan:
    """Plan a shallow, byte-preserving merge of top-level frontmatter fields.

    Existing values are replaced at their YAML source spans and missing fields
    are appended in update order. YAML aliases are rejected because their
    shared source nodes cannot be edited safely without broader round-tripping.
    """

    return _plan_document_change(
        bundle,
        Path(path),
        lambda resolved_path, original_content: _merge_frontmatter(
            resolved_path,
            original_content,
            updates,
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
    generated_heading = _MARKDOWN.parse(f"{'#' * level} {heading}\n")
    if (
        len(generated_heading) < 2
        or generated_heading[0].type != "heading_open"
        or generated_heading[1].type != "inline"
        or generated_heading[1].content != heading
    ):
        raise DocumentChangePlanningError(
            path,
            "Section heading cannot be represented unambiguously in ATX syntax",
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


def _merge_frontmatter(
    path: Path,
    content: str,
    updates: Mapping[str, Any],
) -> str:
    if not isinstance(updates, Mapping):
        raise DocumentChangePlanningError(path, "Frontmatter updates must be a mapping")
    update_items = tuple(updates.items())
    for key, value in update_items:
        if not isinstance(key, str) or not key.strip():
            raise DocumentChangePlanningError(
                path, "Frontmatter update keys must be non-empty strings"
            )
        _dump_yaml(path, value, flow_style=False)

    try:
        document = parse_concept_document(content)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not parse document frontmatter: {exc}"
        ) from exc

    if not update_items:
        return content

    bounds = _frontmatter_bounds(content)
    line_ending = _first_line_ending(content)
    if bounds is None:
        generated = _dump_yaml_mapping(path, update_items, line_ending)
        proposed = f"---{line_ending}{generated}---{line_ending}{content}"
        _validate_merged_frontmatter(path, proposed)
        return proposed

    yaml_start, yaml_end = bounds
    yaml_source = content[yaml_start:yaml_end]
    _reject_yaml_aliases(path, yaml_source)
    root = _compose_frontmatter(path, yaml_source)
    nodes = _top_level_nodes(root)

    replacements: list[tuple[int, int, str]] = []
    additions: list[tuple[str, Any]] = []
    for key, value in update_items:
        current = document.frontmatter.get(key, _MISSING)
        if current is not _MISSING and _yaml_values_equal(current, value):
            continue
        value_node = nodes.get(key)
        if value_node is None:
            additions.append((key, value))
            continue
        start = value_node.start_mark.index
        end = value_node.end_mark.index
        original_value_source = yaml_source[start:end]
        inline = value_node.start_mark.line == _node_key_line(root, key)
        replacement = _serialize_replacement_value(
            path,
            value,
            column=value_node.start_mark.column,
            inline=inline,
            preserve_final_line_ending=original_value_source.endswith(("\n", "\r")),
            line_ending=line_ending,
        )
        if start == end:
            replacement = f" {replacement}"
        replacements.append((start, end, replacement))

    merged_yaml = yaml_source
    for start, end, replacement in sorted(replacements, reverse=True):
        merged_yaml = f"{merged_yaml[:start]}{replacement}{merged_yaml[end:]}"
    if additions:
        merged_yaml += _dump_yaml_mapping(path, additions, line_ending)

    proposed = f"{content[:yaml_start]}{merged_yaml}{content[yaml_end:]}"
    _validate_merged_frontmatter(path, proposed)
    return proposed


_MISSING = object()


def _frontmatter_bounds(content: str) -> tuple[int, int] | None:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    yaml_start = len(lines[0])
    position = yaml_start
    for line in lines[1:]:
        if line.rstrip("\r\n") == "---":
            return yaml_start, position
        position += len(line)
    return None


def _compose_frontmatter(path: Path, yaml_source: str) -> MappingNode | None:
    try:
        root = yaml.compose(yaml_source, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not compose document frontmatter: {exc}"
        ) from exc
    if root is None:
        return None
    if not isinstance(root, MappingNode):
        raise DocumentChangePlanningError(path, "YAML frontmatter must be a mapping")
    return root


def _top_level_nodes(root: MappingNode | None) -> dict[str, Node]:
    if root is None:
        return {}
    return {key_node.value: value_node for key_node, value_node in root.value}


def _node_key_line(root: MappingNode | None, target_key: str) -> int:
    assert root is not None
    for key_node, _ in root.value:
        if key_node.value == target_key:
            return key_node.start_mark.line
    raise AssertionError(f"Missing composed frontmatter key: {target_key}")


def _serialize_replacement_value(
    path: Path,
    value: Any,
    *,
    column: int,
    inline: bool,
    preserve_final_line_ending: bool,
    line_ending: str,
) -> str:
    dumped = _dump_yaml(path, value, flow_style=inline)
    dumped = _strip_yaml_document_end(dumped)
    dumped = dumped.removesuffix("\n")
    dumped = dumped.replace("\n", f"\n{' ' * column}")
    dumped = dumped.replace("\n", line_ending)
    if preserve_final_line_ending:
        dumped += line_ending
    return dumped


def _dump_yaml(path: Path, value: Any, *, flow_style: bool) -> str:
    try:
        dumped = yaml.safe_dump(
            value,
            allow_unicode=True,
            default_flow_style=flow_style,
            sort_keys=False,
            width=10_000,
        )
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        raise DocumentChangePlanningError(
            path, f"Frontmatter value cannot be represented as safe YAML: {exc}"
        ) from exc
    _reject_yaml_aliases(path, dumped)
    return dumped


def _dump_yaml_mapping(
    path: Path,
    items: Sequence[tuple[str, Any]],
    line_ending: str,
) -> str:
    if not items:
        return ""
    try:
        dumped = yaml.safe_dump(
            dict(items),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=10_000,
        )
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        raise DocumentChangePlanningError(
            path, f"Frontmatter updates cannot be represented as safe YAML: {exc}"
        ) from exc
    _reject_yaml_aliases(path, dumped)
    return dumped.replace("\n", line_ending)


def _strip_yaml_document_end(dumped: str) -> str:
    if dumped.endswith("\n...\n"):
        return dumped[:-4]
    return dumped


def _reject_yaml_aliases(path: Path, yaml_source: str) -> None:
    try:
        has_alias = any(
            isinstance(token, AliasToken)
            for token in yaml.scan(yaml_source, Loader=yaml.SafeLoader)
        )
    except yaml.YAMLError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not scan document frontmatter: {exc}"
        ) from exc
    if has_alias:
        raise DocumentChangePlanningError(
            path, "Frontmatter merges do not support YAML aliases"
        )


def _validate_merged_frontmatter(path: Path, proposed: str) -> None:
    try:
        document = parse_concept_document(proposed)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Merged frontmatter is invalid: {exc}"
        ) from exc
    bounds = _frontmatter_bounds(proposed)
    if bounds is None:
        raise DocumentChangePlanningError(path, "Merged frontmatter is missing")
    yaml_start, yaml_end = bounds
    _reject_yaml_aliases(path, proposed[yaml_start:yaml_end])
    if not isinstance(document.frontmatter, dict):
        raise DocumentChangePlanningError(path, "Merged frontmatter is not a mapping")


def _yaml_values_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, float) and isnan(left) and isnan(right):
        return True
    if isinstance(left, Mapping):
        if len(left) != len(right):
            return False
        unmatched = list(right.items())
        for left_key, left_value in left.items():
            for index, (right_key, right_value) in enumerate(unmatched):
                if _yaml_values_equal(left_key, right_key):
                    if not _yaml_values_equal(left_value, right_value):
                        return False
                    unmatched.pop(index)
                    break
            else:
                return False
        return not unmatched
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _yaml_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return left == right


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

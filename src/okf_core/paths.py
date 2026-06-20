"""Concept ID and path resolution for configured OKF bundles."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from okf_core.config import BundleConfig


class ConceptPathError(Exception):
    """Raised when a concept ID or path cannot be resolved safely."""


def concept_id_to_path(
    concept_id: str,
    bundle: BundleConfig,
) -> Path:
    """Resolve an OKF concept ID to a Markdown path within its bundle root."""

    _require_relative_path_strategy(bundle)
    root = bundle.bundle_root.resolve(strict=False)
    relative_path = _concept_id_to_relative_markdown_path(concept_id)
    resolved_path = (root / relative_path).resolve(strict=False)

    if not _is_within_root(resolved_path, root):
        raise ConceptPathError(f"Concept ID escapes bundle root: {concept_id}")
    if _is_reserved_filename(resolved_path, bundle):
        raise ConceptPathError(
            f"Reserved filename cannot be a concept: {resolved_path.name}"
        )
    return resolved_path


def path_to_concept_id(path: str | Path, bundle: BundleConfig) -> str:
    """Resolve a Markdown path inside a bundle root to an OKF concept ID."""

    _require_relative_path_strategy(bundle)
    resolved_path = Path(path).expanduser().resolve(strict=False)
    root = bundle.bundle_root.resolve(strict=False)
    if not _is_within_root(resolved_path, root):
        raise ConceptPathError(
            f"Concept path is outside configured bundle root: {resolved_path}"
        )
    return _path_to_concept_id_in_root(resolved_path, root, bundle)


def _path_to_concept_id_in_root(
    resolved_path: Path,
    root: Path,
    bundle: BundleConfig,
) -> str:
    if _is_reserved_filename(resolved_path, bundle):
        raise ConceptPathError(
            f"Reserved filename cannot be a concept: {resolved_path.name}"
        )
    if resolved_path.suffix != ".md":
        raise ConceptPathError(f"Concept path must be a Markdown file: {resolved_path}")

    relative_path = resolved_path.relative_to(root)
    concept_parts = relative_path.with_suffix("").parts
    return "/".join(concept_parts)


def is_reserved_concept_path(path: str | Path, bundle: BundleConfig) -> bool:
    """Return whether a path has a configured reserved filename."""

    return _is_reserved_filename(Path(path), bundle)


def _require_relative_path_strategy(bundle: BundleConfig) -> None:
    if bundle.concept_path_strategy != "relative-path":
        raise ConceptPathError(
            f"Unsupported concept path strategy: {bundle.concept_path_strategy}"
        )


def _concept_id_to_relative_markdown_path(concept_id: str) -> Path:
    if not isinstance(concept_id, str) or not concept_id.strip():
        raise ConceptPathError("Concept ID must be a non-empty string")
    if "\\" in concept_id:
        raise ConceptPathError(f"Concept ID must use '/' separators: {concept_id}")

    raw_parts = tuple(concept_id.split("/"))
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ConceptPathError(f"Unsafe concept ID: {concept_id}")
    if any(":" in part for part in raw_parts):
        raise ConceptPathError(
            f"Concept ID contains a platform-specific path part: {concept_id}"
        )

    posix_path = PurePosixPath(concept_id)
    if posix_path.is_absolute():
        raise ConceptPathError(f"Concept ID must be relative: {concept_id}")
    if posix_path.suffix:
        raise ConceptPathError(
            f"Concept ID must not include a file extension: {concept_id}"
        )

    return Path(*raw_parts).with_suffix(".md")


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_reserved_filename(path: Path, bundle: BundleConfig) -> bool:
    reserved_filenames = {filename.casefold() for filename in bundle.reserved_filenames}
    return path.name.casefold() in reserved_filenames

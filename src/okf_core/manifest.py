"""Bundle scanning and manifest generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError, parse_concept_document
from okf_core.paths import (
    ConceptPathError,
    is_reserved_concept_path,
    path_to_concept_id,
)


@dataclass(frozen=True)
class ConceptManifestEntry:
    """A concept discovered while scanning a configured bundle."""

    concept_id: str
    path: Path
    bundle_root: Path
    mtime_ns: int
    size: int
    sha256: str
    frontmatter: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManifestProblem:
    """A non-fatal problem found while scanning a bundle."""

    path: Path
    kind: str
    message: str


@dataclass(frozen=True)
class BundleManifest:
    """A deterministic manifest for one configured bundle."""

    bundle_name: str
    concepts: tuple[ConceptManifestEntry, ...] = ()
    problems: tuple[ManifestProblem, ...] = ()


def scan_bundle(bundle: BundleConfig) -> BundleManifest:
    """Scan a configured bundle into concept entries and non-fatal problems."""

    entries: list[ConceptManifestEntry] = []
    problems: list[ManifestProblem] = []

    for root in bundle.bundle_roots:
        resolved_root = root.resolve(strict=False)
        if not resolved_root.is_dir():
            continue

        for path in _iter_included_paths(resolved_root, bundle):
            if is_reserved_concept_path(path, bundle):
                continue

            entry, problem = _scan_concept_path(path, resolved_root, bundle)
            if entry is not None:
                entries.append(entry)
            if problem is not None:
                problems.append(problem)

    return BundleManifest(
        bundle_name=bundle.name,
        concepts=tuple(
            sorted(entries, key=lambda entry: (entry.concept_id, str(entry.path)))
        ),
        problems=tuple(
            sorted(problems, key=lambda problem: (str(problem.path), problem.kind))
        ),
    )


def _iter_included_paths(root: Path, bundle: BundleConfig) -> tuple[Path, ...]:
    paths: set[Path] = set()
    for pattern in bundle.include:
        for path in root.glob(pattern):
            resolved_path = path.resolve(strict=False)
            if not resolved_path.is_file():
                continue
            if _is_excluded(resolved_path, root, bundle):
                continue
            paths.add(resolved_path)
    return tuple(sorted(paths, key=str))


def _is_excluded(path: Path, root: Path, bundle: BundleConfig) -> bool:
    try:
        relative_path = path.relative_to(root)
    except ValueError:
        return True

    relative_posix = PurePosixPath(relative_path.as_posix())
    return any(relative_posix.match(pattern) for pattern in bundle.exclude)


def _scan_concept_path(
    path: Path,
    root: Path,
    bundle: BundleConfig,
) -> tuple[ConceptManifestEntry | None, ManifestProblem | None]:
    try:
        owning_root = _matching_bundle_root(path, bundle)
        if owning_root != root:
            return None, None
        concept_id = path_to_concept_id(path, bundle)
    except ConceptPathError as exc:
        return None, ManifestProblem(path=path, kind="path-error", message=str(exc))

    try:
        content = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        return None, ManifestProblem(path=path, kind="read-error", message=str(exc))

    try:
        markdown = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, ManifestProblem(path=path, kind="decode-error", message=str(exc))

    try:
        document = parse_concept_document(markdown)
    except DocumentParseError as exc:
        return None, ManifestProblem(path=path, kind="parse-error", message=str(exc))

    return (
        ConceptManifestEntry(
            concept_id=concept_id,
            path=path,
            bundle_root=owning_root,
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            sha256=sha256(content).hexdigest(),
            frontmatter=document.frontmatter,
        ),
        None,
    )


def _matching_bundle_root(path: Path, bundle: BundleConfig) -> Path:
    matches = [
        root.resolve(strict=False)
        for root in bundle.bundle_roots
        if _is_within_root(path, root.resolve(strict=False))
    ]
    if not matches:
        raise ConceptPathError(
            f"Concept path is outside configured bundle roots: {path}"
        )
    return max(matches, key=lambda root: len(root.parts))


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True

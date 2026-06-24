"""Bundle scanning and manifest generation."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError, parse_concept_document
from okf_core.paths import (
    ConceptPathError,
    _path_to_concept_id_in_root,
    is_reserved_concept_path,
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
    frontmatter: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    _content_cache: str | None = field(default=None, repr=False, compare=False)

    @property
    def content(self) -> str:
        """Return raw Markdown content, reading it once when not scan-cached."""
        content = self._content_cache
        if content is None:
            content = self.path.read_bytes().decode("utf-8")
            object.__setattr__(self, "_content_cache", content)
        return content


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
    root = bundle.bundle_root.resolve(strict=False)

    if root.is_dir():
        for path in _iter_included_paths(root, bundle):
            if is_reserved_concept_path(path, bundle):
                continue

            entry, problem = _scan_concept_path(path, root, bundle)
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
        concept_id = _path_to_concept_id_in_root(path, root, bundle)
    except ConceptPathError as exc:
        return None, ManifestProblem(path=path, kind="path-error", message=str(exc))

    try:
        stat = path.stat()
        content = path.read_bytes()
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
            bundle_root=root,
            mtime_ns=stat.st_mtime_ns,
            size=len(content),
            sha256=sha256(content).hexdigest(),
            frontmatter=_freeze_value(document.frontmatter),
            _content_cache=markdown,
        ),
        None,
    )


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value

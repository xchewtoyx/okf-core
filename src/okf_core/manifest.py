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
    stable_id: str | None = None
    _content_cache: str | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _body_cache: str | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def content(self) -> str:
        """Return raw Markdown content, reading it when not scan-cached."""
        content = self._content_cache
        if content is None:
            content = self.path.read_bytes().decode("utf-8")
            object.__setattr__(self, "_content_cache", content)
        return content

    @property
    def body(self) -> str:
        """Return Markdown body content (with YAML frontmatter stripped)."""
        body = self._body_cache
        if body is None:
            from okf_core.documents import parse_concept_document

            body = parse_concept_document(self.content).body
            object.__setattr__(self, "_body_cache", body)
        return body


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
    root = bundle.bundle_root.resolve(strict=False)
    if not root.is_dir():
        return BundleManifest(
            bundle_name=bundle.name,
            concepts=(),
            problems=(),
        )

    from okf_core.hooks import get_hook_manager

    pm = get_hook_manager(bundle)

    try:
        pm.hook.okf_start_scan(bundle=bundle)
        entries: list[ConceptManifestEntry] = []
        problems: list[ManifestProblem] = []

        for path in _iter_included_paths(root, bundle):
            if is_reserved_concept_path(path, bundle):
                continue

            pm.hook.okf_enter_scan_concept(path=path, root=root, bundle=bundle)
            entry = pm.hook.okf_fetch_scan_concept(path=path, root=root, bundle=bundle)
            problem = None
            if entry is None:
                entry, problem = _scan_concept_path(path, root, bundle)
                if problem is not None:
                    problems.append(problem)
            else:
                if bundle.stable_id_field is not None:
                    if entry.stable_id is None:
                        problem = ManifestProblem(
                            path=path,
                            kind="stable-id-missing",
                            message=f"Missing required stable ID field '{bundle.stable_id_field}'",
                        )
                        problems.append(problem)

            if entry is not None:
                entries.append(entry)

            pm.hook.okf_exit_scan_concept(
                entry=entry,
                problem=problem,
                path=path,
                root=root,
                bundle=bundle,
            )

        # Check for duplicate stable IDs
        if bundle.stable_id_field is not None:
            stable_id_to_paths: dict[str, list[Path]] = {}
            for entry in entries:
                if entry.stable_id is not None:
                    stable_id_to_paths.setdefault(entry.stable_id, []).append(
                        entry.path
                    )

            for stable_id, paths in stable_id_to_paths.items():
                if len(paths) > 1:
                    for path in paths:
                        others = [
                            str(p.relative_to(root).as_posix())
                            for p in paths
                            if p != path
                        ]
                        problems.append(
                            ManifestProblem(
                                path=path,
                                kind="stable-id-conflict",
                                message=f"Duplicate stable ID '{stable_id}' shared with: {', '.join(others)}",
                            )
                        )

        manifest = BundleManifest(
            bundle_name=bundle.name,
            concepts=tuple(
                sorted(entries, key=lambda entry: (entry.concept_id, str(entry.path)))
            ),
            problems=tuple(
                sorted(problems, key=lambda problem: (str(problem.path), problem.kind))
            ),
        )
        pm.hook.okf_end_scan(bundle=bundle, manifest=manifest)
        return manifest
    except Exception:
        pm.hook.okf_abort_scan(bundle=bundle)
        raise


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

    stable_id = None
    problem = None
    if bundle.stable_id_field is not None:
        val = document.frontmatter.get(bundle.stable_id_field)
        if val is None or (isinstance(val, str) and not val.strip()):
            problem = ManifestProblem(
                path=path,
                kind="stable-id-missing",
                message=f"Missing required stable ID field '{bundle.stable_id_field}'",
            )
        else:
            stable_id = str(val).strip()

    entry = ConceptManifestEntry(
        concept_id=concept_id,
        path=path,
        bundle_root=root,
        mtime_ns=stat.st_mtime_ns,
        size=len(content),
        sha256=sha256(content).hexdigest(),
        frontmatter=_freeze_value(document.frontmatter),
        stable_id=stable_id,
    )
    object.__setattr__(entry, "_content_cache", markdown)
    object.__setattr__(entry, "_body_cache", document.body)
    return entry, problem


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

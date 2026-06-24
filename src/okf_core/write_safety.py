"""Write preconditions for OKF bundles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError
from okf_core.index import declared_okf_version
from okf_core.versions import OkfVersionError, validate_supported_okf_version


@dataclass(frozen=True)
class BundleWriteSafetyProblem:
    """A reason writes should not modify a bundle."""

    path: Path
    message: str


def check_bundle_write_safety(bundle: BundleConfig) -> BundleWriteSafetyProblem | None:
    """Return a problem when bundle-level metadata makes writes unsafe."""

    index_path = bundle.bundle_root / "index.md"
    if not index_path.is_file():
        return None

    try:
        content = index_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return BundleWriteSafetyProblem(
            path=index_path,
            message=(
                f"Refusing to write under bundle root {bundle.bundle_root}: "
                f"could not decode bundle root index.md as UTF-8: {exc}"
            ),
        )
    except OSError as exc:
        return BundleWriteSafetyProblem(
            path=index_path,
            message=(
                f"Refusing to write under bundle root {bundle.bundle_root}: "
                f"could not read bundle root index.md: {exc}"
            ),
        )

    try:
        version = declared_okf_version(content)
    except DocumentParseError as exc:
        return BundleWriteSafetyProblem(
            path=index_path,
            message=(
                f"Refusing to write under bundle root {bundle.bundle_root}: "
                f"could not parse bundle root index.md frontmatter: {exc}"
            ),
        )
    except OkfVersionError as exc:
        return BundleWriteSafetyProblem(
            path=index_path,
            message=(
                f"Refusing to write under bundle root {bundle.bundle_root}: "
                f"invalid bundle root okf_version declaration: {exc}"
            ),
        )

    if version is None:
        return None

    try:
        validate_supported_okf_version(version)
    except OkfVersionError as exc:
        return BundleWriteSafetyProblem(
            path=index_path,
            message=(
                f"Refusing to write under bundle root {bundle.bundle_root}: "
                f"unsupported bundle root okf_version declaration: {exc}"
            ),
        )

    return None

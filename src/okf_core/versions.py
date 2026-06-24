"""OKF version parsing and support checks."""

from __future__ import annotations

import re

SUPPORTED_OKF_VERSION = (0, 1)

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class OkfVersionError(ValueError):
    """Raised when an OKF version string is invalid or unsupported."""


def parse_okf_version(version: str) -> tuple[int, int]:
    """Parse an OKF ``major.minor`` version string."""

    match = _VERSION_RE.fullmatch(version)
    if match is None:
        raise OkfVersionError(
            f"OKF version must use '<major>.<minor>' form, got {version!r}"
        )
    return int(match.group(1)), int(match.group(2))


def is_supported_okf_version(version: str) -> bool:
    """Return whether this package understands an OKF version."""

    try:
        return parse_okf_version(version) <= SUPPORTED_OKF_VERSION
    except OkfVersionError:
        return False


def validate_supported_okf_version(version: str) -> str:
    """Validate and return a configured OKF version string."""

    parsed_version = parse_okf_version(version)
    if parsed_version > SUPPORTED_OKF_VERSION:
        supported = ".".join(str(part) for part in SUPPORTED_OKF_VERSION)
        raise OkfVersionError(
            f"OKF version {version!r} is newer than supported version {supported!r}"
        )
    return version


def normalize_okf_version_declaration(value: object) -> str:
    """Normalize a bundle-declared OKF version value from YAML frontmatter."""

    if isinstance(value, str):
        return value
    raise OkfVersionError(
        "OKF version declarations must be quoted strings in root index.md "
        f"frontmatter, got {type(value).__name__}"
    )

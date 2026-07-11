"""Spike for issue #117: same merge contract, ruamel.yaml as the editing core.

Not production code. Reuses the existing infra (_plan_document_change, the
mutation-value validator, the no-op equality check) so the only thing under
test is whether ruamel.yaml's round-trip loader/dumper can replace the
hand-rolled node-mark span-splicing in patching.py.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError, parse_concept_document
from okf_core.patching import (
    DocumentChangePlan,
    DocumentChangePlanningError,
    _MISSING,
    _alias_linked_keys,
    _compose_frontmatter,
    _first_line_ending,
    _frontmatter_bounds,
    _plan_document_change,
    _validate_frontmatter_update_value,
    _yaml_values_equal,
)

_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True
_yaml.width = 10_000


def plan_frontmatter_merge_ruamel(
    bundle: BundleConfig,
    path: Path | str,
    updates: Mapping[str, Any],
) -> DocumentChangePlan:
    return _plan_document_change(
        bundle,
        Path(path),
        lambda resolved_path, original_content: _merge_frontmatter_ruamel(
            resolved_path, original_content, updates
        ),
    )


def _merge_frontmatter_ruamel(
    path: Path, content: str, updates: Mapping[str, Any]
) -> str:
    if not isinstance(updates, Mapping):
        raise DocumentChangePlanningError(path, "Frontmatter updates must be a mapping")
    update_items = tuple(updates.items())
    seen_containers: set[int] = set()
    for key, value in update_items:
        if (
            not isinstance(key, str)
            or not key
            or key != key.strip()
            or "\n" in key
            or "\r" in key
        ):
            raise DocumentChangePlanningError(
                path,
                "Frontmatter update keys must be single-line strings without "
                "leading or trailing whitespace",
            )
        _validate_frontmatter_update_value(path, value, seen_containers)

    if not update_items:
        return content

    try:
        document = parse_concept_document(content)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not parse document frontmatter: {exc}"
        ) from exc

    bounds = _frontmatter_bounds(content)
    line_ending = _first_line_ending(content)

    if bounds is None:
        data = {key: value for key, value in update_items}
        merged_yaml = _dump(data).replace("\n", line_ending)
        proposed = f"---{line_ending}{merged_yaml}---{line_ending}{content}"
        _validate(path, proposed)
        return proposed

    yaml_start, yaml_end = bounds
    yaml_source = content[yaml_start:yaml_end]
    root = _compose_frontmatter(path, yaml_source)
    alias_linked_keys = _alias_linked_keys(root)
    try:
        data = _yaml.load(yaml_source)
    except Exception as exc:  # ruamel raises its own error hierarchy
        raise DocumentChangePlanningError(
            path, f"Could not compose document frontmatter: {exc}"
        ) from exc

    if data is None:
        data = {}

    changed = False
    for key, value in update_items:
        current = document.frontmatter.get(key, _MISSING)
        if current is not _MISSING and _yaml_values_equal(current, value):
            continue
        if current is not _MISSING and key in alias_linked_keys:
            raise DocumentChangePlanningError(
                path,
                f"Frontmatter field {key!r} is a YAML alias and cannot be changed",
            )
        data[key] = value
        changed = True

    if not changed:
        return content

    merged_yaml = _dump(data).replace("\n", line_ending)
    proposed = f"{content[:yaml_start]}{merged_yaml}{content[yaml_end:]}"
    _validate(path, proposed)
    return proposed


def _dump(data: Any) -> str:
    buf = io.StringIO()
    _yaml.dump(data, buf)
    return buf.getvalue()


def _validate(path: Path, proposed: str) -> None:
    try:
        document = parse_concept_document(proposed)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Merged frontmatter is invalid: {exc}"
        ) from exc
    if not isinstance(document.frontmatter, dict):
        raise DocumentChangePlanningError(path, "Merged frontmatter is not a mapping")

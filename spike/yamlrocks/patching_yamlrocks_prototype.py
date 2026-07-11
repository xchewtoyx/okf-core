"""Issue #117 spike: use YAMLRocks as the frontmatter editing core.

This module is deliberately isolated from the package. It reuses okf-core's
planning, mutation-policy, no-op, and post-validation machinery so the
experiment measures only the YAML editing engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yamlrocks

from okf_core.config import BundleConfig
from okf_core.documents import DocumentParseError, parse_concept_document
from okf_core.patching import (
    DocumentChangePlan,
    DocumentChangePlanningError,
    _MISSING,
    _first_line_ending,
    _frontmatter_bounds,
    _plan_document_change,
    _validate_frontmatter_update_value,
    _validate_merged_frontmatter,
    _yaml_values_equal,
)

_ROUND_TRIP_OPTIONS = yamlrocks.OPT_ROUND_TRIP | yamlrocks.OPT_DUPLICATE_KEYS_ERROR


def plan_frontmatter_merge_yamlrocks(
    bundle: BundleConfig,
    path: Path | str,
    updates: Mapping[str, Any],
) -> DocumentChangePlan:
    """Plan a merge using YAMLRocks without changing production behavior."""

    return _plan_document_change(
        bundle,
        Path(path),
        lambda resolved_path, original_content: _merge_frontmatter_yamlrocks(
            resolved_path,
            original_content,
            updates,
        ),
    )


def _merge_frontmatter_yamlrocks(
    path: Path,
    content: str,
    updates: Mapping[str, Any],
) -> str:
    if not isinstance(updates, Mapping):
        raise DocumentChangePlanningError(path, "Frontmatter updates must be a mapping")

    update_items = _validated_update_items(path, updates)
    if not update_items:
        return content
    return _merge_validated_frontmatter(path, content, update_items)


def _validated_update_items(
    path: Path,
    updates: Mapping[str, Any],
) -> tuple[tuple[str, Any], ...]:
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
    return update_items


def _merge_validated_frontmatter(
    path: Path,
    content: str,
    update_items: tuple[tuple[str, Any], ...],
) -> str:
    try:
        document = parse_concept_document(content)
    except DocumentParseError as exc:
        raise DocumentChangePlanningError(
            path, f"Could not parse document frontmatter: {exc}"
        ) from exc

    bounds = _frontmatter_bounds(content)
    line_ending = _first_line_ending(content)
    if bounds is None:
        generated = _dump_new_mapping(dict(update_items), line_ending)
        proposed = f"---{line_ending}{generated}---{line_ending}{content}"
        _validate_merged_frontmatter(path, proposed)
        return proposed

    yaml_start, yaml_end = bounds
    yaml_source = content[yaml_start:yaml_end]

    try:
        data = yamlrocks.loads(
            yaml_source.encode("utf-8"),
            option=_ROUND_TRIP_OPTIONS,
        )
    except Exception as exc:
        raise DocumentChangePlanningError(
            path, f"Could not compose document frontmatter: {exc}"
        ) from exc

    if not yaml_source.strip():
        generated = _dump_new_mapping(dict(update_items), line_ending)
        proposed = f"{content[:yaml_start]}{generated}{content[yaml_end:]}"
        _validate_merged_frontmatter(path, proposed)
        return proposed

    changed = False
    for key, value in update_items:
        current = document.frontmatter.get(key, _MISSING)
        if current is not _MISSING and _yaml_values_equal(current, value):
            continue
        if current is not _MISSING and _is_alias_linked(data, key):
            raise DocumentChangePlanningError(
                path,
                f"Frontmatter field {key!r} is a YAML alias and cannot be changed",
            )
        try:
            data[key] = value
        except (TypeError, ValueError) as exc:
            raise DocumentChangePlanningError(
                path,
                f"Frontmatter value cannot be represented as safe YAML: {exc}",
            ) from exc
        changed = True

    if not changed:
        return content

    try:
        merged_yaml = data.to_yaml().decode("utf-8")
    except Exception as exc:
        raise DocumentChangePlanningError(
            path, f"Frontmatter updates cannot be represented as safe YAML: {exc}"
        ) from exc

    proposed = f"{content[:yaml_start]}{merged_yaml}{content[yaml_end:]}"
    _validate_merged_frontmatter(path, proposed)
    return proposed


def _is_alias_linked(data: Any, key: str) -> bool:
    node = data.node[key]
    return node.is_alias or bool(node.aliases)


def _dump_new_mapping(data: dict[str, Any], line_ending: str) -> str:
    try:
        dumped = yamlrocks.dumps(
            data,
            option=yamlrocks.OPT_NULL_AS_KEYWORD,
            width=10_000,
        ).decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Could not serialize frontmatter: {exc}") from exc
    return dumped.replace("\n", line_ending)

"""Issue #50 probe for YAMLRocks' built-in JSON Schema validation.

The schema is intentionally an internal experiment, not a proposal for the
public ``_schema.yml`` format. Profile/schema precedence and warning policy
remain application responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yamlrocks

TYPE_FIELD_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "type": {"type": "string", "minLength": 1},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "resource": {},
        "tags": {"type": "array"},
        "timestamp": {},
        "platform": {"type": "string", "minLength": 1},
    },
    "required": ["type"],
    "oneOf": [
        {
            "properties": {
                "type": {"const": "platform-implementation"},
                "platform": {"type": "string", "minLength": 1},
            },
            "required": ["type", "platform"],
        },
        {
            "properties": {
                "type": {"not": {"const": "platform-implementation"}},
            },
            "required": ["type"],
        },
    ],
}


@dataclass(frozen=True)
class SchemaProbeResult:
    """Normalized evidence available from one YAMLRocks schema failure."""

    valid: bool
    message: str | None = None
    field_path: str | None = None
    line: int | None = None
    column: int | None = None


def validate_type_fields(
    source: bytes,
    schema: dict[str, Any] = TYPE_FIELD_SCHEMA,
) -> SchemaProbeResult:
    """Validate once and expose YAMLRocks' structured diagnostic fields."""

    try:
        yamlrocks.loads(source, schema=schema)
    except yamlrocks.YAMLRocksSchemaError as exc:
        return SchemaProbeResult(
            valid=False,
            message=exc.message,
            field_path=exc.schema_path,
            line=exc.line,
            column=exc.column,
        )
    return SchemaProbeResult(valid=True)

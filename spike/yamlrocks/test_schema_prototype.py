from __future__ import annotations

import yamlrocks

from spike.yamlrocks.schema_prototype import (
    TYPE_FIELD_SCHEMA,
    validate_type_fields,
)


def test_platform_implementation_requires_platform() -> None:
    result = validate_type_fields(b"type: platform-implementation\n")

    assert not result.valid
    assert result.field_path == "$"
    assert "oneOf" in (result.message or "")
    assert "platform" not in (result.message or "")
    assert (result.line, result.column) == (1, 1)


def test_platform_implementation_accepts_non_empty_platform() -> None:
    result = validate_type_fields(b"type: platform-implementation\nplatform: core\n")

    assert result.valid


def test_other_type_does_not_require_platform() -> None:
    assert validate_type_fields(b"type: concept\n").valid


def test_platform_must_be_non_empty() -> None:
    result = validate_type_fields(b"type: platform-implementation\nplatform: ''\n")

    assert not result.valid
    assert result.field_path == "$.platform"
    assert "minLength" in (result.message or "")


def test_unknown_fields_remain_permitted() -> None:
    result = validate_type_fields(b"type: concept\nproducer_extension: true\n")

    assert result.valid


def test_error_exposes_only_first_schema_failure() -> None:
    result = validate_type_fields(
        b"type: platform-implementation\nplatform: ''\ntags: wrong\n"
    )

    assert not result.valid
    assert result.field_path in {"$.platform", "$.tags"}


def test_field_path_is_not_the_missing_property_path() -> None:
    result = validate_type_fields(b"type: platform-implementation\n")

    assert result.field_path == "$"
    assert result.field_path != "$.platform"


def test_malformed_schema_is_currently_accepted_without_diagnostic() -> None:
    malformed = {"type": 42}

    assert yamlrocks.loads(b"type: concept\n", schema=malformed) == {"type": "concept"}


def test_if_then_conditionals_are_currently_ignored() -> None:
    conditional = {
        "if": {"properties": {"type": {"const": "platform-implementation"}}},
        "then": {"required": ["platform"]},
    }

    assert yamlrocks.loads(
        b"type: platform-implementation\n",
        schema=conditional,
    ) == {"type": "platform-implementation"}


def test_schema_is_local_and_has_no_remote_reference() -> None:
    encoded = repr(TYPE_FIELD_SCHEMA)

    assert "$ref" not in encoded

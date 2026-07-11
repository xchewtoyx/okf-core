from __future__ import annotations

import io

import pytest
import yamlrocks
from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError

YAMLROCKS_OPTIONS = yamlrocks.OPT_ROUND_TRIP | yamlrocks.OPT_DUPLICATE_KEYS_ERROR

RUAMEL = YAML(typ="rt")
RUAMEL.preserve_quotes = True
RUAMEL.width = 10_000


def _ruamel_edit(source: str, key: str, value: object) -> str:
    data = RUAMEL.load(source)
    data[key] = value
    buffer = io.StringIO()
    RUAMEL.dump(data, buffer)
    return buffer.getvalue()


def _yamlrocks_edit(source: bytes, key: str, value: object) -> bytes:
    data = yamlrocks.loads(source, option=YAMLROCKS_OPTIONS)
    data[key] = value
    return data.to_yaml()


def test_candidates_preserve_basic_comments_quotes_and_order() -> None:
    ruamel_source = (
        "# before\n"
        "type: concept # inline\n"
        'title: "Quoted"\n'
        "description: |\n"
        "  first\n"
        "  second\n"
    )
    yamlrocks_source = ruamel_source.encode()

    assert _ruamel_edit(ruamel_source, "type", "updated") == (
        "# before\n"
        "type: updated # inline\n"
        'title: "Quoted"\n'
        "description: |\n"
        "  first\n"
        "  second\n"
    )
    assert _yamlrocks_edit(yamlrocks_source, "type", "updated") == (
        b"# before\n"
        b"type: updated # inline\n"
        b'title: "Quoted"\n'
        b"description: |\n"
        b"  first\n"
        b"  second\n"
    )


def test_candidates_normalize_noncanonical_flow_spacing_after_edit() -> None:
    assert _ruamel_edit("type: concept\ntags: [one,two]\n", "type", "updated") == (
        "type: updated\ntags: [one, two]\n"
    )
    assert _yamlrocks_edit(b"type: concept\ntags: [one,two]\n", "type", "updated") == (
        b"type: updated\ntags: [one, two]\n"
    )


def test_candidates_do_not_preserve_crlf_after_edit() -> None:
    assert "\r" not in _ruamel_edit("type: concept\r\ntitle: old\r\n", "title", "new")
    assert (
        _yamlrocks_edit(b"type: concept\r\ntitle: old\r\n", "title", "new")
        == b"type: concept\ntitle: new\n"
    )


def test_candidates_reject_duplicate_keys_with_native_errors() -> None:
    with pytest.raises(DuplicateKeyError):
        RUAMEL.load("type: concept\ntitle: first\ntitle: second\n")
    with pytest.raises(yamlrocks.YAMLRocksDuplicateKeyError):
        yamlrocks.loads(
            b"type: concept\ntitle: first\ntitle: second\n",
            option=YAMLROCKS_OPTIONS,
        )


def test_yamlrocks_cannot_round_trip_assign_python_date() -> None:
    from datetime import date

    data = yamlrocks.loads(b"type: concept\nvalue: old\n", option=YAMLROCKS_OPTIONS)

    with pytest.raises(TypeError, match="datetime.date"):
        data["value"] = date(2026, 7, 4)

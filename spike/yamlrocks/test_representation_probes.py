from __future__ import annotations

import pytest
import yamlrocks

OPTIONS = yamlrocks.OPT_ROUND_TRIP | yamlrocks.OPT_DUPLICATE_KEYS_ERROR


def _edit(source: bytes, key: str, value: object) -> bytes:
    document = yamlrocks.loads(source, option=OPTIONS)
    document[key] = value
    return document.to_yaml()


@pytest.mark.parametrize(
    "source",
    [
        b'# before\ntype: concept # inline\ntitle: "Quoted"\n',
        b"type: concept\ntags: [one,two]\n",
        b"type: concept\ndescription: |\n  first\n  second\n",
        b"---\ntype: concept\n",
        b"type: concept\r\ntitle: old\r\n",
    ],
)
def test_unmodified_document_round_trips_byte_for_byte(source: bytes) -> None:
    document = yamlrocks.loads(source, option=OPTIONS)

    assert document.to_yaml() == source


def test_edit_preserves_comments_quotes_order_and_multiline_scalar() -> None:
    source = (
        b"# before\n"
        b"type: concept # inline\n"
        b'title: "Quoted"\n'
        b"description: |\n  first\n  second\n"
    )

    assert _edit(source, "type", "updated") == (
        b"# before\n"
        b"type: updated # inline\n"
        b'title: "Quoted"\n'
        b"description: |\n  first\n  second\n"
    )


def test_edit_normalizes_untargeted_flow_spacing() -> None:
    source = b"type: concept\ntags: [one,two]\n"

    assert _edit(source, "type", "updated") == (b"type: updated\ntags: [one, two]\n")


def test_edit_changes_crlf_frontmatter_to_lf() -> None:
    source = b"type: concept\r\ntitle: old\r\n"

    assert _edit(source, "title", "new") == b"type: concept\ntitle: new\n"


def test_mixed_line_endings_round_trip_until_an_edit() -> None:
    source = b"type: concept\r\ntitle: old\ncustom: keep\r\n"
    document = yamlrocks.loads(source, option=OPTIONS)

    assert document.to_yaml() == source
    document["title"] = "new"
    assert document.to_yaml() == b"type: concept\ntitle: new\ncustom: keep\n"


def test_edit_repositions_comment_on_implicit_null() -> None:
    source = b"type: concept\nowner: # keep\nnext: value\n"

    assert _edit(source, "owner", "team") == (
        b"type: concept\nowner: team\n# keep\nnext: value\n"
    )


def test_duplicate_key_is_rejected_with_location() -> None:
    with pytest.raises(
        yamlrocks.YAMLRocksDuplicateKeyError,
        match=r"line 3, column 1",
    ):
        yamlrocks.loads(
            b"type: concept\ntitle: first\ntitle: second\n",
            option=OPTIONS,
        )


def test_malformed_yaml_is_rejected_with_location() -> None:
    with pytest.raises(
        yamlrocks.YAMLRocksParseError,
        match=r"line 2, column 1",
    ):
        yamlrocks.loads(b"type: [broken\n", option=OPTIONS)


def test_yaml_12_is_default_and_yaml_11_is_opt_in() -> None:
    source = b"enabled: yes\nlegacy_octal: 012\n"

    assert yamlrocks.loads(source) == {
        "enabled": "yes",
        "legacy_octal": "012",
    }
    assert yamlrocks.loads(source, option=yamlrocks.OPT_YAML_1_1) == {
        "enabled": True,
        "legacy_octal": 10,
    }


def test_round_trip_document_exposes_alias_metadata() -> None:
    document = yamlrocks.loads(
        b"type: concept\na: &shared value\nb: *shared\n",
        option=OPTIONS,
    )

    assert set(document.anchors) == {"shared"}
    assert document.node["a"].aliases
    assert document.node["b"].is_alias


def test_library_allows_alias_linked_edit_and_preserves_alias() -> None:
    source = b"type: concept\na: &shared value\nb: *shared\n"

    assert _edit(source, "a", "updated") == (
        b"type: concept\na: &shared updated\nb: *shared\n"
    )

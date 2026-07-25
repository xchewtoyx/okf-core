"""Tests for .github/scripts/check_readme_exports.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load check_readme_exports.py from its non-package location
_SCRIPT = (
    Path(__file__).parent.parent / ".github" / "scripts" / "check_readme_exports.py"
)
_spec = importlib.util.spec_from_file_location("check_readme_exports", _SCRIPT)
assert _spec and _spec.loader
check_readme_exports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_readme_exports)  # type: ignore[union-attr]


_API_HEADER = "## Python Library API\n\n"
_NEXT_HEADER = "\n\n## Development Expectations\n"


def _wrap_in_api_section(body: str) -> str:
    return f"{_API_HEADER}{body}{_NEXT_HEADER}"


# ---------------------------------------------------------------------------
# extract_referenced_names
# ---------------------------------------------------------------------------


def test_extract_referenced_names_parenthesized_import_block() -> None:
    text = _wrap_in_api_section(
        "```python\n"
        "from okf_core import (\n"
        "    build_bundle_graph,\n"
        "    load_config,\n"
        ")\n"
        "```\n"
    )
    assert {
        "build_bundle_graph",
        "load_config",
    } <= check_readme_exports.extract_referenced_names(text)


def test_extract_referenced_names_single_line_import() -> None:
    text = _wrap_in_api_section(
        "```python\nfrom okf_core import list_concepts, load_config\n```\n"
    )
    assert {
        "list_concepts",
        "load_config",
    } <= check_readme_exports.extract_referenced_names(text)


def test_extract_referenced_names_dotted_reference() -> None:
    text = _wrap_in_api_section("See `okf_core.scan_bundle` for details.\n")
    assert "scan_bundle" in check_readme_exports.extract_referenced_names(text)


def test_extract_referenced_names_call_signature_inside_api_section() -> None:
    text = _wrap_in_api_section(
        "`plan_move_concept(bundle, old, new)` does the thing.\n"
    )
    assert "plan_move_concept" in check_readme_exports.extract_referenced_names(text)


def test_extract_referenced_names_call_signature_outside_api_section_ignored() -> None:
    text = (
        "## Command-Line Interface (CLI)\n\n"
        "`not_a_real_export(bundle)` is mentioned here.\n\n"
        "## Python Library API\n\n"
        "nothing relevant here\n\n"
        "## Development Expectations\n"
    )
    assert "not_a_real_export" not in check_readme_exports.extract_referenced_names(
        text
    )


@pytest.mark.parametrize("ignored_name", sorted(check_readme_exports._IGNORE_NAMES))
def test_extract_referenced_names_excludes_known_false_positives(
    ignored_name: str,
) -> None:
    text = _wrap_in_api_section(
        f"`{ignored_name}(bar)` appears in a call-signature span.\n"
    )
    assert ignored_name not in check_readme_exports.extract_referenced_names(text)


@pytest.mark.parametrize(
    "hook_name",
    [
        "okf_fetch_moved_concept_path",
        "okf_start_something",
        "okf_end_something",
        "okf_abort_something",
        "okf_enter_something",
        "okf_exit_something",
    ],
)
def test_extract_referenced_names_excludes_hook_shaped_names(hook_name: str) -> None:
    text = _wrap_in_api_section(
        f"`{hook_name}(dead_concept_id, bundle) -> Path | None` hook.\n"
    )
    assert hook_name not in check_readme_exports.extract_referenced_names(text)


def test_extract_referenced_names_ignores_non_python_fenced_block() -> None:
    text = _wrap_in_api_section("```bash\nokf validate --format text\n```\n")
    assert check_readme_exports.extract_referenced_names(text) == set()


# ---------------------------------------------------------------------------
# find_missing_exports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("referenced", "exported", "expected"),
    [
        (set(), set(), set()),
        ({"missing_fn"}, set(), {"missing_fn"}),
        ({"a", "b"}, {"a", "b", "c"}, set()),
    ],
    ids=["empty-diff", "one-missing-name", "referenced-subset-of-exported"],
)
def test_find_missing_exports(
    referenced: set[str], exported: set[str], expected: set[str]
) -> None:
    assert check_readme_exports.find_missing_exports(referenced, exported) == expected


# ---------------------------------------------------------------------------
# Integration: real README.md against real okf_core.__all__
# ---------------------------------------------------------------------------


def test_real_readme_has_no_missing_exports() -> None:
    import okf_core

    readme_path = Path(__file__).parent.parent / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8")

    referenced = check_readme_exports.extract_referenced_names(readme_text)
    missing = check_readme_exports.find_missing_exports(
        referenced, set(okf_core.__all__)
    )

    assert missing == set()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_reports_missing_exports_and_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A README reference to a name absent from okf_core.__all__ must fail
    the check: main() should print the missing name and return exit code 1.

    This is the regression the check exists to catch (PR #163's
    `plan_document_change_from_reader` bug) — without this test, main()
    could be changed to always return 0 and nothing in the suite would
    notice.
    """
    import okf_core

    (tmp_path / "README.md").write_text(
        "See `okf_core.definitely_not_exported` for details.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_readme_exports, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(okf_core, "__all__", [])

    result = check_readme_exports.main()

    captured = capsys.readouterr()
    assert result == 1
    assert "definitely_not_exported" in captured.out
    assert "not exported" in captured.out


def test_main_reports_no_missing_exports_and_returns_0(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When every README-referenced name is exported, main() should print
    the OK summary and return exit code 0."""
    import okf_core

    (tmp_path / "README.md").write_text(
        "See `okf_core.totally_exported` for details.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_readme_exports, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(okf_core, "__all__", ["totally_exported"])

    result = check_readme_exports.main()

    captured = capsys.readouterr()
    assert result == 0
    assert "OK: all 1 referenced okf_core name(s) are exported." in captured.out

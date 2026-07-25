#!/usr/bin/env python3
"""Check that every okf_core name referenced in README.md is actually exported.

Usage: check_readme_exports.py

Scans README.md for three kinds of okf_core name references:
  1. dotted `okf_core.NAME` prose references
  2. names imported in `from okf_core import (...)` code blocks
  3. call-signature spans (`` `name(` ``) inside the "## Python Library API"
     section

Each referenced name is compared against `okf_core.__all__`. Anything
referenced but not exported is reported and the script exits non-zero — this
is the regression check for the class of bug fixed by PR #163, where
`plan_document_change_from_reader` was documented in README.md but missing
from `__all__`, so importing it as documented raised `ImportError`.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_API_SECTION_HEADING = "## Python Library API"
_NEXT_HEADING_RE = re.compile(r"^## ", re.MULTILINE)

_DOTTED_REFERENCE_RE = re.compile(r"\bokf_core\.(\w+)")
_PYTHON_FENCE_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
_CALL_SIGNATURE_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)\(")

# pluggy hook names follow AGENTS.md's `okf_verb_noun` convention (e.g.
# `okf_fetch_moved_concept_path`); they are documented as hook signatures,
# not as okf_core exports, so a call-signature match on one is never a
# missing-export drift.
_HOOK_NAME_RE = re.compile(r"^okf_(start|end|abort|fetch|enter|exit)_")

# Known false positives for the call-signature heuristic that are not
# okf_core exports:
_IGNORE_NAMES = {
    # `foo(bar)baz.md` is a filename example illustrating balanced parens in
    # a Markdown link destination, not a call to a function named `foo`.
    "foo",
    # `build_proposed_content(resolved_path, original_content)` documents the
    # signature of a caller-supplied callback parameter to
    # `plan_document_change_from_reader`, not an okf_core export itself.
    "build_proposed_content",
}


def _api_section_text(readme_text: str) -> str:
    """Return the README slice from the API heading to the next '## ' heading."""
    start = readme_text.find(_API_SECTION_HEADING)
    if start == -1:
        return ""
    start += len(_API_SECTION_HEADING)
    match = _NEXT_HEADING_RE.search(readme_text, start)
    end = match.start() if match else len(readme_text)
    return readme_text[start:end]


def _names_from_python_imports(readme_text: str) -> set[str]:
    """Parse fenced ```python blocks and collect names imported from okf_core."""
    names: set[str] = set()
    for block in _PYTHON_FENCE_RE.findall(readme_text):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "okf_core":
                names.update(alias.name for alias in node.names)
    return names


def extract_referenced_names(readme_text: str) -> set[str]:
    """Collect every name README.md references as an okf_core export.

    Combines three sources: dotted `okf_core.NAME` prose references,
    names imported in `from okf_core import (...)` code blocks, and
    call-signature spans (`` `name( `` ``) restricted to the
    "## Python Library API" section. Names matching the `okf_verb_noun`
    pluggy hook convention and known non-export false positives
    (`_IGNORE_NAMES`) are excluded.
    """
    names: set[str] = set(_DOTTED_REFERENCE_RE.findall(readme_text))
    names.update(_names_from_python_imports(readme_text))

    api_section = _api_section_text(readme_text)
    names.update(_CALL_SIGNATURE_RE.findall(api_section))

    return {
        name
        for name in names
        if name not in _IGNORE_NAMES and not _HOOK_NAME_RE.match(name)
    }


def find_missing_exports(referenced: set[str], exported: set[str]) -> set[str]:
    """Return names README.md references that are not in okf_core.__all__."""
    return referenced - exported


def main() -> int:
    readme_path = _REPO_ROOT / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8")

    import okf_core

    exported = set(okf_core.__all__)
    referenced = extract_referenced_names(readme_text)
    missing = find_missing_exports(referenced, exported)

    if missing:
        print("README.md references okf_core names that are not exported:")
        for name in sorted(missing):
            print(f"  - {name}")
        print(
            "\nEither add these to okf_core.__all__ (and src/okf_core/__init__.py's "
            "imports), or fix the README reference if it was a mistake."
        )
        return 1

    print(f"OK: all {len(referenced)} referenced okf_core name(s) are exported.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

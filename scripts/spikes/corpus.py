"""Shared Markdown fidelity corpus for the #149 round-trip spike.

The samples mirror constructs exercised by the existing patching test suite
(``tests/test_section_patching.py``, ``tests/test_patching.py``,
``tests/test_link_rewrite_patching.py``) plus the OKF-specific citation shape
from OKF v0.1 section 8. They are the fixtures both strategy prototypes are
scored against, so the two harnesses report fidelity over identical inputs.

This module is spike scaffolding under ``scripts/spikes/``; it is intentionally
outside the ``okf_core`` package and is not imported by shipped code.
"""

from __future__ import annotations

# Each entry is (name, raw_markdown). Names describe the preservation hazard the
# sample probes, so a failing strategy points straight at the offending case.
CORPUS: tuple[tuple[str, str], ...] = (
    (
        "frontmatter-and-body",
        "---\n"
        "type: concept\n"
        "custom: keep\n"
        "---\n"
        "Introduction.\n"
        "\n"
        "## Target\n"
        "Old body.\n"
        "### Nested\n"
        "Old nested body.\n"
        "\n"
        "## Next\n"
        "Keep this.\n",
    ),
    (
        "asterisk-bullets",
        "# Heading\n" "\n" "* first\n" "* second\n" "  * nested\n" "* third\n",
    ),
    (
        "okf-citations",
        "# Concept\n"
        "\n"
        "Body prose referencing a source.\n"
        "\n"
        "# Citations\n"
        "\n"
        "1. [Title of Work](https://example.com/a)\n"
        "2. [Second Source](https://example.com/b)\n",
    ),
    (
        "table",
        "# Data\n"
        "\n"
        "| Name  | Value |\n"
        "| ----- | ----- |\n"
        "| alpha | 1     |\n"
        "| beta  | 2     |\n",
    ),
    (
        "html-comment",
        "# Notes\n"
        "\n"
        "<!-- editorial note: do not remove -->\n"
        "\n"
        "Visible prose.\n",
    ),
    (
        "trailing-whitespace",
        "# Heading\n" "\n" "Line with two trailing spaces.  \n" "Next line.\n",
    ),
    (
        "crlf-line-endings",
        "# Existing\r\nBody.\r\n\r\n## Added\r\nNew body.\r\n",
    ),
    (
        "mixed-line-endings",
        "## Target\r\nFirst\nSecond\r\n## Next\r\nKeep.\r\n",
    ),
    (
        "setext-heading",
        "Target\n======\nOld body.\n\n# Next\nKeep this.\n",
    ),
    (
        "no-trailing-newline",
        "# Heading\n\nBody with no final newline.",
    ),
)

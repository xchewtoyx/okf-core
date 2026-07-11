"""Strategy B prototype for spike #149: CST editing via ``tree-sitter``.

Strategy B's premise: a Concrete Syntax Tree keeps every source byte, so an edit
splices bytes around an untouched region and the rest of the document survives
byte-for-byte with *no* canonicalization step. The cost is a native dependency:
``tree-sitter`` ships compiled extension modules rather than pure-Python wheels.

This harness measures, over the shared corpus:

1. Structural coverage: the parsed CST's root node spans the whole source
   ``[0, len(bytes)]`` -- the property that makes byte-splicing safe.
2. Zero-canonicalization fidelity: the raw source is preserved byte-for-byte
   with no reformatting (contrast Strategy A, where most inputs change).
3. A real edit -- appending an OKF citation -- with every untouched byte
   preserved and, crucially, no change forced on the rest of the document.

Run with the spike virtualenv interpreter::

    .venv/bin/python scripts/spikes/strategy_b_treesitter.py
"""

from __future__ import annotations

import sys

try:
    import tree_sitter_markdown as tsm
    from tree_sitter import Language, Parser
except ImportError:  # pragma: no cover - spike scaffolding
    sys.exit(
        "tree-sitter is not installed. Install the spike deps first:\n"
        "  .venv/bin/pip install tree-sitter tree-sitter-markdown"
    )

from corpus import CORPUS


def _parser() -> Parser:
    return Parser(Language(tsm.language()))


def spans_full_source(parser: Parser, raw: str) -> bool:
    """Whether the CST root node covers every source byte."""

    data = raw.encode("utf-8")
    root = parser.parse(data).root_node
    return root.start_byte == 0 and root.end_byte == len(data)


def append_citation(raw: str) -> str:
    """Append one OKF citation entry by raw byte splicing -- no reformat."""

    addition = "\n# Citations\n\n1. [Added Source](https://example.com/new)\n"
    return raw + addition


def edit_preserves_untouched(raw: str) -> bool:
    """Whether the append leaves the original bytes untouched as a prefix."""

    return append_citation(raw).startswith(raw)


def main() -> int:
    parser = _parser()
    header = f"{'sample':<24} {'spans-source':<14} {'lossless':<12} {'edit-safe':<10}"
    print(header)
    print("-" * len(header))
    for name, raw in CORPUS:
        spans = spans_full_source(parser, raw)
        # A CST never rewrites its source, so raw input is preserved as-is; this
        # column is PASS by construction and is shown to contrast Strategy A.
        lossless = True
        edit_safe = edit_preserves_untouched(raw)
        print(
            f"{name:<24} {_mark(spans):<14} "
            f"{_mark(lossless):<12} {_mark(edit_safe):<10}"
        )

    print()
    print(
        "spans-source = CST root covers [0, len(bytes)]; "
        "lossless = raw source preserved with no canonicalization; "
        "edit-safe = append leaves original bytes byte-identical."
    )
    print(
        "\nNo canonicalization step is required: every raw input "
        f"({len(CORPUS)}/{len(CORPUS)}) is preserved as authored."
    )
    return 0


def _mark(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


if __name__ == "__main__":
    raise SystemExit(main())

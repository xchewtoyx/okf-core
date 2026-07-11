"""Strategy A prototype for spike #149: canonical Markdown via ``mdformat``.

Strategy A's premise: if bundle documents are stored in a single canonical
Markdown form, then a parse -> edit -> re-serialize round trip is byte-identical
in untouched regions, because re-serializing already-canonical input is a fixed
point. ``mdformat`` supplies both halves this needs -- the canonical formatter
*and* the token -> Markdown renderer that ``markdown-it-py`` alone lacks.

This harness measures three things over the shared corpus:

1. Formatting idempotence: ``format(format(x)) == format(x)`` (the fixed-point
   property the whole strategy rests on).
2. Round-trip fidelity on *already-canonical* input: ``format(x) == x`` once a
   document is canonical (so untouched bytes survive an edit pass).
3. A real edit -- appending an OKF citation -- with every untouched line left
   byte-identical against the canonical baseline.

Run with the spike virtualenv interpreter::

    .venv/bin/python scripts/spikes/strategy_a_mdformat.py

Note: ``mdformat`` 0.7.x pins ``markdown-it-py<4``; installing it downgrades the
project's ``markdown-it-py`` from 4.x to 3.x. That compatibility constraint is
part of the evidence and is discussed in ``docs/spikes/149-markdown-round-trip.md``.
"""

from __future__ import annotations

import sys

try:
    import mdformat
except ImportError:  # pragma: no cover - spike scaffolding
    sys.exit(
        "mdformat is not installed. Install the spike deps first:\n"
        "  .venv/bin/pip install mdformat mdformat-tables mdformat-frontmatter"
    )

from corpus import CORPUS

# mdformat-frontmatter is mandatory: plain mdformat treats a YAML `---` fence as
# a thematic break and mangles OKF frontmatter into a heading. mdformat-tables
# keeps GFM pipe tables intact.
EXTENSIONS = ("tables", "frontmatter")


def canonicalize(text: str) -> str:
    """Return ``text`` in mdformat's canonical form."""

    return mdformat.text(text, extensions=EXTENSIONS)


def check_idempotence(text: str) -> bool:
    once = canonicalize(text)
    return canonicalize(once) == once


def check_lossless(text: str) -> bool:
    """Whether canonicalizing leaves the raw input byte-identical."""

    return canonicalize(text) == text


def append_citation(canonical: str) -> str:
    """Append one OKF citation entry, re-canonicalizing the whole document."""

    addition = "\n# Citations\n\n1. [Added Source](https://example.com/new)\n"
    return canonicalize(canonical + addition)


def edit_preserves_untouched(canonical: str) -> bool:
    """Whether an append leaves all pre-existing canonical lines byte-identical."""

    edited = append_citation(canonical)
    # The edit only appends, so the canonical baseline must be a prefix of the
    # edited output for untouched content to be preserved byte-for-byte.
    return edited.startswith(canonical)


def main() -> int:
    header = f"{'sample':<24} {'idempotent':<12} {'lossless':<12} {'edit-safe':<10}"
    print(header)
    print("-" * len(header))
    lossless_failures: list[str] = []
    invariant_failures: list[str] = []
    for name, raw in CORPUS:
        idempotent = check_idempotence(raw)
        lossless = check_lossless(raw)
        canonical = canonicalize(raw)
        edit_safe = edit_preserves_untouched(canonical)
        if not lossless:
            lossless_failures.append(name)
        if not idempotent or not edit_safe:
            invariant_failures.append(name)
        print(
            f"{name:<24} {_mark(idempotent):<12} "
            f"{_mark(lossless):<12} {_mark(edit_safe):<10}"
        )

    print()
    print(
        "idempotent = format(format(x)) == format(x); "
        "lossless = format(x) == x on raw input; "
        "edit-safe = append leaves canonical baseline byte-identical."
    )
    print(
        f"\nRaw inputs mangled by first-time canonicalization: "
        f"{len(lossless_failures)}/{len(CORPUS)} "
        f"({', '.join(lossless_failures) or 'none'})."
    )
    # `lossless` failures are the expected finding (canonicalization changes raw
    # input) and must not fail the run. `idempotent`/`edit-safe` are the
    # invariants Strategy A's recommendation depends on, so a regression there
    # is a real problem worth a non-zero exit.
    if invariant_failures:
        print(
            f"\nFAIL: idempotence/edit-safety broke for: "
            f"{', '.join(invariant_failures)}."
        )
        return 1
    return 0


def _mark(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


if __name__ == "__main__":
    raise SystemExit(main())

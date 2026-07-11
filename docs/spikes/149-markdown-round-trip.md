# Spike #149 — Formatting-preserving Markdown round-trip editing

- **Issue:** [#149](https://github.com/xchewtoyx/okf-core/issues/149) (`type:design`)
- **Epic:** [#11](https://github.com/xchewtoyx/okf-core/issues/11) safe patch operations
- **Blocks:** [#148](https://github.com/xchewtoyx/okf-core/issues/148) (round-trip refactor), which unblocks [#113](https://github.com/xchewtoyx/okf-core/issues/113) (citations) and [#61](https://github.com/xchewtoyx/okf-core/issues/61) (link suggestions)
- **Decision:** Adopt **Strategy A** — canonical Markdown via `mdformat`, edited through the `markdown-it-py` token stream already used by the core.

## Problem

Focused document edits (append a citation, insert a link suggestion) must leave
every untouched byte identical. Today the core does this with **line-offset string
splicing**: `patching.py` parses with `markdown-it-py`, reads a heading token's
`map` line range, and slices the source by byte offset
(`_patch_markdown_section`, `_append_markdown_section`, `_line_offsets`). This is
the fragile machinery #148 is chartered to replace with a robust round-trip
serializer. This spike chooses that serializer.

## Strategies evaluated

- **Strategy A — canonical representation via `okf format`.** Store bundle
  documents in one canonical Markdown form (produced by `mdformat`). Because
  re-serializing already-canonical input is a fixed point, parse → edit tokens →
  re-serialize is byte-identical in untouched regions. `markdown-it-py` is a
  parser only and has no Markdown renderer; `mdformat` supplies both the
  canonical formatter and the token→Markdown renderer
  (`mdformat.renderer.MDRenderer`), so this strategy is an extension of the stack
  already in the core, not a new one.
- **Strategy B — Concrete Syntax Tree via `tree-sitter`.** A CST retains every
  source byte, so an edit splices bytes around an untouched region with no
  canonicalization step. The cost is a native, compiled dependency.

Prototypes live at `scripts/spikes/strategy_a_mdformat.py` and
`scripts/spikes/strategy_b_treesitter.py`, scored against a shared fidelity
corpus (`scripts/spikes/corpus.py`) harvested from the existing patching test
suite: OKF citations, `*`/`-` bullets, nested lists, GFM tables, HTML comments,
trailing whitespace, CRLF and mixed line endings, setext headings, and a
frontmatter+body document.

## Evidence

Measured on this environment (Linux x86_64, CPython 3.11) with
`mdformat 0.7.22` (+ `mdformat-tables`, `mdformat-frontmatter`),
`tree-sitter 0.26.0`, `tree-sitter-markdown 0.5.1`.

| Axis | Strategy A (`mdformat`) | Strategy B (`tree-sitter`) |
| --- | --- | --- |
| Dependency footprint | **Pure-Python.** `mdformat`, `mdit-py-plugins`, `mdformat-{tables,frontmatter}`; reuses the existing `markdown-it-py`. No compiled artifacts. | **~3 MB native binaries.** `tree_sitter/_binding` (2.0 MB, CPython-version-specific `cp311`) + `tree_sitter_markdown/_binding` (942 KB, `abi3`). |
| Cross-platform install | Universal wheels; trivial on every OS/Python. | Needs a compiled wheel per (OS × arch × CPython) for the core binding; falls back to a C toolchain build where no wheel exists. |
| `markdown-it-py` compatibility | `mdformat` 0.7.x pins `markdown-it-py < 4`, forcing the project from 4.x down to 3.x. The full suite (**667/667**) still passes under 3.x, but the currently-allowed 4.x line is given up until `mdformat` supports it. | Independent of `markdown-it-py`. |
| Fidelity — raw input preserved | **2/10** samples unchanged. Canonicalization rewrites `*`→`-`, CRLF/mixed→LF, setext→ATX, tightens spacing, strips trailing whitespace, adds a final newline. Frontmatter is destroyed **without** `mdformat-frontmatter`; preserved as an opaque block **with** it. | **10/10** preserved byte-for-byte; no canonicalization. |
| Fidelity — edit on canonical input | **10/10** edit-safe; formatting is an idempotent fixed point (`format(format(x)) == format(x)`, 10/10). | **10/10** edit-safe; CST root spans full source in all cases. |
| Precondition | All bundle docs must be pre-formatted to canonical form (`okf format`). | None. |
| Fit with existing core | Extends the `markdown-it-py` token stack already in `patching.py`/`graph.py`/`index.py`. | Introduces a second, parallel parser paradigm. |

Reproduce with `.venv/bin/python scripts/spikes/strategy_a_mdformat.py` and
`.venv/bin/python scripts/spikes/strategy_b_treesitter.py`.

## Decision: Strategy A

Strategy B's byte-exact fidelity is real, but its cost is exactly the one the
spike was told to scrutinize: a **native binary with a per-Python-version wheel
matrix**. That is at odds with `AGENTS.md`'s direction to prefer well-supported,
broadly-installable libraries and to keep the core light — the constraint the
issue loosely called a "zero-dependency policy." (The project is not
zero-dependency; it ships `click`, `markdown-it-py`, `pydantic`, `PyYAML`. The
real axis is pure-Python cross-platform install ease, and on that axis B loses.)

Strategy A stays pure-Python, reuses the parser already in the core, and delivers
the property #113/#61 actually need: **untouched sections of an already-canonical
document survive an edit byte-for-byte** (edit-safe 10/10). Its price is that
documents must first be canonical — which is precisely the `okf format` helper the
issue anticipated — and a temporary `markdown-it-py < 4` pin.

### The one thing #148 must plan around

`mdformat` 0.7.x forces `markdown-it-py < 4`, but the project currently resolves
4.x. #148 must either pin `markdown-it-py >= 3, < 4` when it adopts `mdformat`, or
gate adoption on an `mdformat` release supporting 4.x. The suite passes under 3.x
today, so pinning is a safe interim path, but the pin change belongs in #148's
diff and must be called out in its PR.

## Proposed design (spike goal 3)

### `okf format` — separate follow-up issue (not in #148 scope)

- **Surface:** `okf format [PATHS…]` CLI plus a library helper, canonicalizing
  Markdown with `mdformat.text(..., extensions=("tables", "frontmatter"))`.
- **Canonical style:** `mdformat` defaults + those plugins — ATX headings, `-`
  bullets, LF endings, blank lines around block structures, single trailing
  newline.
- **Idempotence guarantee:** `format(format(x)) == format(x)` (verified 10/10).
- **`--check` mode:** mirror `black --check` so CI/validation can assert a bundle
  is already canonical without rewriting it — the precondition the patch API relies
  on.
- **Frontmatter boundary:** `mdformat-frontmatter` passes the YAML block through
  opaquely, so frontmatter canonicalization stays owned by the YAML spike (#117).
  The two spikes compose cleanly; neither reformats the other's territory.

### Markdown patch API for #148

Replace the line-splicing helpers with a token-stream pipeline:

1. **Assert canonical input.** Planning raises `DocumentChangePlanningError` if
   the document is not already canonical (`mdformat.text(doc) != doc`). This
   surfaces the precondition explicitly per `AGENTS.md` "never fail silently,"
   rather than silently reformatting a caller's document inside an edit.
2. **Parse** to `markdown-it-py` tokens (the existing `_MARKDOWN` instance).
3. **Manipulate** the token stream — locate or create the target section
   (`# Citations` for #113, `## See also` for #61) and splice in the new list-item
   tokens.
4. **Render** back with `mdformat.renderer.MDRenderer`.
5. Because the input was canonical, untouched regions render byte-identically.

This preserves the #110 plan/apply contract unchanged: planning still yields a
`DocumentChangePlan(original_content, proposed_content, …)` and `apply_document_change`
keeps its conflict/no-op semantics. The only new failure mode is the explicit
"document is not canonical" planning error, which `okf format` resolves.

## Follow-ups this spike defines

- **#148:** implement the Markdown engine above; add the `markdown-it-py < 4` pin;
  keep the #110 contract; land with the existing patching corpus green.
- **New issue:** build `okf format` (CLI + `--check`), documenting the canonical
  style and a one-time bundle migration.

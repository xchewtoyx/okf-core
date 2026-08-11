# ADR-0003: Attribution footnote label charset

- **Status:** ACCEPTED
- **Related:** `src/okf_core/attribution.py` (the module this ADR governs);
  issue #197.

## Context

`check_attribution_consistency` joins Markdown footnote labels in a concept
body (`[^label]` references and `[^label]:` definitions) against
`sources[].id` values in frontmatter. Extraction has to find every real
occurrence and must not be fooled by a `[^label]`-shaped run of characters
that lands inside a code span or code block, where it is literal text, not
footnote syntax.

Early designs iterated through several approaches that each closed one
CommonMark interaction while missing another:

- **Rule-disabling escalation.** The first extraction pass scanned each
  paragraph's already-parsed inline `text` children directly. This missed
  occurrences whenever an unrelated CommonMark rule consumed the raw
  `[^label]` text before it reached a plain `text` token — the block
  `reference` rule rewriting a real footnote definition into a resolved
  link, or the inline `link`/`image` rules consuming a
  `[^label](dest)`/`![^label](url)` shape outright. Disabling rules one at a
  time as each interaction surfaced did not generalize: nested content
  inside a label (an autolink or code span) still split a paragraph's text
  into non-adjacent `text` children that a per-child regex could not
  reassemble, so a real occurrence was silently lost.
- **Raw-source-scan-plus-position-relocation.** The next design abandoned
  token-child scanning for a regex scan over the raw document source, with a
  separate `markdown_it`-derived pass to locate every `code_inline` token's
  own raw byte range so matches inside it could be excluded. Locating that
  byte range by *re-deriving* it — walking only a paragraph's top-level
  children, then searching for the token's backtick delimiters — turned out
  to have its own multi-round history: nested tokens (an image's alt text)
  were missed by a top-level-only walk, and delimiter search could mis-pair
  against a stray backtick belonging to an unrelated construct (an HTML
  comment, an autolink URI, a link/image destination).
- **Content-validation matching.** Requiring a candidate backtick pair's
  normalized inner text to equal the target `code_inline` token's own
  `.content` before accepting it as that token's position closed every case
  found so far, but left one structural gap: a stray backtick pair
  elsewhere in the paragraph can coincidentally normalize to the same
  content as the real code span, matching the wrong span for exclusion
  purposes. Reproduced with this repo's own README, where
  `` `[^label]` `` appears as literal documentation text next to an
  unrelated real code span with identical content.

Every one of these approaches was, in the end, about the same thing:
locating a `code_inline` token's raw source position by re-deriving it
rather than asking `markdown_it` what it already knows — its own token
type. A step-back research spike (surveying `markdown-it-py`'s public API)
confirmed there is no position API that would let position-relocation
shortcut this reliably; every variant of "find where the code span sits in
the raw source" fights the tokenizer instead of using it.

## Decision

Stop relocating code spans. Instead:

1. **Restrict the footnote label charset to `[A-Za-z0-9-]+`** — letters,
   digits, and hyphens only. No character in this set can serve as a
   delimiter, opener, or closer for any active rule in this module's
   `MarkdownIt("commonmark")` configuration, so a label can never be split
   across a token boundary or partially consumed by an unrelated rule. See
   the module docstring in `src/okf_core/attribution.py` for the full
   per-rule derivation this decision depends on.
2. **Scan by token type, not raw position.** `extract_footnote_occurrences`
   parses once and walks each paragraph's inline children in document
   order, regex-matching only `text`-typed children. A `code_inline` token
   is never of type `text`, so it is excluded by construction — no byte
   range ever needs to be computed or relocated. The same holds for fenced
   and indented code blocks: they never enter the inline token stream at
   all.
3. **Images are out of scope entirely.** An image's alt text lives on a
   nested `image.children` list this module never walks; rather than give
   images their own child-tree walk, `image` tokens are never inspected for
   footnote syntax. A `sources[].id` cited only from image alt text
   surfaces as the advisory "unreferenced source" finding — expected, not a
   bug.

## Alternatives rejected

- **Raw-source-scan plus code-span position-relocation** (with or without
  the later content-validation refinement) — rejected because each
  refinement closed one CommonMark interaction while leaving another open;
  the failure mode is structural to re-deriving a token's position rather
  than reading its type, and the research spike found no `markdown-it-py`
  position API that would make relocation reliable instead of merely
  reproducing the same open-ended search.
- **Keep the unrestricted label charset and give images their own
  child-tree walk** — rejected as unnecessary scope: nothing in OKF v0.1
  §5.1 requires citing a source from image alt text, and adding a second
  child-tree walker reintroduces the same class of "which nested list did
  we forget to walk" bug this ADR's decision eliminates for code spans.

## Revisit trigger

If a real-world OKF document needs a footnote label with characters outside
`[A-Za-z0-9-]+`, or needs to cite a source via image alt-text, this
restriction should be revisited against actual demand rather than
hypothetical generality.

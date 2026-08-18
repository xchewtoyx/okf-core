# ADR-0002: Canonical serialization form

- **Status:** ACCEPTED (decision framework, the YAML-side choice, and —
  amended by issue #198 — the Markdown-side choice below).
- **Depends on:** ADR-0001 (supersedes the byte-identity contract that made
  this decision unnecessary before).
- **Related:** `[replan-requirements]`
  (R-C1/R-C2/R-C3), `docs/spikes/issue-117-yaml-frontmatter-editing.md`,
  `docs/spikes/149-markdown-round-trip.md`.

## Context

ADR-0001 retires byte-level preservation as the operative contract for
`okf-core`'s document writers. Something has to take its place, because
"no contract" is not an option: `plan_frontmatter_merge`,
`plan_markdown_section_patch`, and every other write primitive still need a
deterministic answer to "what bytes does this operation actually produce?"
— determinism is what makes diffs reviewable, no-op detection sound, and
corpus style uniform without an agent spending tokens on formatting
(`[replan-requirements]` R-C1
rationale).

`[replan-requirements] §4` (R-C) states the shape of that replacement
contract as requirements, not a specific library choice. This ADR is where
those requirements become a concrete decision for each format.

## Decision

### Framework (applies to both formats, per R-C1/R-C3)

1. **One documented output form per format.** Every write the library
   produces emits a single documented canonical form — not "however the
   current writer happens to render it," a form specified in this ADR (or
   its successor) that can be checked independently of any particular code
   path.
2. **serialize∘parse is identity on the data model.** Parsing the output of
   a serialize call yields the identical logical data model, for every
   conformant input.
3. **serialize is idempotent.** `serialize(parse(serialize(x))) ==
   serialize(x)` — re-serializing already-canonical output is a fixed
   point.
4. **The form is a one-way door.** Reverting a canonical form corpus-wide
   after documents have started converging to it is a mass change, not a
   config flip. Per R-C3's reversibility note, the form must therefore be
   documented *before* the first release that writes it, and changing it
   later requires a new recorded decision (a new or amending ADR) — not a
   silent renderer swap.
5. **Convergence is per-document, on first touch — never a precondition.**
   Per R-C2, every editing operation must succeed on a non-conformant but
   spec-conformant input document; no operation may require a corpus-wide
   reformat first, and no separate "canonicalize the bundle" step is ever a
   precondition of editing. The first edit of a non-canonical document may
   produce formatting churn in that edit's diff — accepted and documented
   as a one-time cost per R-C3, **never treated as a defect** to be
   engineered away. This is the same "canonical-input precondition" that
   disqualified `mdformat` under the old byte-identity framing (see
   ADR-0001's discussion of #148's second revisit criterion); under this
   framework it is the intended behavior.

### YAML side — FIRM

This decision is firm now because issue #195 needs it to proceed.

**Adopt `ruamel.yaml`** as the YAML frontmatter engine, per the mature
round-trip-library route evaluated in
`docs/spikes/issue-117-yaml-frontmatter-editing.md`'s evidence table. That
spike's comparison (current PyYAML span-splice vs. `ruamel.yaml` vs.
YAMLRocks) stands unchanged; only the scoring axis changes, per ADR-0001.
Under the old byte-identity axis, `ruamel.yaml`'s formatting normalization
was scored as a regression; under R-C1/R-C2, it is the desired convergence
behavior.

YAMLRocks is rejected for the same reasons the spike already found, both of
which are independent of the byte-identity question and therefore carry
over unchanged:

- **Python version floor.** YAMLRocks requires Python 3.12+; `okf-core`
  supports Python 3.10+ (`AGENTS.md` Developer Setup / runtime
  compatibility). `ruamel.yaml` supports the full current range.
- **`date`/`datetime` round-trip assignment.** YAMLRocks does not accept a
  Python `date`/`datetime` value assigned into a round-trip-loaded
  document, which `okf-core` requires for its accepted mutation value types
  (`plan_frontmatter_merge`'s documented `datetime.date`/`datetime.datetime`
  support). `ruamel.yaml` supports this.

**Documented canonical form's observable properties** (per the framework's
"one documented output form" requirement — this is the specification an
implementation is checked against, not a description of any particular
code path):

- **Key order preserved** (spec S1: unknown/untargeted keys preserved on
  round-trip; `ruamel.yaml`'s round-trip loader preserves mapping key
  order across load-mutate-dump).
- **Comment handling:** comments attached to preserved keys survive an
  edit that does not target them. A targeted key's own comment is not
  guaranteed to survive if the edit replaces that key's value outright, per
  `ruamel.yaml`'s round-trip semantics; this is a design detail for the
  follow-up implementation issue to specify precisely, not this ADR.
- **Block style:** frontmatter is emitted in block (not flow) style
  regardless of the input document's original style, per `ruamel.yaml`'s
  default round-trip dumper behavior — this is exactly the kind of
  one-time convergence churn R-C3 accepts.
- **Quoting policy:** scalar quoting follows `ruamel.yaml`'s round-trip
  dumper defaults; exact scalar type distinctions the project already
  documents as significant (a quoted date string vs. a YAML date; a boolean
  vs. an integer, per README's "Safe Document Changes" section) remain
  significant and are preserved as such.
- **Line endings:** frontmatter is emitted with LF, per the spike's
  recommended future contract (`docs/spikes/issue-117-yaml-frontmatter-editing.md`,
  "Recommended future contract"). This avoids fragile CRLF post-processing
  around comment tokens; a document's Markdown body line-ending handling is
  a separate, unaffected concern.

**Carried over unchanged from the current contract** (these are semantic
guarantees, not byte-formatting details, so ADR-0001's scoring-axis change
does not touch them):

- Value validation for accepted mutation types (`str`, `bool`, `int`,
  finite `float`, `None`, `datetime.date`, `datetime.datetime`, and nested
  plain lists/string-keyed dicts).
- Alias rejection: a targeted field participating in a YAML alias
  relationship is still rejected, because changing a shared node cannot
  preserve its semantics locally.
- Semantic (not byte) no-op detection: applying an update whose value is
  already present, at its already-present type, produces no write — per
  R-A4, no-op equality is semantic (same data model), not byte equality.

### Markdown side — FIRM (amended by issue #198)

**Adopt Strategy A (`mdformat`)** for the token-tree mutate-and-re-render
engine `plan_markdown_section_patch`/`plan_markdown_link_rewrite` use, per
`docs/spikes/149-markdown-round-trip.md`'s evidence — re-verified below
against R-C1/R-C2/R-C3 (not the retired byte-identity axis the spike was
originally scored on) rather than inherited unexamined. The spike's
Strategy-B (`tree-sitter`) alternative is re-rejected on the same grounds:
byte-exact fidelity is no longer the deciding property (Framework point 5),
so its native-binary dependency cost buys nothing this contract needs.

**Renderer:** `mdformat.renderer.MDRenderer`, fed tokens from the existing
`markdown-it-py` parser instance (`_MARKDOWN` in `patching.py`) rather than
`mdformat`'s own private parser-construction helpers — `MDRenderer.render(
tokens, options, env)` is `mdformat`'s own documented public entry point for
this exact "render an externally-produced token stream" use case, and
`mdformat.plugins.PARSER_EXTENSIONS` (also public) supplies the GFM-table
renderer functions `MDRenderer` has no built-in support for. This keeps the
engine's only markdown-it-py/mdformat coupling to public, documented API —
the property AC3 (`grep -r "rules_inline" src/` empty) checks for — unlike
the deleted inline/core rule instrumentation this replaces.

**Table support (AC1):** `mdformat-gfm`, using only its `"tables"` extension
entry point (`mdit.enable("table")` plus the matching renderer functions),
not its full `"gfm"` bundle — this adds GFM table parsing/rendering without
also pulling in strikethrough, autolinks, or task-list syntax AC1 never
asked for. The standalone `mdformat-tables` package was rejected: at
re-verification time its latest release (`1.0.0`) still declared
`mdformat>=0.7.5,<0.8.0`, forcing a downgrade to `mdformat` 0.7.x; the
`mdformat-gfm` package's `"tables"` extension entry point provides
identical table rendering (same `update_mdit`/`RENDERERS` shape) while
declaring `mdformat>=0.7.5` with no upper bound, so it resolves cleanly
against current `mdformat`. `mdformat-frontmatter` is not a dependency:
frontmatter is sliced off (`_split_frontmatter`) before the Markdown body
ever reaches the parser/renderer, so it never needs to pass through
`mdformat` at all — the same division of ownership the spike's "Frontmatter
boundary" note anticipated.

**`markdown-it-py` version pin: unchanged (no downgrade needed).** The
spike's "one thing #148 must plan around" flagged a `markdown-it-py < 4`
pin as the likely cost of adopting `mdformat`, because `mdformat` 0.7.x
(current at spike time) capped `markdown-it-py<4`. At #198's
re-verification, current `mdformat` (`1.0.0`) declares
`markdown-it-py<5,>=1` — the `<4` cap was lifted upstream between the spike
and this issue. `pyproject.toml`'s existing `markdown-it-py >= 3, < 5` pin
already accommodates this; installing `mdformat`/`mdformat-gfm` resolves
`markdown-it-py` to `4.x` (verified: `4.2.0`) under that same pin. No pin
change ships with #198.

**Observable canonical-form properties**, mirroring the YAML side's
"documented canonical form" structure:

- **Heading style:** always ATX on output. A Setext heading is still
  matched by its parsed content/level (locating a section doesn't require
  the document to already be canonical, per R-C2), but renders as ATX —
  `MDRenderer`'s own heading renderer has no Setext output form at all, so
  this isn't a policy choice this ADR could make differently.
- **Block spacing:** a blank line always separates a heading from its
  following block and between sibling block-level elements, per
  `mdformat`'s own defaults — regardless of the source document's spacing.
- **List markers:** `-` for the primary bullet list nesting level,
  alternating with `*` for consecutive sibling lists at the same level
  (`mdformat`'s own default disambiguation rule, `get_list_marker_type`).
- **Tables:** GFM pipe-table syntax, column-padded and delimiter-aligned per
  each column's declared alignment (`mdformat-gfm`'s `"tables"` extension
  default rendering).
- **Link destinations:** bracket-wrapped (`<...>`) only when the
  destination contains a space, parenthesis, or ASCII control character
  (`mdformat`'s `maybe_add_link_brackets`); otherwise unwrapped. A link
  title is always double-quoted on output regardless of the source's
  quote/paren title style. `plan_markdown_link_rewrite`'s `new_target` is
  normalized (`_normalize_target`, the same normalizer already used to
  *match* `old_target`) before being written into a `link_open` token's
  `href` — required for R-C1 idempotency: an unnormalized destination
  containing e.g. a literal `>` or non-ASCII character renders once as
  literal text but re-parses to a percent-encoded value, so a second
  render of the reparsed document would differ from the first (see
  `tests/test_markdown_canonical_roundtrip.py`).
- **Line endings:** always LF, mirroring the YAML side's own LF-always
  choice and for the same reason (avoids fragile CRLF post-processing) —
  the four line-ending bookkeeping helpers the old splice engine needed
  (`_first_line_ending`, `_normalize_line_endings`,
  `_count_trailing_line_endings`, `_ensure_structural_line_ending`) have no
  canonical-engine equivalent; `mdformat`'s renderer only ever emits LF.

**Section-body ambiguity guard (a new constraint the token-tree engine
requires that the old splice engine did not, since it never reparsed its
own output as tokens):** `plan_markdown_section_patch` rejects a
replacement `body` that itself parses to a heading at or above the target
section's own `level`
(`_reject_body_heading_at_or_above_level`) — discovered by this issue's own
`hypothesis` idempotency property test generating `body="#"`. Such a body
is structurally indistinguishable, on a later reparse, from the section's
own boundary (`_section_body_end`'s "next heading at or above this level
ends the section" rule, the same rule used to *locate* where a section
ends), which would silently duplicate content rather than converge. This is
a documented limitation of the operation, not a defect: surfaced at
planning time via `DocumentChangePlanningError`, per AGENTS.md's "surface
problems explicitly" rule, rather than producing ambiguous output.

**R-C2 "convergence, not precondition" applies identically to Markdown:**
per the Framework above, planning never requires a document to already be
canonical. A non-canonical but spec-conformant document (mixed CRLF,
Setext headings, tight block spacing, non-canonical table padding) is
accepted, and its first edit converges the *whole* Markdown body to
canonical form — not just the section/link an edit targets — the same
scope `_normalize_container_style` already applies on the YAML side. This
retires the spike-era design (`docs/spikes/149-markdown-round-trip.md`'s
"Proposed design" §1, "Assert canonical input... raises
`DocumentChangePlanningError` if the document is not already canonical")
of planning-time rejection of non-canonical input; that design predates
R-C2 and is superseded by it, not carried forward. A request that doesn't
actually change a section's/link's canonical content is still a no-op that
writes nothing (mirroring `plan_frontmatter_merge`'s no-op semantics), so
an untouched document's formatting is never churned by a call that
resolves to no real change.

## Alternatives rejected

- **Make both YAML and Markdown firm now, reusing #149's Strategy A
  recommendation directly.** Rejected: `[replan-requirements]`'s
  R-C3 reversibility note treats the canonical form as a one-way door
  specifically because changing it later requires a new recorded decision
  — that deliberation budget is exactly why #198 exists as a separate,
  scoped re-verification rather than an inherited conclusion. Firming up
  YAML now and leaving Markdown open is consistent with treating each
  format's one-way-door cost independently, per its own evidence.
- **Adopt YAMLRocks for its performance advantage.** Rejected per the
  spike's own finding: performance is not the deciding factor since
  frontmatter edits are file-at-a-time operations and all evaluated
  candidates are fast enough in absolute terms; the Python version floor
  and `date`/`datetime` assignment gaps are disqualifying regardless of
  speed.
- **Keep the current PyYAML span-splice engine for YAML too, pending
  #198's Markdown re-verification, so both formats change together.**
  Rejected: issue #195 needs the YAML decision now to proceed, and the
  YAML and Markdown choices are independent — nothing about #198's
  Markdown re-verification bears on whether `ruamel.yaml` is the right YAML
  engine. Blocking YAML on Markdown's timeline would cost real schedule for
  no coupling benefit.

## Revisit trigger

- **YAML side:** revisit only via a new recorded decision (per the
  framework's one-way-door rule) — e.g. a `ruamel.yaml` maintenance issue
  that makes it non-viable, or a future Python version floor change that
  reopens YAMLRocks' disqualification.
- **Markdown side:** now firm (issue #198). Revisit only via a new recorded
  decision (the same one-way-door rule as the YAML side) — e.g. an
  `mdformat`/`mdformat-gfm` maintenance issue that makes either non-viable,
  or a future `markdown-it-py` major-version bump `mdformat` doesn't follow
  (re-opening the version-pin question this ADR currently resolves as "no
  change needed").

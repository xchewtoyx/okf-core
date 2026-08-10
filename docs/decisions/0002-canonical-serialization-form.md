# ADR-0002: Canonical serialization form

- **Status:** ACCEPTED (decision framework, and the YAML-side choice below).
  PROPOSED (the Markdown-side choice) — not yet binding on implementation
  work; see "Markdown side" below.
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

`[replan-requirements]` §4 (R-C) states the shape of that replacement
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

### Markdown side — PROPOSED, not accepted

This ADR deliberately does **not** make a firm Markdown-side choice yet.
`docs/spikes/149-markdown-round-trip.md` recommended Strategy A
(`mdformat`) under the old byte-identity framing, including a temporary
`markdown-it-py < 4` pin to satisfy `mdformat` 0.7.x's dependency
constraint. Under R-C1/R-C2, `mdformat`'s canonical-input precondition is
no longer disqualifying (see Framework, point 5) — but that does not by
itself make Strategy A the right choice under the new contract, and the
renderer choice plus the markdown-it-py 4.x-vs-<4 pin question both need
re-verification against the actual R-C1/R-C2/R-C3 fit criteria, not
inherited from a spike run against a different requirement. Issue #198 is
scoped to re-verify `docs/spikes/149-markdown-round-trip.md`'s evidence
under the new contract and record a firm decision here (as an amendment to
this ADR or a superseding ADR).

A firm choice now would be premature: it would either re-litigate #198's
work before that issue runs, or lock in Strategy A's dependency pin without
having re-checked whether it's still the right tradeoff once the scoring
axis has changed. Until #198 lands, Markdown-side writes continue on the
existing engines described in ADR-0001; this is not a regression, since
ADR-0001 already documents those engines' byte-pinning tests as
characterization baselines rather than the contract implementors optimize
for going forward.

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
- **Markdown side:** resolves into a firm decision when issue #198 lands
  its re-verification. Until then, this section stays PROPOSED and does
  not bind implementation work.

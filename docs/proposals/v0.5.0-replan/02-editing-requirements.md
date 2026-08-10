# Draft — Requirements for editing & maintenance assistance in okf-core

- **Status:** draft 1 of the requirements document (document 2 of the v0.5.0
  re-plan set). Companion to `01-draft1-requirements-assessment.md`
  (diagnosis), `03-codebase-analysis.md` (current-state assessment), and
  `04-milestone-plan-draft2.md` (delivery plan).
- **Scope:** the editing/patching/maintenance surface of okf-core only. The
  read side (scan, validate, graph, search, context) is established and not
  re-litigated here.
- **Method:** requirements are derived from OKF v0.1/v0.2 and the observed
  behavior of the reference corpus (`xchewtoyx/rgh-sme`), and filtered
  through an explicit inclusion gate (§3). Every requirement carries a
  rationale and a fit criterion; a requirement without a testable fit
  criterion is not finished (Volere fit-criterion discipline, per
  `requirements-architecture/fit-criterion.md` in the reference corpus).
  This document states *what* and *how well* — never *how*.

## 1. Context: who edits this corpus, and how

The corpus okf-core serves is **written and read by LLM agents**. The
reference corpus's own rules state its "only reader is future agent
instances." OKF v0.2 assumes the same world: it justifies its data-model
choices with *"agents constantly rewrite these documents"* and makes
provenance/trust/lifecycle metadata first-class precisely because most
concepts are machine-generated.

Editing therefore happens in two distinct modes:

- **In-context editing.** An agent has a note open, decides its content
  should change, and rewrites it. The note is small (the corpus's
  atomicity rule guarantees this), the agent already holds it in context,
  and prose judgment is the whole task. Deterministic tooling adds nothing
  here except safety around the write.
- **Corpus maintenance.** Operations whose correctness depends on state
  *outside* the working context: link integrity across thousands of files,
  metadata conventions applied uniformly, format consistency, concurrent
  write safety, dedupe/drift detection. An LLM performing these burns
  tokens proportional to corpus size and applies conventions
  probabilistically — each individual agent decision is plausible while the
  cumulative corpus drifts (the decrementalism failure mode: each step is
  judged against the previous state, never against the original baseline;
  no single agent can see the cumulative delta).

**okf-core's niche is the second mode plus the safety envelope around the
first.** The dividing test, stated once and applied throughout:

> An operation belongs in the library when it is (a) mechanically
> decidable, and (b) either cheaper in tokens than doing it inline, or
> harmful when done inconsistently. An operation stays with the agent when
> it requires judgment about meaning. Where a library operation embeds a
> judgment point, the design must hand back to the agent explicitly rather
> than encode a guess (the hyperrationality boundary: know where analysis
> hands back to judgment).

## 2. What the spec actually requires of an editor

From OKF v0.1/v0.2, the binding constraints on any conformant editing
layer are few:

- **S1 — Key preservation.** "Consumers SHOULD preserve unknown keys when
  round-tripping" (§4.1, both versions). This is a *semantic* obligation:
  no data loss on fields the operation does not target.
- **S2 — Permissive consumption.** Malformed or unknown content must
  surface as structured problems, never as rejection of the bundle (§11 /
  v0.1 §9).
- **S3 — Stable semantic identity.** v0.2 attaches identity to keys
  (`sources[].id`, footnote labels, concept paths), not positions, because
  documents are constantly rewritten (§5.1). An editing layer must
  maintain these join keys, not byte positions.
- **S4 — v0.2 data model.** Provenance, trust, and lifecycle live in the
  `sources` / `generated` / `verified` / `status` / `stale_after`
  frontmatter families. The v0.1 body `# Citations` list is retired.

**Explicit non-requirement (the corrected framing):** byte-level
preservation of untouched regions appears nowhere in either spec version.
It is a *design decision* the project previously stated as a requirement
("add a caching layer"-style: a *how* masquerading as a *what*). The real
requirements behind it are S1 (no data loss) plus R-C1 below (stable,
reviewable diffs) — both satisfiable without byte identity.

## 3. The inclusion gate

Every requirement below passed, and every future editing requirement must
pass, this gate (adapted from the Quality Gateway and the
change-mechanism investment inequality in the reference corpus):

1. **Traceable rationale.** Which spec clause, corpus behavior, or
   observed failure does it serve? A requirement whose rationale is "a
   stakeholder wanted it" is gold plating — rejected back to its
   originator, not silently kept.
2. **Requirement, not design.** It states an outcome and a measure, not a
   mechanism. Anything that names a serializer, a diff granularity, or a
   parsing strategy is design and does not belong here.
3. **Fit criterion.** One observable pass/fail measure. If we cannot say
   what would falsify it, it is not a requirement yet.
4. **Worth mechanizing.** `N × (cost per occurrence done by an agent) ≥
   (cost to build) + N × (cost per occurrence via the library)` — with N,
   the predicted occurrence count, written down as a checkable assumption.
   Operations an agent performs rarely and cheaply inline fail this test
   and stay out.
5. **Reversibility class recorded.** Each requirement notes whether the
   operations it demands are two-way doors (cheap to undo — decide fast,
   iterate) or one-way doors (on-disk format, destructive normalization —
   deserve the deliberation budget).

## 4. Requirements

### R-A. The safety envelope (retained from #110 — unchanged in intent)

The plan/apply contract is the library's core change-engineering
mechanism: it is two-phase mutation (propose to an inspectable
intermediate, validate, then commit) applied to documents. Nothing in the
re-framing weakens it.

- **R-A1 — Inspectable proposal.** Any mutating operation can produce its
  full proposed outcome for inspection without touching the target.
  *Rationale:* judging a change against its actual output before it lands
  is the safety mechanism, not a UX nicety. *Fit:* for every mutating
  operation, a plan can be produced, examined, and discarded with zero
  filesystem effect.
- **R-A2 — Stale-input refusal.** Applying a plan against a target that
  changed since planning fails with a structured conflict; the target is
  never overwritten. *Fit:* concurrent-modification test produces a
  machine-readable conflict and an unchanged file.
- **R-A3 — Atomicity.** An interrupted application leaves the complete
  old content or the complete new content, never a torn file. *Fit:*
  fault-injection test on the write path.
- **R-A4 — Semantic no-op detection.** An operation whose outcome is
  semantically identical to the current state reports no-op and writes
  nothing. *Rationale:* in a git-backed corpus the history is provenance;
  churn commits are provenance pollution. Note the shift: no-op equality
  is *semantic* (same data model), not byte equality. *Fit:* re-applying
  any operation twice yields exactly one write.
- **R-A5 — Verifiable safety checks.** Every safety check above must be
  demonstrated capable of failing (fault-injected at least once in the
  test suite). A guard that has never failed is not evidence of anything
  (test-oracle self-validation; >90% of catastrophic failures in the OSDI
  study came from unexercised error paths). *Fit:* each R-A guard has a
  test that triggers it.

### R-B. Metadata operations (the v0.2 growth surface)

These are the highest-value deterministic edits for an agent-maintained
corpus: small, mechanical, convention-critical, and constantly needed.
LLMs get them *approximately* right, which is worse than wrong — malformed
timestamps, actor strings, or list/mapping shapes silently degrade the
trust model the metadata exists to provide.

- **R-B1 — Targeted frontmatter merge.** Set, update, or remove top-level
  frontmatter fields on a document while preserving all untargeted keys
  and their order (S1). *Fit:* after any merge, parsing the result yields
  the untargeted data model unchanged, plus exactly the requested edits.
- **R-B2 — Trust/lifecycle stamping.** First-class operations for the
  v0.2 families: record a `generated` event, append a `verified` event,
  set `status`, set `stale_after` — each validating the actor convention
  (`<producer>/<version>`, `human:<id>`, `process:<id>`), emitting
  spec-shaped ISO 8601 values, and normalizing the bare-mapping-vs-list
  forms the spec allows. *Rationale:* this is the exact class of edit the
  corpus needs on every curation pass, and the exact class LLMs format
  inconsistently. *Fit:* a stamped document validates against the §5
  shapes; property tests over generated inputs produce zero malformed
  stamps.
- **R-B3 — Source/provenance bookkeeping.** Add or update a `sources`
  entry keyed by stable `id`/`resource` identity: dedupe on identity
  (adding an already-present source is a no-op per R-A4), preserve
  existing entries and their order, keep `usage_window` semantics intact.
  *Rationale:* replaces the v0.1 `# Citations` requirement (#113) in
  v0.2 terms; identity-keyed dedupe is a join, and joins are library
  work. *Fit:* the #113 acceptance criteria restated over `sources`:
  add-new, add-duplicate-is-no-op, existing-entries-untouched.
- **R-B4 — Attribution consistency check.** Report, as structured
  problems: footnote labels in a body with no matching `sources[].id`,
  and `sources` entries with `id`s never referenced by a footnote (the
  latter advisory only — an unreferenced source is legal). *Rationale:*
  the label↔id join is the one body construct with cross-part semantics
  (S3); a broken join *fails silently* — the note still reads fine — and
  silent failures are precisely where deterministic checks belong
  (partial-verification allocation rule: spend checks on failures agents
  won't notice). *Fit:* seeded label/id mismatches are all reported with
  locations; clean documents report none.

### R-C. Deterministic output (replaces byte preservation)

- **R-C1 — Canonical, documented serialization.** Every write the library
  produces emits a single documented output form per format. Two
  semantically identical documents serialize identically; the
  serialization of any library output re-parses to the identical data
  model. *Rationale:* determinism is what makes diffs reviewable, no-op
  detection sound, and corpus style uniform without any agent spending
  tokens on formatting. *Fit:* serialize∘parse is the identity on the
  data model; serialize is idempotent (`serialize(parse(serialize(x))) ==
  serialize(x)`).
- **R-C2 — Convergence, not precondition.** The library accepts any
  conformant document as input regardless of formatting; an edit brings
  the *touched document* to canonical form as a side effect. No operation
  may require a corpus-wide reformat before it will run, and no separate
  "format the bundle first" step may be a precondition of editing.
  *Rationale:* migration by expand-and-contract — tolerant reading during
  the window, normalization arriving incrementally with ordinary edits,
  no big-bang cutover. *Fit:* every editing operation succeeds on a
  non-canonical but conformant input document.
- **R-C3 — Bounded normalization on first touch.** The first edit of a
  non-canonical document may produce formatting churn in its diff, once
  per document. This is an accepted and documented cost, not a defect.
  *Reversibility note:* the canonical form itself is a **one-way door in
  practice** (reverting it corpus-wide after convergence is a mass
  change) and therefore gets the deliberation budget: the form must be
  documented before the first release that writes it, and changing it
  later requires a recorded decision.

### R-D. Cross-file integrity (the widest blast radius)

Edits whose effect propagation extends beyond the edited file are the
strongest candidates for determinism: their verification surface is the
whole corpus, which no agent context can hold.

- **R-D1 — Move with link integrity.** Relocating a concept rewrites all
  inbound links across the bundle (existing `move_concept` capability —
  retained). *Fit:* post-move, zero links referencing the old path.
- **R-D2 — Broken-link reporting.** Structured reporting of links whose
  targets do not exist, tolerated per S2 (broken ≠ malformed — it may be
  not-yet-written knowledge; consumers MUST NOT reject). *Fit:* report
  matches a seeded ground truth exactly.
- **R-D3 — Near-duplicate candidate detection.** Deterministic
  identification of *candidate* duplicate concepts (the corpus rule is
  "one canonical note per concept per bundle"), handed to the agent for
  the merge judgment. This is the hand-back boundary in action: detection
  is mechanical (an agent cannot hold 3,000 notes in context to compare);
  merging is meaning-work. *Fit:* seeded near-duplicates appear in the
  candidate list; the operation never merges anything itself.

### R-E. Conformance & convention validation (write-time gate)

- **R-E1 — Per-type frontmatter profiles.** Validation can require or
  permit fields per concept `type` (existing issue #50 — passes the gate:
  the reference corpus already wants `required_frontmatter` per profile,
  and convention enforcement is uniformity work). *Fit:* per #50's
  criteria.
- **R-E2 — Change-shape guardrails.** A mutating operation reports, as an
  advisory problem on the plan, when its effect is anomalously large for
  its kind (e.g. a "targeted" merge that would rewrite most of the
  frontmatter, a move that rewrites links in an unexpectedly large share
  of the corpus). *Rationale:* change-size gating — raw change size is a
  cheap, semantics-free risk proxy, and "an unexpectedly huge diff is
  more likely a mistake than intent"; an agent will not notice this, a
  library will, every time. Advisory, not blocking: the agent (or its
  human) decides. *Fit:* seeded anomalous plans carry the advisory;
  normal-sized plans do not.

### R-F. Auditability

- **R-F1 — Attributable outcomes.** Every applied change reports what was
  done in structured form (operation, target, fields touched, no-op or
  applied, problems), suitable for the caller to log or commit-message.
  *Rationale:* deterministic operations must not become the unexamined
  green dashboard (automation bias: automated output is an assertion to
  check, not a fact); small attributable changes are what make git
  bisectable — six bundled 90%-safe changes are only ~53% safe as a batch,
  and attribution is what un-bundles them. *Fit:* every mutating API
  returns a structured result naming the change; nothing writes silently.

### R-G. Reserved-file structure maintenance

The spec's reserved files (`index.md` §8, `log.md` §9) are pure
*structure*: reverse chronology, date headings, entry line formats,
section grouping. Structure maintenance is the niche in its clearest
form — LLM-maintained hub files are observably incomplete, inconsistently
formatted, and accrete cruft, while the content decisions involved are
trivial. The division: **the agent supplies meaning; the library owns the
file.**

- **R-G1 — Structure-free log append.** An agent can record a log entry
  by supplying only content (and optionally a date and kind) — without
  reading the log, knowing its conventions, or maintaining its structure.
  The library locates or creates the correct date section, preserves
  reverse chronology, leaves all other entries semantically untouched,
  and emits the canonical form. *Rationale:* log.md grows without bound,
  so inline appending costs tokens proportional to corpus age for a task
  with zero judgment content; structure conformance is exactly what
  agents get inconsistently wrong. Gate 4 passes with growing margin
  over time. *Fit:* appending to any conformant (or absent) log yields a
  conformant log with the entry under the right date; property test:
  entries appended in arbitrary date order always yield reverse-
  chronological sections; the agent-supplied content round-trips.
- **R-G2 — Indexes are generated, never authored.** Where a consumer
  wants `index.md`, the library is the only writer: deterministic
  generation from frontmatter (existing capability), plus a validation
  check reporting drift between a committed index and the directory it
  describes — missing entries, entries for absent files, stale
  descriptions — as structured problems. *Rationale:* incompleteness and
  cruft in a hand-maintained index are silent failures (the corpus still
  reads fine); silent failures are where deterministic checks belong.
  Because generation is canonical, drift detection reduces to a semantic
  comparison against regeneration. *Fit:* seeded drift (added file,
  removed file, changed description) is reported; a freshly generated
  index reports clean.

## 5. Explicit non-requirements

Recorded so they cannot re-enter as assumptions (each failed gate check
noted):

- **N1 — Byte-level preservation of untouched regions.** Fails gate 1
  (no traceable spec/corpus rationale; supersedes note: the real needs
  are S1 + R-C1) and gate 2 (a design decision stated as a requirement).
- **N2 — Surgical body/section patching as a core primitive.** In-context
  agents rewrite whole notes; notes are atomic and small by corpus rule.
  Fails gate 4: N is low and the inline cost is near zero because the
  note is already in context. Body-level assistance is limited to R-B4's
  consistency *check*. (If a future consumer demonstrates a real N — e.g.
  templated edits across many files — that is a new gate evaluation with
  its own recorded rationale, likely satisfiable as read-modify-write of
  the whole body under R-A.)
- **N3 — LLM authorship of reserved files.** Inverted from earlier
  drafts: the exclusion is not the *features* (see R-G) but the *author*.
  Agents never hand-write or hand-edit `index.md`/`log.md` content
  structure; consumers that want neither file (the reference corpus bans
  both inside bundles as hub mechanisms) simply don't call R-G. What
  remains excluded is speculative hub tooling with no consumer demand —
  e.g. tag-view files, multi-level index rollups — pending a gate run.
- **N6 — Move-tracking resolver infrastructure ahead of demonstrated
  need.** The stable-id/tombstone/`id_history`/log-scan resolver chain
  (#128/#130/#131/#143/#144) is deferred, with the honest rationale: the
  reference corpus's flat-bundle rule is a deliberate guardrail against
  speculative taxonomy — the disease that causes later reorganization —
  so reorganization pressure is low *by design*, not by accident;
  `okf move` already repairs inbound links transactionally at move time,
  making post-hoc resolution a remedy for bypassing the sanctioned path;
  and the stable-id concept itself is an unproven premise the project
  owner is not yet convinced by. Two-way door: the hookspec has shipped,
  and a consumer without the flat guardrail re-runs the gate with a real
  N.
- **N4 — A better Markdown/YAML implementation than the ecosystem's.**
  Format parsing/serialization is commodity infrastructure with zero
  differentiating value ("should not exist" quadrant of build-vs-buy;
  the operative bias to guard against is not-invented-here). okf-core
  builds *OKF semantics* on mature format libraries and inherits their
  edge-case handling; the requirements above are deliberately satisfiable
  by any competent library-backed implementation.
- **N5 — Corpus-wide style enforcement as a library feature.** Formatting
  convergence arrives via R-C2/R-C3. A standalone formatter/linter for
  bundles is the consumer's choice (the reference corpus already runs
  Prettier); okf-core's obligation ends at deterministic output of its
  own writes.

## 6. Hand-back boundary (what stays with the agent)

For completeness, the judgment work the library must *never* absorb, and
where library operations must return control:

- What a note should say; whether a concept deserves a note; how to
  atomize (wiki-rules judgment).
- Whether two duplicate candidates (R-D3) are actually one concept, and
  what the merged note says.
- Whether an anomalous-size advisory (R-E2) reflects intent or error.
- Whether a broken link (R-D2) is a defect or not-yet-written knowledge.
- Trust judgments themselves: the library stamps `verified` records
  (R-B2); *deciding* something is verified is the actor's claim, and the
  library must never synthesize one.
- What a log entry *says* (R-G1): the library owns chronology, headings,
  and format; the sentence is the agent's.

## 7. Open questions for review

1. Does R-B2 belong in core or as the first exemplar of the hooks/plugin
   surface? (Gate 4's N is high for the reference corpus; other consumers
   unknown.)
2. R-D3's detection method (what "near-duplicate" means mechanically) is
   design, deferred — but the *requirement* needs a fit criterion
   threshold agreed during design review.
3. Is R-E2 wanted in v0.5.0 or parked? It passes the gate but nothing
   currently demands it; candidate for the plan's stretch tier.

# Draft — Codebase and delivery-process analysis before the editing redesign

- **Status:** document 3 of the v0.5.0 re-plan set. Companion to the
  requirements draft (`02-editing-requirements.md`); feeds the milestone
  plan (`04-milestone-plan-draft2.md`).
- **Method:** a full module inventory and concern-mapping of
  `src/okf_core/` and the agent instruction set, read against the
  software-design, change-engineering, and automation-engineering bundles
  of the reference corpus. Wiki concepts are cited as
  `bundle/note-slug` so findings carry their reasoning, not just a
  verdict.
- **Headline:** the library's safety machinery and result-type discipline
  are genuinely good and survive the redesign untouched. The debt is
  concentrated and nameable: one god module, nine sibling parsers, a test
  stratum that pins the invented byte-identity contract, and an agent
  instruction set that polices local quality while enforcing that same
  contract — with no mechanism routing agents to the decisions that could
  change it.

## 1. Inventory summary

9,218 lines in `src/okf_core` across 21 modules; 13,105 lines of tests.
Three modules exceed the 800-line flag threshold — `patching.py` (1,632),
`cli.py` (1,238), `logs.py` (1,199) — together 44% of the library.
`cli.py`'s size is benign (13 thin command adapters); the other two are
not (§3).

## 2. Positive patterns — keep these, and say so out loud

These are the load-bearing assets. The redesign must preserve them
*deliberately*, because they are exactly what a rewrite loses by accident.

1. **The plan/apply safety envelope** (`patching.py:79-238, 636-908,
   1385-1632`): frozen plans with SHA-256 baselines, hash recheck before
   `os.replace`, symlink/ancestor-swap detection, fsync'd atomic writes,
   no-op detection, and the TOCTOU-closing `plan_document_change_from_reader`
   variant — with a read-counting regression test. This is two-phase
   mutation done properly (`change-engineering/two-phase-mutation-testing`)
   and is fully content-agnostic: ~620 lines that do not care how the
   proposal was produced. It is the part of the current design the
   requirements doc (R-A) keeps verbatim.
2. **Frozen result dataclasses with explicit problems channels**
   (`BundleGraph.problems`, `ParsedLog.problems`, `GeneratedIndex.problems`)
   — the "surface problems explicitly" doctrine, applied consistently.
3. **Collector-loop / per-item-helper structure** (`graph.py:176-256`,
   `index.py:329-530`) and **thin CLI adapters** — both codified in
   AGENTS.md and visibly followed.
4. **Disciplined pluggy hook design** (`hooks.py`), including treating
   hook answers as untrusted input (`repair.py:53-88`).
5. **Fail-closed completeness guards**: moves and repair refuse to run on
   a partially-scanned bundle because backlink sets can't be trusted;
   `plan_log_concept_move` refuses to re-render a log that parsed with
   problems. Keep this exact posture.
6. **Idempotent multi-step apply** with resume-after-failure semantics
   (`moves.py:183-217`, `repair.py:223-231`).
7. **Hypothesis round-trip property testing** as a practice — the pattern
   transfers directly to a canonical serializer
   (`parse(serialize(x)) == x`).
8. **One in-repo proof of the target architecture already exists**:
   `logs.py`'s writer path is parse → mutate → `render_log` — canonical
   re-render, not splice. The codebase already contains the design the
   redesign moves everything else onto.

## 3. Smells — each with its name

### 3.1 `patching.py` is Divergent Change plus Special-General Mixture

The module changes for at least five unrelated reasons (safety envelope,
file moves, Markdown section splice, link rewrite, YAML merge) — the
textbook definition of `software-design/divergent-change`. The concern map
shows the deeper structure: a general-purpose change engine (the envelope)
welded to format-specialized splice machinery
(`software-design/special-general-mixture` — "once the general-purpose
core is separated out and given its own class, the rest of the design
tends to fall out naturally"). Measured: **~850 of 1,632 lines exist
solely to guarantee byte-identity of untouched regions**; ~620 are the
reusable envelope; ~160 are semantic edit logic worth carrying forward.

### 3.2 Nine parsers: Temporal Decomposition and Information Leakage

Nine distinct hand-rolled parsing/span implementations exist across six
modules — two frontmatter delimiter scans (`documents.py:64-78` vs
`patching.py:1215-1225`), a YAML node-span splicer, a Markdown section
splicer, an instrumented link-span capturer, a 450-line log token state
machine, a sibling index token walk, a bespoke inline-token renderer, and
a third link-extraction walk. Markdown-significant-character escaping
alone exists in **four** places with four different rules — one of which
(`logs.py:910-998`) rejects rather than escapes, with a 25-line docstring
explaining it can't escape *because the renderer doesn't re-escape*.

This is `software-design/temporal-decomposition` (reader and writer each
independently understanding the format — the note's canonical example)
compounded by `software-design/information-leakage`: one design decision
— "what is the OKF document format" — replicated nine times, so every
edge case is a format decision that must be re-fixed in N places. That is
the causal story for why the parsers "keep sprouting edge-case bugs," and
why the fix is *consolidation* (information hiding improved by making one
module larger), not another extraction. The bar from
`software-design/code-duplication-red-flag` applies: one parser is only an
improvement if its interface stays simple — not a mega-walker with a flag
per caller.

### 3.3 Commodity infrastructure in the "should not exist" quadrant

Markdown/YAML parsing is commodity + zero differentiating value —
`decision-alignment/build-vs-buy-decision-framework`'s "should not exist"
quadrant, sustained by the not-invented-here assumption that okf-core's
needs were too special for existing tools. The complexity audit question
from `requirements-architecture/essential-vs-accidental-complexity` —
"what requirement does this complexity serve, and is that traceable?" —
resolves against most of it: the traceable requirement (byte identity)
was itself invented (document 1). Fragility is concrete, not
theoretical: `patching.py:292-446` copies markdown-it-py's own inline
rule verbatim and imports symbols the code itself labels "undocumented
implementation details"; a behavioral upstream change would corrupt spans
*silently*. `_find_block_offset` locates content by `str.find` within a
line range — a heuristic that mislocates duplicated content.

### 3.4 The test suite pins the wrong contract

~40-45 tests (~1,000 lines), concentrated in `test_section_patching.py`
(~70%) and `test_frontmatter_patching.py` (~60%), assert full byte-exact
output — preserved comments, quote styles, CRLF, setext headings. Per
`evidence-verification/characterization-baseline-as-verification-oracle`,
these conflate two different claims: they are valid *drift* oracles
("has behavior changed?") but were written as *correctness* oracles
("bytes must not change"). They are the invented requirement, executable.
The refactor must treat them as characterization baselines
(`software-design/characterization-tests`): pin current behavior during
each extraction, then consciously retire the assertions that encode the
retired contract. The safety-envelope tests (`test_patching.py`,
`test_file_move_patching.py`, the CLI suite — ~85+ tests) assert semantic
behavior and survive untouched.

### 3.5 Secondary findings

- **Envelope bypasses:** `stable-id --write` does a bare `write_text`
  with no hash guard or atomic write (`cli.py:740-748`); index generation
  writes directly (`cli.py:167-168`, `moves.py:264-265`). Inconsistent
  with the library's own "all bundle writes go through plans" story —
  a conceptual-integrity crack worth closing in passing.
- **ADRs trapped in docstrings:** `logs.py` is ~35-40% docstring,
  including 25-55-line "why" essays that are review-loop rationale with
  no better home (see §4). They will mostly be deleted with the code they
  justify.
- **Stale self-reference:** `AGENTS.md:217` cites a `# noqa: C901` that
  no longer exists in src — the doc-consistency rules don't self-enforce
  on AGENTS.md.
- **Three-way contract duplication:** docstrings ↔ README (§Safe Document
  Changes, ~100 lines restating patching docstrings) ↔ tests. Every
  contract change edits three places.
- **Misplaced:** `compute_pagerank` lives in `graph.py`, used only by
  `listing.py`.

## 4. The agent instruction set: strong police, no map

**Verdict: the instruction files are excellent local-quality enforcement
and process plumbing, but provide almost no architectural golden path.
Direction is decided per-issue by whichever planner runs, and the
artifacts that could provide direction (the spike/decision docs) are
invisible to every agent role.**

Specifics:

- `milestone-planner.md` reads the issue, AGENTS.md, and "enough of the
  current source." It is never told `docs/spikes/` or `docs/proposals/`
  exist. It resolves open design questions unilaterally, one line each,
  with no obligation to consult precedent or record the resolution. Each
  planning run is therefore an independent architecture authority — the
  erosion engine described by
  `requirements-architecture/conceptual-integrity`: individually
  reasonable local decisions, each solving a familiar problem "slightly
  differently this time," with nothing checking globally. Nine parsers
  is what that erosion looks like after a year.
- **The instruction set enforces the invented contract.** The reviewer's
  TOCTOU checklist hard-codes the current `patching.py` function list;
  the approver's worked example criterion is literally "existing content
  stays byte-identical"; AGENTS.md mandates round-trip tests against the
  current renderer. Unless these are edited *first*, the delivery loop's
  own reviewers will generate `request_changes` spirals against the
  redesign — the loop will fight its own plan.
- **Failure demand is measurable and upstream.** ~30% of all commits are
  review-fix commits (`change-engineering/failure-demand`: rework is
  demand created by not doing it right first time; the lever is upstream
  quality, not faster rework). The loop has real intra-issue
  spiral-stoppers (5-round cap, bug-category circuit-breaker, restructure
  escalation) — but they reset per issue. Nothing accumulates the
  cross-issue signal "we have now point-fixed offset arithmetic in four
  different issues," which is precisely the recurring-cost evidence the
  previous descope decision said would justify revisiting.
- **What a golden path would add** (and the plan schedules):
  1. A decision-record home (`docs/decisions/` ADRs with status,
     rationale, alternatives, and a revisit trigger —
     `requirements-architecture/architectural-decision-capture`: "the
     rationale is usually the knowledge that goes missing first"), with
     the existing spike docs adopted into it.
  2. Routing: planner *must* read the requirements doc and scan ADRs
     before planning; reviewer checks divergence from ADRs, not just
     conventions — the "architectural reality check" from
     `requirements-architecture/architecture-codex`.
  3. A design-it-twice obligation in the plan step for any issue touching
     a public contract (`software-design/design-it-twice`: "an hour or
     two of comparative design work against days or weeks of
     implementation" — and against five review rounds).
  4. One sanctioned parse/serialize path, stated in AGENTS.md as the only
     way to touch document content
     (`automation-engineering/uniformity-as-automation-prerequisite`: a
     sufficiently good common path dissolves exceptions; the alternative
     is encoding every exception as branching logic — which is what the
     nine parsers are).
  5. AGENTS.md itself re-audited as a checklist
     (`automation-engineering/checklist-design-principles`): killer items
     within working-memory limits, not exhaustive procedure — "a
     checklist that tries to spell out the entire procedure turns human
     judgment off instead of reinforcing it."

## 5. Refactor readiness and sequencing constraints

The wiki is unambiguous that this must not be a binge:
`software-design/incremental-extraction-over-refactoring-binges` ("when
teams go on a large refactoring binge, system stability breaks down for a
little while, even if they are being careful") and
`software-design/preparatory-refactoring` (one deliberate catch-up effort
is legitimate for a team that has deferred refactoring — but the
sustainable mode is per-issue preparation). The sequencing below is a
strangler inside one codebase (`software-design/strangler-fig-pattern` /
branch-by-abstraction): each step ships green, each is independently
reviewable, and the old machinery is deleted only as its replacement
takes over its callers.

Dependency-ordered runway (detail feeds the milestone plan):

- **Step 0 — decision & instruction hygiene (blocks everything).**
  Supersede `docs/spikes/148-round-trip-refactor-descope.md` with an ADR
  answering its own revisit criteria; update `milestone-reviewer.md`,
  `milestone-approver.md`, `AGENTS.md`, and README's contract section so
  the loop stops enforcing byte-identity. Establish `docs/decisions/` and
  the planner/reviewer routing.
- **Step 1 — extract the safety envelope** into its own module. Zero
  behavior change, halves `patching.py`, decouples all later deletions.
- **Step 2 — frontmatter engine** (most self-contained): replace the
  ~330-line node-span splicer with parse → mutate → canonical dump. A
  canonical frontmatter serializer already exists one file away
  (`documents.serialize_concept_document`) — the codebase currently
  maintains *both* writers. Keep value validation and alias rejection.
  Characterization tests first; then retire ~18 byte-pinning tests
  consciously.
- **Step 3 — Markdown engine**: token-tree mutate + re-render for section
  and link operations; deletes the section splicer (~160 lines), the
  entire instrumented-rule cluster (~250 lines — removing the only
  markdown-it internals coupling), and the four line-ending helpers.
  Resolve the markdown-it-py version pin question flagged in the #149
  spike before committing to a renderer.
- **Step 4 — unify the parse layer**: one shared block-walk +
  re-escaping renderer replaces `_markdown_inline.py`, the log state
  machine's generic block plumbing, and the index sibling walk; the
  reject-don't-escape guards become real escaping once rendering
  re-escapes symmetrically.
- **Step 5 — consumers and docs**: `moves.py`/`repair.py` are API-stable
  throughout; loosen `test_moves.py`'s whole-file byte assertions to
  link-level assertions; rewrite README §Safe Document Changes once.

Net estimate: **−1,300 to −1,500 src lines, +300-450 new**, one new
serializer dependency; ~45 tests retired as contract change-detectors,
~35 loosened, ~85 safety/semantic tests plus the whole CLI suite
untouched. The two schedule risks are not code: the canonical-form policy
is a one-way door needing an explicit recorded decision
(`02-editing-requirements.md` R-C3), and step 0 — without it the
delivery loop fights the refactor.

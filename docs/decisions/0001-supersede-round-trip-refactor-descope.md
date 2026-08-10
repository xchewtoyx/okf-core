# ADR-0001: Supersede the round-trip refactor descope

- **Status:** ACCEPTED
- **Supersedes:** `docs/spikes/148-round-trip-refactor-descope.md` (the
  decision to close #148 as not planned and keep `patching.py`'s hand-rolled
  splice engines).
- **Related:** ADR-0002 (canonical serialization form); the v0.5.0 re-plan
  document set — `docs/proposals/v0.5.0-replan/01-draft1-requirements-assessment.md` (predates
  `references.md`'s registry; cited by bare path per the sanctioned
  exception recorded there), `[replan-requirements]`, `[replan-analysis]`,
  and `[replan-plan]`.

## Context

`docs/spikes/148-round-trip-refactor-descope.md` closed #148 as not planned.
Its reasoning rested on a single premise: byte-level preservation of
untouched regions is the contract the library must serve, and every
candidate replacement (`mdformat`, `ruamel.yaml`) regresses that contract
somewhere. That premise has since been re-examined directly.
`[replan-requirements]` (the requirements
draft for the v0.5.0 re-plan) traces every requirement back to a spec clause
or observed corpus behavior and finds no such clause: byte preservation of
untouched regions appears nowhere in OKF v0.1 or v0.2. It is listed there as
explicit non-requirement N1 — a design decision ("how") that had been stated
as a requirement ("what"), with no traceable rationale of its own. The real
requirements underneath it are S1 (key preservation on round-trip, an actual
spec clause) and R-C1 (deterministic, documented output per format) — both
satisfiable without byte identity.

`[replan-analysis]` (the companion
codebase analysis) independently quantifies the cost of keeping the
byte-identity contract: nine hand-rolled parsing/span implementations across
six modules, four inconsistent Markdown-escaping rules, and roughly 850 of
`patching.py`'s 1,632 lines existing solely to guarantee byte-identity of
untouched regions (`[replan-analysis] §3.1, §3.2`). Separately, the delivery loop's own
change-engineering metric shows ~30% of all commits across the milestone are
review-fix commits (`[replan-analysis] §4`) — evidence of real, ongoing rework cost, though not
yet attributed to this specific machinery (see below).

This ADR does not re-run #148's evidence gathering. The spike prototypes,
fidelity corpus results, and library comparisons in
`docs/spikes/148-round-trip-refactor-descope.md`,
`docs/spikes/149-markdown-round-trip.md`, and
`docs/spikes/issue-117-yaml-frontmatter-editing.md` stand unchanged. What
changed is the scoring axis those spikes were run against.

## Decision

Supersede #148's descope decision. The splice-preserving engines in
`patching.py` are no longer the target architecture. `okf-core` moves toward
one documented canonical output form per format (Markdown, YAML), with
edits applying as parse → mutate → canonical re-render — the direction
ADR-0002 makes concrete for YAML now and leaves PROPOSED for Markdown
pending issue #198.

This ADR exists specifically to answer #148's own stated revisit criteria,
not merely to overrule them.

### Revisit criterion 1: "recurring, attributable maintenance cost"

**Partially met, and the reason it was only partial is the actual finding.**
The evidence for recurring cost is real: nine hand-rolled parsers, four
inconsistent Markdown-escaping rules, and a ~30% review-fix commit rate (see
`[replan-analysis] §3.2` for the parser
count and escaping-rule count, and `[replan-analysis] §4` for the review-fix rate). But #148's
criterion assumed a mechanism that would *track* recurring cost across
issues so it could be recognized as recurring rather than re-litigated as a
fresh point-fix each time — and no such mechanism existed. The 30%
review-fix rate is real signal, but nothing before this ADR recorded *why*
each review-fix round happened, so there was no way to see the same
`bug_category` show up a third time and treat it as structural instead of
coincidental. This ADR installs that mechanism: the failure ledger
(`docs/decisions/failure-ledger.md`, task 4 of issue #193), which
`milestone-reviewer.md` now appends one line to per `request_changes` round,
with an explicit three-strikes rule for escalating a repeated
`bug_category` into a structural issue instead of a fourth point-fix. The
criterion is met going forward because the tracking now exists; it could
not have been fully met retroactively because the tracking never ran.

### Revisit criterion 2: "a library with no canonical-input precondition emerges"

**Moot — not satisfied, because the ground the criterion was standing on
moved, not the library market.** No new pure-Python Markdown library
without a canonical-input precondition has appeared since #148 closed.
What changed is that the criterion's premise no longer holds: it was framed
against byte preservation as the goal, and `mdformat`'s "canonical-input
precondition" was disqualifying only because it forced a class of documents
to be reformatted before an edit could touch them — a violation of a
byte-identity contract that, per this ADR's Context section, was never
actually required by the spec. Under R-C1 (canonical, documented
serialization) and R-C2 (convergence, not precondition — see
`[replan-requirements]`), a library that
canonicalizes on first touch is exhibiting the *desired* behavior, not a
defect: R-C2 requires exactly that a library accept any conformant document
regardless of formatting and bring the touched document to canonical form as
a side effect, with the resulting one-time formatting churn documented and
accepted (R-C3) rather than treated as a contract violation. `mdformat`'s
objection under the old framing dissolves under the new one. This criterion
is therefore not "met" in the sense #148 anticipated (a library emerged that
satisfies the old precondition-free requirement) — it is moot, because the
requirement it was testing for is no longer the one in force.

### The retired byte-identity test suite becomes a characterization baseline, not a correctness oracle

`patching.py`'s ~850 byte-identity-guaranteeing lines
(`[replan-analysis] §3.1`) and the
~40-45 tests (~1,000 lines, concentrated in `test_section_patching.py` and
`test_frontmatter_patching.py`) that assert full byte-exact output — see
`[replan-analysis] §3.4` — were written as *correctness* oracles ("bytes
must not change"). They are retired **deliberately**, not silently: per
`[replan-analysis] §3.4`'s framing, they become *characterization baselines* ("has behavior
changed from the pinned snapshot?") during each extraction step of the
refactor, then their byte-exactness assertions are consciously dropped once
each extraction step lands, rather than being carried forward as the
project's actual correctness contract. The distinction matters for anyone
touching this code during the refactor: a failing byte-pinning assertion
during Step 1-2 of the refactor (per `[replan-analysis] §5`'s dependency-ordered runway) is
expected drift being characterized, not a regression to fix by restoring
byte-identical output. See AGENTS.md's "characterization-first" rule (added
by issue #193) for the same principle applied prospectively to any future
parsing/serialization deletion.

## Alternatives rejected

- **Leave #148's descope decision standing and reopen a narrower refactor
  ad hoc when maintenance cost becomes undeniable.** Rejected: this is what
  #148's own revisit criteria already permitted, and it is exactly the
  failure mode described above — without a tracking mechanism, "undeniable"
  never arrives as a single event, it arrives as a slow accretion that no
  single planning pass can see. An ADR plus a failure ledger makes the
  threshold checkable instead of a judgment call repeated from scratch.
- **Re-run the #148/#149/#117 spikes from scratch under the new
  requirements framing.** Rejected: the spikes' evidence (fidelity
  measurements, dependency footprints, library comparisons) does not depend
  on which requirement it's being scored against — only the scoring axis
  changed. Re-running the same prototypes against the same corpus would
  reproduce the same numbers. ADR-0002 reuses that evidence directly rather
  than re-deriving it.
- **Supersede #148 silently by just proceeding with the refactor.**
  Rejected: `requirements-architecture/architectural-decision-capture`
  (cited in `[replan-analysis] §4`) is explicit that the rationale is
  the knowledge that goes missing first. A future planner re-reading
  `docs/spikes/148-round-trip-refactor-descope.md` without this ADR would
  see an accepted "not planned" decision and have to re-derive why it no
  longer holds.

## Revisit trigger

This ADR is itself revisited only if a future gate run
(`[replan-requirements] §3`, the
inclusion gate) finds that R-C1/R-C2/R-C3 no longer hold as the operative
requirements — for example, if a future consumer demonstrates a real need
for byte-level preservation that passes the gate on its own evidence, not
as a restatement of the original invented requirement. Absent that, this
ADR stands as the direction for the remainder of the v0.5.0 re-plan.

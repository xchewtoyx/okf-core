# Architecture Decision Records

This directory records the decisions that shape `okf-core`'s architecture —
the kind of choice that is expensive to re-litigate from scratch each time it
resurfaces in a new issue. It exists because the instruction set alone
(`AGENTS.md`, `.claude/agents/*.md`) enforces *how* to write code, but had no
place to record *why* a direction was chosen over its alternatives, or what
would have to change for that choice to be revisited. See
`docs/proposals/v0.5.0-replan/03-codebase-analysis.md` §4 for the analysis
that motivated this directory.

Every agent role planning or reviewing design/architecture work must read
this index and the ADRs it lists before starting. See `AGENTS.md`'s
"Decision Records" section and `.claude/agents/milestone-planner.md`.

## Format

Each ADR is a Markdown file named `NNNN-short-slug.md`, numbered sequentially.
An ADR has these sections:

- **Status** — one of `PROPOSED`, `ACCEPTED`, or `SUPERSEDED` (with a pointer
  to the ADR that supersedes it). A `PROPOSED` decision is not yet binding on
  implementation work; treat it as a documented direction under discussion,
  not a rule to enforce.
- **Context** — the problem, and the evidence available at decision time
  (spikes, codebase analysis, prior ADRs).
- **Decision** — the actual choice, stated concretely enough that an
  implementor doesn't have to re-derive it.
- **Alternatives rejected** — what else was considered and why it lost. A
  decision without a rejected alternative usually means the comparison never
  happened.
- **Revisit trigger** — the concrete condition that would justify reopening
  this decision. Not "if we feel like it later" — a checkable event or
  threshold.

## Index

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-supersede-round-trip-refactor-descope.md) | Supersede the round-trip refactor descope | ACCEPTED |
| [0002](0002-canonical-serialization-form.md) | Canonical serialization form | ACCEPTED (framework + YAML); PROPOSED (Markdown) |

## Registered spike-era decisions

These predate this directory and are adopted into it retroactively. They are
left in place under `docs/spikes/` (their evidence stands and existing
issue/PR links reference their current paths) but now carry a pointer banner
into the ADR that governs their current adoption status:

- `docs/spikes/148-round-trip-refactor-descope.md` — superseded by
  [ADR-0001](0001-supersede-round-trip-refactor-descope.md).
- `docs/spikes/149-markdown-round-trip.md` — evidence referenced by
  [ADR-0002](0002-canonical-serialization-form.md); adoption status PROPOSED,
  pending issue #198.
- `docs/spikes/issue-117-yaml-frontmatter-editing.md` — evidence adopted by
  [ADR-0002](0002-canonical-serialization-form.md) (YAML side, ACCEPTED).
- `docs/spikes/177-postmortem-delivery-loop-design.md` — design-only spike,
  not superseded; no ADR currently governs it.

## Failure ledger

`failure-ledger.md` in this directory is a separate append-only log (not an
ADR) of `request_changes` review rounds, used to detect recurring bug
categories across issues. See that file for its format and the three-strikes
rule.

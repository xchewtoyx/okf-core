# Spike #177 — A repeatable delivery-loop postmortem pattern

> **Status: design-only, not superseded.** No ADR under
> [`docs/decisions/`](../decisions/README.md) currently governs this spike;
> it stands as-is pending the follow-up implementation issue named in its
> own "Follow-ups" section below. Check `docs/decisions/` for any ADR that
> may have since adopted or superseded it.

- **Issue:** [#177](https://github.com/xchewtoyx/okf-core/issues/177) (`type:design`)
- **Epic:** [#164](https://github.com/xchewtoyx/okf-core/issues/164) Continuous Improvement: Delivery-loop process & tooling
- **Blocks:** nothing directly — this spike's output is a design that a future
  implementation issue (see Follow-ups) turns into a real
  `.claude/skills/postmortem-delivery-loop/SKILL.md`.
- **Decision:** Trigger **manually or on milestone close**, reuse
  `deliver-milestone`'s existing "Stay thin" + scoped-`general-purpose`-dispatch
  pattern verbatim rather than inventing a parallel one, and file findings under
  #164 using the same label/body convention #165–#176 already established.

## Problem

The findings that produced #164 and its current children (#165–#176) came from
a one-off, manually-triggered postmortem: the user terminated a
`deliver-milestone` run at >500k tokens of supervisor context, produced a
session-handoff artifact, and then separately asked for a dispatched,
context-hygienic analysis of that artifact plus the two merged PRs (#162,
#163) from the run. That analysis worked, but nothing about it is durable —
the next delivery-loop run that warrants a postmortem would have to
re-derive, from scratch, which evidence to pull, how to dispatch analysis
without blowing the same context budget the postmortem exists to catch, and
how to file results so they're comparable to this round's. This spike designs
that repeatable process without yet implementing it.

## Trigger conditions considered

The issue names three options. Comparing them on the axes that matter here —
whether they catch a run that actually needs review, and whether they add
operational surface area beyond what this repo already has:

| Trigger | How it would fire | Catches the case that motivated #164 | New surface required |
| --- | --- | --- | --- |
| **Manual invocation only** | User runs `/postmortem-delivery-loop` (or equivalent) against a session handoff or a completed run's issue/PR numbers, whenever they judge it's warranted. | Yes — the #164 postmortem was itself manual, prompted by the user hitting >500k tokens and terminating the run. | None. Same shape as today's `/deliver-milestone` invocation. |
| **Automatic trigger on context-size threshold** | `deliver-milestone` (or its supervisor loop) monitors its own context size and self-invokes the postmortem skill once it crosses a threshold (e.g. 500k tokens). | Yes, and earlier — would fire *during* a long run rather than requiring the user to notice and terminate it. | Real: requires `deliver-milestone` to introspect its own running context size from inside the skill prompt, which is not a capability `deliver-milestone` currently exercises anywhere (its existing self-monitoring is all external — GitHub state, not context accounting) and there is no evidence for how reliably a skill can read its own token usage mid-run. |
| **Scheduled/periodic trigger** | e.g. fires after every milestone closes, independent of context size or explicit ask. | Only incidentally — the #164 postmortem was not tied to a milestone boundary; it was tied to a specific run getting expensive. A milestone-close trigger could just as easily fire after a short, uneventful milestone and find nothing, or miss a mid-milestone run that ran long and was abandoned before any milestone closed. | Moderate: requires wiring a `create_trigger`-style Routine (or equivalent scheduled hook) to milestone-close events, which is plumbing this repo doesn't have today (`deliver-milestone` has no milestone-close webhook or trigger of its own). |

**Decision:** manual invocation, **plus** a scheduled trigger fired after
every milestone closes, and defer the context-size trigger.

Reasoning:

- Manual invocation is the zero-new-surface baseline and must exist
  regardless of anything else — it's what actually produced #164's findings,
  so it's proven to work today.
- A milestone-close trigger is cheap to add (this environment already exposes
  `create_trigger`/Routines, and `deliver-milestone`'s own "Release step"
  gives an unambiguous, already-detected event — "scope discovery returns
  zero open issues for the milestone" — to hang a trigger off) and gives a
  default cadence so a postmortem isn't only ever run when someone remembers
  to ask for one. Firing it at milestone close rather than per-issue also
  keeps analysis-agent dispatch cost bounded to once per milestone rather
  than once per PR.
- The context-size trigger is deferred, not rejected outright, because it
  would need `deliver-milestone`'s own supervisor loop to read and act on its
  own context consumption mid-run — a capability gap, not a design choice —
  and speculatively building that plumbing now, before a second real
  postmortem has validated the milestone-close cadence is even the right
  default, would be exactly the kind of parallel-approach invention this
  issue's acceptance criteria warn against. If a future postmortem run finds
  that milestone-close is too coarse (e.g. a single milestone runs long
  enough to need mid-flight review), that finding becomes its own scoped
  follow-up issue filed under #164, the same way every other gap in this
  loop has been filed.

## Standard evidence set

The #164 postmortem that produced #165–#176 pulled from exactly three GitHub
sources, in this order. A `postmortem-delivery-loop` run should pull the same
three every time, so results are comparable across runs instead of each
postmortem inventing its own evidence scope:

1. **The session handoff artifact.** The primary narrative record of what the
   terminated (or completed) `deliver-milestone` run actually did, in what
   order, and what the supervisor judged noteworthy before its context ran
   out. This is the seed the other two evidence sources verify claims
   against — it is a starting hypothesis, not ground truth, because it's
   supervisor-authored prose written under the same context pressure the
   postmortem exists to catch.
2. **PR review-comment history for every PR the run produced**, fetched via
   the GitHub API (`pull_request_read` with `get_review_comments` /
   `get_reviews`, or the `mcp__github__` equivalents) — for the #164
   postmortem this was PR #162 and PR #163. This is where automated review
   (GitHub Copilot, in the #164 case) and human reviewers left findings that
   internal `milestone-reviewer`/`milestone-approver` passes did or didn't
   catch on their own. Auditing internal-review misses against this history
   is what produced #165's specific checklist-gap findings, so it is not
   optional evidence — it's the source for the single highest-yield finding
   category in the precedent run.
3. **Issue lists filtered by milestone** (`list_issues` /
   `search_issues` with `milestone:"<name>"`), to establish the actual scope
   and sequencing of what the run covered — which issues were planned,
   which were implemented, in what order, and whether any were skipped or
   reordered relative to the plan. This is what lets a postmortem's filed
   findings reference concrete issue/PR numbers instead of vague narrative
   ("the run struggled with X") the way #165's and #176's Notes sections do.
   This adapts `SKILL.md`'s "Scope discovery" section
   (`.claude/skills/deliver-milestone/SKILL.md`, "Scope discovery (redo this
   before every issue, not just once)"): just as that section re-queries
   `is:open is:issue milestone:"<milestone title>"` fresh on every loop
   iteration rather than trusting a cached worklist, a postmortem run should
   re-query the milestone's actual current issue state rather than relying
   on a stale snapshot captured when the run started or on the handoff
   narrative's account of it.

A postmortem run should treat this as a floor, not a ceiling — a specific
finding may need one more targeted lookup (e.g. a single commit's diff to
confirm a claim) — but the three sources above are always pulled, every run,
regardless of what the handoff narrative already claims.

## Analysis-agent dispatch pattern

This reuses `deliver-milestone`'s two established patterns directly, not a
parallel one:

- **"Stay thin"** (`.claude/skills/deliver-milestone/SKILL.md`, "Stay thin"
  section): the postmortem supervisor never reads full PR diffs, full review
  comment bodies, or the full session handoff itself into its own context.
  It dispatches subagents to do that reading and carries forward only their
  bounded structured reports — the same discipline that section already
  states in general terms ("Never read full source files, full diffs, or
  full issue/comment threads yourself... every subagent call must be told to
  return a short structured summary").
- **Diagnostic dispatch via scoped `general-purpose` Agent calls, not new
  agent files** (`SKILL.md` step 4's "Diagnostic dispatch" bullet, and step
  7's "Comment triage (delegated)" bullet): both of those existing
  mechanisms dispatch a scoped `Agent` call with
  `subagent_type: general-purpose` and a narrowed prompt, explicitly choosing
  *not* to create a new `.claude/agents/*.md` file for the purpose. A
  postmortem run follows the identical shape: for each claim to verify (e.g.
  "did `milestone-reviewer` miss a TOCTOU race that Copilot caught on PR
  #163?"), dispatch one scoped `general-purpose` Agent, in parallel with the
  other claims' dispatches, each told: the specific claim to verify, exactly
  which evidence-set artifacts to check it against (per the section above),
  and that it must return a bounded structured report — verdict plus
  concrete citations (issue/PR/comment numbers, file/line references) —
  never a full transcript or its own investigation narrative. This mirrors
  step 4's output contract ("returns only a recommendation... never code")
  and step 7's ("returns only a `fixed`/`not-fixed` verdict plus a single
  reply paragraph — no investigation transcript").
- **Model tier**: no `model` override on these dispatches, for the same
  reason `SKILL.md` step 4 gives for its own diagnostic dispatch — claim
  verification against enumerated evidence is the same distilled
  pattern-recognition shape the rest of the loop already runs at the default
  tier, not long-horizon or large-context work that would justify a premium
  one.

### Resolved: reuse `general-purpose`, do not add a new agent type

The issue's own framing raises this as an open question implicitly (it says
"model directly on `deliver-milestone`'s own 'stay thin' pattern" without
specifying agent infrastructure). This spike resolves it explicitly:
**reuse `general-purpose` via scoped dispatch; do not add a
`.claude/agents/postmortem-analyst.md` or similar.**

`deliver-milestone` already had two opportunities to introduce a dedicated
agent type for exactly this kind of narrowly-scoped verification work — the
restructure diagnostic (step 4) and comment triage (step 7) — and both times
chose a scoped `general-purpose` dispatch with a narrowed prompt instead, with
step 4's and step 7's diagnostic-dispatch bullets each stating the reasoning
inline ("no new `.claude/agents/*.md` file; this mirrors step 3's... pattern
of reusing existing infrastructure with a narrowed prompt rather than
inventing new agents"). A postmortem-verification task — check one claim
against enumerated GitHub evidence, return a structured verdict — is the same
shape of work as both of those, not a materially different one that would
justify new persistent agent infrastructure (a new agent file would need its
own maintained prompt, its own drift-from-`SKILL.md` risk, and — per the
milestone-planner/-implementor/-reviewer/-approver agents that *do* exist —
that infrastructure is reserved for roles that recur with stable, distinct
responsibilities across many invocations, not a single verification-and-report
task parameterized per claim). If a future postmortem run finds that claim
verification needs capabilities a narrowed `general-purpose` prompt can't
express (not evidenced by the one precedent run so far), that's a new,
separately-justified follow-up — not a default to build ahead of the need.

## Filing convention

Findings synthesize into backlog issues filed under #164, following the
convention #165–#176 already established (confirmed against #165's and
#176's actual issue bodies):

- **Labels:** one `type:*` label (`type:chore` for a mechanical/process fix
  like #165, #176; `type:design` for a finding that itself needs a design
  pass before implementation, like this issue) plus one `status:*` label
  (`status:ready` when the finding is scoped enough to implement directly;
  `status:needs-design` when it isn't).
- **Parent-linking:** every filed issue is created as a sub-issue of #164 (or
  linked via `sub_issue_write` after creation) and its body's Notes section
  states `Part of #164.` verbatim — both #165 and #176 do this, and #164's
  own body documents itself as "an unmilestoned parent tracking issue... Child
  issues accumulate here as postmortems find concrete, scoped fixes."
- **Body structure**, four sections in this order:
  - `## Problem` — the specific gap the evidence surfaced, citing concrete
    issue/PR/comment numbers (e.g. #165's "Auditing PR #162 and PR #163's...
    findings against `.claude/agents/milestone-reviewer.md`'s current
    checklist shows 19 of 21 findings were real bugs").
  - `## Proposed behavior` — the concrete fix, specific enough to hand
    directly to `milestone-planner`/`milestone-implementor` without further
    scoping (or, for a `type:design` finding, a request to design one).
  - `## Acceptance criteria` — a checklist, mirroring the acceptance-criteria
    convention every issue in this milestone uses.
  - `## Notes` — the `Part of #164.` line plus any caveat the synthesizing
    agent's evidence surfaced but that didn't rise to its own criterion
    (#165's Notes documents that two Copilot findings on PR #162 were false
    positives, for exactly this reason).
- **Milestone field:** set to the tracking milestone (`continuous-improvement`
  for the #164 line), matching every current child issue.

## Proposed skill draft (appendix — not to be implemented in this issue)

**This section is prose only.** Per the parent issue's own instruction
("Design (do not yet implement)"), no executable
`.claude/skills/postmortem-delivery-loop/SKILL.md` file is created as part of
this spike. The draft below is what that file's body would contain if a
future follow-up issue (see below) implements it; it deliberately mirrors
`deliver-milestone`'s structure so the two skills stay recognizably siblings.

```markdown
---
name: postmortem-delivery-loop
description: Analyzes a completed or terminated deliver-milestone run (via
  its session handoff and/or the PRs/issues it produced) and files scoped,
  evidence-backed backlog issues under the Continuous Improvement tracking
  issue (#164). Use after a deliver-milestone run ends — either the user
  asks for a postmortem, or the milestone-close trigger fires this
  automatically. Do not use mid-run; this analyzes a run that has already
  stopped.
---

# Postmortem Delivery Loop

You are the supervisor of a postmortem analysis for `xchewtoyx/okf-core`'s
delivery loop. You are given either a session handoff artifact, or a set of
issue/PR numbers from a completed `deliver-milestone` run, or both.

Your job is to verify specific claims about what the run got right and wrong
against real GitHub evidence, then synthesize verified findings into backlog
issues filed under #164. You are the orchestrator only — you dispatch scoped
`general-purpose` Agent calls to do the actual evidence-gathering and claim
verification.

## Stay thin

Never read the full session handoff, a full PR diff, or a full review-comment
thread yourself. Every dispatch must be told to return a bounded structured
report (verdict + citations), not a transcript. Discard a dispatch's raw
evidence from your own context once you've folded its verdict into your
running findings list.

## Standard evidence set

Every run pulls, at minimum:

1. The session handoff artifact (if one exists for this run).
2. PR review-comment history (`get_review_comments`/`get_reviews`) for every
   PR the run produced.
3. The run's issue list, filtered by milestone.

## Claim extraction and dispatch

1. From the handoff (or, if none exists, from a first-pass skim of the PR/
   issue evidence), extract a list of discrete, checkable claims — not vague
   impressions. A claim is checkable if it names a specific artifact
   (a PR, an issue, a review comment, a file) and a specific assertion about
   it.
2. Dispatch one scoped `general-purpose` Agent per claim, in parallel,
   each given: the claim, the specific evidence-set artifacts to check it
   against, and an explicit instruction to return only a verdict
   (confirmed/refuted/partially-confirmed) plus concrete citations.
3. Do not set a `model` override on these dispatches — see the parent spike's
   "Model tier" reasoning; this is the same distilled pattern-recognition
   shape as `deliver-milestone`'s own diagnostic dispatch.

## Synthesis and filing

1. Group confirmed claims into scoped, independently-actionable findings —
   do not file one omnibus issue per run.
2. For each finding, file an issue under #164 following its filing
   convention: `type:*` + `status:*` labels, sub-issue parent-link to #164,
   milestone set to the tracking milestone, and a
   `## Problem`/`## Proposed behavior`/`## Acceptance criteria`/`## Notes`
   body — `## Notes` always includes `Part of #164.` plus any caveat
   (refuted claims, false positives) the dispatched agents surfaced.
3. Report back to the user a short list of filed issue numbers and one-line
   summaries — not the full synthesis reasoning.

## Do not

- Do not implement fixes yourself — this skill only files backlog issues,
  it never dispatches `milestone-implementor`.
- Do not file a finding that wasn't confirmed against the evidence set by a
  dispatched agent — an unverified handoff claim stays out of the backlog.
- Do not invent a new agent type for claim verification — reuse scoped
  `general-purpose` dispatches per this spike's decision.
```

## Follow-ups this spike defines

- **New issue, filed under #164:** implement `postmortem-delivery-loop` as a
  real `.claude/skills/postmortem-delivery-loop/SKILL.md` file per the draft
  above, plus the milestone-close `create_trigger` wiring decided in
  "Trigger conditions considered." (Not filed by this spike itself — named
  here as the concrete next step per #177's task instructions.)

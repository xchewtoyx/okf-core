---
name: deliver-milestone
description: Drives every open issue in a named okf-core GitHub milestone to merged, one at a time, via a plan/implement/review/approve/PR/merge-wait loop, then cuts a version-bump release PR. Use when asked to deliver, work through, or finish a milestone (e.g. "/deliver-milestone v0.5.0-alpha.2"). Do not use for a single one-off issue or PR — use this only for driving a whole milestone end to end.
---

# Deliver Milestone

You are the supervisor of a milestone delivery loop for `xchewtoyx/okf-core`.
The milestone title (e.g. `v0.5.0-alpha.2`) is given as this skill's
argument — if none was given, ask the user which milestone before doing
anything else.

Your job is to drive every open issue in that milestone to merged, then cut a
release PR. You are the orchestrator only — you dispatch subagents
(`milestone-planner`, `milestone-implementor`, `milestone-reviewer`,
`milestone-approver`; see `.claude/agents/`) to do the actual reading,
planning, coding, and reviewing.

## Stay thin

Never read full source files, full diffs, or full issue/comment threads
yourself — that's what the subagents are for. Every subagent call must be
told to return a short structured summary; that summary, not the underlying
material, is what you carry forward in your own context. The moment an
issue's PR merges, discard its working detail entirely — don't accumulate a
running history of every issue across the loop. If you need to report overall
progress, re-derive it from GitHub state (`list_pull_requests`,
`search_issues`), not from memory of earlier iterations.

## Scope discovery (redo this before every issue, not just once)

Query issues with `is:open is:issue milestone:"<milestone title>"` in
`xchewtoyx/okf-core`. This is the authoritative worklist — never hard-code an
issue list across loop iterations; it shrinks as PRs merge and can gain
issues if the human re-milestones something mid-run.

For each open issue, note only number, title, and any "Depends on" /
"Blocked by" / "Prerequisite" / "Parent" reference in the body. Build a
dependency-ordered queue — an issue is eligible only once everything it
depends on is closed. If every remaining issue is blocked on something
*outside* this milestone, or on each other in a way that leaves nothing
eligible, stop and tell the user exactly what's blocking what rather than
guessing at scope or skipping the check.

## Per-issue loop

Work exactly one issue at a time, start to merge — never start a second
issue's branch/PR before the current one is merged.

1. **Plan** — dispatch `milestone-planner` with the issue number and the
   milestone title for context. It returns a task list, acceptance criteria,
   and any blockers — not the full issue text. If it reports a hard blocker,
   treat this issue as ineligible, note why, and move to the next eligible
   one instead of forcing it.

2. **Implement** — dispatch `milestone-implementor` with that task list. It
   returns a branch name, commit SHA(s), and a short summary — not a diff.

3. **Scoped concurrency pass (auto-triggered)** — before the normal review,
   check whether this issue's diff touches `src/okf_core/patching.py`'s
   `plan_*`/`apply_*` function bodies. Detect this with a file-scoped diff
   limited to that one file, e.g. `git diff origin/main...<branch> --
   src/okf_core/patching.py` — this is a narrow, targeted check, not the
   full-diff/full-source read that "Stay thin" bans, so running it yourself
   is fine. If that file-scoped diff is non-empty:
   - Dispatch `milestone-reviewer` an extra time with a narrowed prompt
     asking only: "does every write path in this diff derive its proposed
     content from the same read used for its concurrency baseline?"
   - Treat any finding from this pass the same as a normal
     `request_changes`: dispatch `milestone-implementor` with that finding
     and the branch name, then re-run both the scoped pass and the normal
     review (step 4) until the scoped pass comes back clean.
   - This auto-trigger is narrower than the reviewer's own
     Concurrency/TOCTOU checklist item (which covers `patching.py` itself
     plus its callers, and runs as part of every normal full review below,
     not just when this file-scoped diff is non-empty) — this pass exists to
     catch it earlier, before the full review round.
   - If the file-scoped diff is empty, skip this step and go straight to
     step 4.

4. **Review** — dispatch `milestone-reviewer` with the issue number and the
   branch name — it reviews the branch against that specific issue. The
   review loop resolves to exactly one of three named outcomes:
   - `approve`
   - `restructure`
   - continuing the `request_changes` loop, up to the 5-round cap

   Details for each:
   - On `request_changes`: dispatch `milestone-implementor` again with
     exactly those findings and the branch name, then re-review. Repeat until
     `approve`, capped at 5 rounds — if still not approved after 5, stop and
     escalate to the user via `AskUserQuestion` with the reviewer's last
     findings instead of looping forever.
   - **Round-history tracking (in-context only)** — maintain, per issue, an
     ordered list `{round: N, bug_categories: [tags from that round's
     request_changes findings]}`. This is not a file or artifact — keeping it
     in-context only is consistent with "Stay thin" above. Start the list
     fresh when the issue begins and discard it the moment the issue's PR
     merges (step 8).
   - On each re-review dispatch (round N+1), pass the previous round's
     `bug_categories` into the milestone-reviewer dispatch prompt, along with
     a note if this round's implementor work was pitched as a structural fix
     for one of those categories. This is what lets milestone-reviewer apply
     its repetition circuit-breaker and sibling-code-path check.
   - **`restructure` auto-trigger** — immediately after appending a round to
     the round-history list above, compare its `bug_categories` against the
     previous round's. If they share a category, that's the trigger for the
     `restructure` outcome — the same condition milestone-reviewer's own
     circuit-breaker text already checks, now checked structurally by
     deliver-milestone itself rather than left to the reviewer's prose alone.
     This can fire as early as after round 2, and no later than round 3 —
     always before the 5-round cap is reached.
   - **Diagnostic dispatch** — when the auto-trigger fires, dispatch a scoped
     `Agent` call (`general-purpose`; no new `.claude/agents/*.md` file —
     this mirrors how step 3 reuses `milestone-reviewer` with a narrowed
     prompt rather than inventing new infrastructure). Give it the
     accumulated rounds' findings for the repeated category only, and task it
     solely with naming the shared root cause and proposing a structural fix
     — not with writing code.
   - **Model tier** — do not set a `model` override on this dispatch; it
     inherits the default tier (Sonnet) the rest of the loop's subagents run
     at. This diagnostic is the same distilled-pattern-recognition shape
     milestone-reviewer already performs every round, just re-aimed at its
     own prior findings — not the long-horizon/large-context work a premium
     tier is positioned for. Don't reach for a premium tier without evidence
     the default tier is producing shallow output.
   - **Output contract** — the diagnostic returns only a recommendation (root
     cause + proposed structural fix), never code. Route it through the
     existing `AskUserQuestion` escalation mechanism used at the 5-round cap
     above, now with the proposed fix attached as a concrete option, and
     present it before spending another implement round.
   - **Re-entry on approval** — if the user approves the proposed
     restructure via `AskUserQuestion`, the loop resumes at step 1
     (`milestone-planner`) with the diagnostic's recommendation folded into
     the planner dispatch prompt as the revised approach, rather than
     continuing another implementor/reviewer round on the old plan.
     Approving a restructure also resets this issue's round-history list
     (round count and `bug_categories`) before re-entering step 1, so the new
     approach doesn't inherit stale repetition signal from the abandoned one.
   - **Decline path** — if the user declines the proposed restructure via
     `AskUserQuestion`, the loop continues exactly as today: back to
     `milestone-implementor` with the reviewer's findings, still capped at 5
     rounds. The diagnostic does not re-fire for the same still-repeating
     category on this issue (don't re-ask every round) — the 5-round cap
     remains the fallback escalation.
   - When a restructure's resulting implementation comes back through this
     step, it must still carry the "pitched as a structural fix for category
     X" note (above) into the re-review dispatch, so milestone-reviewer's
     sibling-code-path check engages — this wiring holds across the
     restructure path unchanged; no new mechanism needed.

5. **Approve** — once Review approves, dispatch `milestone-approver` with the
   same issue number and branch name, plus the acceptance-criteria list from
   step 1's plan (it cross-checks that list against the issue itself rather
   than trusting it outright). It independently re-checks the issue's
   acceptance criteria and returns pass/fail per criterion — this is not a
   rubber stamp of step 4. Any failing criterion goes back to step 2
   (`milestone-implementor`) with exactly that gap, then re-approve. Only
   proceed once every criterion passes.

6. **Raise PR** — open a PR from the branch to `main`. Use the repo's PR
   template if one exists; otherwise lead with the one thing the reviewer
   most needs to understand that isn't obvious from the diff — not a
   restatement of what the code shows. Include `Closes #<issue>`. If your
   environment provides PR-activity subscription tooling (e.g. a
   `subscribe_pr_activity`-style tool that delivers review comments and CI
   results as events), use it. If it doesn't, fall back to periodically
   re-checking the PR's check-run and review state yourself instead — either
   way, the gate in step 7 is the same. Subscription and periodic re-checks
   are mutually exclusive per PR, not layered as belt-and-suspenders: once
   subscription succeeds for a PR, do not also schedule periodic re-checks
   for that same PR. When falling back to periodic re-checks, use a backoff
   schedule instead of a fixed interval: start at 1 hour (frequent enough to
   catch CI results and review activity promptly without polling
   needlessly), and after each re-check that finds no state change — defined
   concretely as the same head SHA, the same CI conclusion, and the same
   `mergeable_state` as the previous check, the exact fields the originating
   postmortem cited as unchanged across wasted checks — double the interval,
   capped at 8 hours. Any re-check that finds a change in one of those three
   fields resets the interval back to 1 hour.

7. **Wait for merge — hard gate.** Per `AGENTS.md`, automated agents never
   merge their own PRs, and an issue isn't done until a human approves and
   merges. While waiting:
   - Handle CI failures and review comments as they arrive (via subscription
     events or your own re-checks), using the same implementor/reviewer loop
     as steps 2 and 4 (re-running step 3's scoped pass first if it applies).
   - **Comment triage (delegated)** — for every incoming review finding
     (webhook-delivered or surfaced by your own re-check), always dispatch a
     scoped `general-purpose` Agent call first (no new `.claude/agents/*.md`
     file; this mirrors step 3's and step 4's diagnostic-dispatch pattern of
     reusing existing infrastructure with a narrowed prompt rather than
     inventing new agents) to investigate the finding against the current
     code, tests, and any repro as needed, including whether prior
     implementor/reviewer rounds above already addressed it. Whether the
     finding is `fixed` or `not-fixed` — including "doesn't need a fix" — is
     a verdict the subagent reaches through that investigation. The
     supervisor never makes this call itself as a precondition to dispatch;
     its only job here is to receive the finding, dispatch triage, and act
     on the verdict the subagent returns.
   - **Output contract** — the triage subagent returns only a
     `fixed`/`not-fixed` verdict plus a single reply paragraph — no
     investigation transcript, no diff, no repro output. This mirrors
     `milestone-implementor`'s existing "branch/commit/summary only, never a
     full diff" discipline: the supervisor stays thin on comment triage
     exactly as it does on implementation.
   - **Verdict wiring** — `not-fixed`: dispatch `milestone-implementor` with
     the finding (the existing path above, unchanged), re-running step 3's
     scoped pass and step 4 review if applicable, then re-dispatch the
     triage subagent on the same finding to get a post-fix verdict + reply
     before posting. `fixed` (the finding is already addressed by prior
     work, or judged not to need a code change): post the reply
     immediately, no implementor round.
   - Post that reply paragraph verbatim to the comment thread — no
     re-reading the underlying finding, no re-deriving or editing the
     subagent's wording — explaining the reasoning for the fix, or for not
     making one, rather than pushing silently. Drop the investigation detail
     from context immediately after posting.
   - Do not plan, implement, or open a PR for any other milestone issue while
     this one is open.
   - Do not advance until GitHub actually reports this PR merged — never
     assume or infer merge status from silence.

8. Once merged: if you subscribed to this PR's activity, unsubscribe from it;
   drop this issue's working detail entirely, rerun scope discovery, and move
   to the next eligible issue.

## Release step

When scope discovery returns zero open issues for the milestone:

1. Dispatch `milestone-implementor` to bump `pyproject.toml`'s version and
   roll `CHANGELOG.md`'s `[Unreleased]` section into the new version + date,
   adding a fresh empty `[Unreleased]` above it and a comparison link at the
   bottom, per `AGENTS.md`'s Changelog rules.
2. Open this as its own PR ("Release `<milestone title>`"), subscribe to it,
   and wait for human merge exactly as in steps 6–7 above.
3. Once merged, report to the user: milestone closed, release PR merged, and
   ask whether to continue the loop on the next milestone.

## Do not

- Do not force-push, merge PRs, or skip the human-approval gate.
- Do not batch more than one issue's work onto a single branch or PR.
- Do not fabricate PR status — treat an issue as done only once GitHub
  confirms the merge.

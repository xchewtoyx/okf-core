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

3. **Review** — dispatch `milestone-reviewer` against that branch. It returns
   `approve` or `request_changes` with concrete findings.
   - On `request_changes`: dispatch `milestone-implementor` again with
     exactly those findings and the branch name, then re-review. Repeat until
     `approve`, capped at 5 rounds — if still not approved after 5, stop and
     escalate to the user with the reviewer's last findings instead of
     looping forever.

4. **Approve** — once Review approves, dispatch `milestone-approver` against
   the same branch. It independently re-checks the issue's acceptance
   criteria and returns pass/fail per criterion — this is not a rubber stamp
   of step 3. Any failing criterion goes back to step 2 (`milestone-
   implementor`) with exactly that gap, then re-approve. Only proceed once
   every criterion passes.

5. **Raise PR** — open a PR from the branch to `main`. Use the repo's PR
   template if one exists; otherwise lead with the one thing the reviewer
   most needs to understand that isn't obvious from the diff — not a
   restatement of what the code shows. Include `Closes #<issue>`. Subscribe
   to PR activity so review comments and CI failures arrive as events instead
   of requiring you to poll.

6. **Wait for merge — hard gate.** Per `AGENTS.md`, automated agents never
   merge their own PRs, and an issue isn't done until a human approves and
   merges. While waiting:
   - Handle CI failures and review comments as they arrive, using the same
     implementor/reviewer loop as steps 2–3, and reply to comment threads
     with your reasoning per the PR-comment etiquette rather than pushing
     silently.
   - Do not plan, implement, or open a PR for any other milestone issue while
     this one is open.
   - Do not advance until GitHub actually reports this PR merged — never
     assume or infer merge status from silence.

7. Once merged: unsubscribe from that PR's activity, drop this issue's
   working detail entirely, rerun scope discovery, and move to the next
   eligible issue.

## Release step

When scope discovery returns zero open issues for the milestone:

1. Dispatch `milestone-implementor` to bump `pyproject.toml`'s version and
   roll `CHANGELOG.md`'s `[Unreleased]` section into the new version + date,
   adding a fresh empty `[Unreleased]` above it and a comparison link at the
   bottom, per `AGENTS.md`'s Changelog rules.
2. Open this as its own PR ("Release `<milestone title>`"), subscribe to it,
   and wait for human merge exactly as in steps 5–6 above.
3. Once merged, report to the user: milestone closed, release PR merged, and
   ask whether to continue the loop on the next milestone.

## Do not

- Do not force-push, merge PRs, or skip the human-approval gate.
- Do not batch more than one issue's work onto a single branch or PR.
- Do not fabricate PR status — treat an issue as done only once GitHub
  confirms the merge.

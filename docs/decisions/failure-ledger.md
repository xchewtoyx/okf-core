# Failure ledger

This is an **append-only log**, not an ADR. It exists to make recurring
review findings visible across issues, rather than each one being judged in
isolation against only its own issue's history — the gap ADR-0001 names as
the reason #148's "recurring, attributable maintenance cost" revisit
criterion could not be evaluated at the time (see ADR-0001's discussion of
that criterion).

## Who writes here

`milestone-reviewer.md` appends one line per `request_changes` review round
it issues, as an explicit carve-out from its otherwise read-only posture —
see the "Decision Records" instruction in that file. The entry rides the
issue's own branch into its PR, the same way any other implementation-branch
change would. No other role (implementor, approver, or the supervisor
driving the milestone loop) writes to this file.

## Format

One line per `request_changes` round:

```
date | issue | round | bug_category | one-line finding
```

- **date** — `YYYY-MM-DD`, the date the review round was issued.
- **issue** — the GitHub issue number the branch is implementing (e.g.
  `#193`).
- **round** — the review round number for that issue (starts at 1).
- **bug_category** — the same short free-text category label
  `milestone-reviewer.md` already uses in its findings (e.g.
  `silent-discard`, `toctou`, `docstring-drift`). Use the same string
  verbatim across issues so occurrences of the same category are
  grep-able.
- **one-line finding** — a compact summary of what was wrong, specific
  enough to recognize a repeat without opening the original PR.

Example:

```
2026-08-10 | #150 | 2 | toctou | plan_x read target separately from the hash-baseline read
```

## Three-strikes rule

If the same `bug_category` appears a **third** time across issues (not
necessarily the same issue), that is a signal the category is structural,
not incidental — the same class of mistake keeps recurring across
independent implementation rounds despite each individual fix looking
sufficient at the time. The third occurrence triggers **opening a
structural issue** (a new GitHub issue scoped to fix the underlying pattern,
not just this instance of it) instead of applying a fourth point-fix to the
same symptom. This mirrors `milestone-reviewer.md`'s own within-issue
repetition circuit-breaker (which recommends a structural fix by round 3 of
the *same* issue); the ledger extends that same discipline across issue
boundaries, which no single review round can see on its own.

## Log

<!-- Entries appended below, oldest first. -->

2026-08-10 | #194 | 1 | narrow-except | stable_id_cmd and _index_one_directory catch only a subset of DocumentChangeError subclasses (missing DocumentChangePlanningError, and DocumentChangeSafetyError in the index path), unlike move_cmd/graph_repair_cmd's existing catch-the-base-class pattern, so a planning-time race (e.g. symlink swapped in right before plan_document_change[_from_reader] reads the target) crashes with an unhandled traceback instead of the documented graceful exit(1)
2026-08-10 | #195 | 1 | adr-divergence | plan_frontmatter_merge (_dump_frontmatter) only canonicalizes the touched key's own value to block style; an untouched flow-style key elsewhere in the same document (e.g. `metadata: {owners: [docs, platform]}`) survives an edit to a different key unchanged, contradicting ADR-0002's "Block style ... regardless of the input document's original style" and Framework point 5 "Convergence is per-document, on first touch" — and the README/docstring verbatim restate the unmet claim
2026-08-27 | #225 | 1 | fail-closed-overreach | _broken_links_from_graph/_relative_posix raise GraphModelError when a BundleGraph broken-link target_path resolves outside the bundle (e.g. ../outside.md), so acquire_normalized_graph cannot model a graph that okf graph already reports
2026-08-28 | #227 | 1 | missing-validation | analyze_normalized_graph top_n<0 is min()'d then used as a slice, so top_n=-1 silently drops the last ranked concept instead of raising GraphAnalysisError

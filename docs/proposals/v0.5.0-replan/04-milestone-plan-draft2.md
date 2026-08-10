# Draft 2 — v0.5.0 milestone plan

- **Status:** draft for review (document 4 of the v0.5.0 re-plan set).
  Supersedes the draft-1 re-plan recorded in the #148 closing comment and
  epic #11 comment of 2026-08-10. No tickets have been changed to match
  this draft yet; the disposition table in §5 is the proposed ticket
  update set.
- **Inputs:** `02-editing-requirements.md` (what to build),
  `03-codebase-analysis.md` (the runway), and the existing open backlog.

## 1. Mission statement for v0.5.0

> okf-core v0.5.0 makes it safe and cheap for LLM agents to *maintain* an
> OKF corpus: deterministic metadata edits, a single sanctioned
> parse/serialize path built on mature format libraries, and the
> plan/apply safety envelope around every write. It deletes the
> byte-preservation machinery and the requirement that demanded it, and
> aligns the delivery loop's instructions so agents inherit these
> decisions instead of re-litigating them.

Three principles govern scoping (from the requirements gate and the
change-engineering evidence):

1. **Value first, engine second, only where dependencies force it.**
   Metadata operations (R-B) are the user-visible payoff; they need only
   the frontmatter engine, so that engine lands first and Markdown-side
   work is decoupled from the value train.
2. **Every alpha ships green and is releasable.** Strangler steps, not a
   binge; each increment independently reviewable; old machinery deleted
   only as its callers move.
3. **Decisions are recorded where the loop reads them.** Instruction/ADR
   alignment is scheduled work with an issue number — not something we
   hope happens — because the reviewer currently enforces the contract
   this plan retires.

## 2. v0.5.0-alpha.1 — Decision hygiene and the frontmatter engine

*Theme: stop the loop fighting the plan; land the self-contained half of
the engine swap.*

| # | Work item | Requirements | Notes |
|---|---|---|---|
| 1 | **ADR mechanism + instruction alignment** (new issue) | enables all | Create `docs/decisions/`; adopt the spike docs into it; write the ADR superseding `148-round-trip-refactor-descope.md` (answering its revisit criteria); record the canonical-form decision (one-way door — gets the deliberation). Update `AGENTS.md`, `milestone-planner.md` (must read requirements doc + scan ADRs), `milestone-reviewer.md` (drop byte-identity checks; add ADR-divergence check), `milestone-approver.md` (fix the byte-identical example), README §Safe Document Changes pointer. Fix the stale `noqa: C901` claim while in there. |
| 2 | **Extract the safety envelope** (new issue) | R-A | Move envelope + file-move code out of `patching.py` unchanged (zero behavior change). Add the R-A5 fault-injection tests for any guard not already shown capable of failing. Fold in the envelope-bypass fixes (`stable-id --write`, index writes) or ticket them explicitly. |
| 3 | **Frontmatter engine swap** (new issue, supersedes half of old #148) | R-B1, R-C1/2/3 (YAML side) | Characterization tests first; parse → mutate → canonical dump via the mature-library route the #117 spike recommended; keep value validation, alias rejection, semantic no-op; retire the ~18 byte-pinning tests deliberately. Documented canonical YAML form ships in the same PR (the ADR from item 1 decides it). |

Exit criterion: `plan_frontmatter_merge` produces canonical output under
the documented form; all R-A guards fault-injected; instruction files no
longer reference byte-identity anywhere.

## 3. v0.5.0-alpha.2 — Metadata operations (the value release)

*Theme: the v0.2 growth surface — the deterministic edits the corpus
needs on every curation pass. Everything here builds only on alpha.1.*

| # | Work item | Requirements | Notes |
|---|---|---|---|
| 4 | **Trust/lifecycle stamping** (new issue) | R-B2 | `generated`/`verified`/`status`/`stale_after` operations with actor-convention and ISO 8601 validation, bare-mapping normalization. Library + CLI. |
| 5 | **Source/provenance bookkeeping** (#113 rewritten) | R-B3 | Replace #113's v0.1 `# Citations` framing with v0.2 `sources` entries: identity-keyed add/update, dedupe-as-no-op, order preservation. The original #113 acceptance criteria carry over restated. |
| 6 | **Attribution consistency check** (new issue) | R-B4 | Footnote-label ↔ `sources[].id` join check as structured problems; wire into `okf validate`. |
| 7 | **Per-type frontmatter profiles** (#50, unchanged) | R-E1 | Already ready; independent of the engine work. |
| 8 | **Structure-free log append** (#101 rewritten) | R-G1 | `log_append(content, date?, kind?)` — agent supplies the sentence, library owns chronology, headings, and canonical form; never requires reading the log. Generalizes the existing `plan_log_concept_move` path (#136), which is this operation specialized to one entry type; #146's core is absorbed here, leaving 0.6.0's #146 as CLI sugar or closed. |

Exit criterion: a curator agent can stamp, source, validate, and log a
change entirely through okf-core, with zero hand-formatted metadata and
zero reads of files it isn't editing.

## 4. v0.5.0-alpha.3 — Markdown engine and parse-layer unification

*Theme: finish the engine swap; collapse nine parsers toward one path.*

| # | Work item | Requirements | Notes |
|---|---|---|---|
| 9 | **Markdown engine swap** (new issue, other half of old #148) | R-C1/2/3 (Markdown side), R-D1 | Token-tree mutate + re-render for the section and link-rewrite operations `moves.py`/`repair.py` consume; deletes the instrumented-rule cluster and section splicer. Resolve the markdown-it-py pin question via ADR before implementation. Consumer APIs stay stable; `test_moves.py` assertions loosened to link-level. |
| 10 | **Parse-layer unification** (new issue) | supports R-C1, §3.2 of doc 3 | One shared block-walk + re-escaping renderer; delete `_markdown_inline.py`, collapse the log/index sibling walks; replace reject-don't-escape guards with real escaping. `logs.py`'s writer path is the in-repo exemplar. |
| 11 | **Index drift validation** (new issue, small) | R-G2 | `okf validate` reports semantic drift between a committed `index.md` and its directory (missing entries, entries for absent files, stale descriptions). Generation already exists and is deterministic, so drift ≈ compare-against-regeneration; index writes also move inside the safety envelope here if not already done in item 2. |
| 12 | **`okf graph suggest --apply`** (#61, stretch) | R-D + gate re-check | Passes the gate only as a *bulk* cross-file operation (that is where N is high). Implement as whole-section read-modify-write through the new engine — no splicing. If alpha.3 runs long, this slips to 0.6.0 without blocking anything. |

Exit criterion: zero imports of markdown-it internals; escaping exists in
exactly one place; `patching.py` (or its successor modules) contains no
span arithmetic.

## 5. Disposition of every currently-open v0.5.0 issue

| Issue | Was | Proposed | Why |
|---|---|---|---|
| #148 (closed not-planned) | alpha.1 | Stays closed; superseded by items 3 + 8 with the *opposite* rationale | Document 1; the ADR in item 1 corrects the record. |
| #113 citations | alpha.2 | **Rewrite** as item 5 (sources bookkeeping) | v0.2 retired body `# Citations`; the corpus already uses `sources`. |
| #50 per-type profiles | alpha.2 | **Keep** (item 7) | Passes the gate; independent; ready. |
| #61 suggest --apply | alpha.2 (moved there in draft 1) | **alpha.3 stretch** (item 12) | Needs the Markdown engine; gate passes only for bulk application. |
| #131 id_history resolver | alpha.2 | **Defer to 0.6.0** | Move-tracking infra deferred per N6: the flat-bundle rule is a deliberate guardrail against the speculative taxonomy that *causes* reorganization, so pressure is low by design; `okf move` repairs links transactionally at move time; and the stable-id premise is itself unproven. Hookspec shipped; a consumer without the guardrail re-runs the gate. |
| #130 log.md-scan resolver | alpha.2 | **Defer to 0.6.0** | Same N6 rationale; additionally depends on move records existing in logs to scan. |
| #144 stable-id resolver | alpha.3 | **Defer to 0.6.0** | N6; the stable-id concept needs its own justification before infra is built on it. |
| #143 tombstone table | alpha.3 | **Defer to 0.6.0** | Supports #144. |
| #139 local module plugins | alpha.3 | **Defer to 0.6.0** | Real feature, no 0.5.0 dependency; keeps alpha.3 focused on the engine. |
| #158 cache schema migration | alpha.3 | **Defer to 0.6.0** | Genuine debt from the v0.4.2 fix, but unrelated to the editing mission; the implicit-migration risk is bounded and known. |
| #101 log.md append support | none | **Rewrite** as item 8 (structure-free log append, alpha.2) | R-G1: structure maintenance is the niche — the agent supplies content, the library owns chronology and format, and inline appending costs grow with corpus age. Read/parse support already shipped (#145). |
| #146 log entry generator (v0.6.0) | 0.6.0 | **Absorbed** by item 8; residue is CLI sugar or closes | Its core is R-G1. |

Net 0.5.0 issue count after re-cut: **12 items across three alphas**
(7 new, #50 kept, #113 and #101 rewritten, #61 stretch), versus 10
previously — but with the five infra deferrals removed, the milestone's
critical path runs entirely through the mission.

## 6. Process guardrails for the duration of 0.5.0

Cheap, concrete, and scheduled inside item 1 rather than aspirational:

1. **Characterization-first rule:** any PR deleting or replacing parsing/
   serialization code must land its characterization baseline in the same
   or a prior PR, and must list which retired assertions encoded the old
   contract (drift oracle vs correctness oracle, named explicitly).
2. **Design-it-twice in planning:** the planner produces two sketched
   alternatives for any issue touching a public contract; the plan names
   why the loser lost. An hour of comparison beats five review rounds.
3. **Cross-issue failure ledger:** the reviewer's `bug_category` tags
   accumulate in a file under `docs/decisions/` (append-only); three
   occurrences of one category across issues triggers a structural issue
   instead of a fourth point-fix. This is the mechanism the previous
   descope decision's revisit criteria assumed but never had.
4. **One sanctioned content path:** once item 10 lands, AGENTS.md states
   that document content is touched only through the unified
   parse/serialize layer; the reviewer treats a new ad-hoc walk as an
   architecture finding, not a style nit.

## 7. What this plan does *not* do

- It does not migrate the corpus. Convergence to canonical form arrives
  edit-by-edit (R-C2); no bulk reformat is scheduled or required.
- It does not put LLMs in charge of reserved files — the opposite:
  indexes are library-generated only, log structure is library-maintained
  only (R-G, N3). Existing move features are maintained; the
  move-*tracking* resolver chain waits for a demonstrated need (N6).
- It does not adopt v0.2 wholesale. Items 4-6 implement the v0.2
  *metadata families*, which are additive; a full v0.2 conformance pass
  (spec references in docs currently cite v0.1 sections, `okf_version`
  handling, legacy fallbacks) is proposed as the headline of **0.6.0**,
  where the deferred infra also lands.
- It does not touch the read side (scan/graph/search/context), except
  where the unified parse layer replaces duplicated walks beneath it.

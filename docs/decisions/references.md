# References

Canonical citation targets for `docs/decisions/` and the instruction files
that cite them. Cite with an inline bracket slug, e.g. `[replan-plan]` or
`[replan-plan: characterization-first]` for a specific named item within a
plain (non-heading) numbered list — never a bare path, a list position
number ("item 1"), or an unqualified section number. Slugs are permanent
text identifiers: add a new slug for a new source, never renumber or reuse
one.

- `[replan-requirements]` — `docs/proposals/v0.5.0-replan/02-editing-requirements.md`: the v0.5.0 editing-requirements spec (requirement IDs N1, S1, R-A through R-G). Its `##`/`###` headings (e.g. §3 "The inclusion gate", §4 "Requirements") are real markdown headings — cite as `[replan-requirements] §N` the same way `[replan-analysis]` is cited by section.
- `[replan-analysis]` — `docs/proposals/v0.5.0-replan/03-codebase-analysis.md`: codebase analysis backing the engine-swap decision. Its `##`/`###` headings (§1–§5, §3.1–§3.5) are real markdown headings — cite as `[replan-analysis] §N` or `[replan-analysis] §N.M`.
- `[replan-plan]` — `docs/proposals/v0.5.0-replan/04-milestone-plan-draft2.md`: the v0.5.0 milestone plan. Section 6 ("Process guardrails") is a plain numbered list with NO sub-headings, so its items are cited by their own bolded name, not position:
  - `[replan-plan: characterization-first]` — item 1
  - `[replan-plan: design-it-twice]` — item 2
  - `[replan-plan: cross-issue-failure-ledger]` — item 3
  - `[replan-plan: one-sanctioned-content-path]` — item 4

## Exceptions

- `01-draft1-requirements-assessment.md` (`docs/proposals/v0.5.0-replan/`)
  predates this registry and has no slug — cite it by bare path if ever
  needed. It is cited only in ADR-0001's Related line via this sanctioned
  exception, so a full slug/registry entry isn't warranted.

# Decision — round-trip serializer refactor descoped (#148 closed as not planned)

- **Issue:** [#148](https://github.com/xchewtoyx/okf-core/issues/148), closed 2026-08-10 as not planned
- **Supersedes:** the adoption recommendations in
  [`149-markdown-round-trip.md`](149-markdown-round-trip.md) (Strategy A /
  `mdformat`) and
  [`issue-117-yaml-frontmatter-editing.md`](issue-117-yaml-frontmatter-editing.md)
  (`ruamel.yaml` migration). The spikes' *evidence* stands; their adoption
  recommendations are not being implemented.

## Decision

Keep the existing editing engines in `patching.py` — the Markdown line-offset
section splice and the PyYAML frontmatter span splice. Do not adopt `mdformat`
or `ruamel.yaml`, do not build an `okf format` canonicalization command, and do
not downgrade `markdown-it-py` below 4.x.

Features that edit documents (#113 citations, #61 link-suggestion insertion)
build directly on the existing primitives (`plan_markdown_section_patch` and
the #110 plan/apply contract).

## Why

The refactor was chartered to replace "fragile" hand-written splice machinery
with well-supported libraries. The spikes priced that trade honestly, and the
batteries cost more than the machinery they would replace:

- The guarantee the refactor was meant to deliver — untouched bytes remain
  byte-identical through an edit — is the guarantee the existing engines
  already provide, verified by the patching test corpus and the #173
  hypothesis round-trip property tests.
- The `mdformat` path only achieves byte-identity for *already-canonical*
  documents, so it requires a new `okf format` CLI command, a one-time
  migration of every consuming bundle, planning-time rejection of
  non-canonical input, and a `markdown-it-py` 4.x→3.x downgrade pin. That is
  more new surface than the splice code it removes, and the canonical-input
  precondition pushes work onto users' files — against the project's promise
  to leave human-authored Markdown alone.
- The `ruamel.yaml` path deliberately gives up byte preservation of untouched
  frontmatter (flow spacing, CRLF) after an edit — a contract regression
  versus the current PyYAML span splice, which #117's own evidence table
  scores best for preservation.

The "do not reinvent mature infrastructure" rule in `AGENTS.md` prefers
libraries *when they fit the project constraints*. Here they do not: byte-level
preservation of human-authored files is OKF-specific behavior, which is
exactly the category `AGENTS.md` says to build in-core.

## Revisit criteria

Reopen a (narrower) refactor only if the span machinery causes recurring,
attributable maintenance cost — e.g. repeated corpus regressions traced to
offset arithmetic — or if a pure-Python formatting-preserving library emerges
that requires no canonical-input precondition and no dependency downgrade.

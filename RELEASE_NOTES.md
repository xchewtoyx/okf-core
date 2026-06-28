# okf-core v0.4.0

## Overview

v0.4.0 is a substantial feature release centred on performance, discoverability, and graph intelligence. The headline addition is an opt-in SQLite cache that underpins FTS5 full-text search, PageRank metrics, stable ID tracking, and the new unlinked-mention detection API — all of which ship in this release.

---

## New Features

### SQLite cache for manifest and graph (#79, PR #80)

An opt-in `okf_cache_dir` configuration key enables a lightweight SQLite database that caches manifest scan results and resolved link graphs. The cache is hook-driven via `pluggy`, keeping the core scan and graph paths stateless. On cache hits, file reads, YAML parsing, and link extraction are all skipped.

This cache is a prerequisite for FTS5 search, PageRank, and stable ID — features that also ship in this release.

### Full-text search with FTS5 (#9, PR #82)

`search_concepts(bundle, query)` and the `okf search` CLI command now use a SQLite FTS5 index maintained in the cache database. Queries support all FTS5 operators. Results include the matching concept ID, path, title, and a snippet excerpt.

### Unlinked mention detection (#56, PR #89)

`find_unlinked_mentions(bundle, *, refresh=True) -> UnlinkedMentionsResult` identifies places where one concept's body mentions another concept's title in plain text without a Markdown link. Results are returned as `LinkSuggestion` records with source/target concept IDs, paths, and an annotated FTS excerpt. Non-fatal read and parse errors are surfaced in `result.problems` rather than raised.

New public types: `LinkSuggestion`, `UnlinkedMentionsResult`.

### PageRank scores and orphan detection (#57, PR #88)

`ConceptListing` now includes a `pagerank` score when a cache is available. `BundleListing` gains an `orphans` field — the set of concept IDs with no inbound or outbound links.

### Opt-in stable ID field (#60, PR #86)

Bundles can configure a `stable_id_field` frontmatter key. When set, the value is indexed in the cache alongside the path-derived concept ID, supporting rename tracking and link repair in downstream pipelines. The concept ID itself remains strictly path-derived per the OKF spec.

New CLI command: `okf stable-id [CONCEPT_ID] [--force] [--write]` — retrieves, generates, and safely writes stable IDs to concept frontmatter.

### Consistent `--quiet` flag and `okf --version` (#81, PR #84)

- `okf --version` now works at the root, reporting the installed package version.
- `--quiet` / `-q` is now consistently available on `scan`, `validate`, and `index`. When set, all output is suppressed and the exit code carries the result.
- Query/output-only commands (`list-bundles`, `list-concepts`, `search`, `context`, `graph`) intentionally do not support `--quiet` — running them silently would be a no-op.
- Generated `index.md` files are always written with LF line endings regardless of platform.

---

## Tooling

### Search performance benchmark script (#58, PR #85)

`scripts/benchmark_search.py` (run via `just benchmark-search`) generates a 1,000-concept synthetic bundle and measures cold build, warm no-op refresh, incremental refresh, and FTS query latency across frequency tiers. The script is excluded from CI. Baseline results show that FTS query latency is well under 10 ms; the bottleneck is the manifest re-checksum path on warm refresh, which is the target for future optimisation.

### Cross-platform `justfile`

The `justfile` now supports Windows (`cmd.exe`) natively via platform-specific private recipes, without requiring WSL or shell shebang hacks.

### `actionlint` split into its own optional dependency group

`actionlint-py` is now in a separate `[actionlint]` optional group. `just install` checks for a system `actionlint` on `PATH` first and skips the download if found, avoiding failures in network-restricted environments.

---

## Upgrade Notes

- `okf_cache_dir` must be configured to use FTS5 search, `find_unlinked_mentions`, PageRank, or stable ID. Without it these features raise `SearchConfigError`.
- `find_unlinked_mentions` requires `okf_cache_dir` to be set.
- The `index_cache` config key from earlier drafts has been removed; use `okf_cache_dir` instead.

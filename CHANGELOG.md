# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **`log.md` parsing, rendering, and loading**: added `parse_log`/`render_log`/`load_log` and the `LogEntry`/`LogDateSection`/`LogParseProblem`/`ParsedLog` dataclasses, giving callers a structured, spec-conformant view of a bundle's change-history log without hand-rolling Markdown parsing. Malformed date headings or entries are reported as `LogParseProblem`s and skipped rather than aborting the whole parse, so #136's upcoming merge/insert logic can build on a tolerant read path. (#145)
- **Markdown round-trip spike**: chose a canonical `mdformat`-based approach for the upcoming #148 patching refactor; see `docs/spikes/149-markdown-round-trip.md`. (#149)
- **Safe Markdown link target rewriting**: added `plan_markdown_link_rewrite` patch primitive to surgically rewrite inline Markdown link destinations in the concept body. (#127)
- **`okf move` CLI command and `plan_file_move`/`move_concept` primitives**: relocates a concept file within a bundle while rewriting inbound Markdown links in every referring file, preserving link-graph integrity across the move. `move_concept` also refreshes an existing `index.md` in the source and/or destination directory to reflect the move. (#69)
- **`entries_for_directory()` / `okf_version_for_index_write()`**: extracted from `okf index`'s internal logic into reusable `index.py` primitives, so other callers (e.g. `move_concept`) can regenerate a single directory's `index.md` without reimplementing its directory/subdirectory bookkeeping. (#69)
- **Cyclomatic complexity linting**: `ruff`'s `C901` (mccabe) rule enabled with `max-complexity = 14` as part of `just lint` / CI. (#125)
- **`match`-exhaustiveness linting**: `mypy`'s `exhaustive-match` error code enabled repo-wide via `[tool.mypy] enable_error_code` in `pyproject.toml`, so a `match` statement missing a case for an enum member becomes a CI failure instead of silent runtime fallthrough. `logs.py` is the only file with structural `match` statements in the checked scope, so this doesn't affect any other module. Its `_dispatch_block` is restructured from a flat match on the `(phase, kind)` tuple into nested single-value matches, since mypy's exhaustiveness checker cannot reason about tuple-of-enum subjects but can verify a single-enum `match` is complete. (#145)
- **`okf graph-repair` CLI command and `plan_graph_repair`/`repair_graph` primitives**: repairs broken concept links whose target moved, via a new pluggable `okf_fetch_moved_concept_path` hook; links left unresolved (no plugin registered, a registered plugin returned `None` for that concept ID, its resolved path can't be expressed in the link's original style, or a `DocumentChangePlanningError` in one referring file) are reported with a distinguishing reason rather than aborting the run. (#63)

### Changed

- Reduced cyclomatic complexity of `validate_concept_document_with_profile` and `build_context_pack` below the lint threshold via helper extraction; no behavior change. (#119, #123)
- Reduced cyclomatic complexity of `scan_bundle`, `build_bundle_graph`, `find_unlinked_mentions`, `generate_index`, and the `okf index` CLI command below the lint threshold by extracting staged helpers, retiring their grandfathered `noqa: C901` suppressions; no behavior change. `AGENTS.md` gains a Code Structure section codifying the extraction patterns so new code stays under the budget. (#118, #120, #121, #122, #124)

### Fixed

- `parse_log()` now reports a `LogParseProblem` for a labelled bullet with no prose after the colon (e.g. `* **Update**:`), matching the same skip-and-report behaviour already documented for label-less empty entries. The empty-text check previously only fired when no `**Word**:` label was present, so a labelled bullet with nothing after the colon silently produced a `LogEntry` with empty `.text` instead of being surfaced as malformed input. (#145)
- `parse_log()` no longer silently drops content around bullet entries: a "loose" bullet item's second and later paragraphs are now folded into that entry's `.text` instead of vanishing, and a non-bullet paragraph placed directly under a date heading or a nested sub-bullet inside an entry — both cases the flat `LogEntry` model can't represent — are now reported as `LogParseProblem`s and skipped instead of disappearing untraced. (#145)
- `parse_log()`'s stray-block detection under a date heading is now generalized from bare paragraphs to any block-level construct the flat `LogEntry` model can't represent — fenced/indented code blocks, thematic breaks, sub-headings, raw HTML blocks, and blockquotes were previously discarded with zero diagnostics (the two fixes above only caught paragraphs and nested sub-lists); each is now reported as a `LogParseProblem` instead of vanishing. Also documents a related CommonMark quirk: an HTML block with no blank line before a following bullet absorbs that bullet into the same block before `parse_log` ever sees it as a list, so the entry still cannot be recovered, but the loss is now surfaced as a `LogParseProblem` rather than silent. (#145)
- `parse_log()`'s title-capturing `h1` branch no longer bypasses the stray-block classifier above: an `h1` (ATX or setext) placed directly under an already-open `## YYYY-MM-DD` date heading used to short-circuit past the generalized stray-block check added for other block types, so it either vanished with zero `LogParseProblem`s (if a real title was already captured) or got silently misattributed as `ParsedLog.title` (if no title had been seen yet) — the latter being active data corruption, not just a missed diagnostic. `h1` is now only ever claimed as the document title while no date section has opened yet; once one has, it falls through to the same stray-block handling as any other out-of-place heading. (#145)
- `build_bundle_graph()` now percent-decodes Markdown link hrefs before resolving them against on-disk concept paths, so a link to a path containing a space or other percent-encoded character (e.g. `[old](old%20file.md)`) is no longer treated as broken. (#69)
- `parse_log()` no longer loses track of a stray `h1` that appears after a *malformed* date heading (as opposed to a valid one, fixed previously): a valid date section, a later malformed one, and the pre-first-heading preamble all collapsed onto the same `current_date is None` signal, so an `h1` in the malformed-section position was indistinguishable from legitimate preamble content and could be silently dropped or misattributed as `ParsedLog.title`. The top-level dispatch loop is now driven by an explicit three-state `_SectionState` (preamble / in a valid section / in a skipped section) over a pre-partitioned list of top-level blocks, with every (state, block-kind) combination handled by its own case — removing the class of bug where an unclaimed token silently fell through to a default no-op, not just this one instance of it. (#145)
- `parse_log()` no longer silently drops unexpected content nested *inside* a list item: the same bug class fixed above for top-level blocks under a date heading also applied one level deeper, where only a nested bullet list was ever special-cased, so an ordered sub-list, a fenced or indented code block, a thematic break, a blockquote, a heading, or raw HTML block appearing after an entry's own text vanished with zero `LogParseProblem`s. Each is now reported and skipped the same way a top-level stray block is, without corrupting the entry's own captured text or any sibling entry. (#145)
- `render_linked_span()` (shared by `parse_log`'s entry-prose rendering and `parse_index`'s description rendering) now preserves a link's optional title attribute, e.g. `[text](href "title")`, instead of silently dropping it and re-emitting only `[text](href)`. Both call sites already documented embedded links as "preserved verbatim," and the upcoming `log_concept_move` format (#136) plans a titled link (`[old-id](new-id "moved to")`), so a dropped title would have been a silent round-trip data loss once that lands. (#145)

## [0.4.2] - 2026-07-11

### Fixed

- `okf context` (and any `build_bundle_graph()` call made without a precomputed manifest) no longer fails intermittently with `sqlite3.OperationalError: database is locked`. The graph phase opened a write transaction and then ran a nested `scan_bundle()` on a second connection to the same cache database, which deadlocked against the first — deterministically, even against a freshly created cache. Scan and graph phases now buffer their writes and hold the write lock only for a brief flush at phase end, so the nested scan no longer contends with the enclosing graph phase.
- Reduced SQLite cache lock contention more broadly: warming an already-current cache is now read-only and takes no write lock, PageRank rows are rewritten only when their value changes, connections run in autocommit mode with `synchronous = NORMAL` and a longer `busy_timeout`, and schema initialisation performs no writes once the cache exists.

## [0.4.1] - 2026-07-04

### Added

- **`okf unlinked-mentions` CLI command**: exposes deterministic unlinked concept-title suggestions as structured CLI output. (#96)
- **`okf orient` CLI command**: provides essential onboarding guidance and discovery pointers for developers and automated agents. (#97)
- **`--recurse` flag for `okf index`**: recursively generates `index.md` files for the target directory and all nested concept-bearing subdirectories. (#94)

### Fixed

- `okf index` now reports files excluded by `reserved_filenames`, clarifying zero-entry indexes caused by reserved root files. (#93)
- `find_unlinked_mentions` no longer suggests links based only on code regions or Markdown link destinations. (#95)
- Resolved SQLite concurrent write locks by adjusting PRAGMA configuration order and write-safety retries. (#98)
- Fixed tag name reference usage in the GitHub Actions publish workflow. (#91)

---

## [0.4.0] - 2026-06-28

### Added

- **SQLite cache** (`okf_cache_dir` config key): opt-in hook-driven cache for manifest scan results and resolved link graphs. Skips file reads, YAML parsing, and link extraction on cache hits. Required by FTS5 search, PageRank, stable ID, and unlinked-mention detection. (#79)
- **FTS5 full-text search**: `search_concepts(bundle, query)` and `okf search` CLI command backed by a SQLite FTS5 index maintained in the cache database. Supports all FTS5 query operators. (#9)
- **`find_unlinked_mentions(bundle, *, refresh=True) -> UnlinkedMentionsResult`**: detects places where a concept's body mentions another concept's title in plain text without a Markdown link. Results include annotated FTS excerpts; non-fatal read/parse errors are collected in `result.problems` rather than raised. New public types: `LinkSuggestion`, `UnlinkedMentionsResult`. (#56)
- **PageRank scores and orphan detection**: `ConceptListing.pagerank` populated when a cache is available; `BundleListing.orphans` is the set of concept IDs with no inbound or outbound links. (#57)
- **Opt-in stable ID field** (`stable_id_field` bundle config): frontmatter key indexed in the cache to support rename tracking and link repair in downstream pipelines. New CLI command `okf stable-id [CONCEPT_ID] [--force] [--write]`. (#60)
- **`okf --version`** at the root CLI entry point. (#81)
- **Consistent `--quiet` / `-q` flag** on `scan`, `validate`, and `index`: suppresses all output and relies solely on exit code. (#81)
- **Search scaling benchmark** (`scripts/benchmark_search.py`, `just benchmark-search`): generates a 1,000-concept synthetic bundle and measures cold build, warm refresh, and FTS query latency. Excluded from CI. (#58)

### Changed

- `justfile` refactored to support Windows (`cmd.exe`) natively via platform-specific private recipes; no WSL or shell shebang required. (#81)

### Fixed

- `actionlint-py` split into its own `[actionlint]` optional dependency group; `just install` falls back to a system `actionlint` binary when present, avoiding binary-download failures in network-restricted environments. (#56)
- Generated `index.md` files are always written with LF line endings regardless of platform. (#81)

---

## [0.3.0] - 2026-06-25

### Added

- **`_directory.yml` sidecar support**: directory-level metadata loaded from `_directory.yml` (or `_meta.yml`) files alongside concepts. (#48, PR #65)
- **`okf validate --quiet` / `-q`**: suppress JSON output and rely on exit code. (#49, PR #66)
- **Python 3.12 and 3.13 support** with a multi-version CI test matrix. (#51, PR #67)
- **`okf list-concepts --with-content`**: include raw Markdown body in listing output for clean corpus export. (#59, PR #70)
- **`okf list-bundles`**: discover and list all configured bundles from the project config. (#71, PR #74)
- **`title` attribute on `MarkdownLink` and `ConceptLink`**: exposes the optional Markdown link title `[text](url "title")`. (#64, PR #75)
- **ruff and mypy coverage** extended to `src/`, `tests/`, and CI scripts. (#72, PR #73)

### Fixed

- `list-bundles` output sorted deterministically by bundle name. (#76, PR #77)

---

## [0.2.1] - 2026-06-24

### Added

- **`okf context` CLI command**: builds deterministic context packs from seed concept IDs. Supports repeatable `--seed`, `--depth`, `--direction` (`outbound` | `inbound` | `both`), and `--budget-chars`. Emits structured JSON with resolved seeds, entries, omitted concept IDs, and problems. (#52)

### Fixed

- `okf index` no longer clobbers a bundle whose root `index.md` declares an unsupported or unparsable future OKF version. (#47)
- `okf_version` frontmatter is preserved in root `index.md` when the config omits `okf_version` and the existing declaration is valid. (#47)
- `--force` flag added to `okf index` to intentionally overwrite a supported existing root version declaration. (#47)

---

## [0.2.0] - 2026-06-24

### Added

- **Concept graph traversal**: `build_bundle_graph()` builds a deterministic link graph from concept documents. `backlinks_to()` and `neighborhood()` support depth-limited bidirectional traversal. `okf graph` CLI command outputs full graphs or concept neighbourhoods as structured JSON, flagging broken internal links.
- **Bundle listings and seed discovery**: `list_concepts()` scans a bundle and identifies entry-point seeds. `okf list-concepts` CLI command with link/backlink counts.
- **Context pack assembly**: `build_context_pack()` assembles ordered concept content from explicit seeds with a configurable character-budget (`budget_chars`). Omitted concept IDs reported in output.
- **Scan snapshot caching**: raw Markdown cached on `ConceptManifestEntry` during scan; reused for graph construction and context assembly, reducing file reads from up to 3 to exactly 1 per run.
- **Robust index parsing**: replaced custom regex parser with a tokenized Markdown parser (`markdown-it-py`). Handles nested list depth, inline formatting (code, links, bold), and is more resilient to non-standard index layouts.

### Fixed

- Windows universal-newline translation caused character-count mismatches in raw content reads.
- `mypy` incorrectly parsed comments as type comments in some cases.
- TOML config paths with backslashes failed in tests on Windows.

---

## [0.1.1] - 2026-06-23

### Fixed

- CLI JSON serialization now handles `MappingProxyType` and `frozenset`/`set` from frozen manifest structures via a dedicated `JSONEncoder`. Affected `okf scan`, `okf validate`, and `okf index`.

---

## [0.1.0] - 2026-06-22

Initial release.

### Added

- **`load_config()` / `discover_config()`**: discovers `okf-core.toml` upward from cwd; supports `[defaults]`, `[taxonomy]`, `[profiles.<name>]`, and `[bundles.<name>]` tables. Unknown keys fail closed.
- **`parse_concept_document()` / `serialize_concept_document()`**: round-trip YAML-frontmatter Markdown; tolerates missing optional fields, rejects invalid YAML.
- **`concept_id_to_path()` / `path_to_concept_id()`**: deterministic bundle-relative addressing; rejects path traversal, reserved filenames, and invalid extensions.
- **`scan_bundle()`**: returns a deterministic `BundleManifest` with concept ID, path, SHA-256, mtime, size, and frozen frontmatter. Malformed documents reported as structured problems rather than aborting the scan.
- **`validate_concept_document()`**: base OKF conformance checks.
- **`validate_bundle()`**: whole-bundle validation with optional profile rules (required fields, taxonomy type checks).
- **`generate_index()` / `parse_index()`**: produce and parse conformant `index.md` files; entries grouped by type and sorted alphabetically; round-trips without loss.
- **CLI (`okf`)**: `scan`, `validate`, `index` commands. JSON to stdout, summary to stderr, exit 2 on config/usage errors.

[Unreleased]: https://github.com/xchewtoyx/okf-core/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/xchewtoyx/okf-core/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/xchewtoyx/okf-core/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/xchewtoyx/okf-core/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/xchewtoyx/okf-core/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/xchewtoyx/okf-core/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/xchewtoyx/okf-core/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/xchewtoyx/okf-core/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/xchewtoyx/okf-core/releases/tag/v0.1.0

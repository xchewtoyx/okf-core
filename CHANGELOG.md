# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **`log_concept_move` write primitive**: added `plan_log_concept_move`/`log_concept_move` to `logs.py`, recording a concept move as a `"Moved"`-labelled, dated `log.md` entry (`[old-concept-id](new-relative-path "moved to")`, capturing both the stable old ID and the literal new on-disk path) so a future fallback resolver (#130) can recover a concept's history after it moves. Re-logging an already-recorded move, or a no-op `old == new`, is idempotent rather than growing the log. This required extending `patching.py`'s `plan_document_change`/`apply_document_change` (#110) with an opt-in `allow_missing`/`original_exists` path so a bundle's very first move can be planned and applied against a `log.md` that doesn't exist yet, rather than requiring callers to pre-create an empty file. (#136)
- **`log.md` parsing, rendering, and loading**: added `parse_log`/`render_log`/`load_log` and the `LogEntry`/`LogDateSection`/`LogParseProblem`/`ParsedLog` dataclasses, giving callers a structured, spec-conformant view of a bundle's change-history log without hand-rolling Markdown parsing. Malformed date headings or entries are reported as `LogParseProblem`s and skipped rather than aborting the whole parse, so #136's upcoming merge/insert logic can build on a tolerant read path. (#145)
- **Markdown round-trip spike**: chose a canonical `mdformat`-based approach for the upcoming #148 patching refactor; see `docs/spikes/149-markdown-round-trip.md`. (#149)
- **Safe Markdown link target rewriting**: added `plan_markdown_link_rewrite` patch primitive to surgically rewrite inline Markdown link destinations in the concept body. (#127)
- **`okf move` CLI command and `plan_file_move`/`move_concept` primitives**: relocates a concept file within a bundle while rewriting inbound Markdown links in every referring file, preserving link-graph integrity across the move. `move_concept` also refreshes an existing `index.md` in the source and/or destination directory to reflect the move. (#69)
- **`entries_for_directory()` / `okf_version_for_index_write()`**: extracted from `okf index`'s internal logic into reusable `index.py` primitives, so other callers (e.g. `move_concept`) can regenerate a single directory's `index.md` without reimplementing its directory/subdirectory bookkeeping. (#69)
- **Cyclomatic complexity linting**: `ruff`'s `C901` (mccabe) rule enabled with `max-complexity = 14` as part of `just lint` / CI. (#125)
- **Unused-argument linting**: `ruff`'s `ARG` rule family enabled as part of `just lint` / CI, catching parameters that are declared but never read. `ignore-variadic-names` is scoped on so a `*args`/`**kwargs` catch-all in a test fake or `monkeypatch` target isn't flagged just for matching a wider real signature. (#172)
- **`hypothesis`-based round-trip property tests**: added `hypothesis` to the `test` extra, plus a shared `deadline=None` settings profile in `tests/conftest.py` (CI runners are slower and less consistent than local machines, so a locally-tuned deadline would just be a source of flaky failures, not a real regression signal). Covers `render_linked_span`/`_render_suffix_span` (`_markdown_inline.py`/`index.py`) and `_build_move_entry` (`logs.py`) against markdown-significant characters (`[`, `]`, `(`, `)`, unbalanced parens, backslashes, double quotes) beyond what hand-picked examples reach, surfacing a previously-untested representability gap: CommonMark's unencoded link-destination grammar has no way to write "empty destination, then a title", so an empty href can't carry a title through either function's output. `AGENTS.md` now requires this test pattern for any future function that interpolates a dynamic value into Markdown link/reference syntax. (#173)
- **`match`-exhaustiveness linting**: `mypy`'s `exhaustive-match` error code enabled repo-wide via `[tool.mypy] enable_error_code` in `pyproject.toml`, so a `match` statement missing a case for an enum member becomes a CI failure instead of silent runtime fallthrough. (#145)
- **`okf graph-repair` CLI command and `plan_graph_repair`/`repair_graph` primitives**: repairs broken concept links whose target moved, via a new pluggable `okf_fetch_moved_concept_path` hook; links left unresolved (no plugin registered, a registered plugin returned `None` for that concept ID, its resolved path can't be expressed in the link's original style, or a `DocumentChangePlanningError` in one referring file) are reported with a distinguishing reason rather than aborting the run. (#63)
- **`freezegun` added to the `test` extra**: `AGENTS.md`'s Testing Guidelines now require any test that exercises date/time-boundary logic to freeze time explicitly (e.g. via `freeze_time`) instead of reading the real clock at test-run time. `test_plan_uses_real_today_by_default` (`tests/test_log_writing.py`) — previously bounded against flakiness by snapshotting the real UTC date before and after the call, which suppressed failures near a day rollover without ever exercising the rollover itself — now freezes an instant in the last minute of a UTC day and asserts the plan's date matches it exactly. (#174)
- **`check_readme_exports.py` CI check**: added `.github/scripts/check_readme_exports.py`, wired into `just ci` (`check-readme-exports` recipe) and the `lint-and-format` GitHub Actions job, comparing every `okf_core` name README.md documents (via `okf_core.NAME` prose, `from okf_core import (...)` code blocks, and call-signature spans in the "Python Library API" section) against `okf_core.__all__`. This is the regression guard for the exact class of bug PR #163 fixed — a primitive documented in README.md but missing from `__all__`, so importing it as documented raised `ImportError` — catching it automatically instead of relying on a future review to notice by hand. (#175)
- **Branch coverage and a coverage-diff CI gate**: enabled `[tool.coverage.run] branch = true` in `pyproject.toml` and added a PR-only `coverage-diff` GitHub Actions job plus a matching `just coverage-diff` recipe (wired into `just ci`), running `pytest --cov-branch` and gating `diff-cover` on the PR's new/changed lines (`--fail-under=90`). Line coverage alone was repeatedly found hiding untested error branches across this milestone's postmortem (e.g. #145's `parse_log` fixes); branch coverage catches an exercised-but-unbranched `if`/`except` that line coverage reports as fully covered, and scoping the gate to diff-changed lines (rather than the whole-codebase aggregate) means it flags newly-introduced gaps without blocking on pre-existing debt. `pytest`/`just test` stay plain — coverage collection is opt-in via `just coverage-diff`, not part of every test run. (#176)
- **Postmortem delivery-loop design spike**: chose a manual-plus-milestone-close trigger, a fixed three-source evidence set (session handoff, PR review-comment history, milestone-filtered issue lists), and scoped `general-purpose` Agent dispatch (no new agent type) for a future repeatable delivery-loop postmortem process, reusing `deliver-milestone`'s "Stay thin" and diagnostic-dispatch patterns rather than a parallel approach; see `docs/spikes/177-postmortem-delivery-loop-design.md`. (#177)

### Changed

- **ADR mechanism established; delivery-loop instructions aligned with the v0.5.0 re-plan**: added `docs/decisions/` as the project's architecture-decision-record mechanism and updated the milestone-planner/reviewer/approver instructions and `AGENTS.md` to stop enforcing the byte-identical-preservation contract the re-plan found was never actually required. (#193)
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
- `plan_log_concept_move()` no longer derives its proposed `log.md` content from a separate, earlier read of the file than the one `plan_document_change` hashes as the plan's baseline: a concurrent edit landing between those two reads could pass the apply-time hash check (which only compared against the later read) while still being silently discarded, since the proposed content reflected the earlier one. `plan_document_change_from_reader`, a new sibling of `plan_document_change` in `patching.py`, closes this by having callers derive their proposed content from within the same read the plan hashes, rather than supplying an already-computed string; `plan_log_concept_move` now uses it instead. (#136)
- `plan_log_concept_move()` now refuses to plan against an existing `log.md` that `parse_log` reports any `LogParseProblem` against, raising `DocumentChangePlanningError` instead of proceeding. `render_log(parse_log(...))`'s re-render has no way to reconstruct content the parser couldn't represent (a stray block under a date heading, an unexpected nested block in an entry, a malformed date heading, ...), so silently rewriting such a log would permanently delete that content — a real risk for any `log.md` not written exclusively by this code path (hand-edited, written by an older tool, or just containing a typo). A missing or already-empty `log.md` is unaffected, since `parse_log("")` never reports problems. (#136)
- `plan_document_change_from_reader` is now re-exported from `okf_core` (`from okf_core import plan_document_change_from_reader`), matching every other `patching.py` planning primitive (`plan_document_change`, `plan_file_move`, `plan_frontmatter_merge`, `plan_markdown_link_rewrite`, `plan_markdown_section_patch`). It had been documented in README.md as part of that surface since its introduction but was missing from `okf_core/__init__.py`'s imports and `__all__`, so importing it as documented raised `ImportError`. (#136)
- `plan_log_concept_move()` now rejects an `old` concept ID containing `[` or `]` with `DocumentChangePlanningError`, rather than embedding it unescaped as the move entry's Markdown link text. `paths.py`'s concept ID validation permits both characters, but an unescaped `[`/`]` there breaks the emitted link's own delimiter matching, corrupting both `parse_log`'s later read of the entry and `_move_already_logged`'s dedup comparison. Escaping was considered and rejected in favor of rejection: `logs.py`'s parser reconstructs entry text from parsed tokens on every read and does not re-escape brackets found in already-parsed link text, so an escaped-on-write entry would stop comparing equal to itself after a read/render round trip. (#136)
- `apply_document_change()` now detects a bundle-relative ancestor directory that was swapped for a symlink between planning and apply for a plan built with `allow_missing=True` (i.e. the target didn't exist yet at planning time), the same protection `plan_document_change`'s existing-target apply path already had. Previously only `plan.path.exists()`/`.is_symlink()` were checked before creating the file, which missed the case where the swapped ancestor's resolved target has nothing at the final filename yet — letting the file be created outside the bundle root through the symlink instead of being rejected as a conflict. (#136)
- `plan_log_concept_move()` now rejects a `new` relative path containing an unbalanced `(` or `)` with `DocumentChangePlanningError`, rather than embedding it unescaped as the move entry's Markdown link destination. The `old`-side bracket check added above only ever covered link *text*; the href side had the same class of bug and, unlike the bracket case, was not hypothetical — `normalizeLink` leaves parens untouched and `path_to_concept_id`/`_resolve_move_target` don't reject them, so a real on-disk path like `topics/foo)bar.md` silently truncated the recorded destination at the first `)` while `parse_log` reported zero problems against the result. Balanced parens (e.g. `topics/foo(bar)baz.md`) are left alone, since CommonMark's link-destination grammar consumes those as one href correctly. (#136)
- `plan_document_change_from_reader()`'s `build_proposed_content` callback result is now type-checked the same way `plan_document_change()` already checks its own `proposed_content` argument: a callback that returns something other than `str` (e.g. `None` from a missing `return`) now raises `DocumentChangePlanningError` instead of a raw `AttributeError` once the value reaches UTF-8 encoding. (#136)
- `plan_document_change()`/`plan_document_change_from_reader()` called with `allow_missing=True` now reject a target whose parent directory doesn't exist (or isn't a directory), raising `DocumentChangePlanningError` at plan time instead of returning a plan `apply_document_change` can never actually apply — its `tempfile.mkstemp` call needs that directory to already exist, and previously only failed with a raw `FileNotFoundError` once a caller tried to apply the unusable plan. (#136)

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

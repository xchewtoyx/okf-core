# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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

[0.4.0]: https://github.com/xchewtoyx/okf-core/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/xchewtoyx/okf-core/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/xchewtoyx/okf-core/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/xchewtoyx/okf-core/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/xchewtoyx/okf-core/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/xchewtoyx/okf-core/releases/tag/v0.1.0

# okf-core

`okf-core` is a reusable Python toolkit for working with Open Knowledge Format (OKF) bundles in local repositories.

OKF itself is deliberately simple: a bundle is a directory tree of UTF-8 Markdown files with YAML frontmatter. The format is readable by humans and ordinary tools. This project is not intended to replace that openness with a hosted knowledge service or a required model API. Instead, `okf-core` adds a deterministic layer for discovery, validation, graph traversal, search context, and safe updates across OKF-style documents.

The target pattern is semi-opaque:

- Markdown remains available on disk for humans, editors, scripts, and agents.
- Consistency-sensitive operations go through deterministic library or CLI functions instead of ad hoc filesystem crawling and rewriting.
- Consuming projects can keep their own repository layouts, document taxonomies, model providers, and agent runtimes.

## Status

`okf-core` is under active development. It provides a fully functional Python library and CLI for scanning, validating, indexing, and traversing Open Knowledge Format bundles.

## Installation

`okf-core` is distributed via a self-hosted PEP 503 simple index on GitHub Pages. It is not published on PyPI.

**pip:**

```sh
pip install okf-core \
  --index-url https://xchewtoyx.github.io/okf-core/simple/ \
  --extra-index-url https://pypi.org/simple/
```

**uv** (`pyproject.toml`):

```toml
[[tool.uv.index]]
url = "https://xchewtoyx.github.io/okf-core/simple/"
```

Then add `okf-core` to your dependencies as usual.


## Configuration

The default project configuration file is `okf-core.toml`.

`load_config()` searches upward from the current working directory for `okf-core.toml`. If no config file is found, it returns built-in defaults rooted at the current working directory. Callers may pass `config_path` to load a specific file, `project_root` to choose a discovery/default root, and `overrides` to supply explicit Python API overrides.

Explicit config paths are loaded directly and must exist. When no explicit path is provided, CLI commands use the same behavior: explicit `--config` first, otherwise cwd-upward discovery, otherwise built-in defaults.

Supported top-level tables are:

- `[defaults]`
- `[taxonomy]`
- `[profiles.<name>]`
- `[bundles.<name>]`

Supported `[defaults]` keys are:

- `bundle_root`
- `include`
- `exclude`
- `reserved_filenames`
- `concept_path_strategy`
- `listing_fields`
- `directory_metadata_file` (Non-Spec local tool enhancement: string, defaults to `"_directory.yml"`). The filename of the directory metadata sidecar file used to carry folder-level descriptions/titles.
- `okf_version`

Supported `[taxonomy]` keys are `known_types` and `allowed_types`.

Supported `[profiles.<name>]` keys are `required_frontmatter`, `optional_frontmatter`, nested `taxonomy` settings, and nested `type_fields.<type>` tables. Supported `[bundles.<name>]` keys are the same path/glob/reserved-name settings as `[defaults]`, plus `profile`, `okf_cache_dir`, and `stable_id_field` (see below).

`[profiles.<name>.type_fields.<type>]` scopes required/optional frontmatter fields to a single concept `type`, applied additively on top of the profile-wide `required_frontmatter`/`optional_frontmatter` lists — a type-scoped entry adds requirements or exemptions for that type, it never drops the profile-wide baseline for it. Each type entry supports its own `required_frontmatter` and `optional_frontmatter` keys:

```toml
[profiles.default]
required_frontmatter = ["title"]

[profiles.default.type_fields.platform-implementation]
required_frontmatter = ["platform"]

[profiles.default.type_fields.concept]
optional_frontmatter = ["platform"]
```

With this profile, every concept still needs `title` (the profile-wide requirement). Documents of type `platform-implementation` additionally require `platform`; documents of type `concept` may include `platform` without triggering the unknown-frontmatter-field warning, but it stays optional for that type. Documents of any other type are unaffected — a missing `type_fields` entry for a type behaves exactly as if `type_fields` were not configured at all.

Relative paths are normalized against the resolved project root, and referenced files or directories do not need to exist yet. The exception is `okf_cache_dir`, which is a bundle-only key resolved relative to each bundle's `bundle_root` (see the bundle configuration example below). Unknown config keys fail closed with a configuration error so typos do not silently change behavior.

Built-in defaults are equivalent to:

```toml
[defaults]
bundle_root = "."
include = ["**/*.md"]
exclude = []
reserved_filenames = ["index.md", "log.md"]
concept_path_strategy = "relative-path"
listing_fields = []
directory_metadata_file = "_directory.yml"
# okf_version = "0.2"
```

If no bundles are declared, `okf-core` exposes one resolved bundle named `default` using the project defaults. Declared bundles inherit project defaults and may override them per bundle. Multiple OKF areas in one repository should be configured as separate named bundles, each with one `bundle_root`. Caching can be enabled for individual bundles by setting `okf_cache_dir`:

```toml
[bundles.docs]
bundle_root = "docs"
okf_cache_dir = ".okf-cache"
```

### Non-Standard Extensions

Optional, tool-specific features that do not comply with the base OKF v0.2 specification—such as `stable_id_field`—are deliberately kept out of the global `[defaults]` configuration. This keeps project-wide default settings strictly compliant with the base specification, ensuring that non-standard behaviors are explicitly scoped and opted-into per bundle.

For example, to configure a stable metadata-based ID (to track document renames or moves downstream without modifying the path-derived concept ID in the manifest), set `stable_id_field` on a specific bundle:

```toml
[bundles.docs]
bundle_root = "docs"
stable_id_field = "id"
```

`okf_version` is optional. When set to a supported OKF version such as `"0.2"`, the bundle-root `index.md` generated by `okf index` includes `okf_version: "0.2"` frontmatter, as allowed by OKF v0.2 §12. When unset, generated root indexes preserve an existing supported root `okf_version` declaration by default; pass `okf index --force` to overwrite without preserving it. Versions must use `<major>.<minor>` form, and this release only accepts configured versions up to `0.2`.

Read-only operations consume bundles best-effort when a root index declares a newer OKF version, matching the OKF consumer guidance. Write operations fail closed when the root version is newer than this tool understands.

## Command-Line Interface (CLI)

Install the package to register the `okf` command:

```sh
pip install -e .
```

All commands load `okf-core.toml` by searching upward from the current working directory. Use `--config PATH` to specify a config file explicitly and `--bundle NAME` to select a named bundle (default: `default`).

Call `okf --version` to print the package version.

By default, commands emit machine-readable JSON on stdout and a one-line human-readable summary on stderr. The `--quiet` or `-q` option is supported on validation and file generation commands (`scan`, `validate`, `index`) to suppress command output and summary. Output-only query commands (such as `list-bundles`, `list-concepts`, `search`, `unlinked-mentions`, `context`, and `graph`) intentionally do not support `--quiet` since running them quietly would result in a complete no-op. Exit codes: `0` success, `1` errors or validation failures, `2` config or usage error.

### `okf list-bundles`

Lists all bundles configured in `okf-core.toml`:

```sh
okf list-bundles [--config PATH]
```

Output: `{"config_path": "...", "bundles": [...]}`

Each bundle entry includes `name`, `bundle_root`, `profile`, and `okf_version`. Bundles are emitted in ascending alphabetical order by name. A human-readable count is written to stderr (`Found N bundle(s)`). Unlike other commands, `list-bundles` accepts only `--config` — it operates at the config file level rather than selecting a single bundle.

Exits `2` on config or usage error.

### `okf scan`

Scans a bundle and emits a manifest:

```sh
okf scan [--config PATH] [--bundle NAME] [--quiet]
```

Output: `{"bundle": "...", "concepts": [...], "problems": [...]}`

Each concept entry includes `concept_id`, `path`, `size`, `sha256`, and `frontmatter`. Scan problems (parse errors, etc.) are non-fatal and appear in `problems` with `path`, `kind`, and `message` fields; exit code is always `0` under normal execution.

Use `--quiet` or `-q` to suppress command output and summary. When quiet mode is active, the command will exit `1` if any scan problems occurred. Configuration/load errors (which exit with code `2`) are not suppressed.

### `okf validate`

Validates all concept documents against the configured profile, and checks every concept-bearing directory's committed `index.md` for drift against its directory:

```sh
okf validate [--config PATH] [--bundle NAME] [--quiet]
```

Output: `{"bundle": "...", "findings": {"path": [{"severity": "...", "message": "...", "field": "...", "line": ...}]}}`

Only paths with findings appear as keys. `line` is the 1-based source line the finding pertains to when known (e.g. a footnote occurrence in the body), and `null` otherwise (e.g. a frontmatter-only finding). Exits `1` if any error-severity findings are present; exits `0` if there are only warnings or no findings.

Findings include an attribution consistency check (spec §5.1): a `[^label]` footnote reference or `[^label]:` definition in the body whose label has no matching `sources[].id` in frontmatter is an error, and a declared `sources[].id` that no footnote ever cites is a warning (an unreferenced source is legal, so this is advisory rather than an error). A document with neither footnotes nor `sources` reports nothing from this check. This check recognizes only labels shaped like a safe slug (letters, digits, and hyphens; see `check_attribution_consistency` below) — a `sources[].id` that isn't slug-shaped can never be matched by a footnote reference, so it will always surface as the "unreferenced source" warning. Image alt text (`![...](...)`) is out of scope entirely: a `[^label]`-shaped construct inside an image's alt text, whether it exactly matches a label or merely contains one (e.g. `![Diagram [^label]](url.png)`), is never recognized as footnote syntax. A `sources[].id` cited only from image alt text will therefore always surface as the "unreferenced source" warning too — see `src/okf_core/attribution.py`'s module docstring ("Images are out of scope") for why.

Findings also include an index freshness check: for every concept-bearing directory that has a committed `index.md`, the command regenerates that directory's index in memory (the same way `okf index` would) and compares it against the committed file via `diff_index()` (see [Index Files](#index-files) above) — semantic (canonical-form) comparison, not byte-for-byte, so formatting-only differences and manually reordered entries never report as drift. Findings appear keyed at the `index.md` path itself: a concept added since the last regeneration, a stale entry pointing at a concept that no longer exists, a title/description that no longer matches the concept, and a malformed hand-edited list item are all reported this way. A directory with no committed `index.md` at all is out of scope for this check — that is a "missing index" concern, not drift. Every index-drift finding is `severity="warning"`: it is advisory only (a bundle may curate an index by hand) and never affects the exit code, regardless of how many are found.

Use `--quiet` or `-q` to suppress validation findings (on stdout) and the validation summary (on stderr), leaving the exit code as the sole signal for validation success/failure. Note that configuration/load errors (which exit with code `2`) are still printed to stderr.

### `okf list-concepts`

Lists addressable concept documents for seed discovery:

```sh
okf list-concepts [--config PATH] [--bundle NAME] [--with-graph-counts] [--with-content]
```

Output: `{"bundle": "...", "concepts": [...], "problems": [...], "orphans": [...]}`

Each concept entry includes `concept_id`, `path`, `type`, `title`, `description`, promoted `fields`, preserved `frontmatter`, optional `outbound_link_count` / `inbound_link_count`, optional `pagerank` score, and optional raw Markdown body `content` (with frontmatter stripped). Graph metrics (`outbound_link_count`, `inbound_link_count`, `pagerank`) are `null` unless `--with-graph-counts` is supplied. The top-level `orphans` array lists concept IDs with no inbound or outbound links; it is empty unless `--with-graph-counts` is supplied. `content` is `null` unless `--with-content` is supplied. Listing problems are non-fatal and include `concept_id`, `path`, `kind`, and `message`.

### `okf search`

Searches valid concept documents with local SQLite FTS5 lexical search:

```sh
okf search QUERY [--config PATH] [--bundle NAME] [--limit N] [--no-refresh]
```

Search requires bundle-level `okf_cache_dir` and reuses the existing `okf-cache.db` SQLite cache. It does not create a separate search database. By default the command refreshes the search index from the current bundle scan before querying; pass `--no-refresh` to search the current FTS rows only.

Output: `{"bundle": "...", "query": "...", "results": [...], "problems": [...]}`

Each result includes `concept_id`, `path`, `title`, `description`, `score`, and `snippets`. Search covers title, description, configured `listing_fields`, and Markdown body text. Search is intended for scale support and seed discovery before context packing; `index.md`, `list-concepts`, and explicit context seeds remain the primary progressive-disclosure surfaces.

Missing `okf_cache_dir`, config errors, unknown bundles, and invalid limits exit `2`.

### `okf unlinked-mentions`

Finds visible concept-title mentions that are not already Markdown links, and optionally writes selected suggestions back into their source concept as inline Markdown links:

```sh
okf unlinked-mentions [--config PATH] [--bundle NAME] [--no-refresh] [--apply] [--select SOURCE_ID:TARGET_ID ...] [--heading TEXT] [--heading-level N]
```

The command requires bundle-level `okf_cache_dir`. By default it refreshes the persistent FTS index before finding suggestions; `--no-refresh` uses its current rows.

Without `--apply` (the default, unchanged read-only behavior), output contains `bundle`, `suggestions`, and non-fatal `problems`. Each suggestion includes `source_concept_id`, `source_path`, `target_concept_id`, `target_path`, `target_title`, `target_href` (the Markdown link destination that would be written, relative to the source concept), and the annotated FTS excerpt `matched_text`.

With `--apply`, every discovered suggestion is written into its source concept's body as `- [target_title](target_href)`, appended under a `--heading` section (default `## See also`, `--heading-level` default `2`) -- pass one or more `--select SOURCE_ID:TARGET_ID` to restrict which discovered suggestions are written instead of applying all of them; a `--select` value with no matching discovered suggestion exits `2` without writing anything. Suggestions targeting the same source concept are grouped into a single write to that file. A suggestion whose target already has a link somewhere in the section is skipped, so re-running `--apply` (with an overlapping or identical selection) is idempotent rather than duplicating bullets. Output is `{"bundle": "...", "applied_suggestions": [...], "updated_files": [...], "problems": [...]}`.

Missing `okf_cache_dir`, config errors, unknown bundles, a malformed `--select` value, and an unmatched `--select` pair exit `2`. Empty suggestions and non-fatal read or parse problems exit `0`. A write-planning failure (e.g. an invalid `--heading-level`, or an unrelated reference-style link definition in a target file) exits `1`.

### `okf context`

Builds a deterministic context pack from one or more seed concept IDs:

```sh
okf context [--config PATH] [--bundle NAME] --seed CONCEPT_ID [--seed CONCEPT_ID ...] [--depth N] [--direction outbound|inbound|both] [--budget-chars N]
```

Output: `{"bundle": "...", "seeds": [...], "entries": [...], "omitted_concept_ids": [...], "problems": [...]}`

Each entry includes `concept_id`, `path`, `title`, `selection_reason`, `graph_distance`, `char_count`, and raw Markdown `content`. Seeds are de-duplicated, kept in input order, and emitted before graph-expanded concepts. The `seeds` field contains only valid resolved seed IDs; unknown seeds appear in `problems` and are omitted from `seeds` and `entries`. `--depth` controls graph expansion, `--direction` selects outbound links, backlinks, or both, and `--budget-chars` applies the same stable prefix budget used by the Python API. Concepts excluded by budget appear in `omitted_concept_ids` without making the command fail.

Unknown seeds and read problems appear in `problems` and exit `1`. Scan errors, config errors, and unknown bundles exit `2`.

### `okf index`

Generates `index.md` for a directory within a bundle:

```sh
okf index [--config PATH] [--bundle NAME] [--directory PATH] [--force] [--quiet] [--recurse]
```

`--directory` defaults to the bundle root. Scans the bundle, collects concepts and immediate subdirectories for the target directory, calls `generate_index()`, and writes `index.md` to that directory. If `--recurse` is provided, it recursively generates or updates `index.md` for the target directory and all nested subdirectories that contain concept documents recursively (non-concept-bearing directories that contain no concepts directly or recursively are ignored, and do not have an `index.md` generated). For the bundle root only, configured `okf_version` is emitted as frontmatter. Before writing any index in a bundle, the command checks the bundle-root `index.md`; if that file declares an unsupported, invalid, or unparsable `okf_version`, the command leaves the bundle untouched. Read-only commands such as `scan`, `list-concepts`, and `graph` continue best-effort consumption of newer-version bundles.

When config omits `okf_version`, root index generation preserves an existing supported root `okf_version` declaration by default. `--force` intentionally overwrites the root index without preserving that declaration, but it does not bypass unsupported-version write safety.

Output: A JSON status dictionary, or a JSON array of status dictionaries when `--recurse` is specified:
`{"path": "...", "entries": N, "problems": [...], "scan_problems": [...], "excluded_reserved_files": [...], "write_conflict": null}`

`entries` is the number of entries actually written (candidates minus skipped). `problems` lists index-level skipped entries (e.g. missing `type` field). `scan_problems` lists parse/read failures for files in the target directory that were silently omitted from the index. `excluded_reserved_files` lists regular files directly in the target directory whose filenames matched configured or spec-reserved names and were therefore ignored as concept documents; when no entries are written and reserved files were present, the stderr summary calls this out so users can distinguish an empty directory from one containing only reserved files. `write_conflict` is `null` when that directory's `index.md` was written (or left as-is because nothing changed) and a message string when a concurrent change to that file was detected between planning the write and applying it -- the existing `index.md` is left untouched in that case, the same as `okf move`'s stale-content conflict below. When `write_conflict` is set, `entries` is always `0`, regardless of how many candidate entries the discarded, never-written body would have contained -- the field never reports a count for entries that were not actually written. The stderr summary reflects the same rule: on a conflict, only the conflict message (and a "not written" note) is printed, never the `Wrote index.md ...` success line.

Exits `1` if any entries were skipped, any scan problems occurred, or any directory's write was refused due to a conflict, in any targeted directories; exits `0` on clean generation.

Use `--quiet` or `-q` to suppress command output and summary. Configuration/load errors (which exit with code `2`) are not suppressed.

### `okf graph`

Builds a deterministic graph from Markdown links in concept bodies:

```sh
okf graph [--config PATH] [--bundle NAME]
okf graph [--config PATH] [--bundle NAME] --concept CONCEPT_ID [--depth N]
okf graph [--config PATH] [--bundle NAME] --broken
```

Full output includes `concepts`, resolved `links`, `broken_links`, and `problems`. `--concept` emits outbound links, backlinks, broken links from that concept, and a depth-limited `neighborhood`. `--broken` emits only broken internal concept links and graph problems.

Each link entry includes `source_concept_id`, `source_path`, `text`, `target`, `title`, `target_path`, and `target_concept_id`. `title` is the CommonMark link title attribute (e.g. `[B](b.md "related")` → `"related"`); it is `null` when no title is present or when the title is an empty string — both are treated as absent.

Broken links do not make the command fail. Unknown bundles, unknown concept IDs, invalid depth values, and config errors exit `2`.

### `okf graph-report`

Writes per-bundle `GRAPH_REPORT.md` / `graph.json` artifacts and a cross-bundle `SUMMARY.md` maintenance rollup (not a merged graph):

```sh
okf graph-report [--config PATH] [--bundle NAME]... [--output DIR] [--json]
```

`--bundle` is repeatable and defaults to every configured bundle; an unknown name exits `2`. `--output` defaults to `wiki-graph-out/` at the project root and is resolved from the current working directory when given. The resolved output directory must not be equal to or inside any configured bundle root or `fleeting/`. Every file write and stale-artifact unlink `resolve()`s the final path and refuses unless it is a strict descendant of that output directory and not equal to or inside a forbidden root — so `--output` at the project root cannot write `docs/GRAPH_REPORT.md` into `[bundles.docs]`, and a leftover `SUMMARY.md` or `<slug>/GRAPH_REPORT.md` symlink into a bundle is refused rather than followed. When the default would land inside a `bundle_root = "."` project, pass `--output` outside those authoring surfaces. `SUMMARY.md` covers the selected bundles only (with a note when that is a requested subset; a selected bundle that produced no row is reported as omitted, not as a subset). `--json` prints a compact run-result (rows, written paths, subset flag) on stdout; without it, a one-line summary goes to stderr. Config, unknown-bundle, and path-guard failures exit `2`; model, analysis, or render failures exit `1`.

### `okf stable-id`

Retrieves, generates, or writes a stable ID for a concept:

```sh
okf stable-id [CONCEPT_ID] [--config PATH] [--bundle NAME] [--force] [--write]
```

This command interacts with the bundle's `stable_id_field` (which must be configured on the bundle).
- **Without `CONCEPT_ID`:** Generates a fresh UUID4, prints it to `stdout`, and exits.
- **With `CONCEPT_ID`:**
  - Resolves the concept ID to its path.
  - If a stable ID already exists in the frontmatter, it prints it to `stdout`.
  - If the stable ID is missing (or if `--force` is specified), it generates a new UUID4 and prints it to `stdout`.
  - If `--write` is specified, it writes the new stable ID back to the concept file on disk, printing a confirmation message to `stderr`. The write goes through the same plan/apply safety envelope as `okf move`: it exits `1` and leaves the file unchanged if a write-safety refusal applies, or if the file's content no longer matches what was read when the write was planned (including the target having been replaced by a symlink in the meantime).

### `okf move`

Relocates a concept file within a bundle, rewriting every other file's inbound Markdown links so the bundle's link graph stays intact:

```sh
okf move SOURCE DEST [--config PATH] [--bundle NAME] [--dry-run]
```

SOURCE and DEST are concept file paths, not concept IDs: relative to the bundle root, or absolute paths that resolve inside it. DEST must be a full path ending in a valid concept path; there is no shell-`mv`-style "move into a directory" shorthand. The concept file is relocated last, after every referring file's link has been rewritten, so a failure partway through always leaves the concept file at a well-defined location and the command is safe to re-run to completion. Moving a file to its own current path is a no-op. Exits `2` for an invalid SOURCE/DEST (wrong extension, reserved filename, escapes the bundle root); exits `1` for a missing source, an existing destination, a symlinked SOURCE/DEST argument, a write-safety refusal, or a stale-content conflict.

If SOURCE's old directory and/or DEST's new directory already has an `index.md`, it is regenerated from a fresh scan to reflect the move (an index that doesn't already exist is never created as a side effect). This refresh goes through the same plan/apply safety envelope as the concept file move itself, so a concurrent edit to an affected `index.md` between planning and applying the refresh is also reported as the same stale-content conflict, exiting `1` without overwriting it. `--dry-run` does not preview which indexes would be regenerated, since this refresh only runs after a real move.

### `okf log-append`

Appends one agent-supplied entry to the bundle-root `log.md`, letting the library own the file's structure:

```sh
okf log-append CONTENT [--date YYYY-MM-DD] [--kind LABEL] [--config PATH] [--bundle NAME] [--dry-run]
```

CONTENT is the entry's prose; the library locates or creates the correct `## YYYY-MM-DD` date section, preserves reverse chronology, and leaves every other entry untouched, so the caller never reads or parses `log.md` itself. `--date` defaults to today (UTC) and selects which date section the entry is inserted under; `--kind` (e.g. `Update`, `Creation`) becomes the entry's bold label convention word. Unlike `okf move`'s log entries, appended entries are never deduplicated — repeating the same CONTENT records it again rather than being treated as idempotent. Output: `{"bundle": "...", "path": "...", "changed": true}` (`"would_change"` instead of `"changed"` for `--dry-run`).

Exits `2` for an invalid `--date` value or other config/usage error; exits `1` when CONTENT/`--kind` cannot be recorded unambiguously (multi-paragraph or otherwise non-flat content, an unrepresentable label, or content that would be misread as a labelled entry), when the existing `log.md` has unparseable content that a rewrite would silently drop, or for a stale-content write conflict.

### `okf source-add`

Adds one OKF v0.2 §5.1 `sources` provenance entry to a concept document's frontmatter:

```sh
okf source-add PATH --resource RESOURCE [--id ID] [--title TITLE] [--author ACTOR] [--usage-count N] [--last-modified YYYY-MM-DD] [--config PATH] [--bundle NAME] [--dry-run]
```

PATH is the concept file, relative to the bundle root or an absolute path resolving inside it. `--resource` is the only required field (a URL, bundle-relative path, or free-text scope description); every other option is an optional §5.1 field. The library owns `sources` list bookkeeping: an absent list is created, an existing list's order and content are preserved, and a source whose identity (`--id`, falling back to `--resource`) already matches an entry in the list is a no-op — the existing entry is left completely untouched rather than merged, so nothing is written. Every other frontmatter key, including the top-level `usage_window` sibling, is left untouched. Output: `{"bundle": "...", "path": "...", "changed": true}` (`"would_change"` instead of `"changed"` for `--dry-run`).

Exits `2` for an invalid `--last-modified` value, a missing `--resource`, or other config/usage error; exits `1` for malformed candidate/existing `sources` content, or for a stale-content write conflict.

### `okf stamp-generated`

Stamps a concept document's top-level `generated` trust record (OKF v0.2 §5.2):

```sh
okf stamp-generated PATH --by ACTOR [--at ISO8601] [--config PATH] [--bundle NAME] [--dry-run]
```

`--by` is a required actor string (§7: `<producer>/<version>`, `human:<id>`, or `process:<id>`); `--at` is an ISO 8601 datetime with a UTC offset (e.g. a trailing `Z`), defaulting to the real current UTC datetime when omitted. Both are validated before any file is touched. Setting `generated` to a value that already matches the document's current value is a no-op — nothing is written. Output: `{"bundle": "...", "path": "...", "changed": true}` (`"would_change"` instead of `"changed"` for `--dry-run`).

Exits `1` for an invalid `--by`/`--at` value, or for a stale-content write conflict.

### `okf stamp-verified`

Appends one `verified` trust event to a concept document's frontmatter (OKF v0.2 §5.2):

```sh
okf stamp-verified PATH --by ACTOR [--at ISO8601] [--config PATH] [--bundle NAME] [--dry-run]
```

`--by` and `--at` follow the same validation and default as `stamp-generated`. The library owns `verified` list bookkeeping: an absent value is created, an existing bare `{by, at}` mapping (spec §5.2's single-verifier shorthand) or list is read and normalized only in memory, and an event whose exact `--by`/`--at` pair already matches an existing entry is a no-op — unlike `source-add`'s identity rule, two checks by the same actor at different times are distinct events (§5.2), so only an *exact* match is a no-op, and an existing bare-mapping value is not rewritten into list form just to canonicalize it when nothing is appended. A genuinely new event is appended, and the field is always written back as a list from that point on. Output: `{"bundle": "...", "path": "...", "changed": true}` (`"would_change"` instead of `"changed"` for `--dry-run`).

Exits `1` for an invalid `--by`/`--at` value, malformed existing `verified` content, or a stale-content write conflict.

### `okf stamp-status`

Sets a concept document's top-level `status` lifecycle field (OKF v0.2 §5.4):

```sh
okf stamp-status PATH --status {draft|stable|deprecated} [--config PATH] [--bundle NAME] [--dry-run]
```

`--status` must be one of `draft`, `stable`, or `deprecated`. Setting `status` to its already-current value is a no-op. Output: `{"bundle": "...", "path": "...", "changed": true}` (`"would_change"` instead of `"changed"` for `--dry-run`).

Exits `2` for an invalid `--status` choice (Click's own usage-error handling); exits `1` for a stale-content write conflict.

### `okf stamp-stale-after`

Sets a concept document's top-level `stale_after` lifecycle field (OKF v0.2 §5.5):

```sh
okf stamp-stale-after PATH --stale-after YYYY-MM-DD [--config PATH] [--bundle NAME] [--dry-run]
```

`--stale-after` is an absolute ISO 8601 calendar date (not a datetime — a timestamp is rejected, since §5.5 defines an absolute date). Setting `stale_after` to its already-current value is a no-op. Output: `{"bundle": "...", "path": "...", "changed": true}` (`"would_change"` instead of `"changed"` for `--dry-run`).

Exits `1` for an invalid `--stale-after` value or a stale-content write conflict.

### `okf graph-repair`

Repairs broken concept links whose target moved, by asking a pluggable hook whether it knows the target's new location:

```sh
okf graph-repair [--config PATH] [--bundle NAME] [--dry-run]
```

Every broken link in the bundle (a link whose target concept doesn't exist at the path it points to) is checked against any plugin implementing the `okf_fetch_moved_concept_path(dead_concept_id, bundle) -> Path | None` hook. If a plugin resolves a dead concept ID to a path, that link's href is rewritten to point there; if no plugin resolves it -- including the out-of-the-box default, since no plugin ships with `okf-core` today -- the link is reported as unresolved rather than causing a failure. A link whose target escapes the bundle root entirely (no concept-id-shaped target to look up) is also reported unresolved. Exits `1` only for an operational failure: a scan/parse problem elsewhere in the bundle (which could be hiding broken links, so the run aborts rather than risk an incomplete repair) or an unrelated write-safety refusal. Unresolved links never affect the exit code -- that's an expected steady state, not an error; check the `unresolved_links` field in the JSON output if you need to know whether anything is still broken.

If a broken link's containing file also has an unrelated reference-style link definition anywhere in it, planning that file's rewrite fails -- but only that file's link(s) are downgraded to unresolved; every other file's resolvable links are still repaired.

### `okf orient`

Shows onboarding and orientation guidance for OKF bundles:

```sh
okf orient
```

Emits a well-structured Markdown document providing an onboarding overview, a common configuration example, and discovery pointers for further commands and options.

## Python Library API

Import `okf_core` to programmatically interact with bundles:

```python
from okf_core import (
    build_bundle_graph,
    concept_id_to_path,
    load_config,
    parse_concept_document,
    scan_bundle,
)

config = load_config()
document = parse_concept_document("---\ntype: concept\n---\nBody\n")
path = concept_id_to_path("topics/example", config.bundles["default"])
manifest = scan_bundle(config.bundles["default"])
graph = build_bundle_graph(config.bundles["default"], manifest)
```

### Concept Documents

`parse_concept_document()` parses a Markdown string into YAML frontmatter and body content. Documents without frontmatter are accepted and return empty frontmatter with the original Markdown as the body. Invalid YAML, unterminated frontmatter, non-mapping frontmatter, duplicate mapping keys at any depth, and non-string top-level frontmatter keys raise `DocumentParseError`.

`serialize_concept_document()` writes a parsed concept document back to Markdown. Unknown frontmatter keys are preserved when callers keep them in the parsed frontmatter dictionary. Documents with empty frontmatter serialize as body-only Markdown.

### Safe Document Changes

Every write primitive below — YAML frontmatter (`plan_frontmatter_merge`) and
the Markdown-body primitives (`plan_markdown_section_patch`,
`plan_markdown_link_rewrite`, and friends) alike — targets `okf-core`'s
documented canonical output form, per `docs/decisions/` (ADR-0001 and
ADR-0002; the Markdown-side decision landed with issue #198). Untargeted
content survives an edit *semantically*, not necessarily byte-for-byte:
headings, lists, tables, code fences, and link titles keep their meaning, but
block spacing, list markers, heading markup (Setext converges to ATX), and
line endings (always LF) may converge to the canonical style on a document's
first edit. This is expected one-time formatting churn, not a defect — a
non-canonical but spec-conformant document is always accepted; there is no
"canonicalize the bundle first" precondition, and a request that doesn't
actually change anything (by canonical/data-model comparison, not source
bytes) never rewrites the file.

`plan_document_change(bundle, path, proposed_content, *, allow_missing=False)`
prepares an inspectable full-content change for one UTF-8 file under a
configured bundle root. The returned `DocumentChangePlan` contains the
original and proposed content, their exact byte-level SHA-256 hashes, the
resolved path and bundle root, an `original_exists` flag, and a `changed`
property. Planning reads the file but never modifies it. Relative paths are
interpreted from the bundle root. By default the target must already exist,
matching every other planning primitive below; pass `allow_missing=True` to
also accept a target that does not exist yet, in which case its original
content is treated as empty (`original_exists=False` records this on the
returned plan) — used by `plan_log_concept_move` below to plan against a
bundle that has no `log.md` yet. With `allow_missing=True`, the target's
parent directory must still exist (and be a directory, not a file);
otherwise planning raises `DocumentChangePlanningError` rather than
returning a plan that can never be applied, since `apply_document_change`
creates the file via a temp file in that same parent directory.

`plan_document_change_from_reader(bundle, path, build_proposed_content, *, allow_missing=False)`
is `plan_document_change`'s counterpart for callers whose proposed content
must be *derived from* the document's current content — parsing it,
inserting something, and re-rendering — rather than already computed.
`build_proposed_content(resolved_path, original_content)` receives the exact
content this call reads once and hashes as the plan's baseline; deriving the
proposal from any other read of the same file (an earlier, separate read
before delegating here) would let a concurrent edit between the two reads go
undetected, since the plan's hash would match whichever read happened last
while the proposed content silently reflected the other one. `plan_log_concept_move`
below uses this rather than `plan_document_change` for exactly this reason.

`apply_document_change(bundle, plan)` rechecks the bundle's OKF version write
safety and verifies that the target still has the planned original hash. A
stale, deleted, or replaced target raises `DocumentChangeConflictError` with
machine-readable `path`, `expected_sha256`, and `actual_sha256` attributes.
For a plan with `original_exists=False`, "still has the planned original
hash" instead means the target must still be missing — one that was created
concurrently after planning raises the same `DocumentChangeConflictError`.
Other planning, safety, and application failures use the corresponding
`DocumentChangeError` subclasses. A `DocumentChangeSafetyError` identifies the
bundle metadata file that made the write unsafe through its `path` attribute.

No-op plans are verified but do not rewrite the target. Changed content is
prepared in the target directory, flushed, and installed with an atomic file
replacement: for an existing target this keeps the original's permission
bits, and for a plan created via `allow_missing=True` where the target still
does not exist, the file is created fresh with a default `0o644` mode. This is
a single-file optimistic-concurrency primitive, not a filesystem lock,
multi-file transaction, or power-loss durability guarantee. It supports
existing regular files, plus (with `allow_missing=True`) a not-yet-existing
target; symbolic links, directories, paths outside the bundle root, and
non-UTF-8 input are rejected. Other focused patch operations are planned
separately.

`plan_markdown_section_patch(bundle, path, heading, body, *, level=1)` builds
the same inspectable `DocumentChangePlan` for one named Markdown section, via
parse → locate/replace the heading's token subtree → re-render canonically
(ADR-0002). Sections are matched case-sensitively by exact parsed Markdown
heading content and level; existing ATX (`# Heading`) and Setext headings are
both supported for *matching*, but output is always canonical ATX — a matched
Setext heading renders as ATX, the same convergence-on-first-touch churn
frontmatter's block-style normalization already documents. A section body
extends through nested lower-level headings and stops before the next heading
of equal or higher level; a replacement `body` containing a heading at or
above the target `level` is rejected (`DocumentChangePlanningError`), since it
would be indistinguishable from the section's own boundary on a later edit.
Multiple matching headings are rejected as ambiguous. Heading input that would
parse differently when generated as ATX Markdown is rejected rather than
producing a section that cannot be matched idempotently.

When no matching heading exists, the section is appended at the end using ATX
syntax. A request whose body is already semantically present under the target
heading (by canonical rendering, not source bytes) is a no-op that rewrites
nothing — the same "no-op never writes" behavior `plan_frontmatter_merge`
documents for frontmatter. Applying the plan uses `apply_document_change()`
and retains its stale-content protection.

`plan_markdown_section_append(bundle, path, heading, lines, *, level=1)` adds
to a section's *existing* body instead of replacing it wholesale: it reads
the section's current body and appends `lines` after it. Content already in
the section, and the rest of the document, survives semantically, not
necessarily byte-for-byte — the same convergence `plan_markdown_section_patch`
documents above: output is always canonical Markdown, so surrounding block
spacing/list-marker/table style outside the target section can also converge
to `mdformat`'s canonical form. An absent section is created the same way
`plan_markdown_section_patch` documents
(appended at the end in ATX syntax); a `lines` element that itself parses to
a heading at or above `level` is rejected for the same reason a replacement
body containing one is. A `lines` element carrying exactly one inline
Markdown link is deduplicated against links already present anywhere in the
section — an element whose link target already appears in the section is
silently skipped, keeping repeated calls with an overlapping `lines` set
idempotent instead of growing duplicate bullets; an element with no inline
link (or more than one) is never deduplicated. When the section already
ends in a bullet list, new list-shaped lines are merged into that existing
list (one combined list) rather than opened as a second, visually distinct
list right after it. Like `plan_source_upsert`, this reads the document
exactly once (`plan_document_change_from_reader`) so the appended body is
always derived from the same content the plan's baseline hash covers. Used
by `plan_link_suggestions_apply`/`apply_link_suggestions` (Graph Operations,
below) to write selected `find_unlinked_mentions` suggestions.

`plan_frontmatter_merge(bundle, path, updates)` builds a safe plan for a
shallow merge of selected top-level YAML frontmatter fields. Frontmatter is
parsed with a round-trip YAML engine (`ruamel.yaml`), mutated in place, and
re-serialized in `okf-core`'s documented canonical form (ADR-0002): key order
is preserved, missing fields are appended in update order, comments attached
to keys the merge does not target survive, output uses block style (not
flow) regardless of the source document's style, and frontmatter always ends
with LF line endings regardless of the source document's line-ending style —
the Markdown body's own line endings are untouched. Quote style on a *touched*
value is not guaranteed to survive; untouched scalars generally keep their
original quoting. Equivalent values (by data model, not by source bytes) and
empty updates produce a no-op; `None` is written as YAML `null`.

A frontmatter block that is non-canonical but otherwise conformant (e.g. a
flow-style list, or non-block indentation) is accepted with no
precondition to canonicalize the file first; its first edit converges the
*whole document* to canonical form — including untouched sibling
collections, not just the key(s) the edit targets — which can produce
one-time formatting churn in that edit's diff. This is expected, not a
defect, per ADR-0002; a no-op merge (nothing actually changes) never
rewrites the file, so an unedited document's formatting is untouched until
an edit actually happens.

Requested values may use plain `str`, `bool`, `int`, finite `float`, `None`,
`datetime.date`, and `datetime.datetime` values, plus recursively nested plain
lists and string-keyed dictionaries. Other Python objects, non-finite floats,
and shared or cyclic containers raise `DocumentChangePlanningError`. Exact
scalar types remain significant: for example, a quoted date string differs
from a YAML date, and a boolean differs from an integer.

These restrictions apply only to values supplied for mutation; they do not
narrow OKF conformance or general frontmatter consumption. Richer untargeted
values and YAML aliases are preserved. A targeted field that participates in
an alias relationship is rejected because changing a shared node cannot
preserve its semantics locally. Malformed or non-mapping frontmatter,
duplicate mapping keys, and invalid update keys are also reported through
`DocumentChangePlanningError`. The merge does not delete fields or
recursively merge nested mappings. Applying its plan uses
`apply_document_change()` and retains the same stale-content protection.

`plan_source_upsert(bundle, path, source)` and `source_upsert(bundle, path,
source)` add or match one OKF v0.2 §5.1 `sources` provenance entry, mirroring
`plan_log_append`/`log_append`'s planning/applying pairing. `source` is a
mapping with a required, non-empty string `resource` (a URL, bundle-relative
path, or free-text scope description) and any of the optional §5.1 fields:
`id` (a non-empty string when present -- it doubles as a Markdown footnote
label for per-claim attribution), `title`, and the credibility signals
`author`, `usage_count`, `last_modified`, plus a per-entry `usage_window`
override. Fields beyond this base shape are not otherwise validated or
interpreted. An explicit `id: None` is treated the same as omitting `id`
entirely, including in the stored shape: it is stripped before writing, so
the persisted entry never carries a literal `id: null`.
`DocumentChangePlanningError` is raised for a malformed
`source` argument, and -- before any write -- for a document whose existing
`sources` frontmatter value is not a list, or whose entries don't meet the
same shape.

Identity is `id`, falling back to `resource`: when an existing entry already
carries `source`'s identity, planning resolves to a semantic no-op -- the
existing entry, and every other entry, is left completely untouched
(byte-for-byte, same position) rather than merged, so `plan.changed` is
`False` and nothing is written. A genuinely new identity is appended to the
end of the list; an absent `sources` key is created fresh as a one-entry
list. Every other frontmatter key, including the top-level `usage_window`
sibling §5.1 describes, is left untouched -- this operation never reads or
writes it. The list is absent when there's nothing to represent it (AC2);
appending or leaving it untouched otherwise preserves every other entry's
content and order (AC4). The write itself, when actually needed, delegates
to the same `_merge_frontmatter` engine `plan_frontmatter_merge` (#195) uses,
applied to the exact read `plan_document_change_from_reader` performs --
never a separate, earlier read -- so a concurrent edit to `sources` between
planning and applying is still reported as `DocumentChangeConflictError`
rather than silently discarded. Applying uses `apply_document_change()` and
retains the same stale-content protection.

`plan_stamp_generated(bundle, path, by, *, at=None)` and `stamp_generated(bundle, path, by, *, at=None)` set a concept document's top-level `generated` trust record (OKF v0.2 §5.2), mirroring `plan_source_upsert`/`source_upsert`'s planning/applying pairing. `by` is a required actor string (§7: `<producer>/<version>`, `human:<id>`, or `process:<id>`); `at` is an ISO 8601 datetime -- a native `datetime.datetime` or a string, either way requiring a UTC offset (e.g. a trailing `Z`) -- defaulting to the real current UTC datetime when omitted (`None`), overridable for deterministic tests (e.g. via `freezegun`). Both are validated before any file is touched, raising `DocumentChangePlanningError` for a malformed actor, an unparseable or offset-naive datetime, or a `datetime.date` passed where a datetime is required. The write delegates entirely to `plan_frontmatter_merge`, so setting `generated` to a value that already matches the document's current value is a no-op (`plan.changed is False`, nothing written); a malformed pre-existing `generated` value is not separately rejected, since `generated` is fully replaced on every stamp rather than preserving history. Applying uses `apply_document_change()` and retains the same stale-content protection.

`plan_stamp_verified(bundle, path, by, *, at=None)` and `stamp_verified(bundle, path, by, *, at=None)` append one `verified` trust event (OKF v0.2 §5.2) to a concept document's frontmatter. `by` and `at` are validated exactly as `plan_stamp_generated` validates them. The document's existing `verified` value is read and validated the same way each candidate event is: an absent key is treated as an empty list; a bare `{by, at}` mapping -- spec §5.2's single-verifier shorthand, which "Consumers MUST treat ... as a one-element list" -- is normalized to a one-element list, purely in memory; a list has every entry validated the same way, and a malformed existing entry raises `DocumentChangePlanningError` before any write (`verified` is append-only history, so a malformed entry is never silently discarded or overwritten, unlike `generated`'s replace-on-write semantics). Identity for the no-op decision is the exact `(by, at)` pair, not just `by`: spec §5.2 treats two checks by the same actor at different times as distinct events, unlike `plan_source_upsert`'s single-field identity key. When an identical event is already present, the existing content -- including an existing bare-mapping shape -- is returned completely untouched, so a true no-op never rewrites even a bare mapping into list form just to canonicalize it. When the event is new, it is appended and the result is always written as a list from that point on, even if the result has only one element. The write itself, when actually needed, delegates to the same `_merge_frontmatter` engine `plan_frontmatter_merge` and `plan_source_upsert` use, applied to the exact read `plan_document_change_from_reader` performs -- never a separate, earlier read -- so a concurrent edit to `verified` between planning and applying is still reported as `DocumentChangeConflictError` rather than silently discarded. Applying uses `apply_document_change()` and retains the same stale-content protection.

`plan_stamp_status(bundle, path, status)` and `stamp_status(bundle, path, status)` set a concept document's top-level `status` lifecycle field (OKF v0.2 §5.4). `status` must be one of `draft`, `stable`, or `deprecated`; anything else raises `DocumentChangePlanningError` before any file is touched. The write delegates to `plan_frontmatter_merge`, so setting `status` to its already-current value is a no-op. Applying uses `apply_document_change()` and retains the same stale-content protection.

`plan_stamp_stale_after(bundle, path, stale_after)` and `stamp_stale_after(bundle, path, stale_after)` set a concept document's top-level `stale_after` lifecycle field (OKF v0.2 §5.5). `stale_after` must be a `datetime.date` or an ISO 8601 `YYYY-MM-DD` string; a `datetime.datetime` is rejected, since §5.5 defines `stale_after` as an absolute date, not a timestamp. The write delegates to `plan_frontmatter_merge`, so setting `stale_after` to its already-current value is a no-op. Applying uses `apply_document_change()` and retains the same stale-content protection.

`plan_markdown_link_rewrite(bundle, path, rewrites)` builds a safe plan to rewrite the target/href destinations of one or more inline Markdown links in the document body, via parse → mutate matching `link_open` token hrefs → re-render canonically (ADR-0002). `rewrites` must be a sequence of `LinkRewrite(old_target, new_target)` instances. The primitive operates strictly on the Markdown body, leaving the frontmatter completely untouched. Duplicate `old_target` inputs (after normalization) are rejected.

Matching is driven entirely by parsing the document with `markdown-it-py`: each link's resolved href is compared against a caller-supplied target normalized the same way, and only real inline links found by the parser are ever rewritten. Text that merely looks like a link — inside code spans, fenced code blocks, or reference-style syntax — is never touched, without needing a separate raw-text scan or safety cross-check. Reference-style links are not supported and cause planning to raise `DocumentChangePlanningError`. `new_target` is normalized the same way `old_target` is matched and re-rendered via `mdformat`'s own bracket-wrapping/escaping and always-double-quoted title style, not the target's original source styling — this keeps a rewritten destination idempotent (a later parse recovers the same value) rather than merely close to the caller's literal input. Applying the plan uses `apply_document_change()` and retains the same stale-content protection.

`plan_file_move(bundle, source, dest)` prepares an inspectable relocation of one existing bundle file, returning a `FileMovePlan` with the resolved source/dest paths and the source's SHA-256 hash. Planning reads and hashes the source but never moves it; source and dest resolving to the same path produces an idempotent no-op plan (`.noop`). `apply_file_move(bundle, plan)` rechecks bundle write safety and the source's current hash, then relocates the file with a create-hard-link-then-unlink sequence rather than an atomic replace: this means a destination that appears concurrently after planning is never silently overwritten (`FileMoveConflictError` is raised instead), at the cost of requiring source and dest to reside on the same filesystem. If the link succeeds but removing the source fails, both copies are left in place rather than losing the document.

### Concept Relocation

`plan_move_concept(bundle, source, dest)` and `move_concept(bundle, source, dest)` compose the primitives above into a concept-aware move: every other file that currently links to the concept at `source` has its link rewritten to point at `dest` (preserving each link's original `#fragment`/`?query` suffix and its relative-vs-bundle-root-anchored style), and only then is the concept file itself relocated. The moved file's own body can also change: a self-referential link is rewritten to point at its new location, and any relative outbound link to another concept is rebased if the move changes directory (an absolute, bundle-root-anchored link is left untouched either way). `plan_move_concept` is read-only (safe for a `--dry-run` preview); `move_concept` applies every referring file's rewrite before moving the concept file last, so a failure partway through always leaves the concept file at a well-defined location, and re-running `move_concept` with the same arguments resumes and completes the operation. After a successful move, `MoveResult.regenerated_indexes` lists any `index.md` (in SOURCE's old directory and/or DEST's new directory) that was refreshed via `generate_index` to reflect the new state; an index that didn't already exist is never created. See `okf move` above for the CLI entry point.

### Graph Repair

`plan_graph_repair(bundle)` and `repair_graph(bundle)` compose the graph and patching primitives into a self-healing pass over every broken link in the bundle: for each one, a plugin implementing the `okf_fetch_moved_concept_path(dead_concept_id, bundle) -> Path | None` hook is asked whether the dead concept's ID has a new on-disk location; the first plugin to answer wins (`firstresult=True`). A plugin's returned path is treated as untrusted input: a relative `Path` is resolved against `bundle_root` (not the process's current working directory), and the result is validated the same way `graph.py` validates any on-disk link target -- if it escapes `bundle_root`, isn't a `.md` path, or the file doesn't actually exist (e.g. a stale cache entry), the answer is rejected rather than used. If a plugin returns a usable path, the link's href is rewritten there (preserving fragment/query and relative-vs-absolute style, exactly like concept relocation above). Otherwise the link is recorded as an `UnresolvedBrokenLink` with a reason that distinguishes *why*: `"not-concept-shaped"` (the link's target escapes the bundle root entirely, so there's no concept-id-shaped target to look up), `"no-plugin-registered"` (no plugin implements the hook at all -- the out-of-the-box default, since no plugin ships with `okf-core` today), `"unresolved"` (a plugin is registered but returned `None` for this particular dead concept ID), or `"invalid-resolved-path: ..."` (a plugin's resolved path escaped `bundle_root`, wasn't a `.md` path, didn't exist on disk, or -- for a bundle-root-anchored link specifically -- couldn't be expressed in that style). The hook is called at most once per distinct `dead_concept_id` per run, and rewrites (not reported occurrences) are deduped by `(file, href)`: multiple broken links sharing an identical href in one file produce a single rewrite but each still appears separately in `resolved_links`/`unresolved_links`.

`plan_graph_repair` is read-only (safe for a `--dry-run` preview) and returns a `RepairPreparation`; `repair_graph` applies every plan and returns a `RepairResult` with the files actually changed. If a broken link's containing file cannot be planned -- e.g. it also has an unrelated reference-style link definition, which makes `plan_markdown_link_rewrite` reject the whole file -- only that file's link(s) are downgraded to unresolved (reason `"planning-failed: ..."`); the run continues and every other file's resolvable links are still repaired. Only an upfront scan/parse failure elsewhere in the bundle aborts the whole run, since it means the broken-links view can't be trusted as complete. See `okf graph-repair` above for the CLI entry point.

### Validation

`validate_concept_document()` performs base OKF concept conformance checks, returning a tuple of structured `ValidationFinding` objects (e.g. reporting missing or empty `type` fields as errors).

`validate_concept_document_with_profile(document, profile, project_taxonomy, *, is_directory_meta=False)` validates a concept document against a specific `ProfileConfig` and optional `TaxonomyConfig`, checking for:
- Base OKF conformance.
- Profile-required frontmatter fields (errors if missing; skipped if `is_directory_meta=True`). When the document's `type` has a matching `[profiles.<name>.type_fields.<type>]` entry, that type's `required_frontmatter` is checked additively alongside the profile-wide list.
- Undocumented custom frontmatter fields (warnings if present but not defined in the profile, standard OKF fields, or the document's type-scoped `type_fields` required/optional lists; skipped if `is_directory_meta=True`).
- Taxonomy type rules (errors if type violates profile/project `allowed_types`, warnings if type violates `known_types`). Note that if `is_directory_meta=True` is provided and the document type starts with an underscore (such as `_directory`), taxonomy checks are bypassed to accommodate local directory metadata without taxonomy configuration changes.

`validate_bundle(bundle, config)` scans a bundle and validates all of its concept documents against the configured profile, returning a mapping of file paths to their respective validation findings. Any scan or parsing failures are reported as validation errors. It also runs `check_attribution_consistency()` (below) against every concept unconditionally, since the check is base spec behavior rather than profile-specific. Finally, for every concept-bearing directory that has a committed `index.md`, it regenerates that directory's index the same way `okf index` would (via `entries_for_directory()` and `generate_index()`) and runs `diff_index()` (see [Index Files](#index-files) above) against the committed content, recording any drift under the `index.md` path itself. `concept_directories(manifest, resolved_bundle_root)` -- extracted from `okf index`'s own directory-discovery logic, the same precedent `entries_for_directory()` set (#69) -- supplies the set of directories this check walks. A directory with no committed `index.md` is skipped entirely (out of scope for this check); every finding it does produce is `severity="warning"`.

`check_attribution_consistency(frontmatter, body)` joins the per-claim attribution footnotes in a concept's `body` against its `sources[].id` frontmatter (spec §5.1), returning a tuple of `ValidationFinding` objects. A `[^label]` footnote reference or `[^label]:` definition whose label has no matching `sources[].id` is an `"error"` finding with `field` set to the label and `line` set to its 1-based source line. A `sources[].id` that no footnote (reference or definition) ever cites is a `"warning"` finding with `field` set to the source id (`line` is `None`, since an unreferenced source has no single occurrence to point at) -- an unreferenced source is legal, so this is advisory rather than an error. A `sources` entry without an `id` is not a join candidate (`id` is optional per §5.1) and is silently skipped. `extract_footnote_occurrences(body)` returns the underlying `FootnoteOccurrence` tuple (`label`, `line`, `is_definition`) that `check_attribution_consistency()` joins against `sources[].id`; it is exported for callers that want the raw occurrences without the join.

A footnote label is recognized only when it matches a restricted safe-slug shape: letters, digits, and hyphens (`[A-Za-z0-9-]+`), with no other label-shaped character immediately following the closing `]` (e.g. `[^label]with-trailing` is not recognized). A `[^label]`-shaped construct whose label falls outside this shape -- underscores, whitespace, brackets, or any other character -- is not recognized as footnote syntax at all: it is neither reported as an occurrence nor treated as a definition, the same as any other prose. This is an interpretation of "stable key" scoped to this check only; `sources[].id` elsewhere in the codebase (e.g. the source-upsert path) is read as-is with no such restriction. See `src/okf_core/attribution.py`'s module docstring for the full rule-by-rule soundness argument for why this shape can never be split or swallowed by any active CommonMark rule.

Images (`![...](...)`) are out of scope entirely -- a `[^label]`-shaped construct inside an image's alt text is never recognized, whether it exactly matches a label (`![^label](img.png)`) or merely contains one alongside other text (`![Diagram [^label]](url.png)`); both are equally inert, not a partial-recognition special case. This sidesteps a class of bug (`src/okf_core/attribution.py`'s module docstring, "Images are out of scope") rather than solving it: an image's alt text lives on a nested token list this check never walks, unlike a link's text, which markdown-it-py flattens into the same list this check scans directly. A `sources[].id` cited only from image alt text always surfaces as the "unreferenced source" warning above, as a consequence.

### Concept ID and Path Resolution

`concept_id_to_path()` maps a concept ID to a Markdown file path under the bundle root. `path_to_concept_id()` maps a Markdown file path inside the bundle root back to a concept ID. This matches the OKF v0.2 and reference implementation model: concept IDs are bundle-relative path segments without the `.md` suffix. For example, `topics/example` resolves to `topics/example.md`.

Path resolution rejects empty IDs, absolute IDs, parent-directory traversal, backslash-separated IDs, and IDs that include a file extension. It also rejects configured reserved filenames such as `index.md` and `log.md` as normal concept documents at any hierarchy level.

### Bundle Manifests

`scan_bundle()` scans a resolved `BundleConfig` and returns a deterministic `BundleManifest`. Manifest entries include the concept ID, path, bundle root, `mtime_ns` timestamp, size, SHA-256 hash, parsed frontmatter summary, and raw Markdown content for each discovered concept document. Frontmatter summaries are returned as immutable mappings so manifest data cannot be accidentally changed in place. Raw content is exposed through `ConceptManifestEntry.content` as the scan-time snapshot; entries constructed outside `scan_bundle()` read and cache their file content on first access.

Scanning applies the bundle's configured include globs, exclude globs, and reserved filename rules. A missing bundle root returns an empty manifest so configuration can refer to a directory that does not exist yet. Reserved filenames such as `index.md` and `log.md` are ignored as normal concepts at any hierarchy level.

Malformed documents and other per-file scan failures are reported as structured manifest problems instead of aborting the full scan, allowing callers to inspect valid concepts and problems from the same scan result.

### Bundle Listings

`list_concepts()` returns a deterministic, machine-readable catalog of valid concept documents that callers can use for task-based seed discovery before building context packs. It is the structured counterpart to `index.md` progressive disclosure: `index.md` remains a human- and agent-readable browsing surface, while bundle listings expose concept IDs and frontmatter for filtering without requiring search infrastructure.

Concept listing entries include the concept ID, path, non-empty string `type`, normalised `title` and `description` values, preserved full frontmatter, a `fields` mapping containing any configured `listing_fields` that are present in frontmatter, and optional raw Markdown body `content` (with frontmatter stripped). Producer-defined fields such as `activity` are preserved and can be promoted through config, but they are not part of base OKF and are never required when no config is present. Unknown valid `type` values are accepted per OKF's permissive consumption model. Missing, blank, or non-string `type` values are reported as `ListingProblem` objects instead of silently omitted.

Callers may pass an existing `BundleManifest` to avoid scanning twice. Callers may also pass a `BundleGraph` to populate resolved inbound and outbound link counts for discovery; otherwise link counts are `None`. Pass `with_content=True` to populate the `content` field of listed concepts; otherwise `content` is `None`. A graph-annotated listing is also the listing half of `normalize_bundle_graph` / `acquire_normalized_graph` (see Graph Operations).

```python
from okf_core import list_concepts, load_config

config = load_config()
bundle = config.bundles["default"]
listing = list_concepts(bundle, with_content=True)
# listing.concepts  — seed candidates with concept IDs, frontmatter, and content
# listing.problems  — tuple of ListingProblem for skipped, malformed, or read-failed entries
```

### Lexical Search

`search_concepts()` provides local FTS5 search over valid listed concepts. It requires `bundle.okf_cache_dir` and stores search rows in the same `okf-cache.db` used by scan and graph caching.

```python
from okf_core import load_config, search_concepts

config = load_config()
bundle = config.bundles["default"]
results = search_concepts(bundle, "incident triage", limit=5)
# results.results  — SearchResult entries with concept IDs, paths, metadata, scores, and snippets
# results.problems — listing problems encountered while refreshing the search index
```

### Index Files

`generate_index()` produces a conformant `index.md` body string from a sequence of `ConceptManifestEntry` objects scoped to a directory. `render_index_document()` can wrap that body with bundle-root `okf_version` frontmatter when a bundle configuration declares a supported version; otherwise indexes remain body-only. Entries are grouped by their `type` frontmatter field and sorted alphabetically within each group. Unknown but valid string `type` values are tolerated and grouped normally per OKF v0.2 spec §11. Entries whose `type` is absent or not a string are a spec §4.1 violation; they are skipped and reported as `IndexProblem` objects in the `problems` field of the result. Entries or subdirectories whose path falls outside `directory` are likewise skipped and reported. Subdirectory entries appear in a trailing `Subdirectories` section.

**Local Tool-Specific Enhancement**: `generate_index()` and the `okf index` CLI command support an optional directory metadata sidecar file (by default `_directory.yml`, configurable via `directory_metadata_file`). Since subdirectories are not concepts and do not have an identity in the base OKF spec, this sidecar allows configuring folder-level metadata as a non-spec local tool enhancement. If the file exists, it is parsed and validated like a concept document's frontmatter (requiring a `type` field which should be `_directory`). Any validation findings or parsing problems are surfaced in the `problems` list of the returned `GeneratedIndex`.

If the sidecar is valid:
- Its `title` key overrides the directory name in the trailing `Subdirectories` section (defaults to the relative directory path).
- Its `description` key provides the directory's description in the `Subdirectories` section.

If a description is not defined in the sidecar, the `describe_directory` callback (if provided) is used as a fallback.

Entry titles come from the `title` frontmatter field, converted to a string, with internal newlines collapsed to spaces and then stripped; if absent, `None`, or empty/whitespace-only, the file stem is used as a fallback. Falsy-but-non-empty values such as `title: 0` are preserved as their string form. The same normalisation applies to `description` and to strings returned by `describe_directory` (and the sidecar description): absent, `None`, or empty/whitespace-only values omit the entry suffix; falsy-but-non-empty values are preserved. The function returns a `GeneratedIndex` dataclass with `.body` and `.problems` fields; writing the file to disk is the caller's responsibility for library use. The CLI `okf index` command owns that write step for command-line use.

```python
from okf_core import generate_index, scan_bundle, load_config

config = load_config()
bundle = config.bundles["default"]
manifest = scan_bundle(bundle)
result = generate_index(bundle.bundle_root, manifest.concepts)
# result.body  — the rendered index.md content
# result.problems  — tuple of IndexProblem for any skipped entries
```

`parse_index()` parses an existing `index.md` body into a `ParsedIndex` containing `IndexSection` and `IndexEntry` objects plus a `.problems` tuple for malformed list items that were skipped. Generated output round-trips through `parse_index` without loss: Markdown-significant characters (`[`, `]`, `(`, `)`, backslash, double quotes) in a title, link, or description are escaped on generation and unescaped on parsing, via the same shared Markdown engine `logs.py` and `patching.py` also use — escaping/unescaping is decided in exactly one place in `okf-core`, not by a per-module rule set. Hand-authored index entries that do not match the generated/spec entry shape are reported as parse problems instead of causing the full index parse to fail.

`diff_index(generated, committed)` compares a freshly regenerated index (a `GeneratedIndex`) against a parsed committed `index.md` (a `ParsedIndex`) and returns a tuple of `ValidationFinding` objects describing semantic drift between them — this is what powers `okf validate`'s index freshness check (below). Both sides are compared as decoded Markdown entries keyed by `link` (`generated` is re-parsed through `parse_index` internally), so formatting-only differences and entry reordering never report as drift. Reported drift: an entry present only in the regeneration (the committed file is missing it), an entry present only in the committed file (it no longer corresponds to any current concept), and an entry present on both sides whose section/title/description differs (reported as stale, naming what changed). A malformed committed list item (surfaced via `ParsedIndex.problems`) is also converted into a finding. Every finding is `severity="warning"` — index drift is advisory, never an error.

The `describe_directory` keyword argument to `generate_index()` is a hook point for callers that want to supply directory-level descriptions — for example, a workflow agent using its own model access. It receives the absolute subdirectory path and should return a description string or `None`. `okf-core` itself never makes model API calls.

### Log Files

`parse_log(content)` parses a `log.md` body per OKF's Log Files section into a `ParsedLog`: an optional `title` (the `# Heading` if present before any date heading, else `None`), a tuple of `LogDateSection` objects (one per `## YYYY-MM-DD` heading, each carrying its date string and a tuple of `LogEntry` objects), and a `.problems` tuple of `LogParseProblem` objects for malformed input. Date headings that are not valid ISO 8601 `YYYY-MM-DD` calendar dates are reported as problems and that section's entries are skipped rather than raising; entries (and other non-heading content) that appear before the first date heading, or under a malformed one, are silently ignored — there is no valid section, and no date, to attribute them to. Each bullet-list item under a valid, open date section becomes a `LogEntry`: a leading `**Word**: ` bold prefix (the spec's `**Update**`/`**Creation**`/`**Deprecation**` convention) is captured as `.label` and stripped from `.text`, otherwise `.label` is `None` and `.text` is the entry's full rendered prose. Embedded Markdown (links, code spans, emphasis) has its content preserved in `.text`, not resolved or altered, though re-rendering can canonicalize formatting details such as a link title's quoting style. An entry with no renderable text at all is skipped and reported as a `LogParseProblem`, matching the same skip-and-report behaviour as malformed date headings. A "loose" bullet item (one with a blank line between paragraphs) folds its second and later paragraphs into that same entry's `.text`, space-joined, rather than dropping them. Any block-level construct other than the expected `## YYYY-MM-DD` date heading and the entry bullet list — a bare paragraph, fenced or indented code block, thematic break, any heading (ATX or setext, including an out-of-place `h1`), raw HTML block, blockquote, or an ordered list — placed directly under an *open, valid* date heading is reported as a single `LogParseProblem` category and skipped, since none of them has a representation in the flat `LogEntry` model. That guarantee holds one nesting level deeper too: within a list item itself, anything after the item's own (and, for a loose item, continuation) paragraphs — a nested bullet or ordered list, fenced or indented code, a thematic break, a blockquote, a heading, or raw HTML — is unexpected nested content and is reported as its own `LogParseProblem` category and skipped, rather than silently discarded, without corrupting that entry's own captured text or any sibling entry. Headings get one further rule beyond that: an `h1` is only ever captured as `.title` while still in the preamble, before any `## YYYY-MM-DD` heading at all — once the document has moved past the preamble, into either a valid date section or a malformed one, any further `h1` is always reported and skipped as a stray block, the same as a `### h3` or blockquote would be, rather than being merged into or mistaken for the document title, however many malformed date headings intervene. Note that CommonMark's own HTML-block rule can absorb a following bullet into the same block when no blank line separates them, before this parser ever sees a separate list; that loss is still reported as a `LogParseProblem`, but the absorbed entry can't be recovered.

`render_log(parsed)` performs the inverse, pure structural serialization: a title heading (if present), then each date section's `## YYYY-MM-DD` heading and bullet entries, in order. It does no merging, inserting, or deduplication against an existing log — that composition is left to callers.

`load_log(path)` reads and parses a `log.md` file, mirroring `scan_bundle()`'s missing-root tolerance: a `path` that is not an existing file — missing entirely, or an existing non-file path such as a directory — returns an empty `ParsedLog(title=None, sections=(), problems=())` instead of raising.

```python
from pathlib import Path
from okf_core import load_log, render_log

parsed = load_log(Path("docs/log.md"))
# parsed.title     — the log's "# Heading", or None
# parsed.sections  — LogDateSection objects, newest first if the file follows the spec convention
# parsed.problems  — tuple of LogParseProblem for malformed date headings or entries

for section in parsed.sections:
    for entry in section.entries:
        print(section.date, entry.label, entry.text)

render_log(parsed)  # renders parsed back to spec-shape log.md Markdown
```

`plan_log_concept_move(bundle, old, new, *, today=None)` and `log_concept_move(bundle, old, new, *, today=None)` record a concept move as a new entry in the bundle-root `log.md`, mirroring `plan_move_concept`/`move_concept`'s planning/applying pairing. `old` is the moved concept's former concept ID; `new` is its new location as a bundle-root-relative `.md` path, which must currently exist on disk — this primitive only records that a move happened (e.g. after `move_concept`), it does not move anything itself. Both are validated via `paths.py`'s existing concept ID/path resolution (bundle-root containment, `.md` shape, reserved-filename rejection, concept path strategy); a validation failure raises `ConceptPathError`. `DocumentChangePlanningError` is raised instead for: a missing `new` target; or an existing `log.md` that `parse_log` reports any parse problems against, since rewriting it would silently drop content the parser couldn't represent — fix `log.md` before recording another move in it. The recorded entry has `label="Moved"` and `text` of the form `` [old-concept-id](new-relative-path "moved to") `` — the anchor text is the stable former concept ID, the href is the literal new on-disk path, capturing both forms in one entry. `old`/`new` are embedded through `okf-core`'s single shared Markdown-escaping engine, so a concept ID containing `[`/`]`, or a path containing an unbalanced `(`/`)`, is escaped correctly rather than rejected. If `old` and `new` resolve to the same path, or an identical move is already recorded anywhere in the log (not just under today's date), planning returns an unchanged plan and nothing is written — re-logging the same move is idempotent. Otherwise the entry is inserted at the top of today's date section (created if absent, keeping the log newest-first even if the existing top section isn't today's). `today` overrides the real current date (UTC) for deterministic tests. Planning delegates to `plan_document_change_from_reader(..., allow_missing=True)` (see above) — not `plan_document_change` — so the parse/dedup/insert/render logic above runs against the exact content that call reads once and hashes as the plan's baseline, rather than a separate, earlier read; a bundle with no `log.md` yet is planned from an empty document instead of raising, and `log_concept_move` retains the same SHA-256 optimistic-concurrency protection as every other write primitive here — including against a `log.md` created or edited concurrently after planning began.

`plan_log_append(bundle, content, *, date=None, kind=None)` and `log_append(bundle, content, *, date=None, kind=None)` append one agent-supplied entry to the bundle-root `log.md`: the library owns the file's structure (locating or creating the correct `## YYYY-MM-DD` section, preserving reverse chronology, leaving every other entry untouched) so a caller never has to read or parse the file itself. `content` is the entry's prose; `kind`, when given, becomes the entry's bold label convention word (`**kind**: `), the same convention `plan_log_concept_move`'s fixed `"Moved"` label uses. Both are validated before any file is touched, raising `DocumentChangePlanningError` for: a non-`str` or blank/whitespace-padded `content`; a `kind` that is not `None` and not a clean, non-empty, single-line string; multi-block content (more than one paragraph, or a nested list, code block, heading, or other block-level construct — not representable as one flat entry); a `kind` that can't survive its own bold-label rendering (e.g. containing `**`); and `content` that itself begins with a `**word**: `-shaped prefix when no `kind` was supplied, which would render indistinguishably from a genuinely labelled entry. Representability is checked by rendering the candidate entry and reparsing it with `parse_log`, so content is accepted even when its recovered text differs from the input in ways re-rendering canonicalizes (e.g. a soft line break collapsing to a single space, or a link title's quote style), but rejected if reparsing would recover a different label or otherwise alter its meaning. An existing `log.md` that `parse_log` reports any parse problems against is refused for the same reason `plan_log_concept_move` refuses one — rewriting it would silently drop content the parser couldn't represent.

Unlike `plan_log_concept_move`, appending never deduplicates: a general prose entry has no natural identity to compare against (a move entry's identity is its `(old, new)` pair; arbitrary prose has none), so calling `plan_log_append`/`log_append` again with the same arguments records the entry again rather than being treated as idempotent. `date` defaults to the real current UTC date; pass a fixed value for deterministic tests. Planning delegates to `plan_document_change_from_reader(..., allow_missing=True)`, the same pairing `plan_log_concept_move` uses, so a bundle with no `log.md` yet is planned from an empty document, and `log_append` retains the same SHA-256 optimistic-concurrency protection as every other write primitive here.

### Context Packs

`build_context_pack(bundle, seed_concept_ids, *, graph=None, depth=1, direction="both", budget_chars=None)` assembles a deterministic context pack from explicit seed concept IDs. Seeds appear first in the returned entries (in the order provided), followed by graph-expanded concepts ordered by distance then concept ID. `depth` controls how many hops of graph expansion are performed (default `1`); `direction` controls whether outbound links, backlinks, or both are followed (default `"both"`). `budget_chars` sets an approximate character-count budget; entries are added in stable order until the budget is exhausted and any remaining discovered concepts are reported in `omitted_concept_ids`. Pass `budget_chars=None` (the default) to include all discovered concepts.

Each `ContextEntry` in the result includes `concept_id`, `path`, `title`, `content`, `selection_reason` (`"seed"`, `"outbound-link"`, or `"backlink"`), `graph_distance`, and `char_count`. The `ContextPack` result provides `bundle_name`, `seeds` (the de-duplicated valid seed IDs in input order), `entries`, `omitted_concept_ids` (budget- and read-error omissions), and `problems` (unknown seeds and file-read errors).

Pass a pre-built `BundleGraph` as `graph` to avoid building the graph twice. When the graph was built from scanned manifest entries, context pack content reuses each entry's scan-time content snapshot instead of rereading files from disk.

```python
from okf_core import build_context_pack, load_config

config = load_config()
bundle = config.bundles["default"]
pack = build_context_pack(bundle, ["topics/example"], depth=2, budget_chars=20_000)
# pack.seeds                — tuple of resolved seed concept IDs
# pack.entries              — tuple of ContextEntry, seeds first
# pack.omitted_concept_ids  — concepts discovered but excluded by budget or read error
# pack.problems             — unknown seeds and file-read errors
for entry in pack.entries:
    print(entry.concept_id, entry.selection_reason, entry.graph_distance)
    # entry.content  — raw file text
```

### Graph Operations

`extract_markdown_links()` extracts standard non-image Markdown links from a Markdown body. It uses a CommonMark-compatible parser so links in fenced code, inline code, and images are ignored. Each returned `MarkdownLink` carries `text`, `target`, and `title` (the CommonMark link title attribute, `None` when absent or empty). `title` is propagated to `ConceptLink` so graph consumers can use it as relationship metadata without requiring custom frontmatter extensions.

`build_bundle_graph(bundle, manifest=None)` scans concept bodies and returns a `BundleGraph` with resolved directed concept links, broken internal concept links, and non-fatal graph problems. Callers may pass an existing `BundleManifest` to avoid scanning twice; scanned manifest entries also let graph construction reuse the raw content snapshot instead of rereading concept files. Graph problems use the same scan-style kind values for document failures, such as `read-error`, `decode-error`, and `parse-error`.

`normalize_bundle_graph(graph, listing, *, bundle_root)` projects that `BundleGraph` and a graph-annotated `BundleListing` into a portable unique-edge `NormalizedBundleGraph` without re-parsing concept bodies or shelling out to the `okf` CLI. Repeated directed `ConceptLink` instances collapse to one edge (instance counts and texts retained); self-links and resolved links whose endpoints are not listed concepts are tracked separately and never become unique edges. `acquire_normalized_graph(bundle, *, manifest=None)` is the thin composition helper: one `scan_bundle`, then `build_bundle_graph` and `list_concepts` sharing that scan, then `normalize_bundle_graph`. Non-fatal OKF problems are carried onto the model; cross-payload disagreement, escaped-root node/problem/source paths, and wrong payload types raise `GraphModelError`. A broken or excluded target that resolves outside the bundle (for example `[out](../outside.md)`) is kept as a portable relative path, including `..`. The result's to_portable_dict method returns a JSON-ready snapshot of the model only (no analysis, timestamps, or absolute paths).

`analyze_normalized_graph(model, *, top_n=10)` computes a deterministic `BundleGraphAnalysis` snapshot from that model: unique-edge density and degree stats, weakly connected components (isolates included; largest-component membership omitted from `other_memberships`), linear-time articulation points on the undirected unique-edge projection, top-N rankings by verbatim OKF PageRank and inbound count, and diagnostic lists (orphans, zero-inbound, zero-outbound) framed as signals rather than defects. Self-links and excluded links stay off the undirected projection. Wrong input types and a negative or non-int `top_n` raise `GraphAnalysisError`. The result's `to_portable_dict` method is JSON-ready with no timestamps or absolute paths; analysis is not baked into `NormalizedBundleGraph.to_portable_dict`.

`render_graph_report(model, analysis, *, provenance)` turns that pair into a `GRAPH_REPORT.md` body. Volatile fields live only in the Provenance section via a caller-injected `GraphReportProvenance` (`generated_at`, `okf_version`, optional `git_revision`, exact `source_commands`); the library does not run `git` or `okf`. The remaining sections — overview, health, high-centrality, bridges, suggested inspections, and a CCP-260 communities placeholder — are byte-stable for the same model and analysis. Concept IDs and titles are literal inline (never Markdown links); suggested inspections are one fenced `okf graph` / `okf context` command per observed condition. `graph_report_payload(model, analysis)` / `render_graph_json(model, analysis)` emit a portable `{schema_version, normalized_graph, analysis}` envelope with no provenance, timestamps, or writer paths. `apply_graph_report_output_file(path, *, output_dir, forbidden_roots=(), text=None, unlink=False)` is the one helper that writes or unlinks a graph-report artifact: it `resolve()`s the final file path and refuses unless that location is a strict descendant of `output_dir` and not equal to or inside a forbidden root, so a leftover symlink into a bundle cannot be followed. `write_bundle_graph_artifacts(output_dir, bundle_slug, *, report_markdown, graph_json, forbidden_roots=())` joins `<output>/<slug>/` and writes `GRAPH_REPORT.md` and `graph.json` through that helper. Wrong types, a bundle-name mismatch, a non-`Path` `output_dir`, a slug that is empty, `.`, `..`, or contains `/` or `\`, or a resolved path that is not a strict descendant of `output_dir` (or that lands in a forbidden root) raise `GraphReportError`.

`render_graph_summary(rows, *, provenance, configured_bundle_names, selected_bundle_names)` renders the cross-bundle `SUMMARY.md` rollup from already-built `GraphSummaryRow` values (Markdown only; no I/O). The subset note compares the requested selection to the configured names; a selected bundle missing from `rows` is reported as omitted, not as a user-requested subset. `run_graph_report(config, *, bundle_names=None, output_dir=None, provenance=None)` is the library orchestrator behind `okf graph-report`: it guards the output directory, then cleans stale artifacts and writes `SUMMARY.md` / per-bundle files through `apply_graph_report_output_file` so leftover symlinks into a bundle are refused, reports each selected bundle, and writes `SUMMARY.md` from selected rows only.

Internal OKF concept links resolve according to OKF v0.2 rules:

- `/path/to/concept.md` resolves relative to the configured bundle root.
- `./concept.md` and `../concept.md` resolve relative to the source concept's directory.
- URL fragments and query strings are preserved in the raw target and ignored for path resolution.

External URLs, fragment-only links, `mailto:` links, non-Markdown assets, and configured reserved filenames such as `index.md` and `log.md` are not concept edges. Missing internal concept targets are reported in `broken_links`; they are not fatal errors because OKF consumers must tolerate broken cross-links.

`links_from(graph, concept_id)`, `backlinks_to(graph, concept_id)`, and `neighborhood(graph, concept_id, depth=1)` provide deterministic traversal over resolved links. Neighborhood traversal treats links as bidirectional for discovery while preserving directed edges in the underlying graph. It raises `ValueError` for unknown concept IDs or negative depths.

`find_unlinked_mentions(bundle, *, refresh=True)` scans visible concept-body prose for mentions of other concept titles that are not already Markdown links, returning an `UnlinkedMentionsResult` with `suggestions` (a tuple of `LinkSuggestion`) and `problems` (non-fatal read/parse failures). Fenced and indented code blocks, inline code, image destinations, and Markdown link destinations are excluded; displayed link text remains eligible prose. Each `LinkSuggestion` identifies the source and target concept, the target's `target_title`, and `matched_text`, an annotated prose excerpt from the FTS engine with `[`/`]` highlight markers around matched terms and `...` for truncation (not a literal string match). Requires `bundle.okf_cache_dir` to be configured (raises `SearchConfigError` otherwise); pass `refresh=False` to query the existing FTS cache without rebuilding it.

`link_suggestion_href(suggestion)` computes the Markdown link destination a suggestion would be written with: a `suggestion.source_path`-relative, POSIX-separator, percent-encoded path to `suggestion.target_path` -- the same relative-target convention `link_target_for_new_location` uses for a moved concept's rewritten link.

`select_link_suggestions(suggestions, pairs)` filters a `find_unlinked_mentions` result down to caller-chosen `(source_concept_id, target_concept_id)` pairs, returning a `SuggestionSelection` with `selected` and any `unmatched_pairs` (a requested pair with no matching suggestion, surfaced rather than silently dropped). `pairs=None` selects every suggestion.

`plan_link_suggestions_apply(bundle, suggestions, *, heading="See also", level=2)` and `apply_link_suggestions(bundle, suggestions, *, heading="See also", level=2)` are the read-only-plan / write pair (mirroring `plan_graph_repair`/`repair_graph`) that write selected suggestions into their source concept's body as inline Markdown links (`- [target_title](target_href)`), grouped one write per source file, appended under `heading` at `level` (default `## See also`). Built on `plan_markdown_section_append` (below): a suggestion whose target already has a link in the section is skipped, so re-applying an overlapping suggestion set is idempotent rather than duplicating bullets. `plan_link_suggestions_apply` returns a `LinkSuggestionApplyPreparation` (`section_plans`, a `DocumentChangePlan` per source file, and `applied_suggestions`); `apply_link_suggestions` applies each plan and returns a `LinkSuggestionApplyResult` with the files actually `updated_files`.

```python
from okf_core import (
    apply_link_suggestions,
    backlinks_to,
    build_bundle_graph,
    find_unlinked_mentions,
    links_from,
    load_config,
    select_link_suggestions,
)

config = load_config()
bundle = config.bundles["default"]
graph = build_bundle_graph(bundle)
outbound = links_from(graph, "topics/example")
inbound = backlinks_to(graph, "topics/example")

result = find_unlinked_mentions(bundle)
for suggestion in result.suggestions:
    print(f"{suggestion.source_concept_id} mentions '{suggestion.matched_text}' → {suggestion.target_concept_id}")

# Write only the suggestions naming "topics/example" as their source.
selection = select_link_suggestions(
    result.suggestions, [("topics/example", s.target_concept_id) for s in result.suggestions]
)
applied = apply_link_suggestions(bundle, selection.selected)
```

## Development Expectations

All implementation work should be delivered through pull requests. Tests are mandatory for delivered behavior, and user-facing behavior changes must include README updates. Issues should stay open until their implementation PRs have been approved by a human and merged.

## License

`okf-core` is licensed under the Apache License, Version 2.0. See `LICENSE` for the full license text.

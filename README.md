# okf-core

`okf-core` is planned as a reusable Python toolkit for working with Open
Knowledge Format (OKF) bundles in local repositories.

OKF itself is deliberately simple: a bundle is a directory tree of UTF-8
Markdown files with YAML frontmatter. The format is readable by humans and
ordinary tools. This project is not intended to replace that openness with a
hosted knowledge service or a required model API. Instead, `okf-core` will add a
deterministic layer for discovery, validation, graph traversal, search context,
and safe updates across OKF-style documents.

The target pattern is semi-opaque:

- Markdown remains available on disk for humans, editors, scripts, and agents.
- Consistency-sensitive operations go through deterministic library or CLI
  functions instead of ad hoc filesystem crawling and rewriting.
- Consuming projects can keep their own repository layouts, document taxonomies,
  model providers, and agent runtimes.
- Any agent instructions in this repository are examples only. They should be
  copied or adapted by consuming projects that already have their own model
  access.

## Status

This repository is in early MVP development. Configuration loading, concept
document parsing, configurable concept ID/path resolution, bundle manifest
scanning, and index file parsing and generation are implemented; the other OKF
operations described below are the planned public shape of the project and are
not implemented yet.

When features are implemented, this README should be updated in the same pull
request. Documentation must distinguish implemented behavior from planned
behavior, and README edits should be reviewed as a whole after patching so the
document stays internally consistent.

## Current Capabilities

`okf-core` currently provides an installable Python package with typed project
configuration loading, structural concept document parsing, deterministic
concept ID/path resolution, and bundle manifest scanning for the configured bundle
root (one per bundle). Public behavior is intended to reduce to the OKF v0.1 base
specification; `okf-core` configuration conveniences are optional and should not
change OKF concepts such as bundles, concept IDs, reserved files, or
frontmatter tolerance.

```python
from okf_core import (
    concept_id_to_path,
    load_config,
    parse_concept_document,
    scan_bundle,
)

config = load_config()
document = parse_concept_document("---\ntype: concept\n---\nBody\n")
path = concept_id_to_path("topics/example", config.bundles["default"])
manifest = scan_bundle(config.bundles["default"])
```

Install the package for local development and tests with:

```sh
python -m pip install -e ".[test]"
```

Run the test suite with:

```sh
pytest
```

### Configuration

The default project configuration file is `okf-core.toml`.

`load_config()` searches upward from the current working directory for
`okf-core.toml`. If no config file is found, it returns built-in defaults rooted
at the current working directory. Callers may pass `config_path` to load a
specific file, `project_root` to choose a discovery/default root, and
`overrides` to supply explicit Python API overrides.

Explicit config paths are loaded directly and must exist. When no explicit path
is provided, future CLI commands should use the same behavior: explicit
`--config` first, otherwise cwd-upward discovery, otherwise built-in defaults.

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
- `index_cache`

Supported `[taxonomy]` keys are `known_types` and `allowed_types`.

Supported `[profiles.<name>]` keys are `required_frontmatter`,
`optional_frontmatter`, and nested `taxonomy` settings. Supported
`[bundles.<name>]` keys are the same path/glob/reserved-name settings as
`[defaults]`, plus `profile`.

Relative paths are normalized against the resolved project root, and referenced
files or directories do not need to exist yet. Unknown config keys fail closed
with a configuration error so typos do not silently change behavior.

Built-in defaults are equivalent to:

```toml
[defaults]
bundle_root = "."
include = ["**/*.md"]
exclude = []
reserved_filenames = ["index.md", "log.md"]
concept_path_strategy = "relative-path"
index_cache = ".okf-cache"
```

If no bundles are declared, `okf-core` exposes one resolved bundle named
`default` using the project defaults. Declared bundles inherit project defaults
and may override them per bundle. Multiple OKF areas in one repository should
be configured as separate named bundles, each with one `bundle_root`.

### Concept Documents

`parse_concept_document()` parses a Markdown string into YAML frontmatter and
body content. Documents without frontmatter are accepted and return empty
frontmatter with the original Markdown as the body. Invalid YAML, unterminated
frontmatter, non-mapping frontmatter, and non-string frontmatter keys raise
`DocumentParseError`.

`serialize_concept_document()` writes a parsed concept document back to
Markdown. Unknown frontmatter keys are preserved when callers keep them in the
parsed frontmatter dictionary. Documents with empty frontmatter serialize as
body-only Markdown.

### Validation

`validate_concept_document()` performs base OKF concept conformance checks, returning a tuple of structured `ValidationFinding` objects (e.g. reporting missing or empty `type` fields as errors).

`validate_concept_document_with_profile(document, profile, project_taxonomy)` validates a concept document against a specific `ProfileConfig` and optional `TaxonomyConfig`, checking for:
- Base OKF conformance.
- Profile-required frontmatter fields (errors if missing).
- Undocumented custom frontmatter fields (warnings if present but not defined in the profile or standard OKF fields).
- Taxonomy type rules (errors if type violates profile/project `allowed_types`, warnings if type violates `known_types`).

`validate_bundle(bundle, config)` scans a bundle and validates all of its concept documents against the configured profile, returning a mapping of file paths to their respective validation findings. Any scan or parsing failures are reported as validation errors.

### Concept ID and Path Resolution

`concept_id_to_path()` maps a concept ID to a Markdown file path under the
bundle root. `path_to_concept_id()` maps a Markdown file path inside the bundle
root back to a concept ID. This matches the OKF v0.1 and reference
implementation model: concept IDs are bundle-relative path segments without the
`.md` suffix. For example, `topics/example` resolves to
`topics/example.md`.

Path resolution rejects empty IDs, absolute IDs, parent-directory traversal,
backslash-separated IDs, and IDs that include a file extension. It also rejects
configured reserved filenames such as `index.md` and `log.md` as normal concept
documents at any hierarchy level.

### Bundle Manifests

`scan_bundle()` scans a resolved `BundleConfig` and returns a deterministic
`BundleManifest`. Manifest entries include the concept ID, path, bundle root,
`mtime_ns` timestamp, size, SHA-256 hash, and parsed frontmatter summary for
each discovered concept document. Frontmatter summaries are returned as
immutable mappings so manifest data cannot be accidentally changed in place.

Scanning applies the bundle's configured include globs, exclude globs, and
reserved filename rules. A missing bundle root returns an empty manifest so
configuration can refer to a directory that does not exist yet. Reserved
filenames such as `index.md` and `log.md` are ignored as normal concepts at any
hierarchy level.

Malformed documents and other per-file scan failures are reported as structured
manifest problems instead of aborting the full scan, allowing callers to inspect
valid concepts and problems from the same scan result.

### Index Files

`generate_index()` produces a conformant `index.md` body string from a sequence
of `ConceptManifestEntry` objects scoped to a directory. Entries are grouped by
their `type` frontmatter field and sorted alphabetically within each group.
Unknown but valid string `type` values are tolerated and grouped normally per
OKF spec §9. Entries whose `type` is absent or not a string are a spec §4.1
violation; they are skipped and reported as `IndexProblem` objects in the
second return value. Entries or subdirectories whose path falls outside
`directory` are likewise skipped and reported. Subdirectory entries appear in a
trailing `Subdirectories` section. The function returns `(body, problems)`;
writing the file to disk is the caller's responsibility (the CLI `okf index`
command will own that step once implemented).

```python
from okf_core import generate_index, scan_bundle, load_config

config = load_config()
bundle = config.bundles["default"]
manifest = scan_bundle(bundle)
body, problems = generate_index(bundle.bundle_root, manifest.concepts)
```

`parse_index()` parses an existing `index.md` body into a `ParsedIndex`
containing `IndexSection` and `IndexEntry` objects. Generated output
round-trips through `parse_index` without loss; markdown link
metacharacters (`]` in titles, `)` in links) are escaped on generation
and unescaped on parsing.

The `describe_directory` keyword argument to `generate_index()` is a hook point
for callers that want to supply directory-level descriptions — for example, a
workflow agent using its own model access. It receives the absolute subdirectory
path and should return a description string or `None`.  `okf-core` itself never
makes model API calls.

## Planned Operations

The planned library and CLI surface is grouped around deterministic operations.
No operation should require this package to own an LLM API token.

### Bundle Operations

- List configured bundles.
- Describe each bundle root and configuration.

### Concept Operations

- Locate a concept by ID.

### Query and Context Operations

- Build and refresh a local SQLite index.
- Search title, description, frontmatter, and body text with lexical search.
- Produce context packs from a query or seed concepts within a token budget.

### Graph Operations

- Extract Markdown links.
- Resolve links according to configured bundle root ownership.
- Compute links from a concept, backlinks, neighborhoods, and broken links.

### Write Operations

- Merge frontmatter changes.
- Patch named Markdown sections.
- Append citations.
- Use file hashes as optimistic preconditions.
- Preserve unrelated content when applying focused updates.

### Integration Examples

Later releases may include optional examples showing how a consuming project can
instruct its own agents to use `okf-core`. Those examples must remain separate
from this repository's root `AGENTS.md` and must not make direct model API calls
from `okf-core`.

## Development Expectations

All implementation work should be delivered through pull requests. Tests are
mandatory for delivered behavior, and user-facing behavior changes must include
README updates. Issues should stay open until their implementation PRs have been
approved by a human and merged.

## License

`okf-core` is licensed under the Apache License, Version 2.0. See `LICENSE` for
the full license text.

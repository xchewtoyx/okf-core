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

v0.2.1 is released. v0.3.0 is in progress. Configuration loading, concept
document parsing, configurable concept ID/path resolution, bundle manifest
scanning, index file parsing and generation, base and profile-based validation,
Markdown link graph traversal, deterministic bundle listings, seed-based context
pack assembly, and the `okf` CLI (scan, validate, index, graph, list-concepts,
context)
are all implemented. The remaining operations described under "Planned
Operations" below are the intended shape of future releases and are not yet
implemented.

When features are implemented, this README should be updated in the same pull
request. Documentation must distinguish implemented behavior from planned
behavior, and README edits should be reviewed as a whole after patching so the
document stays internally consistent.

## Installation

`okf-core` is distributed via a self-hosted PEP 503 simple index on GitHub
Pages. It is not published on PyPI.

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

**Development install** (from a local clone):

```sh
python -m pip install -e ".[test]"
```

## Current Capabilities

`okf-core` currently provides an installable Python package with typed project
configuration loading, structural concept document parsing, deterministic
concept ID/path resolution, bundle manifest scanning for the configured bundle
root (one per bundle), and deterministic Markdown link graph traversal. Public
behavior is intended to reduce to the OKF v0.1 base specification; `okf-core`
configuration conveniences are optional and should not change OKF concepts such
as bundles, concept IDs, reserved files, links, or frontmatter tolerance.

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
- `listing_fields`
- `directory_metadata_file` (Non-Spec local tool enhancement: string, defaults to `"_directory.yml"`). The filename of the directory metadata sidecar file used to carry folder-level descriptions/titles.
- `okf_version`


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
listing_fields = []
directory_metadata_file = "_directory.yml"
# okf_version = "0.1"

```

If no bundles are declared, `okf-core` exposes one resolved bundle named
`default` using the project defaults. Declared bundles inherit project defaults
and may override them per bundle. Multiple OKF areas in one repository should
be configured as separate named bundles, each with one `bundle_root`.

`okf_version` is optional. When set to a supported OKF version such as `"0.1"`,
the bundle-root `index.md` generated by `okf index` includes
`okf_version: "0.1"` frontmatter, as allowed by OKF v0.1 §11. When unset,
generated root indexes preserve an existing supported root `okf_version`
declaration by default; pass `okf index --force` to overwrite without
preserving it. Versions must use `<major>.<minor>` form, and this release only
accepts configured versions up to `0.1`.
Read-only operations consume bundles best-effort when a root index declares a
newer OKF version, matching the OKF consumer guidance. Write operations fail
closed when the root version is newer than this tool understands.

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

`validate_concept_document_with_profile(document, profile, project_taxonomy, *, is_directory_meta=False)` validates a concept document against a specific `ProfileConfig` and optional `TaxonomyConfig`, checking for:
- Base OKF conformance.
- Profile-required frontmatter fields (errors if missing).
- Undocumented custom frontmatter fields (warnings if present but not defined in the profile or standard OKF fields).
- Taxonomy type rules (errors if type violates profile/project `allowed_types`, warnings if type violates `known_types`). Note that if `is_directory_meta=True` is provided and the document type starts with an underscore (such as `_directory`), taxonomy checks are bypassed to accommodate local directory metadata without taxonomy configuration changes.


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
`mtime_ns` timestamp, size, SHA-256 hash, parsed frontmatter summary, and raw
Markdown content for each discovered concept document. Frontmatter summaries are
returned as immutable mappings so manifest data cannot be accidentally changed
in place. Raw content is exposed through `ConceptManifestEntry.content` as the
scan-time snapshot; entries constructed outside `scan_bundle()` read and cache
their file content on first access.

Scanning applies the bundle's configured include globs, exclude globs, and
reserved filename rules. A missing bundle root returns an empty manifest so
configuration can refer to a directory that does not exist yet. Reserved
filenames such as `index.md` and `log.md` are ignored as normal concepts at any
hierarchy level.

Malformed documents and other per-file scan failures are reported as structured
manifest problems instead of aborting the full scan, allowing callers to inspect
valid concepts and problems from the same scan result.

### Bundle Listings

`list_concepts()` returns a deterministic, machine-readable catalog of valid
concept documents that callers can use for task-based seed discovery before
building context packs. It is the structured counterpart to `index.md`
progressive disclosure: `index.md` remains a human- and agent-readable browsing
surface, while bundle listings expose concept IDs and frontmatter for filtering
without requiring search infrastructure.

Concept listing entries include the concept ID, path, non-empty string `type`,
normalised `title` and `description` values, preserved full frontmatter, and a
`fields` mapping containing any configured `listing_fields` that are present in
frontmatter. Producer-defined fields such as `activity` are preserved and can be
promoted through config, but they are not part of base OKF and are never
required when no config is present. Unknown valid `type` values are accepted per
OKF's permissive consumption model. Missing, blank, or non-string `type` values
are reported as `ListingProblem` objects instead of silently omitted.

Callers may pass an existing `BundleManifest` to avoid scanning twice. Callers
may also pass a `BundleGraph` to populate resolved inbound and outbound link
counts for discovery; otherwise link counts are `None`.

```python
from okf_core import list_concepts, load_config

config = load_config()
bundle = config.bundles["default"]
listing = list_concepts(bundle)
# listing.concepts  — seed candidates with concept IDs and frontmatter
# listing.problems  — tuple of ListingProblem for skipped or malformed entries
```

### Index Files

`generate_index()` produces a conformant `index.md` body string from a sequence
of `ConceptManifestEntry` objects scoped to a directory. `render_index_document()`
can wrap that body with bundle-root `okf_version` frontmatter when a bundle
configuration declares a supported version; otherwise indexes remain body-only.
Entries are grouped by
their `type` frontmatter field and sorted alphabetically within each group.
Unknown but valid string `type` values are tolerated and grouped normally per
OKF spec §9. Entries whose `type` is absent or not a string are a spec §4.1
violation; they are skipped and reported as `IndexProblem` objects in the
`problems` field of the result. Entries or subdirectories whose path falls
outside `directory` are likewise skipped and reported. Subdirectory entries
appear in a trailing `Subdirectories` section.

**Local Tool-Specific Enhancement**: `generate_index()` and the `okf index` CLI command support an optional directory metadata sidecar file (by default `_directory.yml`, configurable via `directory_metadata_file`). Since subdirectories are not concepts and do not have an identity in the base OKF spec, this sidecar allows configuring folder-level metadata as a non-spec local tool enhancement. If the file exists (supporting `.yaml` fallback if the name ends with `.yml` and vice versa), it is parsed and validated like a concept document's frontmatter (requiring a `type` field which should be `_directory`). Any validation findings or parsing problems are surfaced in the `problems` list of the returned `GeneratedIndex`.

If the sidecar is valid:
- Its `title` key overrides the directory name in the trailing `Subdirectories` section (defaults to the relative directory path).
- Its `description` key provides the directory's description in the `Subdirectories` section.

If a description is not defined in the sidecar, the `describe_directory` callback (if provided) is used as a fallback.

Entry titles come from the
`title` frontmatter field, converted to a string, with internal newlines
collapsed to spaces and then stripped; if absent, `None`, or
empty/whitespace-only, the file stem is used as a fallback. Falsy-but-non-empty
values such as `title: 0` are preserved as their string form. The same
normalisation applies to `description` and to strings returned by
`describe_directory` (and the sidecar description): absent, `None`, or
empty/whitespace-only values omit the entry suffix; falsy-but-non-empty values
are preserved. The function returns a `GeneratedIndex` dataclass with `.body`
and `.problems` fields; writing the file to disk is the caller's responsibility
for library use. The CLI `okf index` command owns that write step for
command-line use.


```python
from okf_core import generate_index, scan_bundle, load_config

config = load_config()
bundle = config.bundles["default"]
manifest = scan_bundle(bundle)
result = generate_index(bundle.bundle_root, manifest.concepts)
# result.body  — the rendered index.md content
# result.problems  — tuple of IndexProblem for any skipped entries
```

`parse_index()` parses an existing `index.md` body into a `ParsedIndex`
containing `IndexSection` and `IndexEntry` objects plus a `.problems` tuple for
malformed list items that were skipped. Generated output round-trips through
`parse_index` without loss; markdown link metacharacters (`[`, `]` in titles,
`)` in links) are escaped on generation and unescaped on parsing. Hand-authored
index entries that do not match the generated/spec entry shape are reported as
parse problems instead of causing the full index parse to fail.

The `describe_directory` keyword argument to `generate_index()` is a hook point
for callers that want to supply directory-level descriptions — for example, a
workflow agent using its own model access. It receives the absolute subdirectory
path and should return a description string or `None`.  `okf-core` itself never
makes model API calls.

### Context Packs

`build_context_pack(bundle, seed_concept_ids, *, graph=None, depth=1, direction="both", budget_chars=None)` assembles a deterministic context pack from explicit seed concept IDs. Seeds appear first in the returned entries (in the order provided), followed by graph-expanded concepts ordered by distance then concept ID. `depth` controls how many hops of graph expansion are performed (default `1`); `direction` controls whether outbound links, backlinks, or both are followed (default `"both"`). `budget_chars` sets an approximate character-count budget; entries are added in stable order until the budget is exhausted and any remaining discovered concepts are reported in `omitted_concept_ids`. Pass `budget_chars=None` (the default) to include all discovered concepts.

Each `ContextEntry` in the result includes `concept_id`, `path`, `title`, `content`, `selection_reason` (`"seed"`, `"outbound-link"`, or `"backlink"`), `graph_distance`, and `char_count`. The `ContextPack` result provides `bundle_name`, `seeds` (the de-duplicated valid seed IDs in input order), `entries`, `omitted_concept_ids` (budget- and read-error omissions), and `problems` (unknown seeds and file-read errors).

Pass a pre-built `BundleGraph` as `graph` to avoid building the graph twice.
When the graph was built from scanned manifest entries, context pack content
reuses each entry's scan-time content snapshot instead of rereading files from
disk.

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

`extract_markdown_links()` extracts standard non-image Markdown links from a
Markdown body. It uses a CommonMark-compatible parser so links in fenced code,
inline code, and images are ignored.

`build_bundle_graph(bundle, manifest=None)` scans concept bodies and returns a
`BundleGraph` with resolved directed concept links, broken internal concept
links, and non-fatal graph problems. Callers may pass an existing
`BundleManifest` to avoid scanning twice; scanned manifest entries also let
graph construction reuse the raw content snapshot instead of rereading concept
files. Graph problems use the same scan-style kind values for document failures,
such as `read-error`, `decode-error`, and `parse-error`.

Internal OKF concept links resolve according to OKF v0.1 rules:

- `/path/to/concept.md` resolves relative to the configured bundle root.
- `./concept.md` and `../concept.md` resolve relative to the source concept's
  directory.
- URL fragments and query strings are preserved in the raw target and ignored
  for path resolution.

External URLs, fragment-only links, `mailto:` links, non-Markdown assets, and
configured reserved filenames such as `index.md` and `log.md` are not concept
edges. Missing internal concept targets are reported in `broken_links`; they are
not fatal errors because OKF consumers must tolerate broken cross-links.

`links_from(graph, concept_id)`, `backlinks_to(graph, concept_id)`, and
`neighborhood(graph, concept_id, depth=1)` provide deterministic traversal over
resolved links. Neighborhood traversal treats links as bidirectional for
discovery while preserving directed edges in the underlying graph. It raises
`ValueError` for unknown concept IDs or negative depths.

```python
from okf_core import backlinks_to, build_bundle_graph, links_from, load_config

config = load_config()
bundle = config.bundles["default"]
graph = build_bundle_graph(bundle)
outbound = links_from(graph, "topics/example")
inbound = backlinks_to(graph, "topics/example")
```

### CLI

Install the package to register the `okf` command:

```sh
pip install -e .
```

All commands load `okf-core.toml` by searching upward from the current working
directory. Use `--config PATH` to specify a config file explicitly and
`--bundle NAME` to select a named bundle (default: `default`).

Commands emit machine-readable JSON on stdout and a one-line human-readable
summary on stderr. Exit codes: `0` success, `1` errors or validation failures,
`2` config or usage error.

#### `okf scan`

Scans a bundle and emits a manifest:

```sh
okf scan [--config PATH] [--bundle NAME]
```

Output: `{"bundle": "...", "concepts": [...], "problems": [...]}`

Each concept entry includes `concept_id`, `path`, `size`, `sha256`, and
`frontmatter`. Scan problems (parse errors, etc.) are non-fatal and appear in
`problems` with `path`, `kind`, and `message` fields; exit code is always `0`.

#### `okf validate`

Validates all concept documents against the configured profile:

```sh
okf validate [--config PATH] [--bundle NAME]
```

Output: `{"bundle": "...", "findings": {"path": [{"severity": "...", "message": "...", "field": "..."}]}}`

Only paths with findings appear as keys. Exits `1` if any error-severity
findings are present; exits `0` if there are only warnings or no findings.

#### `okf list-concepts`

Lists addressable concept documents for seed discovery:

```sh
okf list-concepts [--config PATH] [--bundle NAME] [--with-graph-counts]
```

Output: `{"bundle": "...", "concepts": [...], "problems": [...]}`

Each concept entry includes `concept_id`, `path`, `type`, `title`,
`description`, promoted `fields`, preserved `frontmatter`, and optional
`outbound_link_count` / `inbound_link_count`. Counts are `null` unless
`--with-graph-counts` is supplied. Listing problems are non-fatal and include
`concept_id`, `path`, `kind`, and `message`.

#### `okf context`

Builds a deterministic context pack from one or more seed concept IDs:

```sh
okf context [--config PATH] [--bundle NAME] --seed CONCEPT_ID [--seed CONCEPT_ID ...] [--depth N] [--direction outbound|inbound|both] [--budget-chars N]
```

Output: `{"bundle": "...", "seeds": [...], "entries": [...], "omitted_concept_ids": [...], "problems": [...]}`

Each entry includes `concept_id`, `path`, `title`, `selection_reason`,
`graph_distance`, `char_count`, and raw Markdown `content`. Seeds are
de-duplicated, kept in input order, and emitted before graph-expanded concepts.
The `seeds` field contains only valid resolved seed IDs; unknown seeds appear
in `problems` and are omitted from `seeds` and `entries`. `--depth` controls
graph expansion, `--direction` selects outbound links, backlinks, or both, and
`--budget-chars` applies the same stable prefix budget used by the Python API.
Concepts excluded by budget appear in `omitted_concept_ids` without making the
command fail.

Unknown seeds and read problems appear in `problems` and exit `1`. Invalid
options, config errors, and unknown bundles exit `2`.

#### `okf index`

Generates `index.md` for a directory within a bundle:

```sh
okf index [--config PATH] [--bundle NAME] [--directory PATH] [--force]
```

`--directory` defaults to the bundle root. Scans the bundle, collects concepts
and immediate subdirectories for the target directory, calls `generate_index()`,
and writes `index.md` to that directory. For the bundle root only, configured
`okf_version` is emitted as frontmatter. Before writing any index in a bundle,
the command checks the bundle-root `index.md`; if that file declares an
unsupported, invalid, or unparsable `okf_version`, the command leaves the
bundle untouched. Read-only commands such as `scan`, `list-concepts`, and
`graph` continue best-effort consumption of newer-version bundles.

When config omits `okf_version`, root index generation preserves an existing
supported root `okf_version` declaration by default. `--force` intentionally
overwrites the root index without preserving that declaration, but it does not
bypass unsupported-version write safety.

Output: `{"path": "...", "entries": N, "problems": [...], "scan_problems": [...]}`

`entries` is the number of entries actually written (candidates minus skipped).
`problems` lists index-level skipped entries (e.g. missing `type` field).
`scan_problems` lists parse/read failures for files in the target directory that
were silently omitted from the index.

Exits `1` if any entries were skipped or any scan problems occurred in the target
directory; exits `0` on clean generation.

#### `okf graph`

Builds a deterministic graph from Markdown links in concept bodies:

```sh
okf graph [--config PATH] [--bundle NAME]
okf graph [--config PATH] [--bundle NAME] --concept CONCEPT_ID [--depth N]
okf graph [--config PATH] [--bundle NAME] --broken
```

Full output includes `concepts`, resolved `links`, `broken_links`, and
`problems`. `--concept` emits outbound links, backlinks, broken links from that
concept, and a depth-limited `neighborhood`. `--broken` emits only broken
internal concept links and graph problems.

Broken links do not make the command fail. Unknown bundles, unknown concept IDs,
invalid depth values, and config errors exit `2`.

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

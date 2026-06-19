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

This repository is in early MVP development. Configuration loading and concept
document parsing are implemented; the other OKF operations described below are
the planned public shape of the project and are not implemented yet.

When features are implemented, this README should be updated in the same pull
request. Documentation must distinguish implemented behavior from planned
behavior, and README edits should be reviewed as a whole after patching so the
document stays internally consistent.

## Current Capabilities

`okf-core` currently provides an installable Python package with typed project
configuration loading and structural concept document parsing.

```python
from okf_core import load_config, parse_concept_document

config = load_config()
document = parse_concept_document("---\ntype: concept\n---\nBody\n")
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

- `bundle_roots`
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
bundle_roots = ["."]
include = ["**/*.md"]
exclude = []
reserved_filenames = ["index.md", "log.md"]
concept_path_strategy = "relative-path"
index_cache = ".okf-cache"
```

If no bundles are declared, `okf-core` exposes one resolved bundle named
`default` using the project defaults. Declared bundles inherit project defaults
and may override them per bundle.

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

`validate_concept_document()` performs base OKF concept conformance checks. The
base requirement is only a non-empty string `type` in frontmatter; missing
optional fields are tolerated.

## Planned Operations

The planned library and CLI surface is grouped around deterministic operations.
No operation should require this package to own an LLM API token.

### Bundle Operations

- List configured bundles.
- Describe bundle roots and configuration.
- Scan bundles into a manifest of concept IDs, paths, hashes, mtimes, and
  frontmatter summaries.
- Validate base OKF conformance and optional project-specific profiles.

### Concept Operations

- Locate a concept by ID.
- Resolve concept IDs to safe paths and paths back to concept IDs.

### Query and Context Operations

- Build and refresh a local SQLite index.
- Search title, description, frontmatter, and body text with lexical search.
- Produce context packs from a query or seed concepts within a token budget.

### Graph Operations

- Extract Markdown links.
- Resolve links according to configured bundle roots.
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

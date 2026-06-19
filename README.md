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

This repository is being bootstrapped. The operations described below are the
planned public shape of the project and are not implemented yet.

When features are implemented, this README should be updated in the same pull
request. Documentation must distinguish implemented behavior from planned
behavior, and README edits should be reviewed as a whole after patching so the
document stays internally consistent.

## Current Capabilities

`okf-core` currently provides an installable Python package skeleton. The only
public Python surface is:

```python
import okf_core

okf_core.__version__
```

Install the package for local development and tests with:

```sh
python -m pip install -e ".[test]"
```

Run the test suite with:

```sh
pytest
```

The OKF operations described below remain planned and unimplemented.

## Planned Configuration

The default project configuration file will be `okf-core.toml`.

The configuration model is expected to support:

- one or more OKF bundle roots;
- include and exclude globs;
- reserved filenames such as `index.md` and `log.md`;
- concept ID to path mapping rules;
- optional local profile validation rules;
- taxonomy hints such as known or allowed concept `type` values;
- optional index or cache locations.

Python APIs should also accept explicit paths and options so `okf-core` remains
usable without a configuration file.

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
- Parse and serialize Markdown documents with YAML frontmatter.
- Preserve unknown frontmatter keys during round trips.
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

"""Orientation guidance content for OKF bundles."""

ORIENTATION_GUIDE = """# OKF Bundle Developer & Agent Orientation

Welcome to your orientation for Open Knowledge Format (OKF) bundles. This guide provides the necessary information for developers and automated agents to interact with, scan, validate, and navigate an OKF bundle using `okf-core`.

## 1. What is an OKF Bundle?
An Open Knowledge Format (OKF) bundle is a directory containing concept documents, indexes, and logs structured according to the OKF specification. Concept documents are Markdown files containing YAML frontmatter and a body.

## 2. Configuration (`okf-core.toml`)
A bundle is configured using an `okf-core.toml` file, which defines defaults and named bundles.

Example `okf-core.toml`:
```toml
[defaults]
bundle_root = "docs"
reserved_filenames = ["index.md", "log.md"]

[bundles.custom]
bundle_root = "another_dir"
profile = "custom-profile"
stable_id_field = "stable_id"
```

Common configuration keys:
- `bundle_root`: Path to the root directory containing the concept documents (relative to config file).
- `reserved_filenames`: List of filenames ignored as normal concept documents (e.g. `index.md`, `log.md`).
- `stable_id_field`: Document frontmatter field used to store an opaque stable identifier for the concept (bundle-level only; not supported under defaults).
- `okf_cache_dir`: Path to the directory where the SQLite database cache is stored (bundle-level only; required for search).
- `directory_metadata_file`: File used for directory-level metadata (default: `_directory.yml`).

## 3. Common CLI Commands
Use these commands to interact with the bundle:

### List Bundles
Lists all configured bundles:
```sh
okf list-bundles [--config PATH]
```

### List Concepts
Lists addressable concepts within a bundle:
```sh
okf list-concepts [--config PATH] [--bundle NAME] [--with-graph-counts] [--with-content]
```

### Scan the Bundle
Scans the bundle and emits a JSON manifest detailing concepts and problems:
```sh
okf scan [--config PATH] [--bundle NAME] [--quiet]
```

### Validate Conformance
Validates the bundle against base OKF rules and optional profiles:
```sh
okf validate [--config PATH] [--bundle NAME] [--quiet]
```

### Generate Indexes
Generates or updates `index.md` files for concept directories:
```sh
okf index [--config PATH] [--bundle NAME] [--directory PATH] [--force] [--quiet] [--recurse]
```

### Search Concepts
Searches the bundle using SQLite FTS5:
```sh
okf search <query> [--config PATH] [--bundle NAME] [--limit N] [--no-refresh]
```

### Inspect the Link Graph
Analyzes links and neighborhood topology:
```sh
okf graph [--config PATH] [--bundle NAME] [--concept ID] [--depth N] [--broken]
```

### Build a Context Pack
Assembles a contextual prompt snippet starting from seed concepts:
```sh
okf context --seed ID [--seed ID2] [--depth N] [--direction both|inbound|outbound] [--budget-chars N]
```

### Manage Stable IDs
Retrieves, generates, or writes stable UUIDs back to concept documents:
```sh
okf stable-id [CONCEPT_ID] [--config PATH] [--bundle NAME] [--force] [--write]
```

### Show Orientation
Displays this onboarding and orientation guidance:
```sh
okf orient
```
"""

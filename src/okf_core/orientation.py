"""Orientation guidance content for OKF bundles."""

ORIENTATION_GUIDE = """# OKF Bundle Agent & Developer Orientation

Welcome! This guide provides the core information needed for developers and automated agents to quickly onboard and integrate with an Open Knowledge Format (OKF) bundle.

## 1. What is an OKF Bundle?
An OKF bundle is a structured folder containing knowledge assets. At a minimum, it consists of:
- **Concept Documents**: Markdown files containing YAML frontmatter defining their metadata (specifically the `type` field).
- **Configuration**: An optional `okf-core.toml` file defining defaults and named bundles.

## 2. Configuration (`okf-core.toml`)
A bundle is configured using an optional `okf-core.toml` file at the root. Here is an example of common configuration options (the full reference is in README.md):
```toml
[defaults]
# Relative path to the folder containing concept markdown files (default: ".")
bundle_root = "docs"
# Glob patterns to include/exclude as concepts
include = ["**/*.md"]
exclude = []
# Files ignored as concepts (default: ["index.md", "log.md"])
reserved_filenames = ["index.md", "log.md"]
# Strategy to generate concept IDs (default: "relative-path")
concept_path_strategy = "relative-path"
# Metadata file used for directory-level attributes
directory_metadata_file = "_directory.yml"

[bundles.docs]
# Named bundle inherits/overrides [defaults]
bundle_root = "docs"
# Path to cache directory (bundle-level only; required for search)
okf_cache_dir = ".okf-cache"
# Frontmatter field for stable concept ID (bundle-level only)
stable_id_field = "id"
# Optional validation profile name
profile = "standard"
```

## 3. Core Integration Commands
To interact with or integrate a bundle, start with these essential commands:

- **List Concepts**: Discover all addressable concepts in the bundle.
  ```sh
  okf list-concepts
  ```
- **Validate**: Check the bundle for spec conformance and profile rules.
  ```sh
  okf validate
  ```
- **Scan**: Retrieve a complete JSON manifest of all concepts and identified problems.
  ```sh
  okf scan
  ```

## 4. Progressive Discovery
To discover more advanced capabilities, commands, and options:
- **CLI Options & Help**: Run `okf --help` to list all subcommands. For detailed options on any specific command, run:
  ```sh
  okf <command> --help
  ```
- **Detailed Reference**: Check the bundle's `README.md` for full command specifications and Python library APIs.
- **Orientation Guide**: Run `okf orient` to print this onboarding guide.
"""

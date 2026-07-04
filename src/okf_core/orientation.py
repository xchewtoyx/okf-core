"""Orientation guidance content for OKF bundles."""

ORIENTATION_GUIDE = """# OKF Bundle Agent & Developer Orientation

Welcome! This guide provides the core information needed for developers and automated agents to quickly onboard and integrate with an Open Knowledge Format (OKF) bundle.

## 1. What is an OKF Bundle?
An OKF bundle is a structured folder containing knowledge assets. At its minimum, it consists of:
- **Concept Documents**: Markdown files containing YAML frontmatter defining their metadata (specifically the `type` field).
- **Configuration**: An optional `okf-core.toml` file defining defaults and named bundles.

## 2. Core Integration Commands
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

## 3. Progressive Discovery
To discover more advanced capabilities, commands, and options:
- **CLI Options & Help**: Run `okf --help` to list all subcommands. For detailed options on any specific command, run:
  ```sh
  okf <command> --help
  ```
- **Detailed Reference**: Check the bundle's `README.md` for full command specifications and Python library APIs.
- **Orientation Guide**: Run `okf orient` to print this onboarding guide.
"""

# AGENTS.md

This file is contributor guidance for humans and LLM coding agents working
inside the `okf-core` repository. It is not a prompt template for projects that
consume `okf-core`.

## Developer Setup

- Always develop and run tests within a local virtual environment named `.venv` to prevent package pollution.
- Bootstrap the environment and dependencies using:
  ```sh
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install -e ".[test]"
  ```

## Project Shape

- Implement `okf-core` as a Python package.
- Use `src/okf_core` for package code.
- Use `tests` for pytest coverage.
- Keep the package usable as a library first, with CLI behavior layered on top.
- Use `okf-core.toml` as the default project configuration convention.

## Design Constraints

- Keep `okf-core` aligned with the public OKF v0.1 specification:
  https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md
- For spec-sensitive work, cache the spec content to `.agent-cache/SPEC.md`
  if it is not already present, then consult that local copy while working.
  `.agent-cache/` is local agent/development state and must not be committed.
- Use the upstream reference path implementation as context for concept ID and
  path behavior:
  https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/src/enrichment_agent/bundle/paths.py
- Treat these OKF v0.1 sections as authoritative for current MVP behavior:
  Section 2 Terminology, Section 3 Bundle Structure, Section 3.1 Reserved
  filenames, Section 4 Concept Documents, Section 4.1 Frontmatter, Section 6
  Index Files, Section 7 Log Files, Section 9 Conformance, and Section 11
  Versioning.
- Behavior must always be capable of reduction to the base OKF specification.
  No extension introduced by this repository should be mandatory, or
  fundamentally change base OKF concepts such as bundle, concept ID, reserved
  files, or frontmatter tolerance.
- Keep behavior configurable and layout-agnostic so different repositories can
  use different OKF roots, document taxonomies, and path conventions.
- Do not reinvent mature infrastructure. Prefer well-supported Python libraries
  for parsing, validation, CLI surfaces, plugin/hook dispatch, and workflow
  orchestration when they fit the project constraints. Build `okf-core` code
  where OKF-specific behavior is the value.
- Do not add mandatory direct LLM API calls, funded API token requirements, or
  hosted model dependencies.
- Treat agent-facing examples for consuming projects as optional templates only.
  Keep them outside this root `AGENTS.md`, for example under
  `examples/agent-instructions/`.
- Preserve unknown OKF frontmatter fields unless a change explicitly targets
  them.
- Prefer deterministic parsing, validation, indexing, graph, and patch logic
  over agent interpretation.
- Keep agent runtimes and workflow orchestrators optional. For example,
  LangGraph may be an integration target for consuming projects, but should not
  become a required core dependency unless a future issue explicitly justifies
  that tradeoff.

## Delivery Rules

- Tests are mandatory for delivered behavior.
- User-facing behavior changes must update `README.md`.
- After editing `README.md`, review the document as a whole so it stays accurate
  and internally consistent.
- Updates to `main` must happen through pull requests only.
- Do not close story issues until the implementation PR has been approved by a
  human and merged.
- Do not try to align pull request numbers with issue numbers. GitHub assigns
  them from one shared sequence, and issues and PRs do not have a stable
  one-to-one relationship. Link work explicitly with `Closes #N`, `Refs #N`, or
  `Part of #N` in PR bodies instead.
- Copilot review can supplement human review, but it does not replace the human
  approval requirement.

## Testing Guidelines

- **Decompose Tests**: Avoid monolithic "happy-path" tests that assert multiple independent configurations in a single test case. Decompose them into focused, single-responsibility tests to prevent assertion shadowing.
- **Utilize Parameterization**: Prefer `pytest.mark.parametrize` to cleanly cover variations of configurations, inputs, and boundaries rather than duplicating test structures.
- **Ensure Negative Coverage**: Every feature or parsing capability must have explicit negative tests verifying failure modes, such as:
  - Incorrect data/config types (e.g., list vs. string).
  - Malformed file inputs or config structures (e.g. invalid syntax).
  - Explicit error handling checks (asserting that `ConfigError` or expected domain exceptions are raised).
- **Enforce Code Formatting**: Run code formatting with `black` on the codebase prior to executing tests and before pushing/submitting code changes:
  ```sh
  black src tests
  ```

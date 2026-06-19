# AGENTS.md

This file is contributor guidance for humans and LLM coding agents working
inside the `okf-core` repository. It is not a prompt template for projects that
consume `okf-core`.

## Project Shape

- Implement `okf-core` as a Python package.
- Use `src/okf_core` for package code.
- Use `tests` for pytest coverage.
- Keep the package usable as a library first, with CLI behavior layered on top.
- Use `okf-core.toml` as the default project configuration convention.

## Design Constraints

- Keep behavior configurable and layout-agnostic so different repositories can
  use different OKF roots, document taxonomies, and path conventions.
- Do not add mandatory direct LLM API calls, funded API token requirements, or
  hosted model dependencies.
- Treat agent-facing examples for consuming projects as optional templates only.
  Keep them outside this root `AGENTS.md`, for example under
  `examples/agent-instructions/`.
- Preserve unknown OKF frontmatter fields unless a change explicitly targets
  them.
- Prefer deterministic parsing, validation, indexing, graph, and patch logic
  over agent interpretation.

## Delivery Rules

- Tests are mandatory for delivered behavior.
- User-facing behavior changes must update `README.md`.
- After editing `README.md`, review the document as a whole so it stays accurate
  and internally consistent.
- Updates to `main` must happen through pull requests only.
- Do not close story issues until the implementation PR has been approved by a
  human and merged.
- Copilot review can supplement human review, but it does not replace the human
  approval requirement.

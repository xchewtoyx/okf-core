# AGENTS.md

This file is contributor guidance for humans and LLM coding agents working
inside the `okf-core` repository. It is not a prompt template for projects that
consume `okf-core`.

## Developer Setup

- Always develop and run tests within a local virtual environment named `.venv` to prevent package pollution.
- Note: Developer environment bootstrapping (`just install`) requires Python 3.11+ to utilize the standard library's `tomllib` and avoid dependency workarounds in development. Runtime compatibility for consuming projects still fully supports Python 3.10+.
- Install [`just`](https://just.systems/) for a simpler command surface. If unavailable, use the manual steps below instead.
- Bootstrap the environment and dependencies using:
  ```sh
  just install
  ```
  This creates `.venv` and installs the package with test dependencies. `just` recipes invoke the venv Python directly so no manual activation is needed for `just fmt`, `just test`, etc. For interactive use (e.g. a Python shell), activate with `source .venv/bin/activate`. Equivalent manual steps:
  ```sh
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install -e ".[test,dev]"
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
- Avoid over-engineering type/schema validation within core validation APIs. Core validation must focus on base OKF conformance (like the `type` string) and simple presence/non-emptiness checks for profile-required fields, leaving rich type/schema enforcement to the consuming project or custom workflow hooks.
- **Surface problems explicitly; never fail silently.** When a function
  encounters input it cannot process (malformed data, spec violations, missing
  required fields), expose the problem through a structured return channel.
  Preferred channels in order:
  1. **Named-dataclass return** — for functions that produce collections or
     generated output. Mirrors `scan_bundle`'s `BundleManifest` pattern: return
     a frozen dataclass with named fields (e.g. `.body` and `.problems`, or
     `.concepts` and `.problems`) so callers access results by name and the
     return type can gain fields without breaking call sites. Use when the
     caller should always see what was skipped, even if they choose not to act
     on it.
  2. **Raised exception** — for functions where any failure makes the result
     meaningless (e.g. `parse_concept_document`, `load_config`). Use a
     domain-specific exception type already established in the module.
  3. **Callback / hook** — only when the caller explicitly opts in and
     silent-skip on no-callback is documented and acceptable.
  Do not use `logging.warning()`, `warnings.warn()`, or `print()` as the
  primary problem channel — they are invisible to library callers and
  untestable without patching.

## Delivery Rules

- Tests are mandatory for delivered behavior.
- User-facing behavior changes must update `README.md` and any affected
  function docstrings in the same commit.  After editing either, search for
  all references to the changed function or parameter across `README.md`,
  `AGENTS.md`, and module docstrings to ensure nothing is left stale.
- After editing `README.md`, review the document as a whole so it stays accurate
  and internally consistent.
- After editing `AGENTS.md`, review the affected section as a whole for internal
  consistency (e.g. a sentence that says X is required should not be immediately
  followed by instructions for when X is unavailable).
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
  just fmt
  # or without just:
  black src tests
  ```
- **Static Analysis**: During development, use `just lint` to run ruff, mypy, and actionlint:
  ```sh
  just lint
  # or without just (requires venv activated or using venv binaries directly):
  python -m ruff check src tests .github/scripts/
  python -m mypy src tests .github/scripts/ --ignore-missing-imports
  .venv/bin/actionlint .github/workflows/*.yml
  ```
  `ruff` checks Python style and common bugs in the codebase and scripts; `mypy` checks types; `actionlint` validates GitHub Actions workflow YAML. All three are included in `.[dev]` deps and run in CI. Note: `just lint` does not include the Black formatting check — use `just ci` before pushing.
- **GitHub scripts**: Python files under `.github/scripts/` must have unit tests in `tests/` where feasible. Prefer testing pure functions directly without network calls by passing a stub or fake for any `_api`-style dependency.
- **Before pushing**, always run `just ci` — it is the definitive local equivalent of the full CI pipeline:
  ```sh
  just ci
  # or without just:
  black --check src tests && python -m ruff check src tests .github/scripts/ && python -m mypy src tests .github/scripts/ --ignore-missing-imports && .venv/bin/actionlint .github/workflows/*.yml && pytest
  ```
- Use `just test-matrix` to run the full pytest suite locally across all Python versions configured in the GHA workflow matrix (e.g. 3.10, 3.11, 3.12, 3.13) via Docker:
  ```sh
  just test-matrix
  # or without just:
  python .github/scripts/run_local_matrix.py
  ```
  This parses the workflow matrix from `.github/workflows/test.yml` and spins up containerized pytest checks to detect version incompatibilities before raising a pull request.


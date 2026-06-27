python := if os() == "windows" { ".venv\\Scripts\\python.exe" } else { ".venv/bin/python" }
_venv_actionlint := if os() == "windows" { ".venv\\Scripts\\actionlint.exe" } else { ".venv/bin/actionlint" }
actionlint := if path_exists(_venv_actionlint) == "true" { _venv_actionlint } else { "actionlint" }

set windows-shell := ["cmd.exe", "/c"]

# Create venv and install package with test deps
install:
    @just --justfile {{justfile()}} _install-{{os()}}

[private]
_install-windows:
    @python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || (echo error: Python 3.11+ required >&2 && exit 1)
    python -m venv .venv
    @if where actionlint >nul 2>&1 ({{python}} -m pip install -e ".[test,dev]") else ({{python}} -m pip install -e ".[test,dev,actionlint]")

[private]
_install-linux: _install-posix
[private]
_install-macos: _install-posix

[private]
_install-posix:
    @python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || (echo "error: Python 3.11+ required" >&2 && exit 1)
    python3 -m venv .venv
    @if command -v actionlint > /dev/null 2>&1; then \
        {{python}} -m pip install -e ".[test,dev]"; \
    else \
        {{python}} -m pip install -e ".[test,dev,actionlint]"; \
    fi

[private]
_require-venv:
    @just --justfile {{justfile()}} _require-venv-{{ if os() == "windows" { "windows" } else { "posix" } }}

[private]
_require-venv-windows:
    @if not exist {{python}} (echo error: venv not found — run 'just install' first >&2 && exit 1)

[private]
_require-venv-posix:
    @[ -x {{python}} ] || (echo "error: venv not found — run 'just install' first" >&2 && exit 1)

# Format code with black
fmt: _require-venv
    {{python}} -m black src tests

# Check formatting with black (non-destructive)
check: _require-venv
    {{python}} -m black --check src tests

# Run tests
test: _require-venv
    {{python}} -m pytest

# Run the CI test matrix locally in Docker
test-matrix: _require-venv
    {{python}} .github/scripts/run_local_matrix.py

# Run ruff and mypy static analysis
lint: _require-venv
    {{python}} -m ruff check src tests .github/scripts/ scripts/
    {{python}} -m mypy src tests .github/scripts/ scripts/ --ignore-missing-imports

# Lint GitHub Actions workflows with actionlint (skipped in Claude cloud instances)
lint-actions: _require-venv
    @if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && ! command -v {{actionlint}} > /dev/null 2>&1; then \
        echo "actionlint not available in cloud instance; skipping workflow lint"; \
    else \
        {{actionlint}} .github/workflows/publish.yml .github/workflows/test.yml; \
    fi

# Run check + lint + test (local superset of CI; also lints scripts/)
ci: check lint lint-actions test

# Run search benchmarks
benchmark-search: _require-venv
    {{python}} scripts/benchmark_search.py

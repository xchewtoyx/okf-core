python := if os() == "windows" { ".venv\\Scripts\\python.exe" } else { ".venv/bin/python" }
actionlint := if os() == "windows" { ".venv\\Scripts\\actionlint.exe" } else { ".venv/bin/actionlint" }

set windows-shell := ["cmd.exe", "/c"]

# Create venv and install package with test deps
install:
    @just --justfile {{justfile()}} _install-{{os()}}

[private]
_install-windows:
    @python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || (echo error: Python 3.11+ required && exit 1)
    python -m venv .venv
    {{python}} -m pip install -e ".[test,dev]"

[private]
_install-linux: _install-posix
[private]
_install-macos: _install-posix

[private]
_install-posix:
    @python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || (echo "error: Python 3.11+ required" && exit 1)
    python3 -m venv .venv
    {{python}} -m pip install -e ".[test,dev]"

[private]
_require-venv:
    @just --justfile {{justfile()}} _require-venv-{{ if os() == "windows" { "windows" } else { "posix" } }}

[private]
_require-venv-windows:
    @if not exist {{python}} (echo error: venv not found — run 'just install' first >&2 && exit 1)

[private]
_require-venv-posix:
    @[ -f {{python}} ] || (echo "error: venv not found — run 'just install' first" >&2 && exit 1)

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

# Run ruff, mypy, and actionlint static analysis
lint: _require-venv
    {{python}} -m ruff check src tests .github/scripts/
    {{python}} -m mypy src tests .github/scripts/ --ignore-missing-imports
    {{actionlint}} .github/workflows/publish.yml .github/workflows/test.yml

# Run check + lint + test (mirrors CI)
ci: check lint test

python := ".venv/bin/python"

# Create venv and install package with test deps
install:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
        echo "error: Python 3.11+ required, got $(python3 --version)" >&2
        exit 1
    fi
    python3 -m venv .venv
    {{python}} -m pip install -e ".[test,dev]"

[private]
_require-venv:
    #!/usr/bin/env bash
    if [ ! -x "{{python}}" ]; then
        echo "error: venv not found — run 'just install' first" >&2
        exit 1
    fi

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
    .venv/bin/actionlint .github/workflows/*.yml

# Run check + lint + test (mirrors CI)
ci: check lint test

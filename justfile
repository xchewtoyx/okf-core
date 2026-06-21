python := ".venv/bin/python"

# Create venv and install package with test deps
install:
    python3 -m venv .venv
    {{python}} -m pip install -e ".[test]"

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

# Run check + test (mirrors CI)
ci: check test

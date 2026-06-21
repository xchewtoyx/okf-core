python := ".venv/bin/python"

# Create venv and install package with test deps
install:
    python3 -m venv .venv
    {{python}} -m pip install -e ".[test]"

[private]
_ensure-venv:
    #!/bin/bash
    if [ ! -f {{python}} ]; then
        just install
    fi

# Format code with black
fmt: _ensure-venv
    {{python}} -m black src tests

# Check formatting with black (non-destructive)
check: _ensure-venv
    {{python}} -m black --check src tests

# Run tests
test: _ensure-venv
    {{python}} -m pytest

# Run check + test (mirrors CI)
ci: check test

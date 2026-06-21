python := ".venv/bin/python"

# Create venv and install package with test deps
install:
    python3 -m venv .venv
    {{python}} -m pip install -e ".[test]"

# Format code with black
fmt:
    {{python}} -m black src tests

# Check formatting with black (non-destructive)
check:
    {{python}} -m black --check src tests

# Run tests
test:
    {{python}} -m pytest

# Run check + test (mirrors CI)
ci: check test

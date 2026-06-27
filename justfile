system-python := if os() == "windows" { "python" } else { "python3" }
python := if os() == "windows" { ".venv\\Scripts\\python.exe" } else { ".venv/bin/python" }
venv-bin := if os() == "windows" { ".venv\\Scripts" } else { ".venv/bin" }
sep := if os() == "windows" { "\\" } else { "/" }

set windows-shell := ["cmd.exe", "/c"]

# Create venv and install package with test deps
install:
    {{ if os() == "windows" { "@python -c \"import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)\" || (echo error: Python 3.11+ required && exit 1)" } else { "@python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || (echo \"error: Python 3.11+ required\" && exit 1)" } }}
    {{system-python}} -m venv .venv
    {{python}} -m pip install -e ".[test,dev]"

[private]
_require-venv:
    {{ if os() == "windows" { "@if not exist " + python + " (echo error: venv not found — run 'just install' first && exit 1)" } else { "@[ -f " + python + " ] || (echo \"error: venv not found — run 'just install' first\" && exit 1)" } }}

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
    {{venv-bin}}{{sep}}actionlint .github/workflows/publish.yml .github/workflows/test.yml

# Run check + lint + test (mirrors CI)
ci: check lint test

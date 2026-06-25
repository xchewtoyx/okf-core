"""Tests for .github/scripts/run_local_matrix.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
import pytest

# Load run_local_matrix.py from its non-package location
_SCRIPT = Path(__file__).parent.parent / ".github" / "scripts" / "run_local_matrix.py"
_spec = importlib.util.spec_from_file_location("run_local_matrix", _SCRIPT)
assert _spec is not None, f"Could not load spec from {_SCRIPT}"
assert _spec.loader is not None, f"Spec for {_SCRIPT} has no loader"
run_local_matrix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_local_matrix)


def test_parse_workflow_matrix_success() -> None:
    """Verify that a valid workflow YAML structure is successfully parsed."""
    yaml_content = """
name: Test
jobs:
  pytest:
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
"""
    versions = run_local_matrix.parse_workflow_matrix(yaml_content)
    assert versions == ["3.11", "3.12", "3.13"]


def test_parse_workflow_matrix_missing_key() -> None:
    """Verify KeyError is raised when the python-version matrix is missing."""
    yaml_content = """
name: Test
jobs:
  pytest:
    strategy:
      matrix:
        wrong-key: ["3.11"]
"""
    with pytest.raises(
        KeyError, match="Could not find jobs.pytest.strategy.matrix.python-version"
    ):
        run_local_matrix.parse_workflow_matrix(yaml_content)


def test_parse_workflow_matrix_not_a_list() -> None:
    """Verify TypeError is raised when python-version in the matrix is not a list."""
    yaml_content = """
name: Test
jobs:
  pytest:
    strategy:
      matrix:
        python-version: "3.11"
"""
    with pytest.raises(TypeError, match="python-version in matrix must be a list"):
        run_local_matrix.parse_workflow_matrix(yaml_content)


def test_parse_workflow_matrix_not_a_dict() -> None:
    """Verify TypeError is raised when top-level YAML content is not a mapping."""
    yaml_content = "[]"
    with pytest.raises(TypeError, match="Workflow YAML content must be a mapping"):
        run_local_matrix.parse_workflow_matrix(yaml_content)


def test_main_docker_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify main exits if docker is not installed."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    with pytest.raises(SystemExit) as excinfo:
        run_local_matrix.main()
    assert excinfo.value.code == 1


def test_main_docker_daemon_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify main exits if the docker daemon is not accessible."""
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/docker")

    def mock_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if cmd == ["docker", "info"]:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(SystemExit) as excinfo:
        run_local_matrix.main()
    assert excinfo.value.code == 1

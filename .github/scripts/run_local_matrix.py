"""Run the CI pytest matrix locally inside Docker containers."""

from __future__ import annotations

import sys
import subprocess
from pathlib import Path
import yaml


def parse_workflow_matrix(yaml_content: str) -> list[str]:
    """Parse the Python version list from the test workflow YAML content.

    Args:
        yaml_content: Raw YAML content of the test workflow.

    Returns:
        List of python version strings in the matrix.
    """
    data = yaml.safe_load(yaml_content)
    if not isinstance(data, dict):
        raise TypeError("Workflow YAML content must be a mapping/dictionary")
    try:
        versions = data["jobs"]["pytest"]["strategy"]["matrix"]["python-version"]
    except KeyError as exc:
        raise KeyError(
            "Could not find jobs.pytest.strategy.matrix.python-version in the workflow YAML"
        ) from exc

    if not isinstance(versions, list):
        raise TypeError("python-version in matrix must be a list of strings")

    return [str(v) for v in versions]


def main() -> None:
    """Load test workflow and run pytest inside Docker for each Python version."""
    import shutil

    repo_root = Path(__file__).resolve().parents[2]
    workflow_path = repo_root / ".github/workflows/test.yml"

    if not workflow_path.is_file():
        print(f"Error: Workflow file not found at {workflow_path}", file=sys.stderr)
        sys.exit(1)

    # Check if docker is installed
    if not shutil.which("docker"):
        print(
            "Error: 'docker' command not found. Please install Docker to run local matrix tests.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check if docker daemon is running and accessible by the current user
    try:
        subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "Error: Cannot connect to the Docker daemon. "
            "Is the daemon running, and do you have permissions to run docker?",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        yaml_content = workflow_path.read_text(encoding="utf-8")
        versions = parse_workflow_matrix(yaml_content)
    except Exception as exc:
        print(f"Error parsing workflow file: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Found Python versions in matrix: {versions}")

    failed_versions = []
    for version in versions:
        print(f"\n{'='*60}")
        print(f"Running pytest matrix local check for Python {version}...")
        print(f"{'='*60}")

        # Mount the repository root as /app inside a slim Python container
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repo_root.as_posix()}:/app",
            "-w",
            "/app",
            f"python:{version}-slim",
            "sh",
            "-c",
            "pip install -q -e '.[test]' && pytest",
        ]

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            print(f"\n❌ Python {version} matrix check failed!", file=sys.stderr)
            failed_versions.append(version)
        else:
            print(f"\n✅ Python {version} matrix check passed!")

    if failed_versions:
        print(
            f"\n❌ Local matrix validation failed for: {', '.join(failed_versions)}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\n✅ All matrix environments passed successfully!")


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path

from okf_core import (
    ValidationFinding,
    load_config,
    validate_bundle,
)


def test_validate_bundle_identifies_all_problems(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"

[taxonomy]
known_types = ["concept", "decision"]

[profiles.strict]
required_frontmatter = ["type", "title", "status"]

[bundles.product]
bundle_root = "product"
profile = "strict"
""".strip(),
        encoding="utf-8",
    )

    # 1. Write concepts in product bundle
    product_root = tmp_path / "product"
    product_root.mkdir()

    # valid.md: perfectly matches the strict profile
    _write_concept(
        product_root / "valid.md",
        "---\ntype: concept\ntitle: Valid Document\nstatus: approved\n---\nBody\n",
    )

    # invalid_type.md: misses required type field (base conformance error)
    _write_concept(
        product_root / "invalid_type.md",
        "---\ntitle: Missing Type\nstatus: draft\n---\nBody\n",
    )

    # invalid_profile.md: misses required status field
    _write_concept(
        product_root / "invalid_profile.md",
        "---\ntype: decision\ntitle: Missing Status\n---\nBody\n",
    )

    # invalid_scan.md: malformed YAML (scan error)
    _write_concept(
        product_root / "invalid_scan.md",
        "---\ntype: concept\nmalformed: [invalid\n---\nBody\n",
    )

    # 2. Load config and run validation
    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]

    findings = validate_bundle(bundle, config)

    # 3. Assert results
    # valid.md should have no findings
    assert product_root / "valid.md" not in findings

    # invalid_type.md should report missing type error
    assert product_root / "invalid_type.md" in findings
    assert findings[product_root / "invalid_type.md"] == (
        ValidationFinding(
            severity="error",
            message="Missing required frontmatter field: type",
            field="type",
        ),
    )

    # invalid_profile.md should report missing required status field
    assert product_root / "invalid_profile.md" in findings
    assert findings[product_root / "invalid_profile.md"] == (
        ValidationFinding(
            severity="error",
            message="Missing required frontmatter field: status",
            field="status",
        ),
    )

    # invalid_scan.md should report scan error
    assert product_root / "invalid_scan.md" in findings
    scan_finding = findings[product_root / "invalid_scan.md"][0]
    assert scan_finding.severity == "error"
    assert "Scan error (parse-error)" in scan_finding.message


def test_validate_bundle_no_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"
""".strip(),
        encoding="utf-8",
    )
    docs_root = tmp_path / "docs"
    docs_root.mkdir()

    # valid.md: base conformance is checked
    _write_concept(
        docs_root / "valid.md",
        "---\ntype: concept\n---\nBody\n",
    )
    # invalid.md: missing type
    _write_concept(
        docs_root / "invalid.md",
        "---\ntitle: Missing Type\n---\nBody\n",
    )

    config = load_config(config_path=config_path)
    bundle = config.bundles["default"]
    findings = validate_bundle(bundle, config)

    assert docs_root / "valid.md" not in findings
    assert docs_root / "invalid.md" in findings
    assert findings[docs_root / "invalid.md"] == (
        ValidationFinding(
            severity="error",
            message="Missing required frontmatter field: type",
            field="type",
        ),
    )


def _write_concept(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")

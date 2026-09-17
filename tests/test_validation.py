from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import (
    ValidationFinding,
    entries_for_directory,
    generate_index,
    load_config,
    scan_bundle,
    validate_bundle,
)
from okf_core.config import BundleConfig, OkfConfig


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


def test_validate_bundle_type_fields_scoped_to_type(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"

[profiles.default]

[profiles.default.type_fields.platform-implementation]
required_frontmatter = ["platform"]

[bundles.product]
bundle_root = "product"
profile = "default"
""".strip(),
        encoding="utf-8",
    )

    product_root = tmp_path / "product"
    product_root.mkdir()

    # missing_platform.md: type-required field absent for its type
    _write_concept(
        product_root / "missing_platform.md",
        "---\ntype: platform-implementation\ntitle: No Platform\n---\nBody\n",
    )
    # has_platform.md: type-required field present
    _write_concept(
        product_root / "has_platform.md",
        "---\ntype: platform-implementation\nplatform: linux\n---\nBody\n",
    )
    # other_type.md: different type, unaffected by platform-implementation's
    # type_fields entry
    _write_concept(
        product_root / "other_type.md",
        "---\ntype: concept\ntitle: Unrelated\n---\nBody\n",
    )

    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]

    findings = validate_bundle(bundle, config)

    assert product_root / "missing_platform.md" in findings
    assert findings[product_root / "missing_platform.md"] == (
        ValidationFinding(
            severity="error",
            message="Missing required frontmatter field: platform",
            field="platform",
        ),
    )
    assert product_root / "has_platform.md" not in findings
    assert product_root / "other_type.md" not in findings


def test_validate_bundle_reports_attribution_findings(tmp_path: Path) -> None:
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

    # clean.md: footnote label matches its sources[].id -- no findings.
    _write_concept(
        docs_root / "clean.md",
        "---\ntype: concept\nsources:\n  - id: src-a\n    resource: https://example.com\n"
        "---\nA claim.[^src-a]\n",
    )
    # dangling.md: footnote label with no matching sources[].id -- error.
    _write_concept(
        docs_root / "dangling.md",
        "---\ntype: concept\n---\nA claim.[^missing]\n",
    )
    # unreferenced.md: sources[].id never cited by a footnote -- warning.
    _write_concept(
        docs_root / "unreferenced.md",
        "---\ntype: concept\nsources:\n  - id: unused\n    resource: https://example.com\n"
        "---\nNo citations here.\n",
    )

    config = load_config(config_path=config_path)
    bundle = config.bundles["default"]
    findings = validate_bundle(bundle, config)

    assert docs_root / "clean.md" not in findings

    assert findings[docs_root / "dangling.md"] == (
        ValidationFinding(
            severity="error",
            message="Footnote label 'missing' has no matching sources[].id",
            field="missing",
            line=1,
        ),
    )

    assert findings[docs_root / "unreferenced.md"] == (
        ValidationFinding(
            severity="warning",
            message="sources[].id 'unused' is not referenced by any footnote",
            field="unused",
            line=None,
        ),
    )


def test_validate_bundle_reports_index_drift_for_stale_committed_index(
    tmp_path: Path,
) -> None:
    """A committed index.md that predates a newly added concept file is
    reported as drift, keyed at the index.md path (#200)."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"

[bundles.product]
bundle_root = "product"
""".strip(),
        encoding="utf-8",
    )

    product_root = tmp_path / "product"
    product_root.mkdir()
    _write_concept(
        product_root / "alpha.md", "---\ntype: concept\ntitle: Alpha\n---\nBody\n"
    )
    _write_concept(
        product_root / "beta.md", "---\ntype: concept\ntitle: Beta\n---\nBody\n"
    )
    # Committed before beta.md existed -- beta.md is missing from it.
    _write_concept(product_root / "index.md", "# Concept\n\n* [Alpha](alpha.md)\n")

    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]
    findings = validate_bundle(bundle, config)

    index_path = product_root / "index.md"
    assert index_path in findings
    drift = findings[index_path]
    assert len(drift) == 1
    assert drift[0].severity == "warning"
    assert drift[0].field == "beta.md"
    assert "beta.md" in drift[0].message


def test_validate_bundle_freshly_regenerated_index_reports_no_drift(
    tmp_path: Path,
) -> None:
    """A committed index.md that exactly matches a fresh regeneration reports
    no drift finding at all -- the path does not appear in the findings dict."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"

[bundles.product]
bundle_root = "product"
""".strip(),
        encoding="utf-8",
    )

    product_root = tmp_path / "product"
    product_root.mkdir()
    _write_concept(
        product_root / "alpha.md", "---\ntype: concept\ntitle: Alpha\n---\nBody\n"
    )
    _write_concept(
        product_root / "beta.md", "---\ntype: concept\ntitle: Beta\n---\nBody\n"
    )

    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]

    # Regenerate index.md the same way `okf index` would, and commit exactly
    # that content.
    manifest = scan_bundle(bundle)
    direct_entries, subdirs = entries_for_directory(product_root, manifest)
    generated = generate_index(
        product_root,
        direct_entries,
        subdirs,
        directory_metadata_file=bundle.directory_metadata_file,
    )
    (product_root / "index.md").write_text(generated.body, encoding="utf-8")

    findings = validate_bundle(bundle, config)

    assert (product_root / "index.md") not in findings


def test_validate_bundle_reports_read_error_on_index_md_as_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed index.md that exists but can't be read (e.g. a permission
    error, or a concurrent delete after the is_file() check) is surfaced as
    a warning finding rather than propagating an uncaught OSError -- the
    same "surface problems explicitly" channel as every other drift finding."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"

[bundles.product]
bundle_root = "product"
""".strip(),
        encoding="utf-8",
    )

    product_root = tmp_path / "product"
    product_root.mkdir()
    _write_concept(
        product_root / "alpha.md", "---\ntype: concept\ntitle: Alpha\n---\nBody\n"
    )
    index_path = product_root / "index.md"
    _write_concept(index_path, "# Concept\n\n* [Alpha](alpha.md)\n")

    original_read_text = Path.read_text

    def raising_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == index_path:
            raise OSError("Permission denied")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", raising_read_text)

    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]
    findings = validate_bundle(bundle, config)

    assert index_path in findings
    assert len(findings[index_path]) == 1
    finding = findings[index_path][0]
    assert finding.severity == "warning"
    assert "could not read index.md for drift check" in finding.message
    assert "Permission denied" in finding.message


def test_validate_bundle_reports_malformed_index_md_frontmatter_as_finding(
    tmp_path: Path,
) -> None:
    """A committed index.md with unterminated (or otherwise invalid) YAML
    frontmatter must not crash `okf validate` via an uncaught
    DocumentParseError -- it's surfaced as a warning finding, same as any
    other drift-check failure."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"

[bundles.product]
bundle_root = "product"
""".strip(),
        encoding="utf-8",
    )

    product_root = tmp_path / "product"
    product_root.mkdir()
    _write_concept(
        product_root / "alpha.md", "---\ntype: concept\ntitle: Alpha\n---\nBody\n"
    )
    index_path = product_root / "index.md"
    # Opening "---" with no closing delimiter -- DocumentParseError.
    _write_concept(index_path, "---\ntype: concept\n# Concept\n\n* [Alpha](alpha.md)\n")

    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]
    findings = validate_bundle(bundle, config)

    assert index_path in findings
    assert len(findings[index_path]) == 1
    finding = findings[index_path][0]
    assert finding.severity == "warning"
    assert "could not parse index.md for drift check" in finding.message


def test_validate_bundle_reports_non_utf8_index_md_as_finding(
    tmp_path: Path,
) -> None:
    """A committed index.md containing bytes that aren't valid UTF-8 raises
    UnicodeDecodeError from `read_text` -- a ValueError subclass, not an
    OSError -- so it must be caught alongside the existing read-error
    handling rather than crashing `okf validate`."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"

[bundles.product]
bundle_root = "product"
""".strip(),
        encoding="utf-8",
    )

    product_root = tmp_path / "product"
    product_root.mkdir()
    _write_concept(
        product_root / "alpha.md", "---\ntype: concept\ntitle: Alpha\n---\nBody\n"
    )
    index_path = product_root / "index.md"
    # Invalid UTF-8 byte sequence (a lone 0xFF byte).
    index_path.write_bytes(b"# Concept\n\n* [Alpha](alpha.md)\n\xff")

    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]
    findings = validate_bundle(bundle, config)

    assert index_path in findings
    assert len(findings[index_path]) == 1
    finding = findings[index_path][0]
    assert finding.severity == "warning"
    assert "could not read index.md for drift check" in finding.message


def test_validate_bundle_strips_root_okf_version_frontmatter_before_diffing(
    tmp_path: Path,
) -> None:
    """The bundle root's committed index.md may carry an `okf_version`
    frontmatter block (render_index_document adds it only there); the drift
    check must strip it the same way before parsing, or every root index.md
    with a version declaration would false-positive as drift.

    A clean round trip alone can't tell stripped-frontmatter apart from
    unstripped, since markdown-it's block parser skips right past a leading
    ``---`` delimiter pair without disturbing heading/list detection either
    way. What *does* differ is line numbering: a malformed committed entry's
    reported line is relative to the body, so it lands on line 3 (the list
    item, right after the "# Concept" heading and its blank line) only if
    the 3-line frontmatter block was stripped first -- unstripped, the same
    list item would be line 6.
    """
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f"""
[defaults]
bundle_root = "{tmp_path / 'root'}"
okf_version = "0.1"
""".strip(),
        encoding="utf-8",
    )

    bundle_root = tmp_path / "root"
    bundle_root.mkdir()
    (bundle_root / "index.md").write_text(
        "---\nokf_version: '0.1'\n---\n# Concept\n\n* no link here\n",
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)
    bundle = config.bundles["default"]
    findings = validate_bundle(bundle, config)

    index_path = bundle_root / "index.md"
    assert index_path in findings
    assert len(findings[index_path]) == 1
    assert findings[index_path][0].line == 3


def test_validate_bundle_index_drift_does_not_affect_other_findings(
    tmp_path: Path,
) -> None:
    """Index-drift findings and per-concept validation findings are recorded
    under their own distinct paths and do not interfere with each other."""
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"

[profiles.strict]
required_frontmatter = ["type", "title", "status"]

[bundles.product]
bundle_root = "product"
profile = "strict"
""".strip(),
        encoding="utf-8",
    )

    product_root = tmp_path / "product"
    product_root.mkdir()
    _write_concept(
        product_root / "valid.md",
        "---\ntype: concept\ntitle: Valid\nstatus: approved\n---\nBody\n",
    )
    _write_concept(
        product_root / "invalid.md",
        "---\ntitle: Missing Type\nstatus: draft\n---\nBody\n",
    )
    # Stale: predates both valid.md and invalid.md.
    _write_concept(product_root / "index.md", "# Concept\n\n")

    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]
    findings = validate_bundle(bundle, config)

    # The pre-existing per-concept finding is unaffected by drift detection.
    assert findings[product_root / "invalid.md"] == (
        ValidationFinding(
            severity="error",
            message="Missing required frontmatter field: type",
            field="type",
        ),
    )
    assert product_root / "valid.md" not in findings

    # Drift is recorded separately, keyed at index.md's own path.
    index_path = product_root / "index.md"
    assert index_path in findings
    assert {f.field for f in findings[index_path]} == {"valid.md"}
    assert all(f.severity == "warning" for f in findings[index_path])


_NEWEST_FIRST_LOG = (
    "# Log\n\n## 2026-05-22\n* Newer entry.\n\n## 2026-05-15\n* Older entry.\n"
)
_FRIDAY_HEADING_LOG = "# Log\n\n## Friday\n* Entry.\n"
_FRIDAY_HEADING_FINDING = ValidationFinding(
    severity="error",
    message=(
        "skipped malformed date heading: 'Friday' is not ISO 8601 YYYY-MM-DD form"
    ),
    field="Friday",
    line=3,
)
_MISSING_TYPE_FINDING = ValidationFinding(
    severity="error",
    message="Missing required frontmatter field: type",
    field="type",
)


def _default_docs_bundle(tmp_path: Path) -> tuple[OkfConfig, BundleConfig, Path]:
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
    config = load_config(config_path=config_path)
    return config, config.bundles["default"], docs_root


def _write_log(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def test_validate_bundle_newest_first_log_is_clean(tmp_path: Path) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "note.md", "---\ntype: concept\n---\nBody\n")
    _write_log(docs_root / "log.md", _NEWEST_FIRST_LOG)

    findings = validate_bundle(bundle, config)

    assert docs_root / "log.md" not in findings
    assert docs_root / "note.md" not in findings


def test_validate_bundle_without_log_md_leaves_concept_findings_unchanged(
    tmp_path: Path,
) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "invalid.md", "---\ntitle: Missing Type\n---\nBody\n")

    findings = validate_bundle(bundle, config)

    assert docs_root / "log.md" not in findings
    assert findings[docs_root / "invalid.md"] == (_MISSING_TYPE_FINDING,)


def test_validate_bundle_reports_non_iso_log_heading_as_error(tmp_path: Path) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "note.md", "---\ntype: concept\n---\nBody\n")
    log_path = docs_root / "log.md"
    _write_log(log_path, _FRIDAY_HEADING_LOG)

    findings = validate_bundle(bundle, config)

    assert findings[log_path] == (_FRIDAY_HEADING_FINDING,)


def test_validate_bundle_reports_invalid_calendar_log_heading_as_error(
    tmp_path: Path,
) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "note.md", "---\ntype: concept\n---\nBody\n")
    log_path = docs_root / "log.md"
    _write_log(log_path, "## 2026-02-30\n* Entry.\n")

    findings = validate_bundle(bundle, config)

    assert findings[log_path] == (
        ValidationFinding(
            severity="error",
            message=(
                "skipped malformed date heading: '2026-02-30' is not a "
                "valid calendar date (day is out of range for month)"
            ),
            field="2026-02-30",
            line=1,
        ),
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            "## 2026-05-15\n* Older entry.\n\n## 2026-05-22\n* Newer entry.\n",
            ValidationFinding(
                severity="error",
                message=(
                    "date headings 2026-05-15 then 2026-05-22 are not newest-first"
                ),
                field="2026-05-22",
                line=None,
            ),
        ),
        (
            "## 2026-05-22\n* First.\n\n## 2026-05-22\n* Second.\n",
            ValidationFinding(
                severity="error",
                message=(
                    "date headings 2026-05-22 then 2026-05-22 are not newest-first"
                ),
                field="2026-05-22",
                line=None,
            ),
        ),
    ],
    ids=["oldest_first", "duplicate_dates"],
)
def test_validate_bundle_reports_log_dates_that_are_not_newest_first(
    tmp_path: Path, content: str, expected: ValidationFinding
) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "note.md", "---\ntype: concept\n---\nBody\n")
    log_path = docs_root / "log.md"
    _write_log(log_path, content)

    findings = validate_bundle(bundle, config)

    assert findings[log_path] == (expected,)


def test_validate_bundle_ignores_stray_block_in_newest_first_log(
    tmp_path: Path,
) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "note.md", "---\ntype: concept\n---\nBody\n")
    log_path = docs_root / "log.md"
    _write_log(
        log_path,
        "# Log\n\n## 2026-05-22\nA bare paragraph.\n\n* Newer entry.\n\n"
        "## 2026-05-15\n* Older entry.\n",
    )

    findings = validate_bundle(bundle, config)

    assert log_path not in findings


def test_validate_bundle_checks_nested_topics_log_md(tmp_path: Path) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "note.md", "---\ntype: concept\n---\nBody\n")
    nested_log = docs_root / "topics" / "log.md"
    _write_log(nested_log, _FRIDAY_HEADING_LOG)

    findings = validate_bundle(bundle, config)

    assert docs_root / "log.md" not in findings
    assert findings[nested_log] == (_FRIDAY_HEADING_FINDING,)


def test_validate_bundle_reports_unreadable_log_md_as_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "note.md", "---\ntype: concept\n---\nBody\n")
    log_path = docs_root / "log.md"
    _write_log(log_path, _NEWEST_FIRST_LOG)

    original_read_text = Path.read_text

    def raising_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == log_path:
            raise OSError("Permission denied")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", raising_read_text)

    findings = validate_bundle(bundle, config)

    assert findings[log_path] == (
        ValidationFinding(
            severity="error",
            message="could not read log.md: Permission denied",
        ),
    )


def test_validate_bundle_missing_bundle_root_has_no_log_key(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "missing"
""".strip(),
        encoding="utf-8",
    )
    config = load_config(config_path=config_path)
    findings = validate_bundle(config.bundles["default"], config)

    assert findings == {}


def test_validate_bundle_skips_log_md_that_is_not_a_file(tmp_path: Path) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "note.md", "---\ntype: concept\n---\nBody\n")
    (docs_root / "log.md").symlink_to("does-not-exist")

    findings = validate_bundle(bundle, config)

    assert docs_root / "log.md" not in findings


def test_validate_bundle_log_and_concept_findings_coexist(tmp_path: Path) -> None:
    config, bundle, docs_root = _default_docs_bundle(tmp_path)
    _write_concept(docs_root / "invalid.md", "---\ntitle: Missing Type\n---\nBody\n")
    log_path = docs_root / "log.md"
    _write_log(log_path, _FRIDAY_HEADING_LOG)

    findings = validate_bundle(bundle, config)

    assert findings[docs_root / "invalid.md"] == (_MISSING_TYPE_FINDING,)
    assert findings[log_path] == (_FRIDAY_HEADING_FINDING,)


def _write_concept(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")

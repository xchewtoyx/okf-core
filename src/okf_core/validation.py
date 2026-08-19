"""Bundle-level validation logic."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from okf_core.attribution import check_attribution_consistency
from okf_core.documents import (
    ConceptDocument,
    DocumentParseError,
    ValidationFinding,
    parse_concept_document,
    validate_concept_document,
    validate_concept_document_with_profile,
)
from okf_core.index import (
    concept_directories,
    diff_index,
    entries_for_directory,
    generate_index,
    parse_index,
)
from okf_core.manifest import BundleManifest, scan_bundle

if TYPE_CHECKING:
    from okf_core.config import BundleConfig, OkfConfig, ProfileConfig, TaxonomyConfig


def validate_bundle(
    bundle: BundleConfig,
    config: OkfConfig,
) -> dict[Path, tuple[ValidationFinding, ...]]:
    """Validate all concept documents in a bundle against its configured profile."""
    findings: dict[Path, tuple[ValidationFinding, ...]] = {}

    # Scan the bundle to discover and parse concepts
    manifest = scan_bundle(bundle)

    # Record scan problems as validation errors
    for problem in manifest.problems:
        findings[problem.path] = (
            ValidationFinding(
                severity="error",
                message=f"Scan error ({problem.kind}): {problem.message}",
            ),
        )

    # Resolve the profile to use
    profile = None
    if bundle.profile is not None:
        profile = config.profiles[bundle.profile]

    for entry in manifest.concepts:
        # Reconstruct a document with the concept's frontmatter
        doc = ConceptDocument(frontmatter=dict(entry.frontmatter))

        if profile is not None:
            doc_findings = validate_concept_document_with_profile(
                doc, profile, project_taxonomy=config.taxonomy
            )
        else:
            doc_findings = validate_concept_document(doc)

        # Attribution consistency (footnote labels vs. sources[].id) is base
        # spec behavior (§5.1), not profile-specific, so it runs regardless
        # of whether a profile is configured; reuse the already-parsed
        # frontmatter/body from the scan rather than re-parsing the document.
        doc_findings = tuple(doc_findings) + check_attribution_consistency(
            entry.frontmatter, entry.body
        )

        if doc_findings:
            findings[entry.path] = doc_findings

    for index_path, drift_findings in _index_drift_findings_by_path(
        bundle, manifest, profile, config.taxonomy
    ).items():
        findings[index_path] = findings.get(index_path, ()) + drift_findings

    return findings


def _index_drift_findings_by_path(
    bundle: BundleConfig,
    manifest: BundleManifest,
    profile: ProfileConfig | None,
    project_taxonomy: TaxonomyConfig | None,
) -> dict[Path, tuple[ValidationFinding, ...]]:
    """Compare every concept-bearing directory's committed index.md, if any.

    Directories with no committed ``index.md`` are out of scope entirely --
    "missing index" is a different, unrequested check (#200). Advisory only:
    every finding is ``severity="warning"``, so this never affects
    ``okf validate``'s exit code.
    """
    resolved_bundle_root = bundle.bundle_root.resolve()
    concept_dirs, _ = concept_directories(manifest, resolved_bundle_root)

    findings: dict[Path, tuple[ValidationFinding, ...]] = {}
    for directory in sorted(concept_dirs):
        index_path = directory / "index.md"
        if not index_path.is_file():
            continue
        drift = _directory_index_drift(
            directory, index_path, manifest, bundle, profile, project_taxonomy
        )
        if drift:
            findings[index_path] = drift
    return findings


def _directory_index_drift(
    directory: Path,
    index_path: Path,
    manifest: BundleManifest,
    bundle: BundleConfig,
    profile: ProfileConfig | None,
    project_taxonomy: TaxonomyConfig | None,
) -> tuple[ValidationFinding, ...]:
    """Diff one directory's committed index.md against a fresh regeneration.

    Uses the same ``entries_for_directory`` + ``generate_index`` parameters
    ``okf index`` itself uses (no ``describe_directory`` callback), so drift
    findings never reflect a divergent baseline.
    """
    direct_entries, subdirectories = entries_for_directory(directory, manifest)
    generated = generate_index(
        directory,
        direct_entries,
        subdirectories,
        directory_metadata_file=bundle.directory_metadata_file,
        profile=profile,
        project_taxonomy=project_taxonomy,
    )

    try:
        committed_content = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return (
            ValidationFinding(
                severity="warning",
                message=f"could not read index.md for drift check: {exc}",
            ),
        )

    # generate_index's body never carries frontmatter (render_index_document
    # adds it only when writing the bundle root's index.md); strip any
    # committed frontmatter the same way before parsing so both sides compare
    # like for like.
    try:
        committed_body = parse_concept_document(committed_content).body
    except DocumentParseError as exc:
        return (
            ValidationFinding(
                severity="warning",
                message=f"could not parse index.md for drift check: {exc}",
            ),
        )
    committed = parse_index(committed_body)
    return diff_index(generated, committed)

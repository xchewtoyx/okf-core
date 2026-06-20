"""Bundle-level validation logic."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from okf_core.documents import (
    ConceptDocument,
    ValidationFinding,
    validate_concept_document,
    validate_concept_document_with_profile,
)
from okf_core.manifest import scan_bundle

if TYPE_CHECKING:
    from okf_core.config import BundleConfig, OkfConfig


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

        if doc_findings:
            findings[entry.path] = doc_findings

    return findings

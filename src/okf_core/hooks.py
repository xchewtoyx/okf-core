"""Hook specifications and plugin manager for OKF operations."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import pluggy

from okf_core.config import BundleConfig
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from okf_core.manifest import BundleManifest, ConceptManifestEntry
    from okf_core.graph import ConceptLink, BundleGraph

hookspec = pluggy.HookspecMarker("okf")
hookimpl = pluggy.HookimplMarker("okf")


class OkfSpec:
    """Hook specifications for OKF operations."""

    @hookspec
    def okf_start_scan(
        self,
        bundle: BundleConfig,
    ) -> None:
        """Invoked at the beginning of a bundle scan, allowing plugins to open transactions/resources."""

    @hookspec(firstresult=True)
    def okf_enter_scan_concept(
        self,
        path: Path,
        root: Path,
        bundle: BundleConfig,
    ) -> ConceptManifestEntry | None:
        """Invoked before scanning a concept document.

        If a registered plugin returns a ConceptManifestEntry, the scanner will
        reuse it and skip parsing the file.
        """

    @hookspec
    def okf_exit_scan_concept(
        self,
        entry: ConceptManifestEntry,
        path: Path,
        root: Path,
        bundle: BundleConfig,
    ) -> None:
        """Invoked after scanning a concept document, allowing plugins to cache or process it."""

    @hookspec(firstresult=True)
    def okf_enter_resolve_links(
        self,
        entry: ConceptManifestEntry,
        bundle: BundleConfig,
    ) -> Sequence[ConceptLink] | None:
        """Invoked before resolving links from a concept document.

        If a registered plugin returns a sequence of ConceptLink records,
        the graph builder will reuse them directly and skip parsing the body.
        """

    @hookspec
    def okf_exit_resolve_links(
        self,
        entry: ConceptManifestEntry,
        links: Sequence[ConceptLink],
        bundle: BundleConfig,
    ) -> None:
        """Invoked after resolving links from a concept document, allowing plugins to cache or process them."""

    @hookspec
    def okf_end_scan(
        self,
        bundle: BundleConfig,
        manifest: BundleManifest,
    ) -> None:
        """Invoked at the end of a bundle scan, allowing cleanup/pruning of obsolete cache entries."""

    @hookspec
    def okf_abort_scan(
        self,
        bundle: BundleConfig,
    ) -> None:
        """Invoked if a bundle scan fails, allowing plugins to abort transactions/cleanup."""

    @hookspec
    def okf_start_graph(
        self,
        bundle: BundleConfig,
    ) -> None:
        """Invoked at the beginning of a graph build operation, allowing plugins to open transactions/resources."""

    @hookspec
    def okf_end_graph(
        self,
        bundle: BundleConfig,
        graph: BundleGraph,
    ) -> None:
        """Invoked at the end of a graph build operation, allowing plugins to commit/close transactions."""

    @hookspec
    def okf_abort_graph(
        self,
        bundle: BundleConfig,
    ) -> None:
        """Invoked if a graph build fails, allowing plugins to abort transactions/cleanup."""


def get_hook_manager(bundle: BundleConfig) -> pluggy.PluginManager:
    """Initialize and return a plugin manager for the given bundle config.

    If `bundle.okf_cache_dir` is configured, the SQLite cache plugin is loaded and registered.
    """
    pm = pluggy.PluginManager("okf")
    pm.add_hookspecs(OkfSpec)

    if bundle.okf_cache_dir is not None:
        from okf_core.cache import get_cache_plugin

        pm.register(get_cache_plugin(bundle))

    return pm

"""Hook specifications and plugin manager for OKF operations.

Hook Naming Convention
----------------------
All hooks in this module follow the ``okf_verb_noun`` naming pattern:

* **Whole-phase lifecycle hooks** use ``start`` / ``end`` / ``abort`` as the verb.
  These are called once at the beginning, successful completion, or failure of an
  entire operation (e.g. a full bundle scan or graph build transaction)::

      okf_start_scan   -- transaction opens
      okf_end_scan     -- transaction commits
      okf_abort_scan   -- transaction rolls back on error

      okf_start_graph
      okf_end_graph
      okf_abort_graph

* **Substitution hooks** use ``fetch`` as the verb. These allow a plugin to
  substitute a result (e.g., from a cache) and bypass the core computation.
  They are defined with ``firstresult=True``::

      okf_fetch_scan_concept     -- try to retrieve scanned concept (skip parsing)
      okf_fetch_resolve_links    -- try to retrieve resolved links (skip parsing)

* **Per-item observation hooks** use ``enter`` / ``exit`` as the verb. These are
  void hooks called symmetrically for observation, logging, or metrics. They
  always fire regardless of whether the result was fetched or parsed::

      okf_enter_scan_concept     -- before concept processing starts
      okf_exit_scan_concept      -- after concept processing ends

      okf_enter_resolve_links    -- before link resolution starts
      okf_exit_resolve_links     -- after link resolution ends

Plugin authors implementing ``@hookimpl`` methods must use the exact names above.
"""

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
    """Hook specifications for OKF operations.

    See the module docstring for the hook naming convention.
    """

    @hookspec
    def okf_start_scan(
        self,
        bundle: BundleConfig,
    ) -> None:
        """Invoked at the beginning of a bundle scan, allowing plugins to open transactions/resources."""

    @hookspec
    def okf_enter_scan_concept(
        self,
        path: Path,
        root: Path,
        bundle: BundleConfig,
    ) -> None:
        """Invoked before processing a concept document. Always fires."""

    @hookspec(firstresult=True)
    def okf_fetch_scan_concept(
        self,
        path: Path,
        root: Path,
        bundle: BundleConfig,
    ) -> ConceptManifestEntry | None:
        """Invoked to substitute a scanned concept entry (e.g. from a cache).

        If a registered plugin returns a ``ConceptManifestEntry``, the scanner
        reuses it and skips reading and parsing the file.
        """

    @hookspec
    def okf_exit_scan_concept(
        self,
        entry: ConceptManifestEntry,
        path: Path,
        root: Path,
        bundle: BundleConfig,
    ) -> None:
        """Invoked after a concept document is successfully processed.

        Only called when a concept is successfully scanned/parsed or retrieved
        from cache (skipped on read, decode, or parse failures). Allows plugins
        to cache, record, or process the scanned entry.
        """

    @hookspec
    def okf_enter_resolve_links(
        self,
        entry: ConceptManifestEntry,
        bundle: BundleConfig,
    ) -> None:
        """Invoked before resolving links from a concept document. Always fires."""

    @hookspec(firstresult=True)
    def okf_fetch_resolve_links(
        self,
        entry: ConceptManifestEntry,
        bundle: BundleConfig,
    ) -> Sequence[ConceptLink] | None:
        """Invoked to substitute resolved links (e.g. from a cache).

        If a registered plugin returns a sequence of ``ConceptLink`` records,
        the graph builder reuses them directly and skips parsing the body.
        """

    @hookspec
    def okf_exit_resolve_links(
        self,
        entry: ConceptManifestEntry,
        links: Sequence[ConceptLink],
        bundle: BundleConfig,
    ) -> None:
        """Invoked after resolving links from a concept document.

        Only called when links are successfully extracted or retrieved from cache
        (skipped on read, decode, or parse failures). Allows plugins to cache or
        process the resolved links.
        """

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

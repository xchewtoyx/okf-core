from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import (
    BundleConfig,
    DocumentChangePlanningError,
    plan_graph_repair,
    repair_graph,
)
from okf_core.hooks import hookimpl


def _bundle(root: Path) -> BundleConfig:
    return BundleConfig(
        name="test",
        bundle_root=root,
        include=("**/*.md",),
        exclude=(),
        reserved_filenames=("index.md", "log.md"),
        concept_path_strategy="relative-path",
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _ResolvingPlugin:
    """Test-only plugin resolving a fixed dead_concept_id -> Path mapping."""

    def __init__(self, mapping: dict[str, Path]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    @hookimpl
    def okf_fetch_moved_concept_path(
        self, dead_concept_id: str, bundle: BundleConfig
    ) -> Path | None:
        self.calls.append(dead_concept_id)
        return self._mapping.get(dead_concept_id)


def _register_plugin(monkeypatch: pytest.MonkeyPatch, plugin: object) -> None:
    import okf_core.hooks as hooks_module

    original_get_hook_manager = hooks_module.get_hook_manager

    def patched(bundle: BundleConfig):  # type: ignore[no-untyped-def]
        pm = original_get_hook_manager(bundle)
        pm.register(plugin)
        return pm

    monkeypatch.setattr(hooks_module, "get_hook_manager", patched)


def test_plan_graph_repair_reports_broken_link_as_no_plugin_registered(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "a.md", "See [dead](dead.md).\n")

    prep = plan_graph_repair(_bundle(tmp_path))

    assert prep.resolved_links == ()
    assert len(prep.unresolved_links) == 1
    assert prep.unresolved_links[0].reason == "no-plugin-registered"
    assert prep.unresolved_links[0].link.target == "dead.md"


def test_plan_graph_repair_reports_out_of_root_link_as_not_concept_shaped(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _write(root / "a.md", "See [outside](../outside.md).\n")

    prep = plan_graph_repair(_bundle(root))

    assert prep.resolved_links == ()
    assert len(prep.unresolved_links) == 1
    assert prep.unresolved_links[0].reason == "not-concept-shaped"
    assert prep.unresolved_links[0].link.target == "../outside.md"


def test_plan_graph_repair_reports_hook_returning_none_as_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.md", "See [dead](dead.md).\n")

    plugin = _ResolvingPlugin({})  # registered, but resolves nothing
    _register_plugin(monkeypatch, plugin)

    prep = plan_graph_repair(_bundle(tmp_path))

    assert prep.resolved_links == ()
    assert len(prep.unresolved_links) == 1
    assert prep.unresolved_links[0].reason == "unresolved"
    assert prep.unresolved_links[0].link.target == "dead.md"


def test_plan_graph_repair_downgrades_only_the_link_with_an_out_of_root_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "docs"
    _write(root / "a.md", "See [dead](/dead.md) and [dead2](dead2.md).\n")
    new2 = root / "new2.md"
    _write(new2, "Body.\n")
    # A misbehaving plugin resolves "dead" to a path outside the bundle
    # root -- _validate_hook_path rejects it via path_to_concept_id's
    # containment check before it ever reaches link_target_for_new_location.
    outside = tmp_path / "outside.md"
    _write(outside, "Body.\n")

    plugin = _ResolvingPlugin({"dead": outside, "dead2": new2})
    _register_plugin(monkeypatch, plugin)

    prep = plan_graph_repair(_bundle(root))

    assert len(prep.resolved_links) == 1
    assert prep.resolved_links[0].target == "dead2.md"

    assert len(prep.unresolved_links) == 1
    unresolved = prep.unresolved_links[0]
    assert unresolved.link.target == "/dead.md"
    assert "invalid-resolved-path" in unresolved.reason

    # The run must not raise despite the plugin's bad answer.
    result = repair_graph(_bundle(root))
    assert result.updated_files == (root / "a.md",)
    assert (root / "a.md").read_text(encoding="utf-8") == (
        "See [dead](/dead.md) and [dead2](new2.md).\n"
    )


def test_plan_graph_repair_resolves_link_via_registered_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.md", "See [dead](dead.md).\n")
    new = tmp_path / "new.md"
    _write(new, "Body.\n")

    plugin = _ResolvingPlugin({"dead": new})
    _register_plugin(monkeypatch, plugin)

    prep = plan_graph_repair(_bundle(tmp_path))

    assert prep.unresolved_links == ()
    assert len(prep.resolved_links) == 1
    assert prep.resolved_links[0].source_path == tmp_path / "a.md"
    assert (tmp_path / "a.md") in prep.link_rewrite_plans


def test_plan_graph_repair_preserves_fragment_and_rebases_across_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "sub1" / "a.md", "See [dead](../dead.md#section?x=1).\n")
    new = tmp_path / "sub2" / "new.md"
    _write(new, "Body.\n")

    plugin = _ResolvingPlugin({"dead": new})
    _register_plugin(monkeypatch, plugin)

    repair_graph(_bundle(tmp_path))

    assert (tmp_path / "sub1" / "a.md").read_text(encoding="utf-8") == (
        "See [dead](../sub2/new.md#section?x=1).\n"
    )


def test_plan_graph_repair_resolves_relative_hook_path_against_bundle_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "sub1" / "a.md", "See [dead](../dead.md).\n")
    new = tmp_path / "sub2" / "new.md"
    _write(new, "Body.\n")
    # Run from an unrelated cwd so a naive os.path.relpath/Path join against
    # cwd (rather than bundle.bundle_root) would resolve to the wrong place.
    unrelated_cwd = tmp_path.parent
    monkeypatch.chdir(unrelated_cwd)

    # The plugin returns a bundle-root-relative Path rather than an absolute
    # one (unlike concept_id_to_path(), which always returns absolute paths)
    # to exercise the non-absolute normalization branch specifically.
    plugin = _ResolvingPlugin({"dead": Path("sub2/new.md")})
    _register_plugin(monkeypatch, plugin)

    repair_graph(_bundle(tmp_path))

    assert (tmp_path / "sub1" / "a.md").read_text(encoding="utf-8") == (
        "See [dead](../sub2/new.md).\n"
    )


def test_plan_graph_repair_dedupes_hook_calls_by_dead_concept_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.md", "See [dead](dead.md).\n")
    _write(tmp_path / "b.md", "Also [dead](dead.md).\n")
    new = tmp_path / "new.md"
    _write(new, "Body.\n")

    plugin = _ResolvingPlugin({"dead": new})
    _register_plugin(monkeypatch, plugin)

    prep = plan_graph_repair(_bundle(tmp_path))

    assert plugin.calls == ["dead"]
    assert len(prep.resolved_links) == 2


def test_plan_graph_repair_dedupes_multiple_occurrences_in_one_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.md", "First [x](dead.md) and second [y](dead.md).\n")
    new = tmp_path / "new.md"
    _write(new, "Body.\n")

    plugin = _ResolvingPlugin({"dead": new})
    _register_plugin(monkeypatch, plugin)

    prep = plan_graph_repair(_bundle(tmp_path))
    # Both occurrences share one LinkRewrite (there's only one distinct href
    # to rewrite), but each is still a separate broken link and must be
    # separately accounted for, not overwritten in the reported count.
    assert len(prep.resolved_links) == 2

    result = repair_graph(_bundle(tmp_path))

    assert result.updated_files == (tmp_path / "a.md",)
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == (
        "First [x](new.md) and second [y](new.md).\n"
    )
    assert len(result.resolved_links) == 2


def test_plan_graph_repair_downgrades_every_occurrence_on_planning_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path / "a.md",
        "First [x](dead.md) and second [y](dead.md).\n\n[ref]: something.md\n",
    )
    new = tmp_path / "new.md"
    _write(new, "Body.\n")

    plugin = _ResolvingPlugin({"dead": new})
    _register_plugin(monkeypatch, plugin)

    prep = plan_graph_repair(_bundle(tmp_path))

    assert prep.resolved_links == ()
    assert len(prep.unresolved_links) == 2
    assert all("planning-failed" in u.reason for u in prep.unresolved_links)


def test_plan_graph_repair_reports_missing_resolved_file_as_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.md", "See [dead](dead.md) and [dead2](dead2.md).\n")
    new2 = tmp_path / "new2.md"
    _write(new2, "Body.\n")
    # A misbehaving (or stale-cache) plugin resolves "dead" to a path that's
    # a valid bundle-root-contained .md location but doesn't actually exist.
    missing = tmp_path / "does-not-exist.md"

    plugin = _ResolvingPlugin({"dead": missing, "dead2": new2})
    _register_plugin(monkeypatch, plugin)

    prep = plan_graph_repair(_bundle(tmp_path))

    assert len(prep.resolved_links) == 1
    assert prep.resolved_links[0].target == "dead2.md"

    assert len(prep.unresolved_links) == 1
    unresolved = prep.unresolved_links[0]
    assert unresolved.link.target == "dead.md"
    assert "invalid-resolved-path" in unresolved.reason
    assert "does not exist" in unresolved.reason

    result = repair_graph(_bundle(tmp_path))
    assert result.updated_files == (tmp_path / "a.md",)
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == (
        "See [dead](dead.md) and [dead2](new2.md).\n"
    )


def test_plan_graph_repair_downgrades_only_the_file_with_a_planning_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # File A has a resolvable broken link AND an unrelated reference-style
    # link definition -- plan_markdown_link_rewrite raises for the whole
    # file, so its link(s) must be downgraded to unresolved, not abort the
    # run.
    _write(tmp_path / "a.md", "See [dead](dead.md).\n\n[ref]: something.md\n")
    # File B has a different resolvable broken link, no reference-style
    # links -- it must still be repaired.
    _write(tmp_path / "b.md", "Also [dead2](dead2.md).\n")
    new1 = tmp_path / "new1.md"
    _write(new1, "Body.\n")
    new2 = tmp_path / "new2.md"
    _write(new2, "Body.\n")

    plugin = _ResolvingPlugin({"dead": new1, "dead2": new2})
    _register_plugin(monkeypatch, plugin)

    prep = plan_graph_repair(_bundle(tmp_path))

    assert len(prep.resolved_links) == 1
    assert prep.resolved_links[0].source_path == tmp_path / "b.md"

    assert len(prep.unresolved_links) == 1
    unresolved = prep.unresolved_links[0]
    assert unresolved.link.source_path == tmp_path / "a.md"
    assert "planning-failed" in unresolved.reason

    result = repair_graph(_bundle(tmp_path))
    assert result.updated_files == (tmp_path / "b.md",)
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "Also [dead2](new2.md).\n"
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == (
        "See [dead](dead.md).\n\n[ref]: something.md\n"
    )


def test_plan_graph_repair_raises_on_scan_failure(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "See [dead](dead.md).\n")
    _write(tmp_path / "bad.md", "---\ntype: concept\n")

    with pytest.raises(DocumentChangePlanningError):
        plan_graph_repair(_bundle(tmp_path))

    with pytest.raises(DocumentChangePlanningError):
        repair_graph(_bundle(tmp_path))


def test_repair_graph_applies_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.md", "See [dead](dead.md).\n")
    new = tmp_path / "new.md"
    _write(new, "Body.\n")

    plugin = _ResolvingPlugin({"dead": new})
    _register_plugin(monkeypatch, plugin)

    result = repair_graph(_bundle(tmp_path))

    assert result.updated_files == (tmp_path / "a.md",)
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "See [dead](new.md).\n"
    assert len(result.resolved_links) == 1
    assert result.unresolved_links == ()

    # Second run: the link now resolves to a real concept, so it's no
    # longer broken at all -- nothing left for graph.broken_links to report.
    result2 = repair_graph(_bundle(tmp_path))
    assert result2.updated_files == ()
    assert result2.resolved_links == ()
    assert result2.unresolved_links == ()

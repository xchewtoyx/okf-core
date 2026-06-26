from __future__ import annotations
from typing import Any

from pathlib import Path
import re

import pytest

from okf_core import ConfigError, ConfigOverrides, discover_config, load_config
from okf_core import is_supported_okf_version


def test_absent_config_uses_built_in_defaults(tmp_path: Path) -> None:
    config = load_config(project_root=tmp_path)

    assert config.project_root == tmp_path
    assert config.config_path is None
    assert config.defaults.bundle_root == tmp_path
    assert config.defaults.include == ("**/*.md",)
    assert config.defaults.exclude == ()
    assert config.defaults.reserved_filenames == ("index.md", "log.md")
    assert config.defaults.concept_path_strategy == "relative-path"
    assert config.defaults.index_cache == tmp_path / ".okf-cache"
    assert config.defaults.directory_metadata_file == "_directory.yml"
    assert config.defaults.okf_version is None
    assert config.bundles["default"].okf_version is None
    assert config.bundles["default"].bundle_root == tmp_path
    assert config.bundles["default"].directory_metadata_file == "_directory.yml"


def test_absent_config_with_existing_file_project_root_uses_parent(
    tmp_path: Path,
) -> None:
    start_file = tmp_path / "script.py"
    start_file.write_text("", encoding="utf-8")

    config = load_config(project_root=start_file)

    assert config.project_root == tmp_path
    assert config.config_path is None
    assert config.defaults.bundle_root == tmp_path
    assert config.defaults.index_cache == tmp_path / ".okf-cache"


def test_absent_config_with_nonexistent_project_root_uses_requested_path(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "future-project"

    config = load_config(project_root=project_root)

    assert config.project_root == project_root
    assert config.config_path is None
    assert config.defaults.bundle_root == project_root
    assert config.defaults.index_cache == project_root / ".okf-cache"


def test_explicit_config_path_loads_file(tmp_path: Path) -> None:
    config_path = tmp_path / "custom.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "knowledge"
index_cache = ".cache/okf"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.config_path == config_path
    assert config.project_root == tmp_path
    assert config.defaults.bundle_root == tmp_path / "knowledge"
    assert config.defaults.index_cache == tmp_path / ".cache" / "okf"


def test_missing_explicit_config_path_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file does not exist"):
        load_config(config_path=tmp_path / "missing.toml")


def test_config_read_errors_raise_config_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text("[defaults]\n", encoding="utf-8")
    original_open = Path.open

    def fail_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == config_path:
            raise OSError("read failed")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)

    with pytest.raises(ConfigError, match="Could not read config file"):
        load_config(config_path=config_path)


def test_discovers_config_upward_from_start_path(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    config_path.write_text("[defaults]\nbundle_root = 'docs'\n", encoding="utf-8")

    assert discover_config(nested) == config_path
    config = load_config(project_root=nested)

    assert config.config_path == config_path
    assert config.project_root == tmp_path
    assert config.defaults.bundle_root == tmp_path / "docs"


@pytest.mark.parametrize(
    ("start_kind", "relative_start"),
    [
        ("root directory", "."),
        ("nested directory", "a/b"),
        ("nested file", "a/b/concept.md"),
    ],
)
def test_discover_config_start_path_contract(
    tmp_path: Path,
    start_kind: str,
    relative_start: str,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text("[defaults]\n", encoding="utf-8")
    start_path = tmp_path / relative_start
    if start_path.suffix:
        start_path.parent.mkdir(parents=True, exist_ok=True)
        start_path.write_text("", encoding="utf-8")
    else:
        start_path.mkdir(parents=True, exist_ok=True)

    assert discover_config(start_path) == config_path, start_kind


def test_discover_config_returns_none_without_config(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert discover_config(nested) is None


def test_discover_config_expands_tilde_start_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    nested = home / "project" / "nested"
    nested.mkdir(parents=True)
    config_path = home / "project" / "okf-core.toml"
    config_path.write_text("[defaults]\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    assert discover_config("~/project/nested") == config_path


def test_multiple_named_bundles_have_separate_roots(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[bundles.docs]
bundle_root = "docs"

[bundles.notes]
bundle_root = "notes"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.bundles["docs"].bundle_root == tmp_path / "docs"
    assert config.bundles["notes"].bundle_root == tmp_path / "notes"


def test_custom_include_exclude_globs(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
include = ["concepts/**/*.md"]
exclude = ["**/drafts/**", "**/.*/**"]
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.defaults.include == ("concepts/**/*.md",)
    assert config.defaults.exclude == ("**/drafts/**", "**/.*/**")


def test_reserved_filename_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
reserved_filenames = ["README.md", "index.md"]
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.defaults.reserved_filenames == ("README.md", "index.md")


def test_taxonomy_and_profile_rule_parsing(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[taxonomy]
known_types = ["concept", "decision"]
allowed_types = ["concept"]

[profiles.strict]
required_frontmatter = ["type", "title"]
optional_frontmatter = ["status"]

[profiles.strict.taxonomy]
allowed_types = ["decision"]
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.taxonomy.known_types == ("concept", "decision")
    assert config.taxonomy.allowed_types == ("concept",)
    assert config.profiles["strict"].required_frontmatter == ("type", "title")
    assert config.profiles["strict"].optional_frontmatter == ("status",)
    assert config.profiles["strict"].taxonomy.allowed_types == ("decision",)


def test_bundle_level_profile_references_and_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "docs"
include = ["**/*.md"]
exclude = ["**/tmp/**"]

[profiles.strict]
required_frontmatter = ["type"]

[bundles.product]
bundle_root = "product"             # Overridden
profile = "strict"                  # Referenced profile
include = ["topics/**/*.md"]        # Overridden (relative to bundle_root)
reserved_filenames = ["home.md"]    # Overridden
concept_path_strategy = "slug"      # Overridden
index_cache = ".cache/product"      # Overridden
listing_fields = ["activity"]       # Overridden
# Note: exclude is inherited from [defaults]
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]

    assert bundle.bundle_root == tmp_path / "product"
    assert bundle.profile == "strict"
    assert bundle.include == ("topics/**/*.md",)
    assert bundle.exclude == ("**/tmp/**",)
    assert bundle.reserved_filenames == ("home.md",)
    assert bundle.concept_path_strategy == "slug"
    assert bundle.index_cache == tmp_path / ".cache" / "product"
    assert bundle.listing_fields == ("activity",)


def test_listing_fields_inherit_from_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
listing_fields = ["activity", "owner"]

[bundles.docs]
bundle_root = "docs"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.defaults.listing_fields == ("activity", "owner")
    assert config.bundles["docs"].listing_fields == ("activity", "owner")


def test_okf_version_inherits_from_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
okf_version = "0.1"

[bundles.docs]
bundle_root = "docs"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.defaults.okf_version == "0.1"
    assert config.bundles["docs"].okf_version == "0.1"


def test_bundle_okf_version_overrides_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
okf_version = "0.1"

[bundles.docs]
bundle_root = "docs"
okf_version = "0.0"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.bundles["docs"].okf_version == "0.0"


@pytest.mark.parametrize("version", ["1", "0.1.0", "v0.1", "0.01"])
def test_invalid_okf_version_format_raises_config_error(
    tmp_path: Path, version: str
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        f'[defaults]\nokf_version = "{version}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="OKF version must use"):
        load_config(config_path=config_path)


def test_unsupported_configured_okf_version_raises_config_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text('[defaults]\nokf_version = "0.2"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="newer than supported"):
        load_config(config_path=config_path)


def test_is_supported_okf_version_returns_false_for_invalid_input() -> None:
    assert not is_supported_okf_version("not-a-version")


def test_explicit_empty_bundle_values_are_honored_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
include = ["**/*.md"]
reserved_filenames = ["index.md"]
listing_fields = ["activity"]

[bundles.docs]
include = []
exclude = []
reserved_filenames = []
listing_fields = []
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)
    bundle = config.bundles["docs"]

    assert bundle.include == ()
    assert bundle.exclude == ()
    assert bundle.reserved_filenames == ()
    assert bundle.listing_fields == ()


@pytest.mark.parametrize(
    "toml",
    [
        "unexpected = true",
        "[unknown]\nvalue = true",
        "[defaults]\nunexpected = true",
        "[profiles.strict]\nunexpected = true",
        "[bundles.docs]\nunexpected = true",
    ],
)
def test_unknown_keys_fail_closed(tmp_path: Path, toml: str) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(toml, encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid OKF configuration"):
        load_config(config_path=config_path)


def test_validation_error_includes_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text("[defaults]\nunexpected = true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=re.escape(str(config_path))) as exc_info:
        load_config(config_path=config_path)

    assert str(exc_info.value).count("Invalid OKF configuration") == 1


def test_invalid_override_error_uses_config_error() -> None:
    with pytest.raises(ConfigError, match="Invalid OKF configuration"):
        load_config(overrides={"unexpected": True})


def test_python_overrides_take_precedence_over_file_values(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "from-file"
include = ["file/**/*.md"]
exclude = ["file-exclude/**"]
reserved_filenames = ["file.md"]
concept_path_strategy = "file-strategy"
index_cache = ".file-cache"
listing_fields = ["file"]
okf_version = "0.1"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(
        config_path=config_path,
        overrides=ConfigOverrides(
            bundle_root=Path("from-api"),
            include=("api/**/*.md",),
            exclude=("api-exclude/**",),
            reserved_filenames=("api.md",),
            concept_path_strategy="api-strategy",
            index_cache=Path(".api-cache"),
            listing_fields=("api",),
            okf_version="0.0",
        ),
    )

    assert config.defaults.bundle_root == tmp_path / "from-api"
    assert config.defaults.include == ("api/**/*.md",)
    assert config.defaults.exclude == ("api-exclude/**",)
    assert config.defaults.reserved_filenames == ("api.md",)
    assert config.defaults.concept_path_strategy == "api-strategy"
    assert config.defaults.index_cache == tmp_path / ".api-cache"
    assert config.defaults.listing_fields == ("api",)
    assert config.defaults.okf_version == "0.0"
    assert config.bundles["default"].bundle_root == tmp_path / "from-api"


def test_python_overrides_take_precedence_over_declared_bundle_values(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "defaults"

[bundles.docs]
bundle_root = "from-file"
include = ["file/**/*.md"]
exclude = ["file-exclude/**"]
reserved_filenames = ["file.md"]
concept_path_strategy = "file-strategy"
index_cache = ".file-cache"
listing_fields = ["file"]
okf_version = "0.1"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(
        config_path=config_path,
        overrides={
            "bundle_root": "from-api",
            "include": ["api/**/*.md"],
            "exclude": ["api-exclude/**"],
            "reserved_filenames": ["api.md"],
            "concept_path_strategy": "api-strategy",
            "index_cache": ".api-cache",
            "listing_fields": ["api"],
            "okf_version": "0.0",
        },
    )

    bundle = config.bundles["docs"]

    assert bundle.bundle_root == tmp_path / "from-api"
    assert bundle.include == ("api/**/*.md",)
    assert bundle.exclude == ("api-exclude/**",)
    assert bundle.reserved_filenames == ("api.md",)
    assert bundle.concept_path_strategy == "api-strategy"
    assert bundle.index_cache == tmp_path / ".api-cache"
    assert bundle.listing_fields == ("api",)
    assert bundle.okf_version == "0.0"


def test_explicit_empty_bundle_values_are_honored_from_python_overrides(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
include = ["**/*.md"]
reserved_filenames = ["index.md"]
listing_fields = ["activity"]

[bundles.docs]
include = ["file/**/*.md"]
reserved_filenames = ["file.md"]
listing_fields = ["owner"]
""".strip(),
        encoding="utf-8",
    )

    config = load_config(
        config_path=config_path,
        overrides={
            "include": [],
            "exclude": [],
            "reserved_filenames": [],
            "listing_fields": [],
        },
    )
    bundle = config.bundles["docs"]

    assert bundle.include == ()
    assert bundle.exclude == ()
    assert bundle.reserved_filenames == ()
    assert bundle.listing_fields == ()


def test_missing_profile_reference_raises_config_error(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_root = "."

[bundles.docs]
profile = "nonexistent"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigError,
        match="bundle 'docs' references profile 'nonexistent' which does not exist",
    ):
        load_config(config_path=config_path)


def test_directory_metadata_file_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
directory_metadata_file = "custom-meta.yml"

[bundles.docs]
bundle_root = "docs"

[bundles.custom]
bundle_root = "custom"
directory_metadata_file = "special-meta.yaml"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.defaults.directory_metadata_file == "custom-meta.yml"
    assert config.bundles["docs"].directory_metadata_file == "custom-meta.yml"
    assert config.bundles["custom"].directory_metadata_file == "special-meta.yaml"


def test_directory_metadata_file_python_overrides(tmp_path: Path) -> None:
    config = load_config(
        project_root=tmp_path,
        overrides=ConfigOverrides(directory_metadata_file="override-meta.yml"),
    )
    assert config.defaults.directory_metadata_file == "override-meta.yml"
    assert config.bundles["default"].directory_metadata_file == "override-meta.yml"


def test_directory_metadata_file_validation_rejects_paths(tmp_path: Path) -> None:
    # 1. Defaults path validation
    config_path1 = tmp_path / "okf-core1.toml"
    config_path1.write_text(
        "[defaults]\ndirectory_metadata_file = 'sub/meta.yml'\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must be a simple filename"):
        load_config(config_path=config_path1)

    # 2. Bundle path validation
    config_path2 = tmp_path / "okf-core2.toml"
    config_path2.write_text(
        "[defaults]\n[bundles.docs]\nbundle_root = 'docs'\ndirectory_metadata_file = 'sub/meta.yml'\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must be a simple filename"):
        load_config(config_path=config_path2)


def test_okf_cache_dir_configuration(tmp_path: Path) -> None:
    # 1. Verify default is None
    config = load_config(project_root=tmp_path)
    assert not hasattr(config.defaults, "okf_cache_dir")
    assert config.bundles["default"].okf_cache_dir is None

    # 2. Verify setting defaults.okf_cache_dir raises ConfigError (not supported)
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        "[defaults]\nokf_cache_dir = '.okf-cache'\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(config_path=config_path)

    # 3. Verify bundle-level caching and relative path resolution
    config_path.write_text(
        "[bundles.b1]\nbundle_root = 'b1'\nokf_cache_dir = '.custom-cache'\n",
        encoding="utf-8",
    )
    config = load_config(config_path=config_path)
    assert config.bundles["b1"].okf_cache_dir == tmp_path / "b1" / ".custom-cache"

    # 4. Verify overrides parameter resolves relative to bundle root
    config = load_config(
        config_path=config_path, overrides={"okf_cache_dir": ".api-cache"}
    )
    assert config.bundles["b1"].okf_cache_dir == tmp_path / "b1" / ".api-cache"

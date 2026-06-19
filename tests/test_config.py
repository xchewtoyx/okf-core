from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import ConfigError, ConfigOverrides, discover_config, load_config


def test_absent_config_uses_built_in_defaults(tmp_path: Path) -> None:
    config = load_config(project_root=tmp_path)

    assert config.project_root == tmp_path
    assert config.config_path is None
    assert config.defaults.bundle_roots == (tmp_path,)
    assert config.defaults.include == ("**/*.md",)
    assert config.defaults.exclude == ()
    assert config.defaults.reserved_filenames == ("index.md", "log.md")
    assert config.defaults.concept_path_strategy == "relative-path"
    assert config.defaults.index_cache == tmp_path / ".okf-cache"
    assert config.bundles["default"].bundle_roots == (tmp_path,)


def test_explicit_config_path_loads_file(tmp_path: Path) -> None:
    config_path = tmp_path / "custom.toml"
    config_path.write_text(
        """
[defaults]
bundle_roots = ["knowledge"]
index_cache = ".cache/okf"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.config_path == config_path
    assert config.project_root == tmp_path
    assert config.defaults.bundle_roots == (tmp_path / "knowledge",)
    assert config.defaults.index_cache == tmp_path / ".cache" / "okf"


def test_missing_explicit_config_path_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file does not exist"):
        load_config(config_path=tmp_path / "missing.toml")


def test_discovers_config_upward_from_start_path(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    config_path.write_text("[defaults]\nbundle_roots = ['docs']\n", encoding="utf-8")

    assert discover_config(nested) == config_path
    assert load_config(project_root=nested).config_path == config_path


def test_multiple_bundle_roots(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_roots = ["docs", "notes"]
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)

    assert config.defaults.bundle_roots == (tmp_path / "docs", tmp_path / "notes")
    assert config.bundles["default"].bundle_roots == (
        tmp_path / "docs",
        tmp_path / "notes",
    )


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
bundle_roots = ["docs"]
include = ["**/*.md"]
exclude = ["**/tmp/**"]

[profiles.strict]
required_frontmatter = ["type"]

[bundles.product]
bundle_roots = ["product"]
profile = "strict"
include = ["product/**/*.md"]
reserved_filenames = ["home.md"]
concept_path_strategy = "slug"
index_cache = ".cache/product"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)
    bundle = config.bundles["product"]

    assert bundle.bundle_roots == (tmp_path / "product",)
    assert bundle.profile == "strict"
    assert bundle.include == ("product/**/*.md",)
    assert bundle.exclude == ("**/tmp/**",)
    assert bundle.reserved_filenames == ("home.md",)
    assert bundle.concept_path_strategy == "slug"
    assert bundle.index_cache == tmp_path / ".cache" / "product"


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


def test_python_overrides_take_precedence_over_file_values(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_roots = ["from-file"]
include = ["file/**/*.md"]
exclude = ["file-exclude/**"]
reserved_filenames = ["file.md"]
concept_path_strategy = "file-strategy"
index_cache = ".file-cache"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(
        config_path=config_path,
        overrides=ConfigOverrides(
            bundle_roots=(Path("from-api"),),
            include=("api/**/*.md",),
            exclude=("api-exclude/**",),
            reserved_filenames=("api.md",),
            concept_path_strategy="api-strategy",
            index_cache=Path(".api-cache"),
        ),
    )

    assert config.defaults.bundle_roots == (tmp_path / "from-api",)
    assert config.defaults.include == ("api/**/*.md",)
    assert config.defaults.exclude == ("api-exclude/**",)
    assert config.defaults.reserved_filenames == ("api.md",)
    assert config.defaults.concept_path_strategy == "api-strategy"
    assert config.defaults.index_cache == tmp_path / ".api-cache"
    assert config.bundles["default"].bundle_roots == (tmp_path / "from-api",)


def test_python_overrides_take_precedence_over_declared_bundle_values(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
bundle_roots = ["defaults"]

[bundles.docs]
bundle_roots = ["from-file"]
include = ["file/**/*.md"]
exclude = ["file-exclude/**"]
reserved_filenames = ["file.md"]
concept_path_strategy = "file-strategy"
index_cache = ".file-cache"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(
        config_path=config_path,
        overrides={
            "bundle_roots": ["from-api"],
            "include": ["api/**/*.md"],
            "exclude": ["api-exclude/**"],
            "reserved_filenames": ["api.md"],
            "concept_path_strategy": "api-strategy",
            "index_cache": ".api-cache",
        },
    )

    bundle = config.bundles["docs"]

    assert bundle.bundle_roots == (tmp_path / "from-api",)
    assert bundle.include == ("api/**/*.md",)
    assert bundle.exclude == ("api-exclude/**",)
    assert bundle.reserved_filenames == ("api.md",)
    assert bundle.concept_path_strategy == "api-strategy"
    assert bundle.index_cache == tmp_path / ".api-cache"

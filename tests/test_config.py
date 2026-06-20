from __future__ import annotations

from pathlib import Path

import pytest

from okf_core import ConfigError, ConfigOverrides, discover_config, load_config


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
    assert config.bundles["default"].bundle_root == tmp_path


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

    def fail_open(path: Path, *args: object, **kwargs: object) -> object:
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


def test_explicit_empty_bundle_values_are_honored_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
include = ["**/*.md"]
reserved_filenames = ["index.md"]

[bundles.docs]
include = []
exclude = []
reserved_filenames = []
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path)
    bundle = config.bundles["docs"]

    assert bundle.include == ()
    assert bundle.exclude == ()
    assert bundle.reserved_filenames == ()


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

    with pytest.raises(ConfigError, match=str(config_path)) as exc_info:
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
        ),
    )

    assert config.defaults.bundle_root == tmp_path / "from-api"
    assert config.defaults.include == ("api/**/*.md",)
    assert config.defaults.exclude == ("api-exclude/**",)
    assert config.defaults.reserved_filenames == ("api.md",)
    assert config.defaults.concept_path_strategy == "api-strategy"
    assert config.defaults.index_cache == tmp_path / ".api-cache"
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
        },
    )

    bundle = config.bundles["docs"]

    assert bundle.bundle_root == tmp_path / "from-api"
    assert bundle.include == ("api/**/*.md",)
    assert bundle.exclude == ("api-exclude/**",)
    assert bundle.reserved_filenames == ("api.md",)
    assert bundle.concept_path_strategy == "api-strategy"
    assert bundle.index_cache == tmp_path / ".api-cache"


def test_explicit_empty_bundle_values_are_honored_from_python_overrides(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "okf-core.toml"
    config_path.write_text(
        """
[defaults]
include = ["**/*.md"]
reserved_filenames = ["index.md"]

[bundles.docs]
include = ["file/**/*.md"]
reserved_filenames = ["file.md"]
""".strip(),
        encoding="utf-8",
    )

    config = load_config(
        config_path=config_path,
        overrides={"include": [], "exclude": [], "reserved_filenames": []},
    )
    bundle = config.bundles["docs"]

    assert bundle.include == ()
    assert bundle.exclude == ()
    assert bundle.reserved_filenames == ()

"""Project configuration loading for OKF repositories."""

from __future__ import annotations

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from okf_core.versions import OkfVersionError, validate_supported_okf_version

CONFIG_FILENAME = "okf-core.toml"


class ConfigError(Exception):
    """Raised when OKF configuration cannot be loaded or validated."""


class TaxonomyConfig(BaseModel):
    """Taxonomy hints for OKF concept documents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    known_types: tuple[str, ...] = ()
    allowed_types: tuple[str, ...] = ()


class ProfileConfig(BaseModel):
    """Optional local validation profile settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_frontmatter: tuple[str, ...] = ()
    optional_frontmatter: tuple[str, ...] = ()
    taxonomy: TaxonomyConfig = Field(default_factory=TaxonomyConfig)


class ProjectDefaults(BaseModel):
    """Project-wide defaults inherited by bundle configs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_root: Path = Path(".")
    include: tuple[str, ...] = ("**/*.md",)
    exclude: tuple[str, ...] = ()
    reserved_filenames: tuple[str, ...] = ("index.md", "log.md")
    concept_path_strategy: str = "relative-path"
    index_cache: Path = Path(".okf-cache")
    listing_fields: tuple[str, ...] = ()
    directory_metadata_file: str = "_directory.yml"
    okf_version: str | None = None

    @field_validator("okf_version")
    @classmethod
    def _validate_okf_version(cls, v: str | None) -> str | None:
        return _validate_optional_okf_version(v)

    @field_validator("directory_metadata_file")
    @classmethod
    def _validate_metadata_file(cls, v: str) -> str:
        res = _validate_metadata_filename(v)
        assert res is not None
        return res


class BundleConfig(BaseModel):
    """Resolved configuration for one OKF bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    bundle_root: Path
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    reserved_filenames: tuple[str, ...]
    concept_path_strategy: str
    index_cache: Path
    listing_fields: tuple[str, ...] = ()
    okf_version: str | None = None
    profile: str | None = None
    directory_metadata_file: str = "_directory.yml"
    okf_cache_dir: Path | None = None

    @field_validator("bundle_root", "index_cache", mode="after")
    @classmethod
    def _normalize_paths(cls, v: Path) -> Path:
        return v.expanduser().resolve(strict=False)

    @field_validator("okf_version")
    @classmethod
    def _validate_okf_version(cls, v: str | None) -> str | None:
        return _validate_optional_okf_version(v)

    @field_validator("directory_metadata_file")
    @classmethod
    def _validate_metadata_file(cls, v: str) -> str:
        res = _validate_metadata_filename(v)
        assert res is not None
        return res

    @field_validator("okf_cache_dir", mode="after")
    @classmethod
    def _normalize_optional_path(cls, v: Path | None) -> Path | None:
        if v is None:
            return None
        return v.expanduser().resolve(strict=False)


class OkfConfig(BaseModel):
    """Resolved OKF project configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_root: Path
    config_path: Path | None = None
    defaults: ProjectDefaults
    taxonomy: TaxonomyConfig = Field(default_factory=TaxonomyConfig)
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    bundles: dict[str, BundleConfig]

    @model_validator(mode="after")
    def _validate_profile_references(self) -> OkfConfig:
        for name, bundle in self.bundles.items():
            if bundle.profile is not None and bundle.profile not in self.profiles:
                raise ValueError(
                    f"bundle '{name}' references profile '{bundle.profile}' which does not exist"
                )
        return self


class ConfigOverrides(BaseModel):
    """Explicit Python API overrides applied after file and bundle settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_root: Path | None = None
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None
    reserved_filenames: tuple[str, ...] | None = None
    concept_path_strategy: str | None = None
    index_cache: Path | None = None
    listing_fields: tuple[str, ...] | None = None
    directory_metadata_file: str | None = None
    okf_version: str | None = None
    okf_cache_dir: Path | None = None

    @field_validator("okf_version")
    @classmethod
    def _validate_okf_version(cls, v: str | None) -> str | None:
        return _validate_optional_okf_version(v)

    @field_validator("directory_metadata_file")
    @classmethod
    def _validate_metadata_file(cls, v: str | None) -> str | None:
        return _validate_metadata_filename(v)


class _BundleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_root: Path | None = None
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None
    reserved_filenames: tuple[str, ...] | None = None
    concept_path_strategy: str | None = None
    index_cache: Path | None = None
    listing_fields: tuple[str, ...] | None = None
    okf_version: str | None = None
    profile: str | None = None
    directory_metadata_file: str | None = None
    okf_cache_dir: Path | None = None

    @field_validator("okf_version")
    @classmethod
    def _validate_okf_version(cls, v: str | None) -> str | None:
        return _validate_optional_okf_version(v)

    @field_validator("directory_metadata_file")
    @classmethod
    def _validate_metadata_file(cls, v: str | None) -> str | None:
        return _validate_metadata_filename(v)


class _ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    defaults: ProjectDefaults = Field(default_factory=ProjectDefaults)
    taxonomy: TaxonomyConfig = Field(default_factory=TaxonomyConfig)
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    bundles: dict[str, _BundleInput] = Field(default_factory=dict)


def discover_config(start_path: str | Path | None = None) -> Path | None:
    """Search upward from ``start_path`` for ``okf-core.toml``."""

    current = _resolve_search_root(start_path)

    for directory in (current, *current.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(
    config_path: str | Path | None = None,
    project_root: str | Path | None = None,
    overrides: ConfigOverrides | dict[str, Any] | None = None,
) -> OkfConfig:
    """Load and resolve OKF project configuration."""

    explicit_config_path = Path(config_path) if config_path is not None else None
    resolved_config_path = _resolve_config_path(explicit_config_path, project_root)
    resolved_project_root = _resolve_project_root(project_root, resolved_config_path)

    file_config = _read_config_file(resolved_config_path)
    resolved_overrides = _coerce_overrides(overrides)
    defaults = _apply_overrides(file_config.defaults, resolved_overrides)
    defaults = _normalize_defaults(defaults, resolved_project_root)

    bundles = _resolve_bundles(
        raw_bundles=file_config.bundles,
        defaults=defaults,
        overrides=resolved_overrides,
        project_root=resolved_project_root,
    )

    try:
        return OkfConfig(
            project_root=resolved_project_root,
            config_path=resolved_config_path,
            defaults=defaults,
            taxonomy=file_config.taxonomy,
            profiles=file_config.profiles,
            bundles=bundles,
        )
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc


def _resolve_config_path(
    config_path: Path | None,
    project_root: str | Path | None,
) -> Path | None:
    if config_path is not None:
        resolved = config_path.expanduser().resolve(strict=False)
        if not resolved.is_file():
            raise ConfigError(f"Config file does not exist: {resolved}")
        return resolved

    return discover_config(project_root)


def _resolve_project_root(
    project_root: str | Path | None,
    config_path: Path | None,
) -> Path:
    if config_path is not None:
        return config_path.parent.resolve(strict=False)
    if project_root is not None:
        return _resolve_search_root(project_root)
    return Path.cwd().resolve(strict=False)


def _read_config_file(config_path: Path | None) -> _ConfigFile:
    if config_path is None:
        return _ConfigFile()

    try:
        with config_path.open("rb") as handle:
            raw_config = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"Could not read config file {config_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc

    try:
        return _ConfigFile.model_validate(raw_config)
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid OKF configuration in {config_path}: "
            f"{_format_validation_details(exc)}"
        ) from exc


def _coerce_overrides(
    overrides: ConfigOverrides | dict[str, Any] | None,
) -> ConfigOverrides:
    if overrides is None:
        return ConfigOverrides()
    if isinstance(overrides, ConfigOverrides):
        return overrides
    try:
        return ConfigOverrides.model_validate(overrides)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc


def _apply_overrides(
    defaults: ProjectDefaults,
    overrides: ConfigOverrides,
) -> ProjectDefaults:
    update = {
        name: value
        for name, value in overrides.model_dump().items()
        if value is not None
    }
    if not update:
        return defaults
    try:
        return defaults.model_copy(update=update)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc


def _normalize_defaults(
    defaults: ProjectDefaults,
    project_root: Path,
) -> ProjectDefaults:
    update = {
        "bundle_root": _normalize_path(defaults.bundle_root, project_root),
        "index_cache": _normalize_path(defaults.index_cache, project_root),
    }
    return defaults.model_copy(update=update)


def _resolve_bundles(
    raw_bundles: dict[str, _BundleInput],
    defaults: ProjectDefaults,
    overrides: ConfigOverrides,
    project_root: Path,
) -> dict[str, BundleConfig]:
    if not raw_bundles:
        resolved_bundle_root = defaults.bundle_root
        okf_cache_dir = overrides.okf_cache_dir
        return {
            "default": BundleConfig(
                name="default",
                bundle_root=resolved_bundle_root,
                include=defaults.include,
                exclude=defaults.exclude,
                reserved_filenames=defaults.reserved_filenames,
                concept_path_strategy=defaults.concept_path_strategy,
                index_cache=defaults.index_cache,
                listing_fields=defaults.listing_fields,
                okf_version=defaults.okf_version,
                directory_metadata_file=defaults.directory_metadata_file,
                okf_cache_dir=(
                    _normalize_path(okf_cache_dir, resolved_bundle_root)
                    if okf_cache_dir is not None
                    else None
                ),
            )
        }

    return {
        name: _resolve_bundle(name, bundle, defaults, overrides, project_root)
        for name, bundle in raw_bundles.items()
    }


def _resolve_bundle(
    name: str,
    raw_bundle: _BundleInput,
    defaults: ProjectDefaults,
    overrides: ConfigOverrides,
    project_root: Path,
) -> BundleConfig:
    bundle_root = _select_config_value(
        overrides.bundle_root,
        raw_bundle.bundle_root,
        defaults.bundle_root,
    )
    include = _select_config_value(
        overrides.include,
        raw_bundle.include,
        defaults.include,
    )
    exclude = (
        overrides.exclude
        if overrides.exclude is not None
        else raw_bundle.exclude if raw_bundle.exclude is not None else defaults.exclude
    )
    reserved_filenames = _select_config_value(
        overrides.reserved_filenames,
        raw_bundle.reserved_filenames,
        defaults.reserved_filenames,
    )
    concept_path_strategy = _select_config_value(
        overrides.concept_path_strategy,
        raw_bundle.concept_path_strategy,
        defaults.concept_path_strategy,
    )
    index_cache = _select_config_value(
        overrides.index_cache,
        raw_bundle.index_cache,
        defaults.index_cache,
    )
    listing_fields = _select_config_value(
        overrides.listing_fields,
        raw_bundle.listing_fields,
        defaults.listing_fields,
    )
    okf_version = _select_config_value(
        overrides.okf_version,
        raw_bundle.okf_version,
        defaults.okf_version,
    )
    directory_metadata_file = _select_config_value(
        overrides.directory_metadata_file,
        raw_bundle.directory_metadata_file,
        defaults.directory_metadata_file,
    )
    okf_cache_dir = _select_config_value(
        overrides.okf_cache_dir,
        raw_bundle.okf_cache_dir,
        None,
    )

    resolved_bundle_root = _normalize_path(bundle_root, project_root)

    return BundleConfig(
        name=name,
        bundle_root=resolved_bundle_root,
        include=include,
        exclude=exclude,
        reserved_filenames=reserved_filenames,
        concept_path_strategy=concept_path_strategy,
        index_cache=_normalize_path(index_cache, project_root),
        listing_fields=listing_fields,
        okf_version=okf_version,
        profile=raw_bundle.profile,
        directory_metadata_file=directory_metadata_file,
        okf_cache_dir=(
            _normalize_path(okf_cache_dir, resolved_bundle_root)
            if okf_cache_dir is not None
            else None
        ),
    )


def _select_config_value(
    override_value: Any | None,
    file_value: Any | None,
    default_value: Any,
) -> Any:
    if override_value is not None:
        return override_value
    if file_value is not None:
        return file_value
    return default_value


def _normalize_path(path: Path, project_root: Path) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve(strict=False)
    return (project_root / expanded).resolve(strict=False)


def _validate_optional_okf_version(version: str | None) -> str | None:
    if version is None:
        return None
    try:
        return validate_supported_okf_version(version)
    except OkfVersionError as exc:
        raise ValueError(str(exc)) from exc


def _validate_metadata_filename(v: str | None) -> str | None:
    if v is not None:
        p = Path(v)
        if p.name != v or p.is_absolute():
            raise ValueError(
                f"directory_metadata_file {v!r} must be a simple filename, not a path"
            )
    return v


def _resolve_search_root(start_path: str | Path | None) -> Path:
    start = Path.cwd() if start_path is None else Path(start_path).expanduser()
    current = start if not start.exists() or start.is_dir() else start.parent
    return current.resolve(strict=False)


def _format_validation_error(exc: ValidationError) -> str:
    return "Invalid OKF configuration: " + _format_validation_details(exc)


def _format_validation_details(exc: ValidationError) -> str:
    errors = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "config"
        errors.append(f"{location}: {error['msg']}")
    return "; ".join(errors)

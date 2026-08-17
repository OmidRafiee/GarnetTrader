"""بسته تنظیمات پروژه."""

from config.loader import (
    DEFAULT_CONFIG_PATH,
    EXAMPLE_CONFIG_PATH,
    PROJECT_ROOT,
    build_dataclass,
    deep_merge,
    default_settings,
    load_settings,
    resolve_path,
    section,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "EXAMPLE_CONFIG_PATH",
    "PROJECT_ROOT",
    "build_dataclass",
    "deep_merge",
    "default_settings",
    "load_settings",
    "resolve_path",
    "section",
]

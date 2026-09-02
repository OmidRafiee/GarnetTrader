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

def force_utf8_stdio() -> None:
    """خروجی کنسول را UTF-8 کن.

    خروجی این پروژه فارسی است، ولی کنسول پیش‌فرض ویندوز cp1252 است و اولین
    کاراکتر فارسی برنامه را با UnicodeEncodeError می‌خواباند. سپردن این کار
    به کاربر (PYTHONUTF8=1) یعنی دستور مستندشده روی نصب تازه کار نمی‌کند.
    """
    import contextlib
    import sys

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # جریان ممکن است ری‌دایرکت، بسته، یا جایگزین‌شده باشد؛ شکستش بی‌اهمیت است.
        with contextlib.suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")

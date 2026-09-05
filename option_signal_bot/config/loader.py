"""بارگذاری، ادغام و اعتبارسنجی تنظیمات پروژه.

تنها جایی که «مقدار پیش‌فرض» تعریف می‌شود همین ماژول است؛ بقیه کد فقط از
دیکشنری تنظیمات می‌خواند. نبودن `settings.yaml` یا نصب نبودن PyYAML خطا نیست،
تا `python main.py --dry-run` همیشه بدون هیچ تنظیمی کار کند.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: ریشه پروژه (پوشه‌ای که main.py در آن است)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.example.yaml"


def default_settings() -> dict[str, Any]:
    """تنظیمات کمینه‌ای که اجرای بدون فایل yaml را ممکن می‌کند."""
    return {
        "general": {
            "log_level": "INFO",
            "poll_interval_seconds": 300,
            "run_only_when_market_open": False,
        },
        # پیش‌فرض عمداً داده‌ی **واقعی** است. پروژه داده‌ی ساختگی ندارد،
        # پس هیچ خطای تنظیماتی نمی‌تواند بی‌صدا به قیمت جعلی منجر شود.
        "market_data": {
            "provider": "tsetmc",
            "symbols": ["خودرو", "شستا"],
            "history_days": 90,
            "risk_free_rate": 0.25,
        },
        "option_chain": {"provider": "tsetmc"},
        "signals": {
            "validity_minutes": 30,
            "dedupe_window_minutes": 60,
            "min_confidence": None,
        },
        "risk": {},
        # خالی = همه استراتژی‌های ثبت‌شده با پارامترهای پیش‌فرض خودشان
        "strategies": {},
        "notifiers": {
            "console": {"enabled": True, "as_json": False},
            "telegram": {"enabled": False},
        },
        "storage": {
            "enabled": True,
            "sqlite_path": "var/signals.db",
            "jsonl_path": "var/signals.jsonl",
        },
        # اتصال به حساب کارگزاری: پیش‌فرض خاموش. حتی روشن هم فقط می‌خواند.
        "broker": {
            "enabled": False,
            "provider": "emofid",
            "session_file": "var/emofid/session.json",
            "base_url": "https://api-mts.orbis.easytrader.ir",
            "timeout": 15,
            "retries": 3,
        },
        "backtest": {
            "history_days": 180,
            "horizon_days": 10,
            "warmup_days": 30,
            "step_days": 1,
        },
        # مایل‌استون ۱: اجرای سفارش وجود ندارد و این مقدار باید false بماند.
        "execution": {"enabled": False},
    }


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """ادغام بازگشتی دو دیکشنری (مقادیر `override` برنده‌اند).

    ادغام عمیق لازم است تا مثلاً بازنویسی یک پارامتر استراتژی، بقیه پارامترهای
    همان استراتژی را پاک نکند.
    """
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = deep_merge(current, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class SettingsError(RuntimeError):
    """خطای تنظیمات که باید اجرا را متوقف کند، نه اینکه بی‌صدا رد شود."""


def load_settings(config_path: Path | str | None = None) -> dict[str, Any]:
    """خواندن تنظیمات yaml و ادغام عمیق آن با پیش‌فرض‌ها."""
    defaults = default_settings()
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    if not path.exists():
        logger.info("فایل تنظیمات %s پیدا نشد؛ از مقادیر پیش‌فرض استفاده می‌شود.", path)
        return defaults

    try:
        import yaml  # وابستگی نرم
    except ImportError:
        # فایل تنظیمات **وجود دارد** ولی خوانده نمی‌شود. حالا که داده‌ی
        # ساختگی حذف شده، خطرِ «قیمت جعلی» نیست — ولی همچنان یعنی نمادها،
        # سقف ریسک و پارامترهای استراتژی شما نادیده گرفته می‌شوند و ربات
        # با پیش‌فرض‌های دیگری کار می‌کند. این خطاست، نه هشدار.
        raise SettingsError(
            f"فایل تنظیمات {path} وجود دارد ولی PyYAML نصب نیست، پس خوانده نشد.\n"
            "یعنی نمادها، سقف ریسک و پارامترهای استراتژی شما اعمال نمی‌شود.\n"
            "راه‌حل:  .venv\\Scripts\\python.exe -m pip install PyYAML"
        ) from None

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"ساختار فایل تنظیمات {path} باید یک دیکشنری باشد.")

    logger.info("تنظیمات از %s خوانده شد.", path)
    return deep_merge(defaults, loaded)


def section(settings: dict[str, Any], name: str) -> dict[str, Any]:
    """یک بخش از تنظیمات را همیشه به‌صورت دیکشنری برمی‌گرداند."""
    value = settings.get(name)
    return value if isinstance(value, dict) else {}


def resolve_path(value: str | Path, root: Path | None = None) -> Path:
    """مسیر نسبی را نسبت به ریشه پروژه حل می‌کند تا cwd روی خروجی اثر نگذارد."""
    path = Path(value)
    return path if path.is_absolute() else (root or PROJECT_ROOT) / path


def build_dataclass(cls: type[T], values: dict[str, Any], label: str = "") -> T:
    """ساخت یک dataclass از دیکشنری تنظیمات، با نادیده‌گرفتن کلیدهای ناشناخته.

    یک کلید اضافه یا غلط‌املایی در yaml نباید کل ربات را با TypeError بخواباند؛
    فقط هشدار می‌دهیم تا در لاگ دیده شود.
    """
    field_names = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(values or {}) - field_names)
    if unknown:
        logger.warning(
            "کلیدهای ناشناخته در بخش %s نادیده گرفته شدند: %s",
            label or cls.__name__,
            ", ".join(unknown),
        )
    return cls(**{k: v for k, v in (values or {}).items() if k in field_names})

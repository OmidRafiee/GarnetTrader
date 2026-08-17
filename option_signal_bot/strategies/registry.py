"""رجیستری استراتژی‌ها — افزودن استراتژی جدید بدون دست‌زدن به `main.py`.

روش کار:
    1. یک فایل جدید در `strategies/` بساز و کلاس را از `BaseStrategy` ارث بده.
    2. بالای کلاس دکوریتور `@register_strategy` را بگذار.
    3. یک خط import در `strategies/__init__.py` اضافه کن تا دکوریتور اجرا شود.

بعد از این، فعال/غیرفعال کردن و پارامترهای استراتژی فقط از `settings.yaml` کنترل می‌شود.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

StrategyT = TypeVar("StrategyT", bound=type[BaseStrategy])

#: نام استراتژی → کلاس آن
_REGISTRY: dict[str, type[BaseStrategy]] = {}


def register_strategy(cls: StrategyT) -> StrategyT:
    """ثبت یک کلاس استراتژی با کلید `cls.name`."""
    name = getattr(cls, "name", "")
    if not name or name == BaseStrategy.name:
        raise ValueError(f"کلاس {cls.__name__} باید یک `name` یکتا داشته باشد.")
    existing = _REGISTRY.get(name)
    if existing is not None and existing is not cls:
        raise ValueError(f"نام استراتژی «{name}» قبلاً توسط {existing.__name__} گرفته شده است.")
    _REGISTRY[name] = cls
    return cls


def available_strategies() -> list[str]:
    """نام همه استراتژی‌های ثبت‌شده."""
    return sorted(_REGISTRY)


def get_strategy_class(name: str) -> type[BaseStrategy] | None:
    return _REGISTRY.get(name)


def create_strategy(name: str, params: dict[str, Any] | None = None) -> BaseStrategy:
    """ساخت یک نمونه از استراتژی ثبت‌شده."""
    strategy_class = _REGISTRY.get(name)
    if strategy_class is None:
        raise ValueError(
            f"استراتژی ناشناخته «{name}». موجود: {', '.join(available_strategies()) or '—'}"
        )
    return strategy_class(params or {})


def create_strategies(config: dict[str, Any] | None) -> list[BaseStrategy]:
    """ساخت استراتژی‌ها از بخش `strategies` تنظیمات.

    بخش خالی به‌معنای «همه استراتژی‌های ثبت‌شده با پارامترهای پیش‌فرض» است تا
    اجرای `--dry-run` بدون فایل تنظیمات هم چیزی برای اجرا داشته باشد.
    """
    if not config:
        return [create_strategy(name) for name in available_strategies()]

    strategies: list[BaseStrategy] = []
    for name, entry in config.items():
        entry = entry or {}
        if not entry.get("enabled", True):
            logger.debug("استراتژی %s در تنظیمات غیرفعال است.", name)
            continue
        if name not in _REGISTRY:
            logger.warning(
                "استراتژی ناشناخته در تنظیمات نادیده گرفته شد: %s (موجود: %s)",
                name,
                ", ".join(available_strategies()) or "—",
            )
            continue
        strategies.append(create_strategy(name, entry.get("params")))

    if not strategies:
        logger.warning("هیچ استراتژی فعالی وجود ندارد؛ سیگنالی تولید نمی‌شود.")
    return strategies

"""بسته استراتژی‌ها.

این import‌ها لازم‌اند تا دکوریتور `@register_strategy` اجرا و استراتژی‌های
درون‌ساخت در رجیستری ثبت شوند. برای افزودن استراتژی جدید، یک خط import
این‌جا اضافه کنید.
"""

from strategies.base_strategy import BaseStrategy, StrategyContext
from strategies.directional_strategy import DirectionalStrategy
from strategies.neutral_strategy import NeutralStrategy
from strategies.registry import (
    available_strategies,
    create_strategies,
    create_strategy,
    get_strategy_class,
    register_strategy,
)

__all__ = [
    "BaseStrategy",
    "DirectionalStrategy",
    "NeutralStrategy",
    "StrategyContext",
    "available_strategies",
    "create_strategies",
    "create_strategy",
    "get_strategy_class",
    "register_strategy",
]

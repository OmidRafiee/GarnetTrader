"""اینترفیس انتزاعی اجرای سفارش — **placeholder برای آینده**.

هیچ اتصال واقعی به API کارگزاری در این پروژه وجود ندارد و نباید ساخته
شود. کاربر سیگنال‌ها را دستی در سامانه معاملاتی خودش ثبت می‌کند.

شبیه‌ساز درون‌حافظه هم حذف شده: چیزی که هیچ‌وقت اجرا نمی‌شود، شبیه‌ساز
لازم ندارد. یک تست گارد AST مانع رسیدن هر ماژولی به این لایه می‌شود.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class OrderStatus(str, Enum):
    """وضعیت چرخه عمر سفارش."""

    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """نمایش یک سفارش در لایه اجرا."""

    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    quantity: int
    price: float
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def remaining_quantity(self) -> int:
        return max(self.quantity - self.filled_quantity, 0)


@dataclass
class Position:
    """پوزیشن تجمیعی روی یک نماد."""

    symbol: str
    quantity: int
    average_price: float

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


class OrderExecutorInterface(ABC):
    """قرارداد انتزاعی کارگزاری. پیاده‌سازی واقعی توسط کاربر اضافه می‌شود."""

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        **kwargs: Any,
    ) -> Order:
        """ثبت سفارش جدید و برگرداندن شیء سفارش."""
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """لغو سفارش؛ True اگر لغو انجام شد."""
        raise NotImplementedError

    @abstractmethod
    def modify_order(
        self, order_id: str, price: float | None = None, quantity: int | None = None
    ) -> Order:
        """ویرایش قیمت و/یا تعداد یک سفارش باز."""
        raise NotImplementedError

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        """وضعیت فعلی سفارش."""
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """لیست پوزیشن‌های باز."""
        raise NotImplementedError

    @abstractmethod
    def get_account_balance(self) -> dict[str, float]:
        """موجودی حساب؛ مثلاً {"cash": ..., "buying_power": ...}."""
        raise NotImplementedError



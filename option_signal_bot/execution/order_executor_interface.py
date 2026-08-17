"""اینترفیس انتزاعی اجرای سفارش — **placeholder برای آینده**.

وضعیت مایل‌استون ۱: هیچ اتصال واقعی به API کارگزاری در این پروژه وجود ندارد و
نباید ساخته شود. کاربر سیگنال‌ها را دستی در سامانه معاملاتی خودش ثبت می‌کند.

این فایل فقط دو چیز دارد:
  1. `OrderExecutorInterface`: قرارداد انتزاعی که پیاده‌سازی واقعی کارگزاری
     در مایل‌استون‌های بعدی از آن ارث می‌برد.
  2. `MockBroker`: شبیه‌ساز کاملاً درون‌حافظه، فقط برای تست و آموزش.

هیچ ماژول دیگری در این پروژه (به‌ویژه `signals/signal_generator.py`) مجاز به
import کردن این فایل نیست.
"""

from __future__ import annotations

import itertools
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


class MockBroker(OrderExecutorInterface):
    """شبیه‌ساز درون‌حافظه — بدون هیچ I/O، شبکه یا اتصال واقعی.

    برای تست معماری و آزمودن سناریوها استفاده می‌شود. مدل پرشدن سفارش عمداً
    ساده است: `fill_all()` را دستی صدا می‌زنید تا سفارش‌های باز پر شوند.
    """

    def __init__(self, initial_cash: float = 1_000_000_000.0, contract_size: int = 1_000) -> None:
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.contract_size = contract_size
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        **kwargs: Any,
    ) -> Order:
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"سمت سفارش نامعتبر: {side!r}")
        order = Order(
            order_id=f"MOCK-{next(self._ids):05d}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            metadata=dict(kwargs),
        )
        if quantity <= 0 or price <= 0:
            order.status = OrderStatus.REJECTED
        elif side == "buy" and quantity * price * self.contract_size > self.cash:
            order.status = OrderStatus.REJECTED
            order.metadata["reject_reason"] = "موجودی کافی نیست"
        else:
            order.status = OrderStatus.OPEN
        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        order = self._require(order_id)
        if order.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING):
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now()
            return True
        return False

    def modify_order(
        self, order_id: str, price: float | None = None, quantity: int | None = None
    ) -> Order:
        order = self._require(order_id)
        if order.status not in (OrderStatus.OPEN, OrderStatus.PENDING):
            raise ValueError(f"سفارش {order_id} قابل ویرایش نیست (وضعیت: {order.status.value}).")
        if price is not None:
            order.price = price
        if quantity is not None:
            if quantity < order.filled_quantity:
                raise ValueError("تعداد جدید کمتر از مقدار پرشده است.")
            order.quantity = quantity
        order.updated_at = datetime.now()
        return order

    def get_order_status(self, order_id: str) -> OrderStatus:
        return self._require(order_id).status

    def get_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if not p.is_flat]

    def get_account_balance(self) -> dict[str, float]:
        return {
            "cash": round(self.cash, 2),
            "buying_power": round(self.cash, 2),
            "initial_cash": self.initial_cash,
        }

    # ------------------------------------------------------------------
    # ابزارهای مخصوص شبیه‌سازی
    # ------------------------------------------------------------------
    def fill_order(self, order_id: str, quantity: int | None = None) -> Order:
        """پر کردن (کامل یا جزئی) یک سفارش باز و به‌روزرسانی پوزیشن و نقدینگی."""
        order = self._require(order_id)
        if order.status not in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
            raise ValueError(f"سفارش {order_id} باز نیست.")

        fill_qty = min(quantity or order.remaining_quantity, order.remaining_quantity)
        if fill_qty <= 0:
            return order

        cash_delta = fill_qty * order.price * self.contract_size
        self.cash += -cash_delta if order.side == "buy" else cash_delta
        order.filled_quantity += fill_qty
        order.status = (
            OrderStatus.FILLED
            if order.remaining_quantity == 0
            else OrderStatus.PARTIALLY_FILLED
        )
        order.updated_at = datetime.now()
        self._apply_to_position(order.symbol, fill_qty if order.side == "buy" else -fill_qty, order.price)
        return order

    def fill_all(self) -> list[Order]:
        """پر کردن همه سفارش‌های باز (میان‌بر تست)."""
        open_ids = [
            oid
            for oid, o in self._orders.items()
            if o.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)
        ]
        return [self.fill_order(oid) for oid in open_ids]

    def get_order(self, order_id: str) -> Order:
        return self._require(order_id)

    def list_orders(self) -> list[Order]:
        return list(self._orders.values())

    def reset(self) -> None:
        self.cash = self.initial_cash
        self._orders.clear()
        self._positions.clear()

    # ------------------------------------------------------------------
    def _require(self, order_id: str) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"سفارش {order_id} یافت نشد.")
        return order

    def _apply_to_position(self, symbol: str, signed_qty: int, price: float) -> None:
        position = self._positions.get(symbol)
        if position is None:
            self._positions[symbol] = Position(symbol, signed_qty, price)
            return
        new_qty = position.quantity + signed_qty
        if new_qty == 0:
            self._positions[symbol] = Position(symbol, 0, 0.0)
        elif position.quantity * signed_qty > 0:
            # افزودن به پوزیشن هم‌جهت ⇒ میانگین وزنی قیمت
            total_cost = position.quantity * position.average_price + signed_qty * price
            self._positions[symbol] = Position(symbol, new_qty, total_cost / new_qty)
        else:
            # کاهش یا برگشت پوزیشن ⇒ میانگین قبلی حفظ می‌شود
            self._positions[symbol] = Position(symbol, new_qty, position.average_price)

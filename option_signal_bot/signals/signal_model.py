"""مدل داده سیگنال معاملاتی — تنها خروجی مجاز استراتژی‌ها.

این ماژول عمداً هیچ وابستگی‌ای به دیتا، کارگزاری یا نوتیفایر ندارد؛
`Signal` فقط یک پیام قابل سریالایز است که کاربر آن را **دستی** اجرا می‌کند.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

# مدت اعتبار پیش‌فرض سیگنال؛ پرمیوم آپشن سریع تغییر می‌کند.
DEFAULT_VALIDITY_MINUTES = 30


class OptionType(str, Enum):
    """نوع قرارداد آپشن."""

    CALL = "call"
    PUT = "put"


class Side(str, Enum):
    """سمت پیشنهادی معامله."""

    BUY = "buy"
    SELL = "sell"


class SignalStatus(str, Enum):
    """وضعیت سیگنال در چرخه عمر خودش (فقط برای Audit، بدون اجرای خودکار)."""

    NEW = "new"
    NOTIFIED = "notified"
    EXPIRED = "expired"


@dataclass
class Signal:
    """یک سیگنال معاملاتی پیشنهادی روی یک نماد آپشن.

    Attributes:
        symbol: نماد آپشن (مثلاً «ضخود۷۰۰۱»)
        option_type: call یا put
        side: buy یا sell
        strike: قیمت اعمال
        expiry: تاریخ سررسید
        suggested_price: پرمیوم پیشنهادی برای ثبت سفارش
        suggested_qty: تعداد قرارداد پیشنهادی (خروجی ماژول ریسک)
        reason: توضیح متنی دلیل صدور سیگنال
        strategy_name: نام استراتژی صادرکننده
        created_at: زمان صدور
        valid_until: پایان اعتبار پیشنهاد
    """

    symbol: str
    option_type: OptionType
    side: Side
    strike: float
    expiry: date
    suggested_price: float
    suggested_qty: int
    reason: str
    strategy_name: str
    created_at: datetime = field(default_factory=datetime.now)
    valid_until: datetime | None = None

    # --- فیلدهای کمکی (اختیاری) ---
    signal_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    underlying: str | None = None
    underlying_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence: float | None = None
    status: SignalStatus = SignalStatus.NEW
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # پذیرش رشته خام هم برای راحتی نویسندگان استراتژی
        self.option_type = OptionType(self.option_type)
        self.side = Side(self.side)
        self.status = SignalStatus(self.status)
        if self.valid_until is None:
            self.valid_until = self.created_at + timedelta(
                minutes=DEFAULT_VALIDITY_MINUTES
            )

    # ------------------------------------------------------------------
    @property
    def days_to_expiry(self) -> int:
        return max((self.expiry - self.created_at.date()).days, 0)

    @property
    def notional(self) -> float:
        """ارزش کل پیشنهاد = پرمیوم × تعداد قرارداد × اندازه قرارداد."""
        contract_size = int(self.metadata.get("contract_size", 1_000))
        return self.suggested_price * self.suggested_qty * contract_size

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now()) > (self.valid_until or datetime.max)

    def with_risk(
        self, suggested_qty: int, stop_loss: float | None, take_profit: float | None
    ) -> Signal:
        """نسخه‌ای تازه از سیگنال با اعداد ریسک پرشده (بدون تغییر شیء اصلی)."""
        return replace(
            self,
            suggested_qty=suggested_qty,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """دیکشنری قابل سریالایز (Enum → str، تاریخ → ISO)."""
        raw = asdict(self)
        raw["option_type"] = self.option_type.value
        raw["side"] = self.side.value
        raw["status"] = self.status.value
        raw["expiry"] = self.expiry.isoformat()
        raw["created_at"] = self.created_at.isoformat()
        raw["valid_until"] = self.valid_until.isoformat() if self.valid_until else None
        return raw

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Signal:
        payload = dict(data)
        payload["expiry"] = date.fromisoformat(payload["expiry"])
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
        valid_until = payload.get("valid_until")
        payload["valid_until"] = (
            datetime.fromisoformat(valid_until) if valid_until else None
        )
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in payload.items() if k in known})

    def summary(self) -> str:
        """یک‌خطی برای لاگ و کنسول."""
        action = "خرید" if self.side is Side.BUY else "فروش"
        kind = "کال" if self.option_type is OptionType.CALL else "پوت"
        return (
            f"[{self.strategy_name}] {action} {kind} {self.symbol} | "
            f"استرایک {self.strike:,.0f} | سررسید {self.expiry} "
            f"({self.days_to_expiry} روز) | پرمیوم {self.suggested_price:,.0f} "
            f"× {self.suggested_qty} قرارداد"
        )

"""کلاس پایه انتزاعی همه استراتژی‌ها.

قرارداد سخت معماری: خروجی هر استراتژی **فقط** لیستی از `Signal` است.
استراتژی هرگز نوتیفایر، دیتابیس یا لایه execution را صدا نمی‌زند.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from data.market_data_client import Candle, Quote
from data.option_chain_client import OptionChain, OptionContract
from pricing.implied_volatility import implied_volatility, realized_volatility
from signals.signal_model import OptionType, Side, Signal


@dataclass(frozen=True)
class StrategyContext:
    """تمام داده‌ای که یک استراتژی برای تصمیم‌گیری لازم دارد (فقط-خواندنی)."""

    underlying: str
    quote: Quote
    history: list[Candle]
    chain: OptionChain
    risk_free_rate: float = 0.25
    now: datetime = field(default_factory=datetime.now)

    @property
    def spot(self) -> float:
        return self.quote.reference_price

    @property
    def closes(self) -> list[float]:
        return [c.close for c in self.history]

    def today(self) -> date:
        return self.now.date()

    def realized_vol(self, window: int = 30) -> float:
        """نوسان تاریخی سالانه‌شده روی پنجره اخیر."""
        return realized_volatility(self.closes[-window:])

    def implied_vol(self, contract: OptionContract) -> float | None:
        """نوسان ضمنی یک نماد آپشن از پرمیوم بازار."""
        premium = contract.mid_price
        if not premium:
            return None
        return implied_volatility(
            market_price=premium,
            spot=self.spot,
            strike=contract.strike,
            time_to_expiry=contract.time_to_expiry(self.today()),
            rate=self.risk_free_rate,
            option_type=contract.option_type,
        )


class BaseStrategy(ABC):
    """پایه همه استراتژی‌ها.

    زیرکلاس‌ها فقط `generate` را پیاده می‌کنند و از `build_signal` برای ساخت
    خروجی استفاده می‌کنند تا شکل سیگنال‌ها یکدست بماند.
    """

    #: نام یکتا برای لاگ، بک‌تست و متن سیگنال
    name: str = "base"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = {**self.default_params(), **(params or {})}

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        """پارامترهای پیش‌فرض استراتژی؛ با فایل تنظیمات override می‌شود."""
        return {}

    @abstractmethod
    def generate(self, context: StrategyContext) -> list[Signal]:
        """در صورت برقراری شرایط، سیگنال(های) پیشنهادی را برمی‌گرداند.

        لیست خالی یعنی «هیچ اقدامی لازم نیست» و کاملاً حالت طبیعی است.
        """

    # ------------------------------------------------------------------
    # ابزارهای مشترک
    # ------------------------------------------------------------------
    def build_signal(
        self,
        context: StrategyContext,
        contract: OptionContract,
        side: Side,
        reason: str,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Signal:
        """ساخت `Signal` از یک قرارداد آپشن.

        `suggested_qty` عدداً صفر می‌ماند؛ پر کردنش وظیفه `RiskCalculator` است.
        """
        premium = contract.mid_price or 0.0
        # خریدار سمت ask را می‌پردازد و فروشنده سمت bid را می‌گیرد.
        if side is Side.BUY:
            price = contract.ask or premium
        else:
            price = contract.bid or premium

        return Signal(
            symbol=contract.symbol,
            option_type=OptionType(contract.option_type),
            side=side,
            strike=contract.strike,
            expiry=contract.expiry,
            suggested_price=round(price, 1),
            suggested_qty=0,
            reason=reason,
            strategy_name=self.name,
            created_at=context.now,
            underlying=context.underlying,
            underlying_price=context.spot,
            confidence=confidence,
            metadata={
                "contract_size": contract.contract_size,
                "days_to_expiry": contract.days_to_expiry(context.today()),
                "open_interest": contract.open_interest,
                **(metadata or {}),
            },
        )

    def select_contract(
        self,
        context: StrategyContext,
        option_type: str,
        moneyness: float = 0.0,
        min_days: int | None = None,
        max_days: int | None = None,
        min_open_interest: int = 1,
    ) -> OptionContract | None:
        """انتخاب نمادی نقدشونده با استرایک نزدیک به `spot × (1 + moneyness)`.

        Args:
            moneyness: 0 برای ATM، مثبت برای استرایک بالاتر، منفی برای پایین‌تر
        """
        min_days = self.params.get("min_days_to_expiry", 7) if min_days is None else min_days
        max_days = self.params.get("max_days_to_expiry", 120) if max_days is None else max_days

        candidates = [
            c
            for c in context.chain.filter(
                option_type=option_type, min_days=min_days, max_days=max_days
            )
            if c.is_liquid(min_open_interest) and c.mid_price
        ]
        if not candidates:
            return None

        target_strike = context.spot * (1.0 + moneyness)
        nearest_expiry = min(c.expiry for c in candidates)
        same_expiry = [c for c in candidates if c.expiry == nearest_expiry]
        return min(same_expiry, key=lambda c: abs(c.strike - target_strike))

    def __repr__(self) -> str:  # pragma: no cover - فقط برای دیباگ
        return f"{type(self).__name__}(name={self.name!r}, params={self.params!r})"

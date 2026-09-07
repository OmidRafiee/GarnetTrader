"""اینترفیس دریافت داده نماد پایه (TSETMC / pytse-client).

این پروژه **داده‌ی ساختگی ندارد**. اگر منبع داده در دسترس نباشد، خطا
بالا می‌رود؛ سیگنالی که پشتش قیمت واقعی نباشد از سیگنال نداشتن بدتر است.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # فقط برای type hint؛ در زمان اجرا import نمی‌شود
    from market.trading_calendar import TradingCalendar

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candle:
    """یک کندل روزانه نماد پایه."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Quote:
    """نماگر لحظه‌ای نماد پایه."""

    symbol: str
    last_price: float
    close_price: float
    timestamp: datetime
    bid: float | None = None
    ask: float | None = None
    volume: float = 0.0

    @property
    def reference_price(self) -> float:
        """قیمتی که برای قیمت‌گذاری استفاده می‌کنیم (آخرین معامله، وگرنه پایانی)."""
        return self.last_price or self.close_price


class MarketDataClient(ABC):
    """قرارداد دریافت داده نماد پایه. پیاده‌سازی‌ها باید فقط داده بدهند، نه تصمیم."""

    #: برای برچسب‌زدن منبع داده روی هر سیگنال (mock در برابر واقعی)
    source_name: str = "unknown"

    #: تقویم معاملاتی. اگر `bootstrap` یکی بسازد و اینجا بنشاند، تعطیلات
    #: رسمی هم لحاظ می‌شوند؛ وگرنه به رفتار قدیمی (فقط آخرهفته) برمی‌گردد.
    trading_calendar: TradingCalendar | None = None

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """آخرین وضعیت قیمتی نماد پایه."""

    @abstractmethod
    def get_history(self, symbol: str, days: int = 90) -> list[Candle]:
        """تاریخچه کندل روزانه، مرتب‌شده از قدیم به جدید."""

    def is_market_open(self, now: datetime | None = None) -> bool:
        """بازار تهران: شنبه تا چهارشنبه، ۰۹:۰۰ تا ۱۲:۳۰.

        اگر `trading_calendar` نشانده شده باشد، تعطیلات رسمی هم اعمال
        می‌شود؛ در غیر این صورت فقط تعطیلی هفتگی — که برای نبودِ تقویم،
        محافظه‌کارانه‌ترین حدسِ ممکن است.
        """
        now = now or datetime.now()
        if self.trading_calendar is not None:
            return self.trading_calendar.is_open(now)
        # weekday(): دوشنبه=0 ... شنبه=5، یکشنبه=6 → پنجشنبه(3) و جمعه(4) تعطیل
        if now.weekday() in (3, 4):
            return False
        minutes = now.hour * 60 + now.minute
        return 9 * 60 <= minutes <= 12 * 60 + 30


class PytseMarketDataClient(MarketDataClient):
    """پیاده‌سازی واقعی روی `pytse-client`.

    خطای دیتا **بالا می‌رود**. این پروژه داده‌ی ساختگی ندارد، پس هیچ
    fallbackی وجود ندارد که سیگنال را با قیمت مصنوعی بسازد؛ نبودِ داده
    باید بلند و واضح باشد، نه پنهان.
    """

    _tickers: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    @property
    def source_name(self) -> str:
        return "pytse"

    def _handle_failure(self, symbol: str, exc: Exception) -> None:
        """خطای دیتا را بالا می‌برد؛ سکوت اینجا یعنی سیگنال بی‌پشتوانه."""
        logger.error("دریافت داده %s از pytse-client شکست خورد: %s", symbol, exc)
        raise RuntimeError(f"دریافت داده {symbol} از pytse-client شکست خورد.") from exc

    def _ticker(self, symbol: str):
        if symbol in self._tickers:
            return self._tickers[symbol]
        from pytse_client import Ticker  # وابستگی نرم: فقط وقتی واقعاً لازم شد

        ticker = Ticker(symbol)
        self._tickers[symbol] = ticker
        return ticker

    def get_quote(self, symbol: str) -> Quote:
        try:
            ticker = self._ticker(symbol)
            return Quote(
                symbol=symbol,
                last_price=float(ticker.last_price or 0.0),
                close_price=float(ticker.adj_close or ticker.last_price or 0.0),
                timestamp=datetime.now(),
                volume=float(getattr(ticker, "value", 0.0) or 0.0),
            )
        except Exception as exc:
            self._handle_failure(symbol, exc)  # همیشه raise می‌کند

    def get_history(self, symbol: str, days: int = 90) -> list[Candle]:
        try:
            frame = self._ticker(symbol).history.tail(days)
            return [
                Candle(
                    date=row.date.date() if hasattr(row.date, "date") else row.date,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=float(row.volume),
                )
                for row in frame.itertuples()
            ]
        except Exception as exc:
            self._handle_failure(symbol, exc)  # همیشه raise می‌کند

"""اینترفیس دریافت داده نماد پایه (TSETMC / pytse-client) + کلاینت mock.

اگر `pytse-client` نصب نباشد یا شبکه در دسترس نباشد، `MockMarketDataClient`
همان ساختار داده را تولید می‌کند تا بقیه سیستم بدون تغییر کار کند.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


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

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """آخرین وضعیت قیمتی نماد پایه."""

    @abstractmethod
    def get_history(self, symbol: str, days: int = 90) -> list[Candle]:
        """تاریخچه کندل روزانه، مرتب‌شده از قدیم به جدید."""

    def is_market_open(self, now: datetime | None = None) -> bool:
        """بازار تهران: شنبه تا چهارشنبه، ۰۹:۰۰ تا ۱۲:۳۰ (تقریبی، بدون تقویم تعطیلات)."""
        now = now or datetime.now()
        # weekday(): دوشنبه=0 ... شنبه=5، یکشنبه=6 → پنجشنبه(3) و جمعه(4) تعطیل
        if now.weekday() in (3, 4):
            return False
        minutes = now.hour * 60 + now.minute
        return 9 * 60 <= minutes <= 12 * 60 + 30


class MockMarketDataClient(MarketDataClient):
    """تولید داده مصنوعی با گام تصادفی؛ برای تست و `--dry-run`.

    با `seed` ثابت، خروجی تکرارپذیر است تا تست‌ها قطعی بمانند.
    """

    def __init__(
        self,
        base_prices: dict[str, float] | None = None,
        seed: int = 1337,
        daily_vol: float = 0.025,
        trend: float = 0.0012,
    ) -> None:
        self.base_prices = base_prices or {"خودرو": 2_500.0, "فولاد": 5_800.0}
        self.daily_vol = daily_vol
        self.trend = trend
        self._rng = random.Random(seed)
        self._history_cache: dict[str, list[Candle]] = {}

    def _base_price(self, symbol: str) -> float:
        return self.base_prices.get(symbol, 1_000.0)

    def get_history(self, symbol: str, days: int = 90) -> list[Candle]:
        cached = self._history_cache.get(symbol)
        if cached and len(cached) >= days:
            return cached[-days:]

        price = self._base_price(symbol) * math.exp(-self.trend * days)
        today = date.today()
        candles: list[Candle] = []
        for i in range(days):
            shock = self._rng.gauss(self.trend, self.daily_vol)
            open_price = price
            price = max(price * math.exp(shock), 1.0)
            high = max(open_price, price) * (1 + abs(self._rng.gauss(0, 0.004)))
            low = min(open_price, price) * (1 - abs(self._rng.gauss(0, 0.004)))
            candles.append(
                Candle(
                    date=today - timedelta(days=days - i),
                    open=round(open_price, 1),
                    high=round(high, 1),
                    low=round(low, 1),
                    close=round(price, 1),
                    volume=float(self._rng.randint(1_000_000, 20_000_000)),
                )
            )
        self._history_cache[symbol] = candles
        return candles

    def get_quote(self, symbol: str) -> Quote:
        history = self.get_history(symbol, days=90)
        last = history[-1]
        return Quote(
            symbol=symbol,
            last_price=last.close,
            close_price=last.close,
            timestamp=datetime.now(),
            bid=round(last.close * 0.998, 1),
            ask=round(last.close * 1.002, 1),
            volume=last.volume,
        )


@dataclass
class PytseMarketDataClient(MarketDataClient):
    """پیاده‌سازی واقعی روی `pytse-client`.

    نصب نبودن کتابخانه یا خطای شبکه، در صورت فعال بودن `fallback`،
    به `MockMarketDataClient` منتقل می‌شود تا حلقه اصلی متوقف نشود.
    """

    fallback: MarketDataClient | None = None
    _tickers: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.fallback = self.fallback or MockMarketDataClient()

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
        except Exception:  # noqa: BLE001 - هر خطای شبکه/کتابخانه به fallback می‌رود
            return self.fallback.get_quote(symbol)

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
        except Exception:  # noqa: BLE001
            return self.fallback.get_history(symbol, days)

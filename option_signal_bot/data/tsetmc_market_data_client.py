"""کلاینت واقعی داده نماد پایه از API عمومی TSETMC (جایگزین mock و pytse-client).

دو endpoint تأییدشده، هر دو بدون احراز هویت:

    تاریخچه روزانه:
        https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{insCode}/0
        خروجی: `closingPriceDaily` — از **جدید به قدیم**، با فیلدهای
        dEven (تاریخ YYYYMMDD)، priceFirst (اولین)، priceMax (بیشترین)،
        priceMin (کمترین)، pClosing (پایانی)، pDrCotVal (آخرین)، qTotTran5J (حجم)

    نگاشت نماد به کد یکتا:
        همان پاسخ دیده‌بان بازار آپشن، که `lval30_UA` و `uaInsCode` را کنار هم دارد.
        پس برای نمادهای پایه‌ای که آپشن دارند، به جدول نماد جداگانه نیازی نیست و
        قیمت لحظه‌ای پایه هم بدون درخواست اضافه به‌دست می‌آید.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from data.market_data_client import Candle, MarketDataClient, Quote
from data.tsetmc_http import DEFAULT_USER_AGENT, fetch_json
from data.tsetmc_option_chain_client import (
    HttpPayloadSource,
    PayloadSource,
    parse_tsetmc_date,
)

logger = logging.getLogger(__name__)

DAILY_HISTORY_URL = (
    "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{ins_code}/0"
)
HISTORY_KEY = "closingPriceDaily"


class TsetmcMarketDataClient(MarketDataClient):
    """داده واقعی نماد پایه از TSETMC.

    Args:
        source: منبع پاسخ دیده‌بان آپشن (برای نگاشت نماد→insCode و قیمت لحظه‌ای).
            اگر همان نمونه‌ای که به `TsetmcOptionChainClient` می‌دهید را این‌جا هم
            بدهید، هر دو از یک پاسخ تغذیه می‌شوند.
        directory_ttl_seconds: عمر کش نگاشت نماد→insCode (کدها ثابت‌اند)
        history_ttl_seconds: عمر کش تاریخچه؛ تاریخچه روزانه در طول روز تغییر نمی‌کند
    """

    source_name = "tsetmc"

    def __init__(
        self,
        source: PayloadSource | None = None,
        timeout: int = 20,
        retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
        directory_ttl_seconds: float = 3_600.0,
        history_ttl_seconds: float = 900.0,
        history_dir: str | Path | None = None,
    ) -> None:
        self.source = source or HttpPayloadSource(market=0)
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        self.directory_ttl_seconds = directory_ttl_seconds
        self.history_ttl_seconds = history_ttl_seconds
        #: اگر داده شود، تاریخچه از فایل ضبط‌شده خوانده می‌شود نه شبکه
        self.history_dir = history_dir
        self._directory: dict[str, dict[str, Any]] = {}
        self._directory_at: float | None = None
        self._history: dict[str, tuple[float, list[Candle]]] = {}

    # ------------------------------------------------------------------
    def _fresh_directory(self) -> dict[str, dict[str, Any]]:
        """نگاشت «نماد پایه → کد یکتا و قیمت لحظه‌ای»، از پاسخ دیده‌بان آپشن."""
        now = time.monotonic()
        if (
            self._directory
            and self._directory_at is not None
            and now - self._directory_at < self.directory_ttl_seconds
        ):
            return self._directory

        payload = self.source.fetch()
        rows = payload.get("instrumentOptMarketWatch") or []
        directory: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = str(row.get("lval30_UA", "")).strip()
            ins_code = str(row.get("uaInsCode", "")).strip()
            if not symbol or not ins_code:
                continue
            # ردیف‌های یک نماد پایه همه یک uaInsCode دارند؛ اولی کافی است
            directory.setdefault(
                symbol,
                {
                    "ins_code": ins_code,
                    "last": row.get("pDrCotVal_UA"),
                    "close": row.get("pClosing_UA"),
                    "previous": row.get("priceYesterday_UA"),
                },
            )
        self._directory = directory
        self._directory_at = now
        logger.info("نگاشت نمادهای پایه به‌روز شد: %s نماد.", len(directory))
        return directory

    def available_symbols(self) -> list[str]:
        """نمادهای پایه‌ای که در بازار آپشن قابل رصد هستند."""
        return sorted(self._fresh_directory())

    def resolve_ins_code(self, symbol: str) -> str:
        """کد یکتای TSETMC یک نماد پایه."""
        directory = self._fresh_directory()
        entry = directory.get(symbol.strip())
        if entry is None:
            raise ValueError(
                f"نماد «{symbol}» در بازار آپشن پیدا نشد. "
                f"نمادهای موجود: {', '.join(sorted(directory))}"
            )
        return str(entry["ins_code"])

    # ------------------------------------------------------------------
    def get_quote(self, symbol: str) -> Quote:
        """قیمت لحظه‌ای پایه — از همان پاسخ زنجیره، بدون درخواست اضافه."""
        directory = self._fresh_directory()
        entry = directory.get(symbol.strip())
        if entry is None:
            raise ValueError(
                f"نماد «{symbol}» در بازار آپشن پیدا نشد. "
                f"نمادهای موجود: {', '.join(sorted(directory))}"
            )
        last = float(entry.get("last") or 0.0)
        close = float(entry.get("close") or 0.0)
        return Quote(
            symbol=symbol,
            last_price=last,
            close_price=close or last,
            timestamp=datetime.now(),
        )

    def get_history(self, symbol: str, days: int = 90) -> list[Candle]:
        """تاریخچه کندل روزانه، مرتب از قدیم به جدید.

        پاسخ TSETMC از جدید به قدیم است و کل تاریخ نماد را می‌دهد (هزاران رکورد)،
        پس معکوس و برش داده می‌شود و نتیجه کش می‌شود.
        """
        key = symbol.strip()
        now = time.monotonic()
        cached = self._history.get(key)
        if cached and now - cached[0] < self.history_ttl_seconds and len(cached[1]) >= days:
            return cached[1][-days:]

        ins_code = self.resolve_ins_code(key)
        payload = self._fetch_history_payload(ins_code, symbol)
        rows = payload.get(HISTORY_KEY)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"تاریخچه‌ای برای نماد «{symbol}» برنگشت.")

        candles = [c for c in (self._to_candle(r) for r in rows) if c is not None]
        candles.sort(key=lambda c: c.date)  # پاسخ از جدید به قدیم است
        self._history[key] = (now, candles)
        logger.info("تاریخچه %s دریافت شد: %s کندل.", symbol, len(candles))
        return candles[-days:]

    # ------------------------------------------------------------------
    def _fetch_history_payload(self, ins_code: str, symbol: str) -> dict[str, Any]:
        """پاسخ خام تاریخچه — از شبکه، یا از فایل ضبط‌شده.

        جداکردنش از `get_history` باعث می‌شود تست بتواند روی پاسخ **واقعیِ**
        ضبط‌شده اجرا شود، بدون شبکه و بدون داده‌ی ساختگی.
        """
        if self.history_dir is not None:
            path = Path(self.history_dir) / f"{ins_code}.json"
            if not path.exists():
                raise FileNotFoundError(
                    f"تاریخچه ضبط‌شده برای {symbol} (کد {ins_code}) پیدا نشد: {path}\n"
                    "با scripts/record_fixtures.py ضبطش کنید."
                )
            return json.loads(path.read_text(encoding="utf-8"))

        return fetch_json(
            DAILY_HISTORY_URL.format(ins_code=ins_code),
            timeout=self.timeout,
            retries=self.retries,
            user_agent=self.user_agent,
            label=f"تاریخچه {symbol}",
        )

    @staticmethod
    def _to_candle(row: dict[str, Any]) -> Candle | None:
        """یک رکورد روزانه را به `Candle` تبدیل می‌کند؛ رکورد ناقص را رد می‌کند."""
        try:
            close = float(row["pClosing"])
            if close <= 0:
                return None
            return Candle(
                date=parse_tsetmc_date(row["dEven"]),
                open=float(row.get("priceFirst") or close),
                high=float(row.get("priceMax") or close),
                low=float(row.get("priceMin") or close),
                close=close,
                volume=float(row.get("qTotTran5J") or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            return None

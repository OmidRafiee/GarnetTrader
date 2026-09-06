"""داده‌ی بازار از ایزی‌تریدر — **دامنه‌ی مجاز نوسان** و مظنه.

این ماژول جایگزین TSETMC نیست، مکمل آن است. تفاوت واقعی:

| داده | TSETMC | ایزی‌تریدر |
|---|---|---|
| زنجیره‌ی کامل (strike، سررسید) | ✅ یک درخواست | ❌ |
| **دامنه‌ی مجاز نوسان** | ❌ | ✅ |
| مظنه و حجم | ✅ | ✅ |

⚠️ **دامنه‌ی مجاز فقط از اینجا می‌آید.** `priceMin`/`priceMax` در پاسخ
TSETMC کمترین و بیشترین **معامله‌ی امروز** است، نه سقف و کف مجاز؛
اشتباه‌گرفتنشان باعث رد شدن سفارش‌های معتبر می‌شود.

هر فراخوان یک درخواست برای یک نماد است، پس برای پیمایش کل بازار مناسب
نیست — برای همان چند نمادی است که واقعاً می‌خواهید سفارش بدهید.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from brokers.base import BrokerAuthError, BrokerError, BrokerUnavailableError

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api-mts.orbis.easytrader.ir"
SYMBOL_INFO_PATH = "/symbols/api/MarketData/symbol-info-data"


@dataclass(frozen=True)
class SymbolInfo:
    """وضعیت یک نماد از دید کارگزاری، با دامنه‌ی مجاز."""

    symbol_isin: str
    last_price: float
    closing_price: float
    previous_close: float
    first_price: float
    day_high: float
    day_low: float
    #: سقف مجاز امروز — سفارش بالاتر از این رد می‌شود
    high_allowed_price: float
    #: کف مجاز امروز
    low_allowed_price: float
    total_volume: float
    total_value: float
    trade_count: int
    price_var: float
    as_of: datetime
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def is_price_allowed(self, price: float) -> bool:
        """آیا این قیمت داخل دامنه‌ی مجاز است؟

        اگر دامنه صفر باشد (نماد متوقف یا داده‌ی ناقص) `False` برمی‌گردد —
        محافظه‌کارانه، چون نمی‌دانیم مجاز است یا نه.
        """
        if self.high_allowed_price <= 0 or self.low_allowed_price <= 0:
            return False
        return self.low_allowed_price <= price <= self.high_allowed_price

    def clamp_to_allowed(self, price: float) -> float | None:
        """قیمت را داخل دامنه می‌آورد؛ اگر دامنه نامعتبر بود `None`.

        `None` عمداً برگردانده می‌شود تا فراخوان مجبور شود تصمیم بگیرد،
        نه اینکه بی‌صدا قیمت اشتباه ثبت شود.
        """
        if self.high_allowed_price <= 0 or self.low_allowed_price <= 0:
            return None
        return min(max(price, self.low_allowed_price), self.high_allowed_price)


class EmofidMarketClient:
    """داده‌ی بازار یک نماد از ایزی‌تریدر.

    احراز هویت مثل `EmofidAccountClient`: هدر `authorization`. توکن با
    OIDC ساخته می‌شود و در فایل سشن نیست.
    """

    source_name = "emofid"

    def __init__(
        self,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 15,
        retries: int = 3,
        cookie_header: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(retries, 1)
        self._token = self._normalize_token(token)
        self._cookie_header = cookie_header

        host = urllib.parse.urlsplit(self.base_url).hostname or ""
        self._opener = (
            urllib.request.build_opener(urllib.request.ProxyHandler({}))
            if host in ("127.0.0.1", "localhost", "::1")
            else urllib.request.build_opener()
        )

    @staticmethod
    def _normalize_token(token: str | None) -> str | None:
        if not token:
            return None
        token = token.strip()
        return token if token.lower().startswith("bearer ") else f"Bearer {token}"

    def is_authenticated(self) -> bool:
        return bool(self._token or self._cookie_header)

    # ------------------------------------------------------------------
    def get_symbol_info(self, symbol_isin: str) -> SymbolInfo:
        """وضعیت یک نماد، شامل دامنه‌ی مجاز نوسان.

        `POST /symbols/api/MarketData/symbol-info-data`
        """
        if not symbol_isin:
            raise ValueError("symbol_isin خالی است.")

        payload = self._post(SYMBOL_INFO_PATH, {"isin": symbol_isin})
        if not isinstance(payload, dict):
            raise BrokerError("پاسخ symbol-info-data شیء نیست؛ شکل API عوض شده.")

        # این دو دلیل وجود این ماژول‌اند؛ نبودشان یعنی API عوض شده
        for key in ("highAllowedPrice", "lowAllowedPrice"):
            if key not in payload:
                raise BrokerError(
                    f"پاسخ symbol-info-data فیلد «{key}» را ندارد. "
                    "docs/broker-emofid.md را به‌روز کنید."
                )

        return SymbolInfo(
            symbol_isin=symbol_isin,
            last_price=float(payload.get("lastTradedPrice") or 0),
            closing_price=float(payload.get("closingPrice") or 0),
            previous_close=float(payload.get("feeOfPreviousDaysClosingPrice") or 0),
            first_price=float(payload.get("firstTradedPrice") or 0),
            day_high=float(payload.get("highPrice") or 0),
            day_low=float(payload.get("lowPrice") or 0),
            high_allowed_price=float(payload["highAllowedPrice"] or 0),
            low_allowed_price=float(payload["lowAllowedPrice"] or 0),
            total_volume=float(payload.get("totalNumberOfSharesTraded") or 0),
            total_value=float(payload.get("totalTradeValue") or 0),
            trade_count=int(payload.get("totalNumberOfTrades") or 0),
            price_var=float(payload.get("priceVar") or 0),
            as_of=datetime.now(),
            raw=payload,
        )

    # ------------------------------------------------------------------
    def _post(self, path: str, body: dict[str, Any]) -> Any:
        """یک POST **فقط خواندنی**.

        این endpoint با POST کار می‌کند ولی چیزی تغییر نمی‌دهد — فقط
        پارامتر در بدنه می‌گیرد. هیچ سفارشی اینجا ثبت نمی‌شود.
        """
        if not (self._token or self._cookie_header):
            raise BrokerAuthError(
                "توکن تنظیم نشده. توکن را از DevTools بردارید و در پنل وارد کنید."
            )

        data = json.dumps(body).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
        }
        if self._token:
            headers["authorization"] = self._token
        if self._cookie_header:
            headers["cookie"] = self._cookie_header

        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method="POST"
        )

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise BrokerAuthError(
                        f"دسترسی رد شد ({exc.code}) روی {path}. توکن منقضی شده."
                    ) from exc
                last_error = exc
                if exc.code < 500:
                    raise BrokerError(f"خطای {exc.code} روی {path}") from exc
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                last_error = exc

            logger.warning(
                "درخواست %s ناموفق بود (تلاش %s از %s): %s",
                path, attempt, self.retries, last_error,
            )

        raise BrokerUnavailableError(f"دریافت {path} شکست خورد: {last_error}")

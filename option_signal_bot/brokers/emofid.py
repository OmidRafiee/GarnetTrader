"""آداپتر خواندن حساب از ایزی‌تریدر (مفید) — **فقط GET**.

بر اساس کشف واقعی از ترافیک وب‌اپ؛ جزئیات در `docs/broker-emofid.md`.

⚠️ **مفید API عمومی مستندی ندارد.** این endpointها از مشاهده‌ی ترافیک
به دست آمده‌اند و هر آپدیت وب‌اپ می‌تواند بشکندشان. به همین دلیل:

* هر پاسخ غیرمنتظره **خطای بلند** می‌دهد، نه مقدار پیش‌فرض بی‌صدا.
* ۴۰۱/۴۰۳ به `BrokerAuthError` نگاشت می‌شود (تلاش مجدد بی‌فایده است) و
  بقیه به `BrokerUnavailableError` (تلاش مجدد منطقی است).
* توکن هرگز لاگ نمی‌شود.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

from brokers.base import (
    AccountDataSource,
    BrokerAuthError,
    BrokerError,
    BrokerUnavailableError,
    OptionContractSpec,
    OptionPosition,
    UnderlyingLimit,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api-mts.orbis.easytrader.ir"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; option-signal-bot/0.1)"

#: در پاسخ `Positions`، مقدار `side` برای موقعیت خرید
_SIDE_BUY = 1


def _parse_date(value: Any) -> date | None:
    """تاریخ ISO کارگزاری را به `date` تبدیل می‌کند؛ خرابی را می‌بلعد.

    تاریخ خراب نباید کل خواندن پوزیشن‌ها را بخواباند — ولی برخلاف مقادیر
    عددی، نبودش هم تصمیم‌ساز نیست، پس `None` قابل قبول است.
    """
    if not value:
        return None
    text = str(value)
    for candidate in (text, text.split("T")[0]):
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    logger.debug("تاریخ ناخوانا از کارگزاری: %r", value)
    return None


def _require(payload: dict[str, Any], key: str, context: str) -> Any:
    """کلید ضروری را می‌خواند و اگر نبود، بلند خطا می‌دهد.

    نبودِ یک فیلد یعنی شکل پاسخ عوض شده. برگرداندن صفر در این حالت
    خطرناک است: `contract_size=0` یا `strike=0` بی‌صدا همه‌ی محاسبات را
    غلط می‌کند بدون اینکه چیزی به نظر خراب بیاید.
    """
    if key not in payload:
        raise BrokerError(
            f"پاسخ {context} فیلد «{key}» را ندارد. "
            "احتمالاً شکل API کارگزاری عوض شده؛ docs/broker-emofid.md را به‌روز کنید."
        )
    return payload[key]


class EmofidAccountClient(AccountDataSource):
    """خواندن پوزیشن، مشخصات قرارداد و سقف موقعیت از ایزی‌تریدر.

    Args:
        token: توکن `authorization`. اگر `Bearer` نداشته باشد، اضافه می‌شود.
        base_url: هاست API
        timeout: تایم‌اوت هر درخواست (ثانیه)
        retries: تعداد تلاش برای خطاهای گذرا
    """

    name = "emofid"

    def __init__(
        self,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 15,
        retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(retries, 1)
        self.user_agent = user_agent
        self._token = self._normalize_token(token)
        self._opener = self._build_opener(self.base_url)

    @staticmethod
    def _build_opener(base_url: str) -> urllib.request.OpenerDirector:
        """opener مناسب مقصد.

        `urllib` متغیرهای HTTP_PROXY را خودکار اعمال می‌کند. برای کارگزاری
        درست است، ولی برای `localhost` (تست و سرور جعلی) پروکسی شرکتی
        وسط می‌افتد و ۵۰۴ می‌دهد که از قطعی واقعی قابل تشخیص نیست.
        """
        host = urllib.parse.urlsplit(base_url).hostname or ""
        if host in ("127.0.0.1", "localhost", "::1"):
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener()

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_token(token: str | None) -> str | None:
        if not token:
            return None
        token = token.strip()
        return token if token.lower().startswith("bearer ") else f"Bearer {token}"

    @classmethod
    def from_session_file(
        cls, path: str | Path, **kwargs: Any
    ) -> EmofidAccountClient:
        """ساخت کلاینت از `var/emofid/session.json` که اسکریپت کشف می‌سازد.

        ⚠️ آن فایل معادل دسترسی به حساب است؛ جایی نفرستیدش.
        """
        session_path = Path(path)
        if not session_path.exists():
            raise BrokerAuthError(
                f"فایل سشن پیدا نشد: {session_path}\n"
                "برای ساختنش، 3-discover-api.bat را در ساعت بازار اجرا کنید."
            )

        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise BrokerAuthError(f"فایل سشن خوانده نشد: {exc}") from exc

        token = cls._token_from_storage_state(data)
        if not token:
            raise BrokerAuthError(
                "توکنی در فایل سشن پیدا نشد. ممکن است سشن منقضی شده باشد؛ "
                "دوباره لاگین کنید."
            )
        return cls(token=token, **kwargs)

    @staticmethod
    def _token_from_storage_state(data: dict[str, Any]) -> str | None:
        """توکن را از ساختار `storage_state` پلی‌رایت بیرون می‌کشد.

        شکل دقیقش تضمین‌شده نیست، پس محافظه‌کارانه می‌گردیم و در صورت
        پیدا نکردن `None` برمی‌گردانیم تا فراخوان پیام روشن بدهد.
        """
        for origin in data.get("origins") or []:
            for item in origin.get("localStorage") or []:
                name = str(item.get("name", "")).lower()
                if "token" not in name and "auth" not in name:
                    continue
                value = item.get("value") or ""
                # گاهی خودِ توکن است، گاهی JSON حاوی آن
                try:
                    parsed = json.loads(value)
                except ValueError:
                    if value:
                        return value
                    continue
                if isinstance(parsed, dict):
                    for key in ("access_token", "accessToken", "token", "id_token"):
                        if parsed.get(key):
                            return str(parsed[key])
        return None

    # ------------------------------------------------------------------
    def is_authenticated(self) -> bool:
        return bool(self._token)

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """یک GET با تلاش مجدد. توکن هرگز در لاگ نمی‌آید."""
        if not self._token:
            raise BrokerAuthError(
                "توکنی تنظیم نشده. یا `token=` بدهید یا از `from_session_file` "
                "استفاده کنید."
            )

        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(
            url,
            headers={
                "authorization": self._token,
                "accept": "application/json",
                "user-agent": self.user_agent,
            },
        )

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    # تلاش مجدد بی‌فایده است؛ کاربر باید دوباره لاگین کند
                    raise BrokerAuthError(
                        f"دسترسی رد شد ({exc.code}) روی {path}. "
                        "سشن منقضی شده؛ دوباره لاگین کنید."
                    ) from exc
                last_error = exc
                if exc.code < 500:
                    # ۴xx دیگر معمولاً با تکرار درست نمی‌شود
                    raise BrokerError(f"خطای {exc.code} از کارگزاری روی {path}") from exc
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                last_error = exc

            logger.warning(
                "درخواست %s ناموفق بود (تلاش %s از %s): %s",
                path,
                attempt,
                self.retries,
                last_error,
            )
            if attempt < self.retries:
                time.sleep(attempt)

        raise BrokerUnavailableError(
            f"دریافت {path} از کارگزاری شکست خورد: {last_error}"
        )

    # ------------------------------------------------------------------
    def get_positions(self) -> list[OptionPosition]:
        """موقعیت‌های باز آپشن.

        `GET /option/api/Positions`
        """
        payload = self._get("/option/api/Positions")
        if not isinstance(payload, list):
            raise BrokerError(
                "پاسخ Positions باید آرایه باشد. شکل API عوض شده است."
            )
        return [self._to_position(row) for row in payload]

    @staticmethod
    def _to_position(row: dict[str, Any]) -> OptionPosition:
        context = "Positions"
        return OptionPosition(
            symbol_isin=str(_require(row, "symbolIsin", context)),
            symbol_name=str(row.get("symbolName") or ""),
            quantity=int(_require(row, "executedQuantity", context) or 0),
            is_long=int(row.get("side") or 0) == _SIDE_BUY,
            strike_price=float(_require(row, "strikePrice", context) or 0),
            base_isin=str(row.get("baseIsin") or ""),
            total_margin=float(row.get("totalMargin") or 0),
            required_margin_per_contract=float(row.get("contractRequiredMargin") or 0),
            open_buy_quantity=int(row.get("openBuyQuantity") or 0),
            open_sell_quantity=int(row.get("openSellQuantity") or 0),
            buy_average_price=float(row.get("buyAveragePrice") or 0),
            sell_average_price=float(row.get("sellAveragePrice") or 0),
            closed_pnl=float(row.get("closedPositionProfitLoss") or 0),
            cash_settlement_date=_parse_date(row.get("cashSettlementDate")),
            physical_settlement_date=_parse_date(row.get("physicalSettlementDate")),
            raw=row,
        )

    def get_contract_spec(self, symbol_isin: str) -> OptionContractSpec:
        """مشخصات یک قرارداد.

        `GET /option/api/Contracts/{symbolIsin}/symbol`
        """
        if not symbol_isin:
            raise ValueError("symbol_isin خالی است.")

        quoted = urllib.parse.quote(symbol_isin, safe="")
        payload = self._get(f"/option/api/Contracts/{quoted}/symbol")
        if not isinstance(payload, dict):
            raise BrokerError("پاسخ Contracts باید شیء باشد. شکل API عوض شده است.")

        context = "Contracts"
        # این دو ضروری‌اند: مقدار غلط‌شان بی‌صدا همه‌ی محاسبات را خراب می‌کند
        contract_size = int(_require(payload, "contractSize", context) or 0)
        if contract_size <= 0:
            raise BrokerError(
                f"اندازه قرارداد نامعتبر ({contract_size}) برای {symbol_isin}."
            )

        return OptionContractSpec(
            symbol_isin=symbol_isin,
            strike_price=float(_require(payload, "strikePrice", context) or 0),
            contract_size=contract_size,
            base_isin=str(payload.get("baseIsin") or ""),
            start_date=_parse_date(payload.get("startDate")),
            end_date=_parse_date(payload.get("endDate")),
            initial_margin=float(payload.get("initialMargin") or 0),
            required_margin=float(payload.get("requiredMargin") or 0),
            maintenance_margin=float(payload.get("maintenanceMargin") or 0),
            max_orders=int(payload.get("maxOrders") or 0),
            max_customer_open_position=int(payload.get("maxCOP") or 0),
            max_market_open_position=int(payload.get("maxMarketOP") or 0),
            open_positions=int(payload.get("openPositions") or 0),
            cash_settlement_date=_parse_date(payload.get("cashSettlementDate")),
            physical_settlement_date=_parse_date(payload.get("physicalSettlementDate")),
            early_exercise=bool(payload.get("cefo")),
            raw=payload,
        )

    def get_underlying_limit(self, base_isin: str, end_date: date) -> UnderlyingLimit:
        """سقف موقعیت روی دارایی پایه در یک سررسید.

        `GET /option/api/contracts/underlying-asset/{baseIsin}?endDate=...`
        """
        if not base_isin:
            raise ValueError("base_isin خالی است.")

        quoted = urllib.parse.quote(base_isin, safe="")
        payload = self._get(
            f"/option/api/contracts/underlying-asset/{quoted}",
            params={"endDate": end_date.isoformat()},
        )
        if not isinstance(payload, dict):
            raise BrokerError(
                "پاسخ underlying-asset باید شیء باشد. شکل API عوض شده است."
            )

        context = "underlying-asset"
        return UnderlyingLimit(
            base_isin=base_isin,
            max_open_position=int(_require(payload, "maxOpenPosition", context) or 0),
            sum_open_positions=int(payload.get("sumOpenPositions") or 0),
            low_limit_open_position=int(payload.get("lowLimitOpenPosition") or 0),
            is_request_allowed=bool(payload.get("isRequestAllowed", True)),
            raw=payload,
        )

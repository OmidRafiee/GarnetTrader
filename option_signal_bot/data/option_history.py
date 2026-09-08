"""تاریخچه‌ی **پرمیوم واقعی** قرارداد آپشن از TSETMC.

**چرا این ماژول لازم شد**

بک‌تست تا اینجا فقط **جهت‌دهی** را می‌سنجید: «قیمت پایه بالا رفت یا نه؟»
پرمیوم آپشن هیچ‌وقت وارد محاسبه نمی‌شد، چون فرض بر این بود که تاریخچه‌ی
آپشن در دسترس نیست و از زنجیره‌ی امروز با سررسیدِ جابه‌جاشده استفاده
می‌شد (`_shift_chain`).

آن فرض **غلط بود.** همان endpointی که تاریخچه‌ی سهم پایه را می‌دهد،
برای خودِ نماد آپشن هم کار می‌کند:

    GET https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{insCode}/0

**چرا این تفاوت مهم است**

بازده‌ی پایه و بازده‌ی آپشن یکی نیستند، و فاصله‌شان کم نیست:

* **اهرم.** ۵٪ حرکت پایه می‌تواند ۵۰٪ حرکت پرمیوم باشد. بک‌تستی که
  جهت را درست بگوید ولی بزرگی را نه، سود و زیان واقعی را نمی‌داند.
* **تتا.** پرمیوم حتی وقتی پایه ثابت بماند آب می‌رود. یک سیگنالِ
  «درست ولی کند» در جهت‌سنجی برنده و در واقعیت بازنده است.
* **نقدشوندگی.** روزهایی که قرارداد اصلاً معامله نشده، قیمت پایانی
  تکرارِ دیروز است. آن را حرکت واقعی حساب کردن یعنی بازده‌ی جعلی.

**آنچه این ماژول نمی‌دهد**

تاریخچه‌ی **زنجیره** (اینکه در فلان روز چه استرایک‌هایی وجود داشتند).
فقط تاریخچه‌ی قراردادهایی را می‌دهد که **امروز** می‌شناسیم. یعنی
سوگیریِ بقا (survivorship bias) هنوز هست: قراردادی که سررسید شده و از
دیده‌بان بازار رفته، در بک‌تست دیده نمی‌شود. این محدودیت واقعی است و
پنهانش نمی‌کنیم.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from data.market_data_client import Candle
from data.tsetmc_http import DEFAULT_USER_AGENT, fetch_json
from data.tsetmc_option_chain_client import parse_tsetmc_date

logger = logging.getLogger(__name__)

DAILY_HISTORY_URL = (
    "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{ins_code}/0"
)
HISTORY_KEY = "closingPriceDaily"


@dataclass(frozen=True)
class PremiumBar:
    """یک روز از تاریخچه‌ی پرمیوم یک قرارداد.

    `close` قیمت **پایانی** است و `last` آخرین معامله. در بازار کم‌عمق
    آپشن این دو می‌توانند خیلی فاصله بگیرند، پس هر دو نگه داشته می‌شوند
    و مصرف‌کننده انتخاب می‌کند.
    """

    date: date
    open: float
    high: float
    low: float
    close: float
    last: float
    volume: float
    trades: int = 0

    @property
    def traded(self) -> bool:
        """آیا این قرارداد در این روز **واقعاً** معامله شد؟

        روزِ بدون معامله، قیمت پایانی‌اش تکرارِ دیروز است. حرکت حساب
        کردنش یعنی بازده‌ی جعلی — به همین دلیل صریح علامت می‌خورد.
        """
        return self.volume > 0 and self.trades > 0

    @property
    def reference_price(self) -> float:
        """قیمتی که برای ارزش‌گذاری استفاده می‌شود."""
        return self.close or self.last


def parse_premium_history(payload: dict[str, Any]) -> list[PremiumBar]:
    """پاسخ خام را به میله‌های پرمیوم تبدیل می‌کند، **قدیم به جدید**.

    TSETMC از جدید به قدیم می‌دهد؛ ترتیب اینجا برعکس می‌شود تا با
    `Candle` بقیه‌ی پروژه یکسان باشد.
    """
    rows = payload.get(HISTORY_KEY)
    if not isinstance(rows, list):
        return []

    bars: list[PremiumBar] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # `parse_tsetmc_date` روی ورودی خراب **استثنا** می‌دهد، نه `None`.
        # یک ردیف خراب نباید کل تاریخچه‌ی آن قرارداد را از بین ببرد.
        try:
            day = parse_tsetmc_date(row.get("dEven"))
        except (ValueError, TypeError):
            logger.debug("ردیف با تاریخ ناخوانا رد شد: %r", row.get("dEven"))
            continue
        close = _number(row.get("pClosing"))
        last = _number(row.get("pDrCotVal"))
        # قراردادی که هیچ قیمتی ندارد، داده نیست
        if close <= 0 and last <= 0:
            continue
        bars.append(
            PremiumBar(
                date=day,
                open=_number(row.get("priceFirst")),
                high=_number(row.get("priceMax")),
                low=_number(row.get("priceMin")),
                close=close,
                last=last,
                volume=_number(row.get("qTotTran5J")),
                trades=int(_number(row.get("zTotTran"))),
            )
        )

    bars.sort(key=lambda bar: bar.date)
    return bars


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


class OptionHistoryClient:
    """تاریخچه‌ی پرمیوم قراردادهای آپشن، با کش.

    کش عمداً **بلند** است (پیش‌فرض یک ساعت): تاریخچه‌ی روزانه در طول روز
    عوض نمی‌شود، و یک بک‌تست ممکن است یک قرارداد را ده‌ها بار بپرسد.

    Args:
        history_dir: پوشه‌ی پاسخ‌های ضبط‌شده. اگر داده شود، **اول** از
            دیسک خوانده می‌شود — همان الگویی که کلاینت نماد پایه دارد،
            تا تست‌ها به شبکه نروند.
    """

    source_name = "tsetmc"

    def __init__(
        self,
        ttl_seconds: float = 3600.0,
        timeout: int = 20,
        retries: int = 2,
        user_agent: str = DEFAULT_USER_AGENT,
        history_dir: str | Path | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        self.history_dir = Path(history_dir) if history_dir else None
        self._cache: dict[str, tuple[float, list[PremiumBar]]] = {}

    # ------------------------------------------------------------------
    def get_history(self, ins_code: str, symbol: str = "") -> list[PremiumBar]:
        """تاریخچه‌ی پرمیوم یک قرارداد. خطای شبکه را **بالا می‌برد**."""
        if not ins_code:
            raise ValueError("کد یکتای قرارداد لازم است.")

        cached = self._cache.get(ins_code)
        if cached is not None and (time.monotonic() - cached[0]) < self.ttl_seconds:
            return cached[1]

        payload = self._load(ins_code, symbol)
        bars = parse_premium_history(payload)
        self._cache[ins_code] = (time.monotonic(), bars)
        logger.debug("تاریخچه‌ی پرمیوم %s: %d روز.", symbol or ins_code, len(bars))
        return bars

    def try_get_history(self, ins_code: str, symbol: str = "") -> list[PremiumBar]:
        """مثل بالا، ولی خطا را می‌بلعد و لیست خالی می‌دهد.

        یک قرارداد بدون تاریخچه نباید کل بک‌تست را بخواباند؛ همان قرارداد
        از نتیجه کنار گذاشته می‌شود.
        """
        try:
            return self.get_history(ins_code, symbol)
        except Exception as exc:  # یک قرارداد خراب، بک‌تست را نکشد
            logger.warning(
                "تاریخچه‌ی پرمیوم %s دریافت نشد: %s", symbol or ins_code, exc
            )
            return []

    def _load(self, ins_code: str, symbol: str) -> dict[str, Any]:
        """اول دیسک، بعد شبکه."""
        if self.history_dir is not None:
            path = self.history_dir / f"{ins_code}.json"
            if path.exists():
                import json

                return json.loads(path.read_text(encoding="utf-8"))

        return fetch_json(
            DAILY_HISTORY_URL.format(ins_code=ins_code),
            timeout=self.timeout,
            retries=self.retries,
            user_agent=self.user_agent,
            label=f"تاریخچه پرمیوم {symbol or ins_code}",
        )

    # ------------------------------------------------------------------
    def price_on(
        self, ins_code: str, day: date, symbol: str = ""
    ) -> PremiumBar | None:
        """پرمیوم یک قرارداد در یک روز مشخص.

        اگر آن روز معامله‌ای نبوده، **`None`** برمی‌گردد نه قیمتِ نزدیک‌ترین
        روز. جایگزین‌کردنِ بی‌صدای قیمت روز دیگر، بک‌تست را خوش‌بین می‌کند:
        معامله‌ای فرض می‌شود که در واقعیت ممکن نبود.
        """
        for bar in self.get_history(ins_code, symbol):
            if bar.date == day:
                return bar
        return None

    def to_candles(self, ins_code: str, symbol: str = "") -> list[Candle]:
        """برای بخش‌هایی از پروژه که `Candle` می‌خواهند."""
        return [
            Candle(
                date=bar.date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in self.get_history(ins_code, symbol)
        ]

"""قرارداد منبع مظنه‌ی لحظه‌ای — تنها چیزی که هسته می‌شناسد.

**چرا این قرارداد لازم شد**

`TsetmcQuoteClient` روی **پولینگ** کار می‌کند: هر بار یک درخواست HTTP.
برای یک ربات که هر ۵ دقیقه پاس می‌زند کافی است، ولی برای دیدن تغییرات
لحظه‌ای نه. ایزی‌تریدر یک اتصال Lightstreamer/SignalR دارد که همان داده
را **push** می‌کند.

اگر هسته مستقیم به کلاینت TSETMC وصل باشد، افزودن آن مسیر یعنی تغییر در
همه‌ی مصرف‌کننده‌ها. با این قرارداد، یعنی یک پیاده‌سازی تازه که همین
سوئیت تست را پاس کند.

**عمداً فقط خواندن.** هیچ متدی اینجا سفارش ثبت نمی‌کند و این ماژول
`execution` را import نمی‌کند؛ گارد AST پروژه تضمینش می‌کند.

⚠️ **دامنه مجاز نوسان اینجا نیست.** `priceMin`/`priceMax` در پاسخ TSETMC
کمترین و بیشترین **معامله‌ی امروز** است، نه سقف و کف مجاز. تنها منبع
دامنه‌ی مجاز، `lowAllowedPrice`/`highAllowedPrice` در API کارگزاری است.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # فقط type hint. در زمان اجرا import نمی‌شود تا حلقه‌ی import بین
    # قرارداد و پیاده‌سازی‌اش ساخته نشود.
    from data.order_book import OrderBook

logger = logging.getLogger(__name__)


class QuoteSourceError(RuntimeError):
    """خطای پایه‌ی همه‌ی منابع مظنه."""


class RealtimeQuoteSource(ABC):
    """مظنه‌ی لحظه‌ای و عمق یک نماد. **هیچ عملیات نوشتنی ندارد.**"""

    #: نامی که در لاگ و UI دیده می‌شود
    name: str = "unknown"

    #: آیا این منبع push است یا پولینگ؟ مصرف‌کننده با همین تصمیم می‌گیرد
    #: هر چند وقت بپرسد.
    is_push: bool = False

    @abstractmethod
    def get_order_book(self, ins_code: str, symbol: str = "") -> OrderBook:
        """دفتر سفارش چندسطحی. خطا را **بالا می‌برد**."""

    def try_get_order_book(
        self, ins_code: str, symbol: str = ""
    ) -> OrderBook | None:
        """مثل بالا، ولی خطا را می‌بلعد.

        عمق یک **افزونه** است، نه پیش‌نیاز سیگنال: اگر در دسترس نباشد
        باید به مظنه‌ی سطح‌اول برگردیم، نه اینکه کل پاس رصد بخوابد.
        """
        try:
            return self.get_order_book(ins_code, symbol)
        except Exception as exc:  # عمق نباید پاس رصد را بخواباند
            logger.warning("عمق مظنه %s دریافت نشد: %s", symbol or ins_code, exc)
            return None

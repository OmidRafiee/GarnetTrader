"""تقویم معاملاتی بورس تهران — جایگزین حدسِ «شنبه تا چهارشنبه».

**چرا این ماژول لازم شد**

`is_market_open` قبلی فقط پنجشنبه و جمعه را تعطیل می‌دانست. نتیجه‌اش دو
خطای واقعی بود:

1. در تعطیلات رسمی (نوروز، تاسوعا، عاشورا و …) ربات فکر می‌کرد بازار باز
   است، پاس رصد می‌زد و روی مظنه‌ی **دیروز** سیگنال می‌ساخت. سیگنالی که
   پشتش قیمت بیات باشد، از سیگنال نداشتن بدتر است.
2. سالی حدود ۲۵ روز تعطیلی رسمی داریم؛ یعنی تقریباً ۱۰٪ پاس‌ها روی داده‌ی
   کهنه اجرا می‌شد.

**منبع حقیقت: خودِ بازار، نه یک جدول دستی**

تعطیلات ایران دو دسته‌اند: شمسی-ثابت (مثل ۱۳ فروردین) که هر سال سر جای
خودند، و قمری-متحرک (تاسوعا، عاشورا، اربعین و …) که هر سال ~۱۱ روز جلو
می‌آیند. نگهداری دستیِ دسته‌ی دوم یعنی هر سال یک بدهی فنی تازه.

پس منبع اصلی، **تاریخچه‌ی واقعی معاملات TSETMC** است: هر روزِ کاریِ
غیرتعطیل که در تاریخچه‌ی یک نماد پرمعامله نیامده، تعطیل بوده. جدول
شمسی-ثابت فقط برای روزهای **آینده** (که هنوز تاریخچه ندارند) به کار
می‌رود.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# تبدیل میلادی ↔ شمسی (بدون وابستگی خارجی، مطابق الگوریتم بیرشک)
# ----------------------------------------------------------------------
_BREAKS = (
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060,
    2097, 2192, 2262, 2324, 2394, 2456, 3178,
)


def _jalali_leap_and_offset(jy: int) -> tuple[int, int, int]:
    """(کبیسه، سالِ میلادیِ متناظر، روزِ مارس که ۱ فروردین در آن می‌افتد)."""
    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]
    if jy < jp or jy >= _BREAKS[-1]:
        raise ValueError(f"سال شمسی خارج از بازه پشتیبانی: {jy}")

    jump = 0
    for jm in _BREAKS[1:]:
        jump = jm - jp
        if jy < jm:
            break
        leap_j += jump // 33 * 8 + (jump % 33) // 4
        jp = jm

    n = jy - jp
    leap_j += n // 33 * 8 + ((n % 33) + 3) // 4
    if jump % 33 == 4 and jump - n == 4:
        leap_j += 1

    leap_g = (gy // 4) - ((gy // 100 + 1) * 3 // 4) - 150
    march = 20 + leap_j - leap_g

    if jump - n < 6:
        n = n - jump + (jump + 4) // 33 * 33
    leap = ((n + 1) % 33 - 1) % 4
    if leap == -1:
        leap = 4
    return leap, gy, march


def is_jalali_leap(jy: int) -> bool:
    """آیا سال شمسی کبیسه است (اسفند ۳۰ روزه)؟"""
    return _jalali_leap_and_offset(jy)[0] == 0


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    """تاریخ شمسی → میلادی."""
    _, gy, march = _jalali_leap_and_offset(jy)
    days = (jm - 1) * 31 if jm <= 6 else (jm - 7) * 30 + 186
    return date(gy, 3, march) + timedelta(days=days + jd - 1)


def gregorian_to_jalali(g: date) -> tuple[int, int, int]:
    """تاریخ میلادی → (سال، ماه، روز) شمسی."""
    jy = g.year - 621
    _, _, march = _jalali_leap_and_offset(jy)
    start = date(g.year, 3, march)
    if g < start:
        jy -= 1
        _, _, march = _jalali_leap_and_offset(jy)
        start = date(g.year - 1, 3, march)
    n = (g - start).days
    if n < 186:
        return jy, n // 31 + 1, n % 31 + 1
    n -= 186
    return jy, n // 30 + 7, n % 30 + 1


def format_jalali(g: date) -> str:
    """`1404/06/15` — برای لاگ و نمایش در داشبورد."""
    jy, jm, jd = gregorian_to_jalali(g)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


# ----------------------------------------------------------------------
# تعطیلات شمسی-ثابت (فقط برای روزهای آینده که تاریخچه ندارند)
# ----------------------------------------------------------------------
#: (ماه، روز) شمسی — تعطیلات رسمیِ غیرقمری که هر سال سر جای خودند.
FIXED_JALALI_HOLIDAYS: frozenset[tuple[int, int]] = frozenset(
    {
        (1, 1), (1, 2), (1, 3), (1, 4),  # نوروز
        (1, 12),                          # روز جمهوری اسلامی
        (1, 13),                          # سیزده‌بدر
        (3, 14),                          # رحلت امام خمینی
        (3, 15),                          # قیام ۱۵ خرداد
        (11, 22),                         # پیروزی انقلاب
        (12, 29),                         # ملی شدن صنعت نفت
    }
)


def is_fixed_holiday(day: date) -> bool:
    """آیا این روز یکی از تعطیلات رسمیِ **شمسی-ثابت** است؟"""
    _, jm, jd = gregorian_to_jalali(day)
    return (jm, jd) in FIXED_JALALI_HOLIDAYS


def is_weekend(day: date) -> bool:
    """پنجشنبه و جمعه، تعطیل هفتگی بازار تهران.

    `weekday()`: دوشنبه=۰ … پنجشنبه=۳، جمعه=۴.
    """
    return day.weekday() in (3, 4)


# ----------------------------------------------------------------------
# تقویم
# ----------------------------------------------------------------------
class HistorySource(Protocol):
    """هرچه بتواند تاریخچه‌ی روزانه بدهد — یعنی خودِ `MarketDataClient`."""

    def get_history(self, symbol: str, days: int = ...) -> list: ...


@dataclass(frozen=True)
class MarketSession:
    """ساعت جلسه‌ی معاملاتی."""

    open: time = time(9, 0)
    close: time = time(12, 30)

    def contains(self, moment: time) -> bool:
        return self.open <= moment <= self.close


class TradingCalendar:
    """می‌گوید یک روز، روز معاملاتی هست یا نه.

    ترتیب تصمیم عمداً این است:

    1. **تعطیلی هفتگی** — قطعی، بدون نیاز به هیچ داده‌ای.
    2. **تعطیلات یادگرفته‌شده از تاریخچه‌ی واقعی** — معتبرترین منبع، چون
       خودِ بازار گفته آن روز معامله‌ای نبوده.
    3. **جدول شمسی-ثابت** — فقط جایی که هنوز تاریخچه‌ای وجود ندارد
       (روزهای آینده).

    Args:
        holidays: تعطیلات از پیش‌ معلوم (مثلاً از yaml).
        cache_path: مسیر ذخیره‌ی تعطیلات یادگرفته‌شده، تا هر بار از نو
            یاد گرفته نشود.
        session: ساعت جلسه‌ی معاملاتی.
    """

    def __init__(
        self,
        holidays: Iterable[date] | None = None,
        cache_path: str | Path | None = None,
        session: MarketSession | None = None,
    ) -> None:
        self.session = session or MarketSession()
        self._cache_path = Path(cache_path) if cache_path else None
        self._holidays: set[date] = set(holidays or ())
        #: بازه‌ای که تاریخچه‌ی واقعی برایش دیده‌ایم؛ بیرونش نمی‌توانیم
        #: از «نبودِ روز در تاریخچه» نتیجه بگیریم تعطیل بوده.
        self._observed_from: date | None = None
        self._observed_to: date | None = None
        #: یادگیریِ عقب‌انداخته‌شده؛ `defer_learning` پرش می‌کند
        self._learner: Callable[[TradingCalendar], None] | None = None
        self._load_cache()

    # -- تداوم روی دیسک ---------------------------------------------
    def _load_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            self._holidays |= {date.fromisoformat(d) for d in data.get("holidays", [])}
            if data.get("observed_from"):
                self._observed_from = date.fromisoformat(data["observed_from"])
            if data.get("observed_to"):
                self._observed_to = date.fromisoformat(data["observed_to"])
        except (OSError, ValueError, TypeError) as exc:
            # کش خراب نباید ربات را بخواباند؛ از نو یاد می‌گیریم.
            logger.warning("کش تقویم معاملاتی خوانده نشد (%s)؛ نادیده گرفته شد.", exc)

    def save(self) -> None:
        """تعطیلات یادگرفته‌شده را ذخیره می‌کند (اگر مسیر کش داده شده باشد)."""
        if self._cache_path is None:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "holidays": sorted(d.isoformat() for d in self._holidays),
            "observed_from": self._observed_from.isoformat() if self._observed_from else None,
            "observed_to": self._observed_to.isoformat() if self._observed_to else None,
        }
        self._cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- یادگیری از بازار واقعی --------------------------------------
    def learn_from_history(self, trading_days: Iterable[date]) -> int:
        """از روزهای واقعیِ معامله، تعطیلات را استخراج می‌کند.

        هر روزِ غیرآخرهفته که **بین** اولین و آخرین روز معاملاتیِ دیده‌شده
        باشد ولی خودش معامله نداشته، تعطیل بوده. قید «بین» مهم است: بیرون
        این بازه، نبودِ داده فقط یعنی نمی‌دانیم.

        Returns:
            تعداد تعطیلات تازه‌ای که یاد گرفت.
        """
        days = sorted(set(trading_days))
        if len(days) < 2:
            return 0

        first, last = days[0], days[-1]
        known = set(days)
        learned = 0
        cursor = first
        while cursor <= last:
            if (
                not is_weekend(cursor)
                and cursor not in known
                and cursor not in self._holidays
            ):
                self._holidays.add(cursor)
                learned += 1
            cursor += timedelta(days=1)

        self._observed_from = (
            first if self._observed_from is None else min(self._observed_from, first)
        )
        self._observed_to = (
            last if self._observed_to is None else max(self._observed_to, last)
        )
        if learned:
            logger.info(
                "تقویم معاملاتی: %d روز تعطیل از تاریخچه‌ی واقعی یاد گرفته شد.", learned
            )
        return learned

    def learn_from_client(
        self, client: HistorySource, symbol: str, days: int = 365
    ) -> int:
        """تاریخچه‌ی یک نماد پرمعامله را می‌گیرد و تعطیلات را یاد می‌گیرد.

        خطای شبکه را **بالا نمی‌برد**: نداشتن تقویم دقیق نباید کل پاس رصد
        را بخواباند؛ در بدترین حالت به رفتار قبلی (فقط آخرهفته) برمی‌گردیم.
        """
        try:
            candles = client.get_history(symbol, days=days)
        except Exception as exc:  # منبع تقویم نباید حیاتی باشد
            logger.warning("تقویم معاملاتی از %s ساخته نشد: %s", symbol, exc)
            return 0
        return self.learn_from_history(c.date for c in candles)

    # -- پرسش‌ها ------------------------------------------------------
    @property
    def holidays(self) -> frozenset[date]:
        """تعطیلات شناخته‌شده (دستی + یادگرفته‌شده)."""
        return frozenset(self._holidays)

    @property
    def observed_range(self) -> tuple[date | None, date | None]:
        """بازه‌ای که تاریخچه‌ی واقعی برایش دیده شده."""
        return self._observed_from, self._observed_to

    def add_holidays(self, days: Iterable[date]) -> None:
        """افزودن تعطیلی دستی (مثلاً تعطیلی اضطراری اعلام‌شده)."""
        self._holidays |= set(days)

    def defer_learning(self, learner: Callable[[TradingCalendar], None]) -> None:
        """یادگیری را تا **اولین پرسش واقعی** عقب می‌اندازد.

        بدون این، ساختنِ تقویم یعنی یک سال تاریخچه از شبکه — و هر کسی که
        فقط wiring می‌خواهد (تست‌ها، `--dry-run`، ساختِ داشبورد) هزینه‌اش
        را می‌داد. یک بار اتفاق می‌افتد؛ آخرهفته حتی همان یک بار را هم
        لازم ندارد، چون قبلش جواب داده می‌شود.
        """
        self._learner = learner

    def _learn_if_needed(self) -> None:
        """قرارداد مهم: حتی اگر یادگیری **شکست بخورد**، دوباره تلاش نمی‌شود.

        وگرنه در نبودِ شبکه، هر پرسشِ تقویم یک تایم‌اوت می‌شد و یک پاس
        رصد ساده دقیقه‌ها طول می‌کشید.
        """
        learner, self._learner = self._learner, None
        if learner is not None:
            learner(self)

    def is_holiday(self, day: date) -> bool:
        """آیا بازار در این روز تعطیل است؟"""
        if is_weekend(day):
            # آخرهفته قطعی است و به هیچ داده‌ای نیاز ندارد — پس اینجا
            # عمداً **قبل از** یادگیری جواب داده می‌شود.
            return True

        self._learn_if_needed()
        if day in self._holidays:
            return True
        # جدول شمسی-ثابت فقط جایی حرف می‌زند که تاریخچه‌ی واقعی نداریم؛
        # داخل بازه‌ی دیده‌شده، سکوتِ جدول یعنی بازار باز بوده.
        if self._observed_to is not None and day <= self._observed_to:
            return False
        return is_fixed_holiday(day)

    def is_trading_day(self, day: date) -> bool:
        return not self.is_holiday(day)

    def is_open(self, now: datetime | None = None) -> bool:
        """آیا همین حالا جلسه‌ی معاملاتی باز است؟"""
        now = now or datetime.now()
        return self.is_trading_day(now.date()) and self.session.contains(now.time())

    def next_trading_day(self, day: date | None = None) -> date:
        """اولین روز معاملاتیِ **بعد از** روز داده‌شده."""
        cursor = (day or date.today()) + timedelta(days=1)
        for _ in range(60):  # نوروز طولانی‌ترین تعطیلی است؛ ۶۰ روز کافی است
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        raise RuntimeError("هیچ روز معاملاتی‌ای در ۶۰ روز آینده پیدا نشد.")

    def trading_days_between(self, start: date, end: date) -> int:
        """تعداد روزهای معاملاتی در بازه (شاملِ دو سر بازه)."""
        if end < start:
            return 0
        cursor, count = start, 0
        while cursor <= end:
            if self.is_trading_day(cursor):
                count += 1
            cursor += timedelta(days=1)
        return count

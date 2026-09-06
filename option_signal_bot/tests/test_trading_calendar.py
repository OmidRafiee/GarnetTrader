"""تست تقویم معاملاتی — تبدیل تاریخ، یادگیری تعطیلات، و اعمال در `is_market_open`.

تست‌های یادگیری روی **تاریخچه‌ی واقعیِ ضبط‌شده‌ی TSETMC** اجرا می‌شوند،
نه روی روزهای ساختگی. تقویمی که فقط با داده‌ی دست‌ساز تست شود، دقیقاً
همان چیزی است که این ماژول قرار بود جایگزینش کند.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from data.market_data_client import Candle, MarketDataClient, Quote
from data.tsetmc_option_chain_client import parse_tsetmc_date
from market.trading_calendar import (
    FIXED_JALALI_HOLIDAYS,
    MarketSession,
    TradingCalendar,
    format_jalali,
    gregorian_to_jalali,
    is_fixed_holiday,
    is_weekend,
    jalali_to_gregorian,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "history"


# ----------------------------------------------------------------------
# تبدیل تاریخ
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "gregorian, jalali",
    [
        (date(2026, 3, 21), (1405, 1, 1)),  # نوروز ۱۴۰۵
        (date(2025, 3, 21), (1404, 1, 1)),  # نوروز ۱۴۰۴
        (date(2024, 3, 20), (1403, 1, 1)),  # نوروز ۱۴۰۳ (یک روز زودتر)
        (date(2025, 1, 1), (1403, 10, 12)),
        (date(2021, 3, 21), (1400, 1, 1)),
        (date(2000, 1, 1), (1378, 10, 11)),
    ],
)
def test_gregorian_to_jalali_matches_known_dates(gregorian, jalali):
    assert gregorian_to_jalali(gregorian) == jalali


@pytest.mark.parametrize(
    "gregorian, jalali",
    [
        (date(2026, 3, 21), (1405, 1, 1)),
        (date(2024, 3, 20), (1403, 1, 1)),
        (date(2025, 1, 1), (1403, 10, 12)),
    ],
)
def test_jalali_to_gregorian_matches_known_dates(gregorian, jalali):
    assert jalali_to_gregorian(*jalali) == gregorian


def test_conversion_roundtrips_over_thirty_years():
    """یک روزِ ناسازگار کافی است تا تشخیص تعطیلی آینده غلط شود."""
    day, last = date(2000, 1, 1), date(2030, 1, 1)
    while day < last:
        assert jalali_to_gregorian(*gregorian_to_jalali(day)) == day
        day += timedelta(days=1)


def test_format_jalali():
    assert format_jalali(date(2026, 3, 21)) == "1405/01/01"


def test_jalali_year_out_of_range_raises():
    with pytest.raises(ValueError):
        gregorian_to_jalali(date(500, 1, 1))


# ----------------------------------------------------------------------
# قواعد پایه
# ----------------------------------------------------------------------
def test_weekend_is_thursday_and_friday():
    assert is_weekend(date(2026, 9, 3))  # پنجشنبه
    assert is_weekend(date(2026, 9, 4))  # جمعه
    assert not is_weekend(date(2026, 9, 5))  # شنبه
    assert not is_weekend(date(2026, 9, 2))  # چهارشنبه


def test_fixed_holiday_table_covers_nowruz_and_revolution_day():
    assert is_fixed_holiday(date(2026, 3, 21))  # ۱ فروردین
    assert is_fixed_holiday(date(2026, 4, 2))  # ۱۳ فروردین (سیزده‌بدر)
    assert not is_fixed_holiday(date(2026, 3, 17))  # روز کاری معمولی
    assert (11, 22) in FIXED_JALALI_HOLIDAYS


# ----------------------------------------------------------------------
# یادگیری از تاریخچه‌ی واقعی
# ----------------------------------------------------------------------
def _real_trading_days() -> list[date]:
    """روزهای معاملاتی از پاسخ **واقعی** ضبط‌شده‌ی TSETMC."""
    path = next(FIXTURE_DIR.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    days = [parse_tsetmc_date(row["dEven"]) for row in payload["closingPriceDaily"]]
    return sorted(d for d in days if d is not None)


def test_learns_nowruz_from_real_history():
    """نوروز ۱۴۰۵ باید از تاریخچه‌ی واقعی بیرون بیاید، بدون هیچ جدول دستی."""
    calendar = TradingCalendar()
    learned = calendar.learn_from_history(_real_trading_days())

    assert learned > 0
    for day in (date(2026, 3, 21), date(2026, 3, 22), date(2026, 3, 23)):
        assert calendar.is_holiday(day), f"{format_jalali(day)} باید تعطیل باشد"


def test_learns_lunar_holiday_the_fixed_table_cannot_know():
    """تعطیلات قمری در جدول ثابت نیستند؛ فقط از بازار واقعی درمی‌آیند."""
    calendar = TradingCalendar()
    calendar.learn_from_history(_real_trading_days())

    lunar = [
        d for d in calendar.holidays if not is_fixed_holiday(d) and not is_weekend(d)
    ]
    assert lunar, "هیچ تعطیلی قمری‌ای یاد گرفته نشد"


def test_learning_ignores_weekends():
    calendar = TradingCalendar()
    calendar.learn_from_history(_real_trading_days())
    assert not any(is_weekend(d) for d in calendar.holidays)


def test_learning_stays_inside_observed_range():
    """بیرون بازه‌ی دیده‌شده، «نبودِ داده» یعنی نمی‌دانیم — نه تعطیل."""
    days = _real_trading_days()
    calendar = TradingCalendar()
    calendar.learn_from_history(days)

    first, last = calendar.observed_range
    assert first == days[0] and last == days[-1]
    assert all(first <= d <= last for d in calendar.holidays)


def test_learning_needs_at_least_two_days():
    calendar = TradingCalendar()
    assert calendar.learn_from_history([date(2026, 9, 5)]) == 0
    assert calendar.learn_from_history([]) == 0
    assert calendar.holidays == frozenset()


def test_observed_range_widens_across_calls():
    calendar = TradingCalendar()
    calendar.learn_from_history([date(2026, 9, 5), date(2026, 9, 6)])
    calendar.learn_from_history([date(2026, 8, 1), date(2026, 8, 2)])
    assert calendar.observed_range == (date(2026, 8, 1), date(2026, 9, 6))


def test_history_wins_over_fixed_table_inside_observed_range():
    """اگر بازار در روزی از جدولِ ثابت واقعاً معامله داشته، بازار باز بوده.

    جدول دستی همیشه یک قدم عقب است؛ داده‌ی واقعی حرف آخر را می‌زند.
    """
    nowruz = date(2026, 3, 21)
    assert is_fixed_holiday(nowruz)

    calendar = TradingCalendar()
    # سناریوی فرضی: بازار در همان روز معامله داشته است
    calendar.learn_from_history([date(2026, 3, 18), nowruz, date(2026, 3, 25)])
    assert calendar.is_trading_day(nowruz)


def test_fixed_table_applies_beyond_observed_range():
    """برای آینده هنوز تاریخچه‌ای نیست؛ آنجا جدول ثابت تنها راهنماست."""
    calendar = TradingCalendar()
    calendar.learn_from_history([date(2025, 3, 1), date(2025, 3, 2)])
    assert calendar.is_holiday(date(2026, 3, 21))  # نوروز ۱۴۰۵، بعدِ بازه


# ----------------------------------------------------------------------
# پرسش‌ها
# ----------------------------------------------------------------------
def test_manual_holidays_are_respected():
    emergency = date(2026, 9, 5)  # شنبه
    calendar = TradingCalendar(holidays=[emergency])
    assert calendar.is_holiday(emergency)


def test_add_holidays_after_construction():
    calendar = TradingCalendar()
    day = date(2026, 9, 6)
    assert calendar.is_trading_day(day)
    calendar.add_holidays([day])
    assert calendar.is_holiday(day)


def test_is_open_respects_session_hours():
    calendar = TradingCalendar()
    trading_day = date(2026, 9, 6)  # یکشنبه
    assert calendar.is_trading_day(trading_day)

    assert calendar.is_open(datetime.combine(trading_day, time(10, 0)))
    assert not calendar.is_open(datetime.combine(trading_day, time(8, 59)))
    assert not calendar.is_open(datetime.combine(trading_day, time(12, 31)))


def test_is_open_false_on_holiday_even_during_session():
    calendar = TradingCalendar(holidays=[date(2026, 9, 6)])
    assert not calendar.is_open(datetime(2026, 9, 6, 10, 0))


def test_custom_session_hours():
    calendar = TradingCalendar(session=MarketSession(open=time(9, 30), close=time(13, 0)))
    assert not calendar.is_open(datetime(2026, 9, 6, 9, 15))
    assert calendar.is_open(datetime(2026, 9, 6, 12, 45))


def test_next_trading_day_skips_weekend():
    # چهارشنبه ۲۰۲۶-۰۹-۰۲ → بعدی باید شنبه ۲۰۲۶-۰۹-۰۵ باشد
    calendar = TradingCalendar()
    assert calendar.next_trading_day(date(2026, 9, 2)) == date(2026, 9, 5)


def test_next_trading_day_skips_learned_holiday():
    calendar = TradingCalendar(holidays=[date(2026, 9, 5)])
    assert calendar.next_trading_day(date(2026, 9, 2)) == date(2026, 9, 6)


def test_trading_days_between_counts_only_open_days():
    calendar = TradingCalendar()
    # شنبه تا چهارشنبه = ۵ روز کاری، پنجشنبه و جمعه حذف
    assert calendar.trading_days_between(date(2026, 9, 5), date(2026, 9, 11)) == 5
    assert calendar.trading_days_between(date(2026, 9, 11), date(2026, 9, 5)) == 0


# ----------------------------------------------------------------------
# کش روی دیسک
# ----------------------------------------------------------------------
def test_cache_roundtrip(tmp_path):
    path = tmp_path / "calendar.json"
    first = TradingCalendar(cache_path=path)
    first.learn_from_history(_real_trading_days())
    first.save()

    second = TradingCalendar(cache_path=path)
    assert second.holidays == first.holidays
    assert second.observed_range == first.observed_range


def test_corrupt_cache_is_ignored_not_fatal(tmp_path):
    """کش خراب نباید ربات را بخواباند — فقط دوباره یاد می‌گیریم."""
    path = tmp_path / "calendar.json"
    path.write_text("{ this is not json", encoding="utf-8")

    calendar = TradingCalendar(cache_path=path)
    assert calendar.holidays == frozenset()
    assert calendar.is_trading_day(date(2026, 9, 6))


def test_save_without_cache_path_is_noop():
    TradingCalendar().save()  # نباید استثنا بدهد


# ----------------------------------------------------------------------
# یادگیری از کلاینت داده
# ----------------------------------------------------------------------
class _StubClient(MarketDataClient):
    def __init__(self, days: list[date] | None = None, error: Exception | None = None):
        self._days = days or []
        self._error = error

    def get_quote(self, symbol: str) -> Quote:  # pragma: no cover - لازم نیست
        raise NotImplementedError

    def get_history(self, symbol: str, days: int = 90) -> list[Candle]:
        if self._error is not None:
            raise self._error
        return [Candle(d, 1.0, 1.0, 1.0, 1.0, 1.0) for d in self._days]


def test_learn_from_client_uses_candle_dates():
    days = _real_trading_days()
    calendar = TradingCalendar()
    assert calendar.learn_from_client(_StubClient(days), "خودرو") > 0
    assert calendar.is_holiday(date(2026, 3, 21))


def test_learn_from_client_survives_network_failure():
    """نبودِ تقویم نباید کل پاس رصد را بخواباند."""
    calendar = TradingCalendar()
    assert calendar.learn_from_client(_StubClient(error=RuntimeError("شبکه")), "خودرو") == 0
    assert calendar.holidays == frozenset()


# ----------------------------------------------------------------------
# اتصال به `MarketDataClient.is_market_open`
# ----------------------------------------------------------------------
def test_market_client_without_calendar_keeps_old_behaviour():
    client = _StubClient()
    assert client.trading_calendar is None
    assert client.is_market_open(datetime(2026, 9, 6, 10, 0))  # یکشنبه
    assert not client.is_market_open(datetime(2026, 9, 4, 10, 0))  # جمعه


def test_market_client_with_calendar_respects_holidays():
    client = _StubClient()
    client.trading_calendar = TradingCalendar(holidays=[date(2026, 9, 6)])
    assert not client.is_market_open(datetime(2026, 9, 6, 10, 0))
    assert client.is_market_open(datetime(2026, 9, 7, 10, 0))

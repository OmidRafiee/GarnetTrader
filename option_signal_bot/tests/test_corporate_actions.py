"""تست تعدیل رویداد شرکتی — روی پاسخ **واقعیِ** ضبط‌شده‌ی TSETMC.

فیکسچر سه حالت واقعی دارد:

* `خودرو` — افزایش سرمایه‌ی ۷۵۰٪ در ۲۰۲۵-۰۴-۲۲ که در تاریخچه‌ی خام مثل
  یک ریزش ۸۴٪ دیده می‌شود.
* `وبملت` — همان رویداد **دو روز پشت‌سرهم** تکرار شده. اگر دوبار اعمال
  شود، استرایک نصفِ نصف می‌شود و هر سیگنالِ آن نماد بی‌صدا خراب است.
* `اهرم` — صندوق، بدون هیچ رویداد.
"""

from __future__ import annotations

import json
from datetime import date
from itertools import pairwise
from pathlib import Path

import pytest

from data.market_data_client import Candle
from data.option_chain_client import OptionContract
from market.corporate_actions import (
    CorporateAction,
    CorporateActionLog,
    fetch_corporate_actions,
    try_fetch_corporate_actions,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tsetmc_share_change.json"


@pytest.fixture(scope="module")
def payloads() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _log(payloads, symbol) -> CorporateActionLog:
    """لاگ را از پاسخ واقعی می‌سازد، بدون شبکه."""
    entry = payloads[symbol]
    actions = []
    seen = set()
    for row in entry["payload"]["instrumentShareChange"]:
        action = CorporateAction.from_share_change(symbol, row)
        if action and (action.shares_old, action.shares_new) not in seen:
            seen.add((action.shares_old, action.shares_new))
            actions.append(action)
    return CorporateActionLog(actions)


def _contract(strike=10_000.0, size=1000) -> OptionContract:
    return OptionContract(
        symbol="ضخود6053",
        underlying="خودرو",
        option_type="call",
        strike=strike,
        expiry=date(2026, 12, 1),
        contract_size=size,
    )


# ----------------------------------------------------------------------
# نگاشت پاسخ خام
# ----------------------------------------------------------------------
def test_parses_real_capital_increase(payloads):
    log = _log(payloads, "خودرو")
    assert len(log.actions) == 6

    event = next(a for a in log.actions if a.effective_date == date(2025, 4, 22))
    assert event.is_capital_increase
    assert event.shares_old == 301_656_068_000.0
    assert event.shares_new == 2_563_746_068_000.0
    assert event.ratio == pytest.approx(301_656_068_000 / 2_563_746_068_000)


def test_actions_are_sorted_by_date(payloads):
    dates = [a.effective_date for a in _log(payloads, "خودرو").actions]
    assert dates == sorted(dates)


def test_symbol_without_events(payloads):
    """صندوق افزایش سرمایه ندارد؛ لاگ خالی یعنی هیچ تعدیلی اعمال نمی‌شود."""
    log = _log(payloads, "اهرم")
    assert log.actions == ()
    assert log.net_ratio(date(2020, 1, 1)) == 1.0


@pytest.mark.parametrize(
    "row",
    [
        {},
        {"numberOfShareOld": 0, "numberOfShareNew": 100, "dEven": 20250422},
        {"numberOfShareOld": 100, "numberOfShareNew": 0, "dEven": 20250422},
        {"numberOfShareOld": 100, "numberOfShareNew": 100, "dEven": 20250422},
        {"numberOfShareOld": "x", "numberOfShareNew": 100, "dEven": 20250422},
        {"numberOfShareOld": 100, "numberOfShareNew": 200, "dEven": None},
    ],
)
def test_unusable_rows_are_skipped(row):
    """رویدادی که نسبتش قابل محاسبه نیست، بهتر است اصلاً اعمال نشود."""
    assert CorporateAction.from_share_change("خودرو", row) is None


def test_duplicate_event_on_consecutive_days_is_deduped(monkeypatch, payloads):
    """وبملت واقعاً یک رویداد را دو روز پشت‌سرهم گزارش می‌کند.

    اعمال دوبار یعنی تعدیل مضاعف — استرایک نصفِ نصف. کلید یکتایی باید خودِ
    گذارِ سهام باشد، نه تاریخ.
    """
    monkeypatch.setattr(
        "market.corporate_actions.fetch_json",
        lambda *a, **k: payloads["وبملت"]["payload"],
    )
    log = fetch_corporate_actions("123", "وبملت")

    raw = len(payloads["وبملت"]["payload"]["instrumentShareChange"])
    assert raw == 13
    assert len(log.actions) == 12, "رویداد تکراری باید حذف شود"

    transitions = [(a.shares_old, a.shares_new) for a in log.actions]
    assert len(transitions) == len(set(transitions))


# ----------------------------------------------------------------------
# اعمال روی قرارداد
# ----------------------------------------------------------------------
def test_capital_increase_preserves_total_value():
    """قید اصلی تعدیل: استرایک × اندازه باید ثابت بماند."""
    action = CorporateAction("خودرو", date(2025, 4, 22), "capital_increase", ratio=0.5)
    before = _contract(strike=10_000.0, size=1000)
    after = action.apply(before)

    assert after.strike == 5_000.0
    assert after.contract_size == 2000
    assert after.strike * after.contract_size == before.strike * before.contract_size


def test_capital_increase_with_real_ratio(payloads):
    log = _log(payloads, "خودرو")
    event = next(a for a in log.actions if a.effective_date == date(2025, 4, 22))

    before = _contract(strike=10_000.0, size=1000)
    after = event.apply(before)

    assert after.strike == pytest.approx(10_000 * event.ratio)
    assert after.contract_size == round(1000 / event.ratio)
    # ارزش کل با گردکردنِ اندازه کمی جابه‌جا می‌شود، ولی نه بیشتر از ۰٫۱٪
    ratio = (after.strike * after.contract_size) / (before.strike * before.contract_size)
    assert ratio == pytest.approx(1.0, rel=1e-3)


def test_apply_does_not_mutate_the_input():
    action = CorporateAction("خودرو", date(2025, 4, 22), "capital_increase", ratio=0.5)
    before = _contract(strike=10_000.0, size=1000)
    action.apply(before)
    assert before.strike == 10_000.0 and before.contract_size == 1000


def test_contract_size_never_rounds_to_zero():
    """نسبت بزرگ نباید اندازه‌ی قرارداد را صفر کند — تقسیم بر صفر در راه است."""
    action = CorporateAction("X", date(2025, 1, 1), "capital_increase", ratio=100_000.0)
    assert action.apply(_contract(size=1000)).contract_size >= 1


def test_cash_dividend_lowers_strike_only():
    action = CorporateAction("خودرو", date(2025, 6, 1), "cash_dividend", dividend=500.0)
    before = _contract(strike=10_000.0, size=1000)
    after = action.apply(before)

    assert after.strike == 9_500.0
    assert after.contract_size == before.contract_size


def test_cash_dividend_never_makes_strike_negative():
    action = CorporateAction("X", date(2025, 6, 1), "cash_dividend", dividend=99_000.0)
    assert action.apply(_contract(strike=10_000.0)).strike == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "capital_increase", "ratio": 0},
        {"kind": "capital_increase", "ratio": -1},
        {"kind": "capital_increase"},
        {"kind": "cash_dividend", "dividend": -5},
        {"kind": "cash_dividend"},
        {"kind": "stock_split"},
    ],
)
def test_invalid_actions_are_rejected_at_construction(kwargs):
    with pytest.raises(ValueError):
        CorporateAction("X", date(2025, 1, 1), **kwargs)


# ----------------------------------------------------------------------
# لاگ: کدام رویدادها اعمال می‌شوند
# ----------------------------------------------------------------------
def test_only_events_after_issue_date_apply():
    """قرارداد منتشرشده بعد از رویداد، از اول تعدیل‌شده است.

    تعدیل دوباره‌اش یعنی خراب‌کردنش — این مرز، قلبِ درستیِ ماژول است.
    """
    log = CorporateActionLog(
        [CorporateAction("X", date(2025, 4, 22), "capital_increase", ratio=0.5)]
    )
    contract = _contract(strike=10_000.0)

    before_event = log.adjust(contract, date(2025, 1, 1), today=date(2025, 12, 1))
    after_event = log.adjust(contract, date(2025, 6, 1), today=date(2025, 12, 1))

    assert before_event.strike == 5_000.0, "قرارداد قدیمی باید تعدیل شود"
    assert after_event.strike == 10_000.0, "قرارداد جدید نباید تعدیل شود"


def test_future_events_are_not_applied_yet():
    log = CorporateActionLog(
        [CorporateAction("X", date(2026, 4, 22), "capital_increase", ratio=0.5)]
    )
    result = log.adjust(_contract(strike=10_000.0), date(2025, 1, 1), today=date(2025, 12, 1))
    assert result.strike == 10_000.0


def test_multiple_events_compound_in_order():
    log = CorporateActionLog(
        [
            CorporateAction("X", date(2025, 3, 1), "capital_increase", ratio=0.5),
            CorporateAction("X", date(2025, 9, 1), "capital_increase", ratio=0.5),
        ]
    )
    result = log.adjust(_contract(strike=10_000.0), date(2025, 1, 1), today=date(2025, 12, 1))
    assert result.strike == 2_500.0


def test_since_filters_the_window():
    actions = [
        CorporateAction("X", date(2025, 3, 1), "capital_increase", ratio=0.5),
        CorporateAction("X", date(2025, 9, 1), "capital_increase", ratio=0.5),
    ]
    log = CorporateActionLog(actions)
    assert len(log.since(date(2025, 1, 1))) == 2
    assert len(log.since(date(2025, 5, 1))) == 1
    assert len(log.since(date(2025, 1, 1), until=date(2025, 5, 1))) == 1
    # مرز: خودِ روز رویداد جزو «بعد از» مرجع نیست
    assert log.since(date(2025, 3, 1))[0].effective_date == date(2025, 9, 1)


def test_add_keeps_the_log_sorted():
    log = CorporateActionLog(
        [CorporateAction("X", date(2025, 9, 1), "capital_increase", ratio=0.5)]
    )
    log.add(CorporateAction("X", date(2025, 3, 1), "capital_increase", ratio=0.8))
    assert [a.effective_date for a in log.actions] == [date(2025, 3, 1), date(2025, 9, 1)]


def test_net_ratio_multiplies_across_events(payloads):
    log = _log(payloads, "خودرو")
    expected = 1.0
    for action in log.actions:
        if action.effective_date > date(2019, 1, 1):
            expected *= action.ratio
    assert log.net_ratio(date(2019, 1, 1)) == pytest.approx(expected)


def test_net_ratio_ignores_cash_dividends():
    """سود نقدی نسبت ندارد؛ واردکردنش در ضریب قیمت غلط است."""
    log = CorporateActionLog(
        [CorporateAction("X", date(2025, 3, 1), "cash_dividend", dividend=500.0)]
    )
    assert log.net_ratio(date(2025, 1, 1)) == 1.0


# ----------------------------------------------------------------------
# تعدیل تاریخچه — جایی که خطا واقعاً گران بود
# ----------------------------------------------------------------------
def _candles(prices: list[tuple[date, float]]) -> list[Candle]:
    return [Candle(d, p, p, p, p, 1000.0) for d, p in prices]


def test_history_adjustment_removes_the_phantom_crash():
    """قبل و بعد از افزایش سرمایه باید در یک مقیاس باشند.

    خودرو در ۲۰۲۵-۰۴-۲۲ در تاریخچه‌ی خام ~۸۴٪ افت نشان می‌دهد که هرگز
    رخ نداده. بدون تعدیل، هر استراتژی تکنیکالی آن را سیگنال نزولی
    می‌فهمد و کل بک‌تست بی‌معنا می‌شود.
    """
    log = CorporateActionLog(
        [CorporateAction("خودرو", date(2025, 4, 22), "capital_increase", ratio=0.1177)]
    )
    raw = _candles([(date(2025, 4, 21), 8500.0), (date(2025, 4, 23), 1000.0)])

    raw_move = (raw[1].close - raw[0].close) / raw[0].close
    assert raw_move < -0.8, "تاریخچه‌ی خام باید ریزش ساختگی داشته باشد"

    adjusted = log.adjust_history(raw)
    adj_move = (adjusted[1].close - adjusted[0].close) / adjusted[0].close
    assert abs(adj_move) < 0.05, "بعد از تعدیل، ریزش ساختگی باید ناپدید شود"


def test_history_adjustment_scales_all_ohlc_fields():
    log = CorporateActionLog(
        [CorporateAction("X", date(2025, 4, 22), "capital_increase", ratio=0.5)]
    )
    before = [Candle(date(2025, 4, 21), 100.0, 110.0, 90.0, 105.0, 7.0)]
    after = log.adjust_history(before)[0]

    assert (after.open, after.high, after.low, after.close) == (50.0, 55.0, 45.0, 52.5)
    assert after.volume == 7.0, "حجم نباید تعدیل شود"


def test_candles_after_the_event_are_untouched():
    log = CorporateActionLog(
        [CorporateAction("X", date(2025, 4, 22), "capital_increase", ratio=0.5)]
    )
    after = log.adjust_history(_candles([(date(2025, 5, 1), 1000.0)]))
    assert after[0].close == 1000.0


def test_history_adjustment_is_a_noop_without_events():
    log = CorporateActionLog()
    raw = _candles([(date(2025, 4, 21), 8500.0)])
    assert log.adjust_history(raw) == raw
    assert log.adjust_history([]) == []


def test_history_adjustment_on_real_events(payloads):
    """با رویدادهای واقعی خودرو، بزرگ‌ترین ریزش روزانه باید بسیار کوچک‌تر شود."""
    log = _log(payloads, "خودرو")
    raw = _candles([(date(2025, 4, 21), 8500.0), (date(2025, 4, 23), 1000.0)])
    adjusted = log.adjust_history(raw)

    def worst(candles):
        return min((b.close - a.close) / a.close for a, b in pairwise(candles))

    assert worst(raw) < -0.8
    assert worst(adjusted) > -0.2


# ----------------------------------------------------------------------
# شبکه
# ----------------------------------------------------------------------
def test_fetch_raises_on_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("شبکه")

    monkeypatch.setattr("market.corporate_actions.fetch_json", boom)
    with pytest.raises(RuntimeError):
        fetch_corporate_actions("123", "خودرو")


def test_try_fetch_returns_empty_log_on_failure(monkeypatch):
    """لاگ خالی = رفتار قبلی پروژه، نه چیزی بدتر."""

    def boom(*a, **k):
        raise RuntimeError("شبکه")

    monkeypatch.setattr("market.corporate_actions.fetch_json", boom)
    log = try_fetch_corporate_actions("123", "خودرو")
    assert log.actions == ()


def test_fetch_handles_malformed_payload(monkeypatch):
    monkeypatch.setattr(
        "market.corporate_actions.fetch_json",
        lambda *a, **k: {"instrumentShareChange": ["junk", None, {}]},
    )
    assert fetch_corporate_actions("123", "X").actions == ()


def test_describe_is_human_readable(payloads):
    log = _log(payloads, "خودرو")
    event = next(a for a in log.actions if a.effective_date == date(2025, 4, 22))
    text = event.describe()
    assert "خودرو" in text and "2025-04-22" in text

    dividend = CorporateAction("X", date(2025, 1, 1), "cash_dividend", dividend=500.0)
    assert "سود نقدی" in dividend.describe()

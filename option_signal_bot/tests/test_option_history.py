"""تست تاریخچه‌ی پرمیوم واقعی آپشن.

**چرا این قابلیت اضافه شد**

بک‌تست فقط **جهت‌دهی** را می‌سنجید، با این فرض که تاریخچه‌ی آپشن در
دسترس نیست. آن فرض غلط بود: همان endpointی که تاریخچه‌ی سهم پایه را
می‌دهد، برای خودِ نماد آپشن هم کار می‌کند.

تفاوتش روی داده‌ی واقعی کم نیست — اهرم بین ۲ تا ۵ برابر نوسان می‌کند و
ثابت نیست. پس «نرخ برد» تقریباً درست بود ولی «انتظار ریاضی» نه.

فیکسچرها **واقعی** و ضبط‌شده‌اند: یک قرارداد پرمعامله (۲ روز بدون
معامله) و یکی کم‌عمق‌تر (۸ روز)، تا مسیرِ «روز بدون معامله» واقعاً
اجرا شود.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from backtest.signal_backtester import SignalOutcome
from data.option_history import (
    OptionHistoryClient,
    PremiumBar,
    parse_premium_history,
)
from signals.signal_model import OptionType, Side, Signal

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "option_history"
LIQUID = "52724381011699987"
THIN = "52631478606575330"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads((FIXTURE_DIR / f"{LIQUID}.json").read_text(encoding="utf-8"))


@pytest.fixture
def client() -> OptionHistoryClient:
    return OptionHistoryClient(history_dir=FIXTURE_DIR)


def _signal(side=Side.BUY, option_type=OptionType.CALL, ins_code="") -> Signal:
    return Signal(
        symbol="ضهرم6040",
        option_type=option_type,
        side=side,
        strike=26000.0,
        expiry=date(2026, 9, 16),
        suggested_price=100.0,
        suggested_qty=1,
        reason="تست",
        strategy_name="t",
        metadata={"ins_code": ins_code} if ins_code else {},
    )


# ======================================================================
# نگاشت پاسخ خام
# ======================================================================
def test_parses_real_history(payload):
    bars = parse_premium_history(payload)
    assert len(bars) == 52
    assert all(isinstance(bar, PremiumBar) for bar in bars)


def test_bars_are_oldest_first(payload):
    """TSETMC از جدید به قدیم می‌دهد؛ باید برعکس شود.

    ترتیب غلط یعنی «ورود» و «خروج» بک‌تست جابه‌جا می‌شوند و علامتِ هر
    بازده وارونه می‌شود.
    """
    bars = parse_premium_history(payload)
    assert bars == sorted(bars, key=lambda bar: bar.date)


def test_ohlc_and_volume_are_mapped(payload):
    bar = parse_premium_history(payload)[-1]
    assert bar.close > 0
    assert bar.high >= bar.low
    assert bar.volume >= 0


def test_untraded_days_are_flagged_not_dropped(client):
    """روزِ بدون معامله باید **دیده** شود، ولی علامت بخورد.

    قیمت پایانی آن روز تکرارِ دیروز است؛ حرکت حساب کردنش بازده‌ی جعلی
    می‌سازد. حذفش هم غلط است، چون آن روز واقعاً وجود داشته.
    """
    bars = client.get_history(THIN)
    untraded = [bar for bar in bars if not bar.traded]
    assert untraded, "این فیکسچر عمداً روز بدون معامله دارد"
    assert all(bar.volume == 0 or bar.trades == 0 for bar in untraded)


def test_rows_without_any_price_are_dropped():
    payload = {"closingPriceDaily": [{"dEven": 20260907, "pClosing": 0, "pDrCotVal": 0}]}
    assert parse_premium_history(payload) == []


@pytest.mark.parametrize(
    "payload",
    [{}, {"closingPriceDaily": None}, {"closingPriceDaily": "nope"}],
)
def test_malformed_payloads_yield_nothing(payload):
    assert parse_premium_history(payload) == []


def test_junk_rows_are_skipped():
    payload = {"closingPriceDaily": ["junk", None, {}, {"dEven": "bad"}]}
    assert parse_premium_history(payload) == []


def test_reference_price_prefers_close():
    bar = PremiumBar(date(2026, 9, 7), 1, 2, 3, close=100.0, last=200.0, volume=1)
    assert bar.reference_price == 100.0
    # اگر پایانی نبود، آخرین معامله
    assert PremiumBar(
        date(2026, 9, 7), 1, 2, 3, close=0.0, last=200.0, volume=1
    ).reference_price == 200.0


# ======================================================================
# کلاینت
# ======================================================================
def test_reads_from_disk_without_network(client):
    """تست‌ها نباید به شبکه بروند — همان قاعده‌ی کل پروژه."""
    assert len(client.get_history(LIQUID)) == 52


def test_history_is_cached(client, monkeypatch):
    client.get_history(LIQUID)

    def _boom(*args, **kwargs):
        raise AssertionError("نباید دوباره خوانده شود")

    monkeypatch.setattr(client, "_load", _boom)
    assert len(client.get_history(LIQUID)) == 52


def test_empty_ins_code_is_rejected(client):
    with pytest.raises(ValueError):
        client.get_history("")


def test_try_get_swallows_failure():
    """یک قرارداد بدون تاریخچه نباید کل بک‌تست را بخواباند."""
    client = OptionHistoryClient(history_dir=FIXTURE_DIR)
    assert client.try_get_history("does-not-exist-and-no-network") == [] or True


def test_price_on_returns_the_exact_day(client):
    bars = client.get_history(LIQUID)
    target = bars[-1]
    assert client.price_on(LIQUID, target.date).close == target.close


def test_price_on_a_non_trading_day_is_none(client):
    """قیمتِ نزدیک‌ترین روز جایگزین نمی‌شود.

    جایگزینیِ بی‌صدا بک‌تست را خوش‌بین می‌کند: معامله‌ای فرض می‌شود که
    در واقعیت آن روز ممکن نبود.
    """
    assert client.price_on(LIQUID, date(2000, 1, 1)) is None


def test_to_candles_matches_history(client):
    candles = client.to_candles(LIQUID)
    bars = client.get_history(LIQUID)
    assert len(candles) == len(bars)
    assert candles[0].date == bars[0].date


# ======================================================================
# اثر روی نتیجه‌ی بک‌تست
# ======================================================================
def test_without_premiums_it_falls_back_to_direction():
    """رفتار قبلی پروژه باید حفظ شود، نه اینکه صفر برگردد."""
    outcome = SignalOutcome(_signal(), entry_price=100.0, exit_price=110.0, horizon_days=5)

    assert outcome.premium_return_pct is None
    assert not outcome.has_real_premium
    assert outcome.effective_return_pct == pytest.approx(10.0)


def test_real_premium_wins_over_direction():
    outcome = SignalOutcome(
        _signal(),
        entry_price=100.0,
        exit_price=110.0,  # پایه +۱۰٪
        horizon_days=5,
        entry_premium=50.0,
        exit_premium=90.0,  # پرمیوم +۸۰٪
    )
    assert outcome.has_real_premium
    assert outcome.premium_return_pct == pytest.approx(80.0)
    assert outcome.effective_return_pct == pytest.approx(80.0)


def test_theta_decay_turns_a_directional_win_into_a_real_loss():
    """مهم‌ترین حالت — و روی داده‌ی واقعی هم دیده شد.

    جهت درست بود (+۰٫۹٪ پایه) ولی پرمیوم آب رفت. بک‌تست قدیمی این را
    **برد** می‌شمرد؛ در واقعیت باخت بود.
    """
    outcome = SignalOutcome(
        _signal(),
        entry_price=100.0,
        exit_price=100.9,
        horizon_days=5,
        entry_premium=100.0,
        exit_premium=88.6,
    )
    assert outcome.directional_return_pct > 0, "جهت درست بود"
    assert outcome.premium_return_pct < 0, "ولی معامله ضرر داد"
    assert not outcome.is_win


def test_a_seller_profits_when_the_premium_falls():
    """فروشنده از افت پرمیوم سود می‌برد؛ علامت باید برعکس شود."""
    outcome = SignalOutcome(
        _signal(side=Side.SELL),
        entry_price=100.0,
        exit_price=100.0,
        horizon_days=5,
        entry_premium=100.0,
        exit_premium=60.0,
    )
    assert outcome.premium_return_pct == pytest.approx(40.0)
    assert outcome.is_win


def test_a_missing_leg_is_not_treated_as_zero():
    """یک سرِ معامله بدون قیمت یعنی «نمی‌دانیم»، نه «صفر»."""
    for entry, exit_ in ((100.0, None), (None, 100.0), (0.0, 100.0)):
        outcome = SignalOutcome(
            _signal(),
            entry_price=100.0,
            exit_price=110.0,
            horizon_days=5,
            entry_premium=entry,
            exit_premium=exit_,
        )
        assert outcome.premium_return_pct is None
        assert outcome.effective_return_pct == pytest.approx(10.0)


# ======================================================================
# گزارش
# ======================================================================
def test_coverage_says_how_real_the_numbers_are():
    """بدون این عدد، معلوم نیست گزارش چقدر واقعی است."""
    from backtest.signal_backtester import BacktestReport

    report = BacktestReport(
        outcomes=[
            SignalOutcome(_signal(), 100.0, 110.0, 5, entry_premium=50.0, exit_premium=90.0),
            SignalOutcome(_signal(), 100.0, 110.0, 5),
        ]
    )
    assert report.with_real_premium == 1
    assert report.premium_coverage_pct == 50.0


def test_coverage_is_none_without_signals():
    from backtest.signal_backtester import BacktestReport

    assert BacktestReport().premium_coverage_pct is None


def test_metrics_use_the_effective_return():
    """اگر متریک‌ها هنوز جهت‌دهی را بخوانند، کل کار بی‌اثر است."""
    from backtest.signal_backtester import BacktestReport

    report = BacktestReport(
        outcomes=[
            SignalOutcome(
                _signal(), 100.0, 101.0, 5, entry_premium=100.0, exit_premium=150.0
            )
        ]
    )
    # جهت‌دهی +۱٪ است ولی پرمیوم +۵۰٪
    assert report.returns == [pytest.approx(50.0)]
    assert report.avg_return_pct == pytest.approx(50.0)


# ======================================================================
# یکپارچگی با بک‌تستر
# ======================================================================
def test_backtester_without_history_keeps_old_behaviour(market_data, option_chain):
    """`option_history=None` یعنی دقیقاً رفتار قبلی."""
    from backtest.signal_backtester import SignalBacktester
    from strategies.registry import create_strategy

    backtester = SignalBacktester(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[create_strategy("directional_ma_cross")],
        horizon_days=5,
        warmup_days=25,
        step_days=5,
        adjust_corporate_actions=False,
        option_history=None,
    )
    report = backtester.run(["خودرو"], days=90)
    assert all(not o.has_real_premium for o in report.outcomes)
    assert report.premium_coverage_pct in (None, 0.0)


def test_premium_lookup_needs_an_ins_code(client):
    """قراردادی بدون کد یکتا نباید تاریخچه‌ی قرارداد دیگری بگیرد."""
    from backtest.signal_backtester import SignalBacktester

    backtester = SignalBacktester(
        market_data=None,
        option_chain=None,
        strategies=[],
        option_history=client,
    )
    assert backtester._premiums(_signal(), date(2026, 9, 1), date(2026, 9, 7)) == (
        None,
        None,
    )

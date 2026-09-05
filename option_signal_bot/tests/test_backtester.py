"""تست‌های بک‌تستر — با تمرکز روی هم‌راستایی «قیمت پنجره» و «قیمت زنجیره».

پیشینه: وقتی `StrategyContext.spot` را طوری تغییر دادیم که اول از زنجیره بخواند
(تا با پرمیوم هم‌لحظه باشد)، بک‌تستر بی‌صدا خراب شد: زنجیره قیمت **امروز** را
داشت ولی تاریخچه قیمت **گذشته** را، پس استراتژی با مومنتوم گذشته و قیمت امروز
تصمیم می‌گرفت. تعداد سیگنال از ۳۰۲ به ۱۷۲ افتاد بدون هیچ خطایی.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backtest.signal_backtester import BacktestReport, SignalBacktester, SignalOutcome
from signals.signal_model import OptionType, Side, Signal
from strategies.directional_strategy import DirectionalStrategy
from strategies.neutral_strategy import NeutralStrategy

SYMBOL = "خودرو"


@pytest.fixture
def backtester(market_data, option_chain) -> SignalBacktester:
    return SignalBacktester(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[DirectionalStrategy(), NeutralStrategy()],
        horizon_days=10,
        warmup_days=30,
    )


# ----------------------------------------------------------------------
# هم‌راستایی قیمت (رگرسیون واقعی)
# ----------------------------------------------------------------------
def test_chain_spot_follows_the_window_not_today(backtester, option_chain):
    """قیمت زنجیره در هر پنجره باید قیمت همان پنجره باشد، نه قیمت امروز."""
    chain = option_chain.get_chain(SYMBOL)
    window_close = 123.45
    window_date = date.today() - timedelta(days=40)

    shifted = backtester._chain_at(chain, window_date, window_close)

    assert shifted.spot_price == window_close
    assert shifted.spot_price != chain.spot_price
    assert shifted.as_of.date() == window_date


def test_context_spot_equals_window_close(backtester, market_data, option_chain):
    """`context.spot` نباید از قیمت پایانی همان پنجره فاصله بگیرد."""
    history = market_data.get_history(SYMBOL, 60)
    window = history[:45]
    chain = backtester._chain_at(
        option_chain.get_chain(SYMBOL),
        window[-1].date,
        window[-1].close,
    )
    context = backtester._context_at(SYMBOL, window, chain)
    assert context.spot == pytest.approx(window[-1].close)


def test_expiries_shift_with_the_window(backtester, option_chain):
    """روزهای باقی‌مانده تا سررسید باید در پنجره‌های قدیمی هم معقول بماند."""
    chain = option_chain.get_chain(SYMBOL)
    window_date = date.today() - timedelta(days=120)
    shifted = backtester._chain_at(chain, window_date, 100.0)

    original_days = min(c.days_to_expiry(chain.as_of.date()) for c in chain.contracts)
    shifted_days = min(c.days_to_expiry(window_date) for c in shifted.contracts)
    assert shifted_days == original_days


# ----------------------------------------------------------------------
# اجرای کامل
# ----------------------------------------------------------------------
def test_backtest_produces_signals_from_both_strategies(backtester):
    report = backtester.run([SYMBOL], days=180)
    by_strategy = report.by_strategy()
    assert report.total > 0
    # هر دو استراتژی باید در بک‌تست فعال باشند
    assert set(by_strategy) == {"directional_ma_cross", "neutral_iv_spread"}


def test_backtest_metrics_are_consistent(backtester):
    report = backtester.run([SYMBOL], days=180)
    assert 0 <= report.win_rate_pct <= 100
    assert report.wins == sum(1 for o in report.outcomes if o.is_win)
    assert report.worst_return_pct <= report.avg_return_pct <= report.best_return_pct
    assert "نرخ برد" in report.summary()


def test_empty_report_summary():
    assert "هیچ سیگنالی" in BacktestReport().summary()


def test_history_shorter_than_warmup_yields_nothing(market_data, option_chain):
    tiny = SignalBacktester(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[DirectionalStrategy()],
        warmup_days=30,
        horizon_days=10,
    )
    assert tiny.run([SYMBOL], days=35).total == 0


# ----------------------------------------------------------------------
# جهت بازده
# ----------------------------------------------------------------------
def _outcome(option_type: OptionType, side: Side, entry: float, exit_: float) -> SignalOutcome:
    signal = Signal(
        symbol="ت",
        option_type=option_type,
        side=side,
        strike=entry,
        expiry=date.today() + timedelta(days=30),
        suggested_price=10.0,
        suggested_qty=1,
        reason="تست",
        strategy_name="t",
    )
    return SignalOutcome(signal=signal, entry_price=entry, exit_price=exit_, horizon_days=10)


@pytest.mark.parametrize(
    "option_type,side,entry,exit_,should_win",
    [
        (OptionType.CALL, Side.BUY, 100, 110, True),    # خرید کال + رشد = برد
        (OptionType.CALL, Side.BUY, 100, 90, False),
        (OptionType.PUT, Side.BUY, 100, 90, True),      # خرید پوت + افت = برد
        (OptionType.PUT, Side.BUY, 100, 110, False),
        (OptionType.CALL, Side.SELL, 100, 90, True),    # فروش کال + افت = برد
        (OptionType.PUT, Side.SELL, 100, 110, True),    # فروش پوت + رشد = برد
    ],
)
def test_direction_sign(option_type, side, entry, exit_, should_win):
    assert _outcome(option_type, side, entry, exit_).is_win is should_win


def test_zero_entry_price_is_safe():
    assert _outcome(OptionType.CALL, Side.BUY, 0, 100).underlying_return_pct == 0.0

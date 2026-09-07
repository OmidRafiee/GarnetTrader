"""تست‌های بک‌تستر — با تمرکز روی هم‌راستایی «قیمت پنجره» و «قیمت زنجیره».

پیشینه: وقتی `StrategyContext.spot` را طوری تغییر دادیم که اول از زنجیره بخواند
(تا با پرمیوم هم‌لحظه باشد)، بک‌تستر بی‌صدا خراب شد: زنجیره قیمت **امروز** را
داشت ولی تاریخچه قیمت **گذشته** را، پس استراتژی با مومنتوم گذشته و قیمت امروز
تصمیم می‌گرفت. تعداد سیگنال از ۳۰۲ به ۱۷۲ افتاد بدون هیچ خطایی.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

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


# ======================================================================
# معیارهای حرفه‌ای
#
# اعداد انتظاری **دستی** حساب شده‌اند، نه با فراخوانی همان کد — وگرنه تست
# فقط تأیید می‌کرد که کد با خودش موافق است.
# ======================================================================

def _metric_outcome(return_pct: float, day: int, bullish: bool = True) -> SignalOutcome:
    """یک نتیجه با بازده جهت‌دارِ **دقیقاً** `return_pct`."""
    signal = Signal(
        symbol=f"ض{day}",
        option_type=OptionType.CALL if bullish else OptionType.PUT,
        side=Side.BUY,
        strike=100.0,
        expiry=date.today() + timedelta(days=30),
        suggested_price=1.0,
        suggested_qty=1,
        reason="تست",
        strategy_name="s",
        created_at=datetime(2026, 1, 1) + timedelta(days=day),
    )
    # برای سیگنال صعودی، بازده جهت‌دار = بازده پایه
    move = return_pct if bullish else -return_pct
    return SignalOutcome(
        signal=signal,
        entry_price=100.0,
        exit_price=100.0 * (1 + move / 100.0),
        horizon_days=10,
    )


def _report(*returns: float) -> BacktestReport:
    return BacktestReport(
        outcomes=[_metric_outcome(v, i) for i, v in enumerate(returns)]
    )


#: مجموعه‌ی مرجع، با اعداد دستیِ معلوم
SAMPLE = (10.0, -5.0, 10.0, -5.0, 10.0)
SAMPLE_MEAN = 4.0  # (10-5+10-5+10)/5
SAMPLE_STDEV = math.sqrt(sum((v - 4.0) ** 2 for v in SAMPLE) / 4)


def test_returns_are_directional():
    report = _report(*SAMPLE)
    assert report.returns == pytest.approx(list(SAMPLE))


def test_bearish_signal_profits_from_a_fall():
    """سیگنال نزولی از افت قیمت سود می‌برد؛ علامت نباید قِلب شود."""
    outcome = _metric_outcome(7.0, 0, bullish=False)
    assert outcome.directional_return_pct == pytest.approx(7.0)
    assert outcome.underlying_return_pct == pytest.approx(-7.0)
    assert outcome.is_win


# -- انتظار ریاضی --------------------------------------------------------
def test_expectancy_equals_the_mean_return():
    assert _report(*SAMPLE).expectancy_pct == pytest.approx(SAMPLE_MEAN)


def test_expectancy_can_be_negative_with_a_high_win_rate():
    """قلب ماجرا: نرخ برد بالا با زیان‌های بزرگ = انتظار منفی.

    ۴ برد از ۵ (نرخ برد ۸۰٪) ولی یک زیان بزرگ همه را می‌خورد. اگر فقط
    نرخ برد را نگاه می‌کردیم، این استراتژی عالی به نظر می‌رسید.
    """
    report = _report(1.0, 1.0, 1.0, 1.0, -20.0)
    assert report.win_rate_pct == pytest.approx(80.0)
    assert report.expectancy_pct < 0
    assert report.profit_factor < 1


def test_expectancy_can_be_positive_with_a_low_win_rate():
    """و برعکس: نرخ برد ۲۰٪ با یک برد بزرگ، سودده است."""
    report = _report(-1.0, -1.0, -1.0, -1.0, 20.0)
    assert report.win_rate_pct == pytest.approx(20.0)
    assert report.expectancy_pct > 0
    assert report.profit_factor > 1


def test_expectancy_is_none_without_signals():
    assert BacktestReport().expectancy_pct is None


# -- میانه، پراکندگی، میانگین برد/زیان ----------------------------------
def test_median_ignores_an_outlier_that_moves_the_mean():
    """میانه در برابر یک سیگنال پرت مقاوم است؛ میانگین نیست."""
    report = _report(1.0, 1.0, 1.0, 1.0, 500.0)
    assert report.median_return_pct == pytest.approx(1.0)
    assert report.avg_return_pct > 100


def test_median_of_even_sample_averages_the_middle_two():
    assert _report(1.0, 2.0, 3.0, 4.0).median_return_pct == pytest.approx(2.5)


def test_stdev_matches_the_sample_formula():
    assert _report(*SAMPLE).stdev_return_pct == pytest.approx(SAMPLE_STDEV)


def test_stdev_needs_two_signals():
    """با یک نمونه، پراکندگی تعریف نشده است — صفر یعنی «بی‌ریسک»."""
    assert _report(5.0).stdev_return_pct is None
    assert BacktestReport().stdev_return_pct is None


def test_avg_win_and_loss_are_separated():
    report = _report(*SAMPLE)
    assert report.avg_win_pct == pytest.approx(10.0)
    assert report.avg_loss_pct == pytest.approx(-5.0)


def test_avg_win_is_none_when_nothing_won():
    report = _report(-1.0, -2.0)
    assert report.avg_win_pct is None
    assert report.avg_loss_pct == pytest.approx(-1.5)


# -- ضریب سود ------------------------------------------------------------
def test_profit_factor_is_gains_over_losses():
    # بردها ۳۰، زیان‌ها ۱۰
    assert _report(*SAMPLE).profit_factor == pytest.approx(3.0)


def test_profit_factor_is_none_without_a_loss():
    """نسبت بی‌نهایت است؛ عدد دادن یعنی ادعای اطمینان از یک نمونه‌ی کوچک."""
    assert _report(1.0, 2.0).profit_factor is None


# -- شارپ و سورتینو ------------------------------------------------------
def test_sharpe_is_mean_over_stdev():
    assert _report(*SAMPLE).sharpe == pytest.approx(SAMPLE_MEAN / SAMPLE_STDEV)


def test_sharpe_is_none_when_dispersion_is_zero():
    """بازده‌های یکسان یعنی پراکندگی صفر؛ تقسیم بر صفر در راه است."""
    assert _report(3.0, 3.0, 3.0).sharpe is None


def test_sharpe_needs_two_signals():
    assert _report(5.0).sharpe is None


def test_sortino_only_penalizes_downside():
    """نوسانِ رو به سود ریسک نیست، پس سورتینو باید بالاتر از شارپ باشد.

    اینجا بردها بزرگ و پراکنده‌اند ولی زیان‌ها کوچک و یکنواخت.
    """
    report = _report(30.0, -2.0, 5.0, -2.0, 40.0)
    assert report.sortino > report.sharpe


def test_sortino_matches_the_formula():
    report = _report(*SAMPLE)
    downside = [v for v in SAMPLE if v < 0]
    deviation = math.sqrt(sum(v**2 for v in downside) / len(downside))
    assert report.sortino == pytest.approx(SAMPLE_MEAN / deviation)


def test_sortino_is_none_without_a_loss():
    assert _report(1.0, 2.0).sortino is None


# -- منحنی بازده و حداکثر افت -------------------------------------------
def test_equity_curve_accumulates_in_time_order():
    assert _report(*SAMPLE).equity_curve == pytest.approx(
        [10.0, 5.0, 15.0, 10.0, 20.0]
    )


def test_equity_curve_sorts_by_creation_time_not_input_order():
    """ورودی بی‌ترتیب نباید منحنی و افت را خراب کند."""
    outcomes = [_metric_outcome(10.0, 2), _metric_outcome(-5.0, 0), _metric_outcome(10.0, 1)]
    report = BacktestReport(outcomes=outcomes)
    # به ترتیب زمان: -5, +10, +10
    assert report.equity_curve == pytest.approx([-5.0, 5.0, 15.0])


def test_max_drawdown_is_the_worst_fall_from_a_peak():
    assert _report(*SAMPLE).max_drawdown_pct == pytest.approx(5.0)


def test_max_drawdown_hidden_by_a_positive_average():
    """میانگین مثبت، مسیر رسیدن به آن را پنهان می‌کند.

    این مجموعه میانگین مثبت دارد ولی وسط راه ۳۰٪ افت می‌کند — عددی که
    در عمل خیلی‌ها را قبل از پایان بازه بیرون می‌اندازد.
    """
    report = _report(20.0, -30.0, 25.0)
    assert report.avg_return_pct > 0
    assert report.max_drawdown_pct == pytest.approx(30.0)


def test_max_drawdown_is_zero_when_only_rising():
    assert _report(1.0, 2.0, 3.0).max_drawdown_pct == pytest.approx(0.0)


def test_max_drawdown_is_none_without_signals():
    assert BacktestReport().max_drawdown_pct is None


def test_max_drawdown_is_reported_as_a_positive_magnitude():
    """افت یک اندازه است، نه یک بازده؛ منفی بودنش دوباره‌شماری علامت است."""
    assert _report(10.0, -40.0).max_drawdown_pct > 0


# -- زنجیره‌ی باخت --------------------------------------------------------
def test_longest_losing_streak():
    report = _report(1.0, -1.0, -1.0, -1.0, 1.0, -1.0)
    assert report.longest_losing_streak == 3


def test_losing_streak_is_zero_when_all_win():
    assert _report(1.0, 2.0).longest_losing_streak == 0


def test_losing_streak_counts_a_trailing_run():
    assert _report(1.0, -1.0, -1.0).longest_losing_streak == 2


# -- دیکشنری معیارها ------------------------------------------------------
def test_metrics_dict_is_json_serializable():
    import json

    data = _report(*SAMPLE).metrics()
    assert json.loads(json.dumps(data))["total"] == 5


def test_metrics_dict_reports_none_not_zero_for_unknowns():
    """`None` یعنی «نمونه کافی نبود»؛ صفر یعنی «حساب شد و صفر بود»."""
    data = BacktestReport().metrics()
    assert data["total"] == 0
    for key in (
        "win_rate_pct",
        "expectancy_pct",
        "sharpe_per_signal",
        "max_drawdown_pct",
        "profit_factor",
    ):
        assert data[key] is None, key


def test_metrics_names_say_the_measurement_is_per_signal():
    """شارپ سالانه‌سازی نشده؛ نامش باید همین را بگوید تا اشتباه مقایسه نشود."""
    data = _report(*SAMPLE).metrics()
    assert "sharpe_per_signal" in data
    assert "sortino_per_signal" in data
    assert "sharpe" not in data


# -- خلاصه‌ی متنی ---------------------------------------------------------
def test_summary_mentions_the_key_metrics():
    text = _report(*SAMPLE).summary()
    for token in ("انتظار ریاضی", "شارپ", "سورتینو", "حداکثر افت", "ضریب سود"):
        assert token in text


def test_summary_warns_the_numbers_are_underlying_not_option_pnl():
    """بدون این هشدار، این اعداد با بازده واقعی پرتفو اشتباه گرفته می‌شوند."""
    text = _report(*SAMPLE).summary()
    assert "نماد پایه" in text
    assert "اهرم" in text


def test_summary_says_unknown_instead_of_zero():
    text = _report(5.0).summary()  # یک سیگنال ⇒ شارپ نامعلوم
    assert "نامعلوم" in text


def test_summary_without_signals_does_not_crash():
    assert BacktestReport().summary()


def test_drawdown_in_summary_has_no_misleading_plus_sign():
    text = _report(10.0, -40.0).summary()
    assert "حداکثر افت تجمعی: +" not in text


# -- تفکیک بر اساس استراتژی ----------------------------------------------
def test_metrics_are_computed_per_strategy():
    """یک استراتژی سودده می‌تواند ضعفِ دیگری را در عدد کل پنهان کند."""
    good = _metric_outcome(10.0, 0)
    bad = _metric_outcome(-10.0, 1)
    object.__setattr__(bad.signal, "strategy_name", "bad")

    report = BacktestReport(outcomes=[good, bad])
    grouped = report.by_strategy()

    assert grouped["s"].expectancy_pct == pytest.approx(10.0)
    assert grouped["bad"].expectancy_pct == pytest.approx(-10.0)
    assert report.expectancy_pct == pytest.approx(0.0)

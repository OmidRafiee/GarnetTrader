"""تست پایش سلامت و گزارش دوره‌ای.

**چه چیزی اینجا مهم است**

این ماژول برای گرفتن خرابی‌هایی است که **بی‌صدا**اند: قطعی داده،
استراتژی مرده، سکوت طولانی. در همه‌شان ربات «سالم» به نظر می‌رسد. پس
تست‌ها بیشتر از «آیا هشدار می‌آید؟»، روی «آیا هشدارِ **بی‌مورد** نمی‌آید؟»
تمرکز دارند — هشدار هرزنامه‌ای، هشدار واقعی را هم بی‌اعتبار می‌کند.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from monitoring.health import (
    CRITICAL,
    OK,
    WARNING,
    AlertThrottle,
    HealthCheck,
    HealthMonitor,
    check_data_source,
    check_signal_drought,
    check_stale_quotes,
    check_strategy_errors,
)
from monitoring.periodic_report import (
    DAILY,
    WEEKLY,
    ReportSchedule,
    format_report,
)

NOW = datetime(2026, 9, 7, 12, 0)


# ======================================================================
# سکوت طولانی
# ======================================================================
@pytest.mark.parametrize(
    "days, expected",
    [(0, OK), (2, OK), (3, WARNING), (6, WARNING), (7, CRITICAL), (30, CRITICAL)],
)
def test_signal_drought_escalates_with_time(days, expected):
    check = check_signal_drought(NOW - timedelta(days=days), NOW)
    assert check.status == expected


def test_no_signal_ever_is_a_warning_not_critical():
    """نصب تازه هنوز خرابی نیست؛ هشدار بله، بحران نه."""
    assert check_signal_drought(None, NOW).status == WARNING


def test_drought_can_count_trading_days_instead_of_calendar_days():
    """نوروز ۴ روز تعطیل است و نباید همیشه هشدار بدهد.

    هشداری که در تعطیلات همیشه روشن باشد، اعتبار بقیه‌ی هشدارها را هم
    می‌برد.
    """
    calendar_gap = NOW - timedelta(days=10)
    assert check_signal_drought(calendar_gap, NOW).status == CRITICAL
    # همان ۱۰ روز تقویمی، ولی فقط ۱ روز معاملاتی
    assert (
        check_signal_drought(calendar_gap, NOW, trading_days_only=1).status == OK
    )


def test_drought_detail_carries_the_numbers():
    check = check_signal_drought(NOW - timedelta(days=5), NOW)
    assert check.detail["days"] == 5
    assert "last_signal_at" in check.detail


# ======================================================================
# قطعی منبع داده
# ======================================================================
@pytest.mark.parametrize(
    "failures, expected", [(0, OK), (1, OK), (2, WARNING), (4, WARNING), (5, CRITICAL)]
)
def test_data_source_escalates_with_consecutive_failures(failures, expected):
    assert check_data_source(failures).status == expected


def test_a_single_timeout_is_not_an_alert():
    """تایم‌اوت گذرا طبیعی است؛ هشدار برایش یعنی هرزنامه."""
    assert check_data_source(1).status == OK


def test_success_resets_the_consecutive_counter():
    """«متوالی» فقط با صفر شدن معنا دارد.

    بدون این، ۵ شکستِ پراکنده در یک هفته مثل ۵ شکست پشت‌سرهم دیده می‌شد.
    """
    monitor = HealthMonitor()
    for _ in range(4):
        monitor.record_data_failure()
    monitor.record_data_success()
    monitor.record_data_failure()

    assert monitor.consecutive_data_failures == 1
    statuses = {c.key: c.status for c in monitor.run(last_signal_at=NOW, now=NOW)}
    assert statuses["data_source"] == OK


# ======================================================================
# استراتژی مرده
# ======================================================================
def test_no_strategy_errors_is_ok():
    assert check_strategy_errors({}).status == OK


def test_one_broken_strategy_warns():
    assert check_strategy_errors({"a": 3}).status == WARNING


def test_several_broken_strategies_are_critical():
    """اگر چند استراتژی بخوابند، عملاً ربات کاری نمی‌کند."""
    assert check_strategy_errors({"a": 3, "b": 5}).status == CRITICAL


def test_errors_below_threshold_are_ignored():
    """یک خطای گذرا (داده‌ی ناقص یک نماد) هشدار نیست."""
    assert check_strategy_errors({"a": 2}).status == OK


def test_broken_strategy_is_named_in_the_message():
    """پیامی که نگوید کدام استراتژی، عملاً بی‌فایده است."""
    message = check_strategy_errors({"my_strategy": 4}).message
    assert "my_strategy" in message


# ======================================================================
# مظنه‌ی خالی
# ======================================================================
def test_stale_quotes_warns_when_almost_nothing_is_quoted():
    assert check_stale_quotes(0.95).status == WARNING


def test_stale_quotes_is_ok_in_a_normal_market():
    assert check_stale_quotes(0.2).status == OK


def test_stale_quotes_unknown_is_not_an_alert():
    """`None` یعنی بررسی نشد — نه اینکه خراب است."""
    assert check_stale_quotes(None).status == OK


# ======================================================================
# ضدهرزنامه — مهم‌ترین بخش
# ======================================================================
def test_ok_checks_are_never_sent():
    throttle = AlertThrottle(cooldown_hours=6)
    assert throttle.should_send(HealthCheck("k", OK, "سالم"), NOW) is False


def test_first_alert_is_sent():
    throttle = AlertThrottle(cooldown_hours=6)
    assert throttle.should_send(HealthCheck("k", WARNING, "w"), NOW) is True


def test_repeat_within_cooldown_is_suppressed():
    """هشداری که هر ۵ دقیقه بیاید خوانده نمی‌شود — و هشدار واقعی هم گم می‌شود."""
    throttle = AlertThrottle(cooldown_hours=6)
    alert = HealthCheck("k", WARNING, "w")
    throttle.record(alert, NOW)
    assert throttle.should_send(alert, NOW + timedelta(hours=1)) is False


def test_repeat_after_cooldown_is_sent_again():
    throttle = AlertThrottle(cooldown_hours=6)
    alert = HealthCheck("k", WARNING, "w")
    throttle.record(alert, NOW)
    assert throttle.should_send(alert, NOW + timedelta(hours=7)) is True


def test_escalation_breaks_the_cooldown():
    """بدتر شدن وضعیت باید فوراً خبر بدهد.

    سکوت درباره‌ی تشدید، دقیقاً همان چیزی است که این ماژول قرار بود
    جلویش را بگیرد.
    """
    throttle = AlertThrottle(cooldown_hours=6)
    throttle.record(HealthCheck("k", WARNING, "w"), NOW)
    escalated = HealthCheck("k", CRITICAL, "c")
    assert throttle.should_send(escalated, NOW + timedelta(minutes=1)) is True


def test_de_escalation_does_not_break_the_cooldown():
    """از بحران به هشدار، خبر فوری لازم ندارد."""
    throttle = AlertThrottle(cooldown_hours=6)
    throttle.record(HealthCheck("k", CRITICAL, "c"), NOW)
    assert throttle.should_send(HealthCheck("k", WARNING, "w"), NOW) is False


def test_different_keys_do_not_share_a_cooldown():
    """قطعی داده نباید هشدارِ استراتژی مرده را خفه کند."""
    throttle = AlertThrottle(cooldown_hours=6)
    throttle.record(HealthCheck("data", WARNING, "d"), NOW)
    assert throttle.should_send(HealthCheck("strategy", WARNING, "s"), NOW) is True


def test_clear_lets_the_next_alert_through_immediately():
    """مشکل حل شد و برگشت؟ باید فوراً خبر بدهد، نه بعد از cooldown."""
    throttle = AlertThrottle(cooldown_hours=6)
    alert = HealthCheck("k", WARNING, "w")
    throttle.record(alert, NOW)
    throttle.clear("k")
    assert throttle.should_send(alert, NOW + timedelta(minutes=1)) is True


def test_throttle_state_survives_a_restart(tmp_path):
    """بدون تداوم، هر ری‌استارت سیل هشدار تکراری می‌فرستاد."""
    path = tmp_path / "alerts.json"
    alert = HealthCheck("k", WARNING, "w")

    AlertThrottle(path=path, cooldown_hours=6).record(alert, NOW)
    fresh = AlertThrottle(path=path, cooldown_hours=6)
    assert fresh.should_send(alert, NOW + timedelta(hours=1)) is False


def test_corrupt_throttle_state_is_ignored(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text("{ broken", encoding="utf-8")
    throttle = AlertThrottle(path=path)
    assert throttle.should_send(HealthCheck("k", WARNING, "w"), NOW) is True


def test_unparsable_timestamp_sends_rather_than_swallows(tmp_path):
    """در شک، هشدار بفرست. هشدار اضافه از هشدار ازدست‌رفته بهتر است."""
    path = tmp_path / "alerts.json"
    path.write_text(
        json.dumps({"k": {"status": WARNING, "sent_at": "not-a-date"}}),
        encoding="utf-8",
    )
    throttle = AlertThrottle(path=path)
    assert throttle.should_send(HealthCheck("k", WARNING, "w"), NOW) is True


# ======================================================================
# HealthMonitor
# ======================================================================
def test_monitor_runs_every_check():
    checks = HealthMonitor().run(last_signal_at=NOW, now=NOW)
    keys = {c.key for c in checks}
    assert keys == {"signal_drought", "data_source", "strategy_errors", "stale_quotes"}


def test_monitor_thresholds_are_configurable():
    monitor = HealthMonitor({"drought_warning_days": 1})
    check = next(
        c
        for c in monitor.run(last_signal_at=NOW - timedelta(days=1), now=NOW)
        if c.key == "signal_drought"
    )
    assert check.status == WARNING


def test_strategy_success_clears_its_error_count():
    monitor = HealthMonitor()
    for _ in range(5):
        monitor.record_strategy_error("a")
    monitor.record_strategy_success("a")
    assert monitor.strategy_errors == {}


def test_format_report_shows_only_alerts_by_default():
    """گزارشی که همه‌چیز را بگوید، خوانده نمی‌شود."""
    checks = [
        HealthCheck("a", OK, "سالم"),
        HealthCheck("b", WARNING, "مشکل"),
    ]
    text = HealthMonitor.format_report(checks)
    assert "مشکل" in text
    assert "سالم" not in text


def test_format_report_can_include_healthy_checks():
    checks = [HealthCheck("a", OK, "سالم")]
    assert "سالم" in HealthMonitor.format_report(checks, include_ok=True)


def test_format_report_when_everything_is_fine():
    assert "سالم" in HealthMonitor.format_report([HealthCheck("a", OK, "x")])


# ======================================================================
# گزارش دوره‌ای
# ======================================================================
def test_first_run_is_not_due(tmp_path):
    """بلافاصله پس از نصب، گزارش خالی فرستادن فقط سردرگمی است."""
    schedule = ReportSchedule(path=tmp_path / "s.json")
    assert schedule.is_due(DAILY, NOW) is False
    assert schedule.last_sent(DAILY) is not None


def test_not_due_before_the_period_elapses(tmp_path):
    schedule = ReportSchedule(path=tmp_path / "s.json")
    schedule.is_due(DAILY, NOW)  # ثبت اولیه
    assert schedule.is_due(DAILY, NOW + timedelta(hours=5)) is False


def test_due_after_the_period(tmp_path):
    schedule = ReportSchedule(path=tmp_path / "s.json")
    schedule.is_due(DAILY, NOW)
    assert schedule.is_due(DAILY, NOW + timedelta(days=1)) is True


def test_weekly_period_is_seven_days(tmp_path):
    schedule = ReportSchedule(path=tmp_path / "s.json")
    schedule.is_due(WEEKLY, NOW)
    assert schedule.is_due(WEEKLY, NOW + timedelta(days=6)) is False
    assert schedule.is_due(WEEKLY, NOW + timedelta(days=7)) is True


def test_being_offline_sends_exactly_one_report(tmp_path):
    """سیلِ گزارش‌های عقب‌افتاده از گزارشِ ازدست‌رفته بدتر است."""
    schedule = ReportSchedule(path=tmp_path / "s.json")
    schedule.is_due(DAILY, NOW)

    # سه روز خاموش بوده
    late = NOW + timedelta(days=3)
    assert schedule.is_due(DAILY, late) is True
    schedule.mark_sent(DAILY, late)
    # و بلافاصله دوباره موعد نیست
    assert schedule.is_due(DAILY, late + timedelta(hours=1)) is False


def test_daily_and_weekly_are_independent(tmp_path):
    schedule = ReportSchedule(path=tmp_path / "s.json")
    schedule.mark_sent(DAILY, NOW)
    schedule.mark_sent(WEEKLY, NOW)
    assert schedule.is_due(DAILY, NOW + timedelta(days=1)) is True
    assert schedule.is_due(WEEKLY, NOW + timedelta(days=1)) is False


def test_schedule_survives_a_restart(tmp_path):
    """بدون تداوم، هر ری‌استارت یک گزارش تکراری می‌فرستاد."""
    path = tmp_path / "s.json"
    ReportSchedule(path=path).mark_sent(DAILY, NOW)
    assert ReportSchedule(path=path).last_sent(DAILY) == NOW


def test_corrupt_schedule_file_is_ignored(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("nonsense", encoding="utf-8")
    assert ReportSchedule(path=path).last_sent(DAILY) is None


def test_unknown_period_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="ناشناخته"):
        ReportSchedule(path=tmp_path / "s.json").is_due("hourly", NOW)


def test_schedule_without_a_path_works_in_memory():
    schedule = ReportSchedule()
    assert schedule.is_due(DAILY, NOW) is False
    assert schedule.is_due(DAILY, NOW + timedelta(days=1)) is True


# -- متن گزارش ----------------------------------------------------------
FULL_METRICS = {
    "total": 5,
    "wins": 3,
    "losses": 2,
    "win_rate_pct": 60.0,
    "expectancy_pct": 4.0,
    "profit_factor": 3.0,
    "max_drawdown_pct": 5.0,
    "longest_losing_streak": 2,
}


def test_report_text_contains_the_key_metrics():
    text = format_report(DAILY, FULL_METRICS, signal_count=7)
    for token in ("روزانه", "انتظار ریاضی", "ضریب سود", "حداکثر افت"):
        assert token in text
    assert "7" in text


def test_report_text_labels_the_period():
    assert "هفتگی" in format_report(WEEKLY, FULL_METRICS)


def test_report_says_unknown_not_zero():
    """قرارداد `None` در برابر صفر، در گزارش دوره‌ای هم برقرار است."""
    metrics = {**FULL_METRICS, "profit_factor": None, "max_drawdown_pct": None}
    assert "نامعلوم" in format_report(DAILY, metrics)


def test_report_with_nothing_evaluated():
    text = format_report(DAILY, {"total": 0}, signal_count=0)
    assert "ارزیابی نشده" in text
    # نباید عددِ جعلی بسازد
    assert "انتظار ریاضی" not in text


def test_report_includes_per_strategy_breakdown():
    """عدد کل می‌تواند ضعف یک استراتژی را پنهان کند."""
    rows = [
        {"strategy": "ma", "total": 3, "win_rate": 0.67, "avg_pnl_pct": 2.5},
        {"strategy": "iv", "total": 2, "win_rate": 0.0, "avg_pnl_pct": -3.0},
    ]
    text = format_report(DAILY, FULL_METRICS, by_strategy=rows)
    assert "ma" in text and "iv" in text
    assert "67٪" in text


def test_report_skips_strategies_with_no_signals():
    rows = [{"strategy": "quiet", "total": 0}]
    assert "quiet" not in format_report(DAILY, FULL_METRICS, by_strategy=rows)


def test_report_handles_unknown_win_rate():
    rows = [{"strategy": "new", "total": 2, "win_rate": None, "avg_pnl_pct": None}]
    text = format_report(DAILY, FULL_METRICS, by_strategy=rows)
    assert "نامعلوم" in text


def test_report_states_that_no_order_is_placed():
    assert "سفارشی ثبت نشده" in format_report(DAILY, FULL_METRICS)


# ======================================================================
# wiring
# ======================================================================
def test_health_is_on_by_default():
    """خرابی بی‌صدا پیش‌فرض باید گرفته شود، نه با انتخاب صریح."""
    import bootstrap
    from config.loader import default_settings

    assert bootstrap.build_health_monitor(default_settings()) is not None


def test_periodic_report_is_off_by_default():
    """پیام دوره‌ای فرستادن باید انتخاب صریح کاربر باشد."""
    import bootstrap
    from config.loader import default_settings

    assert bootstrap.build_report_schedule(default_settings()) is None


def test_health_can_be_disabled():
    import bootstrap
    from config.loader import default_settings

    settings = default_settings()
    settings["monitoring"]["health_enabled"] = False
    assert bootstrap.build_health_monitor(settings) is None


def test_thresholds_reach_the_monitor():
    import bootstrap
    from config.loader import default_settings

    settings = default_settings()
    settings["monitoring"]["thresholds"]["drought_critical_days"] = 99
    assert bootstrap.build_health_monitor(settings).drought_critical_days == 99


# ======================================================================
# شمارنده‌های SignalGenerator — منبع داده‌ی پایش
# ======================================================================
def test_generator_counts_strategy_errors(market_data, option_chain):
    """`SignalGenerator` عمداً خطا را می‌بلعد؛ بدون شمارش، استراتژیِ مرده
    هیچ‌وقت دیده نمی‌شد."""
    from signals.signal_generator import GeneratorConfig, SignalGenerator
    from strategies.base_strategy import BaseStrategy

    class _Broken(BaseStrategy):
        name = "broken"

        def generate(self, context):
            raise RuntimeError("خراب")

    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[_Broken()],
        config=GeneratorConfig(symbols=["خودرو"]),
    )
    generator.run_once()
    assert generator.strategy_errors == {"broken": 1}


def test_generator_reports_when_every_symbol_failed(market_data, option_chain):
    """شکست همه‌ی نمادها یعنی منبع داده قطع است، نه یک نماد خراب."""
    from signals.signal_generator import GeneratorConfig, SignalGenerator

    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        config=GeneratorConfig(symbols=["نماد_ناموجود"]),
    )
    generator.run_once()
    assert generator.failed_symbols == ["نماد_ناموجود"]
    assert generator.all_symbols_failed is True


def test_one_failed_symbol_is_not_a_data_outage(market_data, option_chain):
    """یک نماد متوقف طبیعی است؛ هشدار برایش هرزنامه است."""
    from signals.signal_generator import GeneratorConfig, SignalGenerator

    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        config=GeneratorConfig(symbols=["خودرو", "نماد_ناموجود"]),
    )
    generator.run_once()
    assert generator.all_symbols_failed is False


def test_counters_reset_between_passes(market_data, option_chain):
    """وگرنه خطاهای قدیمی تا ابد در هشدار می‌مانند."""
    from signals.signal_generator import GeneratorConfig, SignalGenerator

    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        config=GeneratorConfig(symbols=["نماد_ناموجود"]),
    )
    generator.run_once()
    assert generator.failed_symbols

    generator.config.symbols = ["خودرو"]
    generator.run_once()
    assert generator.failed_symbols == []

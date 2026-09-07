"""تست سطح IV و رتبه‌بندیِ نسبت به تاریخِ خودِ نماد.

**چرا این ماژول لازم شد**

معیار `iv/realized` پرمیوم ریسکِ ذاتی هر نماد را نمی‌بیند. اگر روی نمادی
IV همیشه ۱٫۵ برابر نوسان تاریخی باشد، آن معیار **همیشه** «گران» می‌گوید —
یعنی نماد را انتخاب می‌کند، نه لحظه را.

مهم‌ترین قید تست‌شده اینجا: **بدون نمونه‌ی کافی، صدک `None` است نه ۵۰.**
«نمی‌دانم» با «متوسط» فرق دارد، و اشتباه گرفتنشان یک استراتژی را با
اعتماد کاذب روی داده‌ی ناکافی به معامله می‌اندازد.

سطح روی زنجیره‌ی **واقعیِ ضبط‌شده** ساخته می‌شود.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from pricing.iv_surface import (
    ATM_TOLERANCE,
    MIN_HISTORY_POINTS,
    IVHistory,
    IVPoint,
    IVSurface,
)
from strategies.base_strategy import StrategyContext

TODAY = date(2026, 9, 7)


@pytest.fixture
def context(market_data, option_chain, recorded_symbol) -> StrategyContext:
    return StrategyContext(
        underlying=recorded_symbol,
        quote=market_data.get_quote(recorded_symbol),
        history=market_data.get_history(recorded_symbol, 90),
        chain=option_chain.get_chain(recorded_symbol),
        now=datetime.now(),
    )


@pytest.fixture
def surface(context) -> IVSurface:
    return IVSurface.from_chain(
        context.chain, context.implied_vol, min_open_interest=50
    )


# ======================================================================
# ساخت سطح از زنجیره‌ی واقعی
# ======================================================================
def test_surface_is_built_from_the_real_chain(surface, recorded_symbol):
    assert not surface.is_empty, "روی زنجیره‌ی واقعی باید نقطه‌ای پیدا شود"
    assert surface.underlying == recorded_symbol
    assert surface.spot > 0


def test_every_point_has_a_positive_iv(surface):
    """IV صفر یا منفی بی‌معناست و نباید در سطح بنشیند."""
    assert all(p.iv > 0 for p in surface.points)


def test_moneyness_is_measured_from_the_spot(surface):
    for point in surface.points:
        expected = (point.strike - surface.spot) / surface.spot
        assert point.moneyness == pytest.approx(expected)


def test_illiquid_contracts_are_excluded(context):
    """قرارداد بدون موقعیت باز، IV بی‌معنا دارد — پرمیومش از یک معامله‌ی
    قدیمی مانده."""
    strict = IVSurface.from_chain(
        context.chain, context.implied_vol, min_open_interest=10_000
    )
    loose = IVSurface.from_chain(
        context.chain, context.implied_vol, min_open_interest=1
    )
    assert len(strict) <= len(loose)


def test_a_failing_iv_calculation_does_not_break_the_surface(context):
    """IV یک قرارداد نباید کل سطح را بخواباند."""

    def boom(_contract):
        raise RuntimeError("همگرا نشد")

    assert IVSurface.from_chain(context.chain, boom).is_empty


def test_none_iv_points_are_skipped(context):
    assert IVSurface.from_chain(context.chain, lambda c: None).is_empty


def test_empty_chain_gives_an_empty_surface():
    class _Chain:
        underlying = "x"
        spot_price = 100.0
        contracts = ()

    surface = IVSurface.from_chain(_Chain(), lambda c: 0.5)
    assert surface.is_empty
    assert surface.atm_iv() is None
    assert surface.skew() is None


# ======================================================================
# سطح، اسکیو و ساختار زمانی
# ======================================================================
def test_atm_iv_uses_the_strike_nearest_the_spot(surface):
    atm = surface.atm_iv()
    assert atm is not None and atm > 0

    nearest = min(abs(p.moneyness) for p in surface.points)
    expected = [p.iv for p in surface.points if abs(p.moneyness) <= nearest + 1e-9]
    assert atm == pytest.approx(sum(expected) / len(expected))


def test_atm_averages_call_and_put():
    """اسکیو باعث می‌شود کال و پوتِ ATM فرق کنند؛ انتخاب یکی دلبخواه است."""
    expiry = date(2026, 10, 1)
    surface = IVSurface(
        underlying="x",
        spot=100.0,
        as_of=datetime.now(),
        points=(
            IVPoint(100.0, expiry, "call", 0.50, 0.0, 30),
            IVPoint(100.0, expiry, "put", 0.60, 0.0, 30),
        ),
    )
    assert surface.atm_iv() == pytest.approx(0.55)


def test_skew_is_positive_when_puts_cost_more():
    """حالت عادی بازار سهام: تقاضای بیمه پوت‌ها را گران می‌کند."""
    expiry = date(2026, 10, 1)
    surface = IVSurface(
        underlying="x",
        spot=100.0,
        as_of=datetime.now(),
        points=(
            IVPoint(85.0, expiry, "put", 0.70, -0.15, 30),
            IVPoint(115.0, expiry, "call", 0.50, 0.15, 30),
        ),
    )
    assert surface.skew() == pytest.approx(0.20)


def test_skew_is_none_when_one_side_is_missing():
    """صفر گفتن یعنی «اسکیو نیست»، که با «نمی‌دانیم» فرق دارد."""
    expiry = date(2026, 10, 1)
    surface = IVSurface(
        underlying="x",
        spot=100.0,
        as_of=datetime.now(),
        points=(IVPoint(85.0, expiry, "put", 0.7, -0.15, 30),),
    )
    assert surface.skew() is None


def test_real_chain_skew_is_computable(surface):
    """روی زنجیره‌ی واقعی، اسکیو باید عددی بدهد (هر علامتی)."""
    assert surface.skew() is not None


def test_term_structure_has_one_value_per_expiry(surface):
    term = surface.term_structure()
    assert set(term) == set(surface.expiries)
    assert all(v > 0 for v in term.values())


def test_filter_narrows_by_type_and_expiry(surface):
    calls = surface.filter(option_type="call")
    assert calls and all(p.option_type == "call" for p in calls)

    first = surface.expiries[0]
    near = surface.filter(expiry=first)
    assert near and all(p.expiry == first for p in near)


def test_filter_by_max_days(surface):
    points = surface.filter(max_days=20)
    assert all(p.days_to_expiry <= 20 for p in points)


def test_is_atm_respects_the_tolerance():
    expiry = date(2026, 10, 1)
    assert IVPoint(100.0, expiry, "call", 0.5, 0.0, 30).is_atm()
    assert not IVPoint(200.0, expiry, "call", 0.5, 1.0, 30).is_atm()
    assert IVPoint(100.0, expiry, "call", 0.5, ATM_TOLERANCE, 30).is_atm()


def test_to_dict_is_json_serializable(surface):
    data = surface.to_dict()
    assert json.loads(json.dumps(data, ensure_ascii=False))["points"] == len(surface)
    assert data["atm_iv"] is not None


# ======================================================================
# تاریخچه و رتبه — قرارداد `None`
# ======================================================================
def _history(values: list[float], **kwargs) -> IVHistory:
    history = IVHistory(**kwargs)
    base = date(2026, 1, 1)
    for i, value in enumerate(values):
        history.record("x", value, base + timedelta(days=i))
    return history


def test_rank_is_none_below_the_minimum_sample():
    """مهم‌ترین قید: «نمی‌دانم» نباید به «متوسط» تبدیل شود."""
    history = _history([0.5] * 5, min_samples=20)
    result = history.rank("x", 0.5)

    assert result.percentile is None
    assert result.rank is None
    assert result.is_known is False
    assert result.is_rich() is None
    assert result.is_cheap() is None
    assert result.samples == 5


def test_rank_becomes_known_at_the_threshold():
    values = [0.4 + i * 0.01 for i in range(MIN_HISTORY_POINTS)]
    result = _history(values, min_samples=MIN_HISTORY_POINTS).rank("x", 0.5)
    assert result.is_known is True


def test_unknown_rank_still_reports_the_observed_range():
    """حتی وقتی صدک معنا ندارد، کمینه/بیشینه اطلاعات مفیدی است."""
    result = _history([0.4, 0.6], min_samples=20).rank("x", 0.5)
    assert result.low == pytest.approx(0.4)
    assert result.high == pytest.approx(0.6)


def test_percentile_counts_days_below_the_current_value():
    values = [0.1 * i for i in range(1, 21)]  # 0.1 .. 2.0
    result = _history(values, min_samples=20).rank("x", 1.05)
    # ده مقدار زیر ۱٫۰۵ است
    assert result.percentile == pytest.approx(50.0)


def test_lowest_value_is_percentile_zero():
    values = [0.4 + i * 0.01 for i in range(20)]
    assert _history(values, min_samples=20).rank("x", 0.0).percentile == 0.0


def test_highest_value_is_percentile_hundred():
    values = [0.4 + i * 0.01 for i in range(20)]
    assert _history(values, min_samples=20).rank("x", 9.0).percentile == 100.0


def test_rank_is_position_between_low_and_high():
    values = [0.4] * 10 + [0.8] * 10
    result = _history(values, min_samples=20).rank("x", 0.6)
    assert result.rank == pytest.approx(50.0)


def test_rank_is_clamped_to_the_range():
    values = [0.4 + i * 0.01 for i in range(20)]
    assert _history(values, min_samples=20).rank("x", 99.0).rank == 100.0
    assert _history(values, min_samples=20).rank("x", 0.0).rank == 0.0


def test_flat_history_gives_a_neutral_rank():
    """بازه‌ی صفر یعنی تقسیم بر صفر؛ ۵۰ محافظه‌کارانه‌ترین جواب است."""
    result = _history([0.5] * 20, min_samples=20).rank("x", 0.5)
    assert result.rank == pytest.approx(50.0)


def test_rich_and_cheap_thresholds():
    values = [0.1 * i for i in range(1, 21)]
    history = _history(values, min_samples=20)

    assert history.rank("x", 2.5).is_rich(threshold=80.0) is True
    assert history.rank("x", 0.05).is_cheap(threshold=20.0) is True
    assert history.rank("x", 1.05).is_rich(threshold=80.0) is False


def test_describe_says_when_history_is_short():
    text = _history([0.5], min_samples=20).rank("x", 0.5).describe()
    assert "کافی نیست" in text


def test_describe_reports_the_percentile_when_known():
    values = [0.4 + i * 0.01 for i in range(20)]
    text = _history(values, min_samples=20).rank("x", 0.5).describe()
    assert "صدک" in text


# -- ثبت و تداوم --------------------------------------------------------
def test_one_point_per_day():
    """چند نقطه در یک روز، صدک را به‌سمت روزهای پرمعامله وزن می‌داد."""
    history = IVHistory(min_samples=1)
    day = date(2026, 1, 1)
    history.record("x", 0.5, day)
    history.record("x", 0.9, day)

    assert history.sample_count("x") == 1
    assert history.series("x") == [0.9], "آخرین مقدار همان روز باید بماند"


def test_series_is_ordered_by_date():
    history = IVHistory(min_samples=1)
    base = date(2026, 1, 1)
    history.record("x", 0.9, base + timedelta(days=2))
    history.record("x", 0.5, base)
    history.record("x", 0.7, base + timedelta(days=1))
    assert history.series("x") == [0.5, 0.7, 0.9]


def test_invalid_values_are_not_recorded():
    history = IVHistory(min_samples=1)
    history.record("x", 0.0)
    history.record("x", -1.0)
    history.record("", 0.5)
    assert history.sample_count("x") == 0


def test_old_points_are_pruned():
    """IV سه سال پیش وضعیت امروز را توضیح نمی‌دهد."""
    history = IVHistory(max_days=10, min_samples=1)
    base = date(2026, 1, 1)
    for i in range(30):
        history.record("x", 0.5, base + timedelta(days=i))
    assert history.sample_count("x") <= 11


def test_history_survives_a_restart(tmp_path):
    """IV تاریخی از هیچ endpoint در دست نیست؛ بی‌تداوم، صدک هرگز معنا
    پیدا نمی‌کرد."""
    path = tmp_path / "iv.json"
    first = IVHistory(path=path, min_samples=1)
    first.record("x", 0.5, date(2026, 1, 1))
    first.save()

    assert IVHistory(path=path, min_samples=1).series("x") == [0.5]


def test_corrupt_history_file_is_ignored(tmp_path):
    path = tmp_path / "iv.json"
    path.write_text("{ broken", encoding="utf-8")
    assert IVHistory(path=path).sample_count("x") == 0


def test_non_numeric_stored_values_are_dropped(tmp_path):
    path = tmp_path / "iv.json"
    path.write_text(
        json.dumps({"x": {"2026-01-01": "بالا", "2026-01-02": 0.5}}),
        encoding="utf-8",
    )
    assert IVHistory(path=path, min_samples=1).series("x") == [0.5]


def test_symbols_are_tracked_separately():
    history = IVHistory(min_samples=1)
    history.record("a", 0.5, date(2026, 1, 1))
    history.record("b", 0.9, date(2026, 1, 1))
    assert history.series("a") == [0.5]
    assert history.series("b") == [0.9]


def test_record_surface_stores_the_atm_value(surface):
    history = IVHistory(min_samples=1)
    history.record_surface(surface, TODAY)
    assert history.series(surface.underlying) == [pytest.approx(surface.atm_iv())]


def test_record_surface_of_an_empty_surface_is_a_noop():
    history = IVHistory(min_samples=1)
    history.record_surface(
        IVSurface(underlying="x", spot=1.0, as_of=datetime.now()), TODAY
    )
    assert history.sample_count("x") == 0


# ======================================================================
# اتصال به استراتژی
# ======================================================================
def test_context_iv_rank_defaults_to_none(context):
    """بدون تاریخچه، استراتژی باید به معیار قبلی برگردد."""
    assert context.iv_rank is None


def test_generator_records_and_ranks_iv(market_data, option_chain, recorded_symbol):
    from signals.signal_generator import GeneratorConfig, SignalGenerator

    history = IVHistory(min_samples=1)
    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        config=GeneratorConfig(symbols=[recorded_symbol]),
        iv_history=history,
    )
    built = generator.build_context(recorded_symbol)

    assert history.sample_count(recorded_symbol) == 1
    assert built.iv_rank is not None
    assert built.iv_rank.current > 0


def test_generator_without_history_leaves_rank_none(
    market_data, option_chain, recorded_symbol
):
    from signals.signal_generator import GeneratorConfig, SignalGenerator

    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        config=GeneratorConfig(symbols=[recorded_symbol]),
    )
    assert generator.build_context(recorded_symbol).iv_rank is None


def test_iv_rank_failure_does_not_break_the_context(
    market_data, option_chain, recorded_symbol
):
    """رتبه‌ی IV یک افزونه است؛ شکستش نباید پاس رصد را بخواباند."""
    from signals.signal_generator import GeneratorConfig, SignalGenerator

    class _Broken:
        def record(self, *a, **k):
            raise RuntimeError("دیسک پر است")

        def save(self):
            pass

        def rank(self, *a, **k):
            raise RuntimeError("خراب")

    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        config=GeneratorConfig(symbols=[recorded_symbol]),
        iv_history=_Broken(),
    )
    assert generator.build_context(recorded_symbol).iv_rank is None


def test_strategy_ignores_an_unknown_rank(context):
    """با تاریخچه‌ی ناکافی، رفتار باید دقیقاً مثل قبل باشد."""
    import dataclasses

    from strategies.neutral_strategy import NeutralStrategy

    unknown = _history([0.5], min_samples=20).rank("x", 0.5)
    strategy = NeutralStrategy()

    plain = strategy.generate(context)
    with_rank = strategy.generate(dataclasses.replace(context, iv_rank=unknown))
    assert len(plain) == len(with_rank)


def test_iv_rank_can_block_a_signal(context):
    """قلب ماجرا: IV نسبت به نوسان تاریخی گران، ولی نسبت به گذشته‌ی
    خودِ نماد **ارزان** ⇒ سیگنال نباید صادر شود."""
    import dataclasses

    from strategies.neutral_strategy import NeutralStrategy

    strategy = NeutralStrategy()
    # رتبه‌ی پایین: صدک ۵ از ۳۰ نمونه
    values = [1.0 + i * 0.01 for i in range(30)]
    low_rank = _history(values, min_samples=20).rank("x", 0.5)
    assert low_rank.is_known and low_rank.percentile < 30

    blocked = strategy.generate(dataclasses.replace(context, iv_rank=low_rank))
    covered = [s for s in blocked if s.metadata.get("structure") == "covered_call"]
    assert covered == []


def test_signal_metadata_carries_the_rank(context):
    import dataclasses

    from strategies.neutral_strategy import NeutralStrategy

    values = [0.1 * i for i in range(1, 31)]
    high = _history(values, min_samples=20).rank("x", 9.0)
    strategy = NeutralStrategy(params={"use_iv_rank": False})

    signals = strategy.generate(dataclasses.replace(context, iv_rank=high))
    for signal in signals:
        assert signal.metadata["iv_percentile"] == pytest.approx(100.0)
        assert signal.metadata["iv_history_days"] == 30


# ======================================================================
# wiring
# ======================================================================
def test_iv_history_is_on_by_default():
    """روشن بودنش رفتار کسی را عوض نمی‌کند، فقط تاریخچه می‌سازد."""
    import bootstrap
    from config.loader import default_settings

    assert bootstrap.build_iv_history(default_settings()) is not None


def test_iv_history_can_be_disabled():
    import bootstrap
    from config.loader import default_settings

    settings = default_settings()
    settings["iv_history"]["enabled"] = False
    assert bootstrap.build_iv_history(settings) is None


def test_iv_history_settings_reach_the_object():
    import bootstrap
    from config.loader import default_settings

    settings = default_settings()
    settings["iv_history"]["min_samples"] = 99
    assert bootstrap.build_iv_history(settings).min_samples == 99

"""تست‌های اسکنر ساختارها روی زنجیره‌ی **واقعی** ضبط‌شده.

تمرکز روی رد کردن ساختارهای نامعتبر: ساختاری که روی کاغذ سودده است
ولی یک پایه‌اش قابل معامله نیست، بدتر از پیدا نکردن آن است.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from data.option_chain_client import OptionChain, OptionContract
from data.tsetmc_option_chain_client import FilePayloadSource, TsetmcOptionChainClient
from signals.signal_model import Side
from strategies.payoff import PositionLeg, StrategyPayoff
from strategies.scanner import (
    RANK_KEYS,
    SCAN_KINDS,
    ScanFilters,
    StrategyScanner,
    rank_strategies,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tsetmc_option_market_watch.json"
UNDERLYING = "خودرو"


@pytest.fixture
def chain():
    if not FIXTURE.exists():
        pytest.skip("نمونه‌ی ضبط‌شده نیست؛ scripts/record_fixtures.py را اجرا کنید.")
    client = TsetmcOptionChainClient(
        FilePayloadSource(FIXTURE), cache_ttl_seconds=float("inf")
    )
    return client.get_chain(UNDERLYING)


@pytest.fixture
def scanner():
    return StrategyScanner(ScanFilters(min_open_interest=10))


def _chain(*contracts, spot=750.0):
    """زنجیره‌ی کمینه برای تست گیت‌ها."""
    return OptionChain(
        underlying=UNDERLYING, spot_price=spot,
        as_of=datetime.now(), contracts=tuple(contracts),
    )


def _contract(kind, strike, expiry, bid=10.0, ask=12.0, oi=500):
    return OptionContract(
        symbol=f"{kind}{strike}", underlying=UNDERLYING, option_type=kind,
        strike=strike, expiry=expiry, bid=bid, ask=ask, last_price=11.0,
        open_interest=oi, volume=100, contract_size=1_000,
    )


# ======================================================================
# روی داده‌ی واقعی
# ======================================================================
def test_scans_find_structures_in_the_real_chain(chain, scanner):
    """هر سه اسکنر باید روی زنجیره‌ی واقعی چیزی پیدا کنند."""
    result = scanner.scan_all(chain)
    assert result["long_straddle"], "استردلی پیدا نشد"
    assert result["collar"], "کالری پیدا نشد"
    assert result["iron_condor"], "آیرون کاندوری پیدا نشد"


def test_straddle_legs_share_strike_and_expiry(chain, scanner):
    for straddle in scanner.scan_long_straddle(chain):
        options = [leg for leg in straddle.legs if leg.is_option]
        assert len({leg.strike for leg in options}) == 1
        assert len({leg.expiry for leg in options}) == 1
        assert {leg.option_type for leg in options} == {"call", "put"}


def test_condor_strikes_are_correctly_ordered(chain, scanner):
    """ترتیب استرایک شرط تعریف آیرون کاندور است."""
    for condor in scanner.scan_iron_condor(chain):
        meta = condor.metadata
        assert (
            meta["long_put_strike"]
            < meta["short_put_strike"]
            < meta["short_call_strike"]
            < meta["long_call_strike"]
        )


def test_condor_is_always_a_credit_structure(chain, scanner):
    """آیرون کاندور بدهکار یعنی ساختار معیوب؛ نباید پیشنهاد شود."""
    for condor in scanner.scan_iron_condor(chain):
        assert condor.net_credit is not None
        assert condor.net_credit > 0


def test_collar_put_is_below_and_call_above_spot(chain, scanner):
    """پوت باید محافظت بدهد و کال سقف — نه برعکس."""
    spot = chain.spot_price
    for collar in scanner.scan_collar(chain):
        assert collar.metadata["put_strike"] < spot
        assert collar.metadata["call_strike"] > spot


def test_every_structure_shares_one_expiration(chain, scanner):
    """ترکیب سررسیدها ساختار دیگری است (تقویمی)، نه اینها.

    اسپرد تقویمی عمداً مستثناست — تعریفش **دو** سررسید است. برای همان،
    `single_expiry=False` می‌گیرد تا معیارهای وابسته به منحنی سررسید
    عددِ غلط ندهند.
    """
    for name, group in scanner.scan_all(chain).items():
        if name == "calendar_spread":
            continue
        for strategy in group:
            expiries = {leg.expiry for leg in strategy.legs if leg.is_option}
            assert len(expiries) == 1, name
            assert strategy.single_expiry is True


def test_calendar_spread_spans_two_expirations(chain, scanner):
    """و برعکس: اسپرد تقویمی **باید** دو سررسید داشته باشد."""
    found = scanner.scan_calendar_spread(chain)
    for strategy in found:
        expiries = {leg.expiry for leg in strategy.legs if leg.is_option}
        assert len(expiries) == 2
        assert strategy.single_expiry is False


# ======================================================================
# رد کردن داده‌ی نامعتبر
# ======================================================================
def test_mismatched_expiry_is_never_paired(scanner):
    """دو سررسید متفاوت نباید یک استردل بسازند."""
    near = date.today() + timedelta(days=30)
    far = date.today() + timedelta(days=60)
    chain = _chain(_contract("call", 750, near), _contract("put", 750, far), spot=750.0)
    assert scanner.scan_long_straddle(chain) == []


def test_invalid_strike_is_rejected(scanner):
    expiry = date.today() + timedelta(days=30)
    chain = _chain(_contract("call", 0, expiry), _contract("put", 0, expiry), spot=750.0)
    assert scanner.scan_long_straddle(chain) == []


def test_missing_quotes_are_rejected(scanner):
    """بدون هر دو طرف مظنه، قیمت اجرا نامعلوم است."""
    expiry = date.today() + timedelta(days=30)
    chain = _chain(
        _contract("call", 750, expiry, bid=None, ask=None),
        _contract("put", 750, expiry, bid=None, ask=None),
        spot=750.0,
    )
    assert scanner.scan_long_straddle(chain) == []


def test_inverted_quotes_are_rejected(scanner):
    """ask کمتر از bid یعنی داده خراب است."""
    expiry = date.today() + timedelta(days=30)
    chain = _chain(
        _contract("call", 750, expiry, bid=50, ask=10),
        _contract("put", 750, expiry, bid=50, ask=10),
        spot=750.0,
    )
    assert scanner.scan_long_straddle(chain) == []


def test_wide_spread_is_rejected(scanner):
    """اسپرد پهن یعنی ورود و خروج گران؛ ساختار عملاً اجرا نمی‌شود."""
    expiry = date.today() + timedelta(days=30)
    chain = _chain(
        _contract("call", 750, expiry, bid=1, ask=100),
        _contract("put", 750, expiry, bid=1, ask=100),
        spot=750.0,
    )
    assert scanner.scan_long_straddle(chain) == []


def test_low_open_interest_is_rejected(scanner):
    expiry = date.today() + timedelta(days=30)
    chain = _chain(
        _contract("call", 750, expiry, oi=1),
        _contract("put", 750, expiry, oi=1),
        spot=750.0,
    )
    assert scanner.scan_long_straddle(chain) == []


def test_expired_and_too_far_contracts_are_rejected(scanner):
    past = date.today() - timedelta(days=1)
    far = date.today() + timedelta(days=400)
    for expiry in (past, far):
        chain = _chain(_contract("call", 750, expiry), _contract("put", 750, expiry), spot=750.0)
        assert scanner.scan_long_straddle(chain) == []


def test_empty_chain_returns_nothing(scanner):
    """هر اسکنری که به `scan_all` اضافه شود هم باید خالی برگردد."""
    result = scanner.scan_all(_chain())
    assert result, "scan_all نباید دیکشنری خالی بدهد"
    assert all(found == [] for found in result.values()), result


def test_zero_spot_price_is_handled(scanner):
    """قیمت پایه صفر یعنی داده ناقص؛ نباید کرش کند."""
    expiry = date.today() + timedelta(days=30)
    chain = _chain(_contract("call", 750, expiry), _contract("put", 750, expiry), spot=0.0)
    assert scanner.scan_collar(chain) == []


# ======================================================================
# رتبه‌بندی
# ======================================================================
def test_ranking_orders_by_roi(chain, scanner):
    ranked = rank_strategies(scanner.scan_iron_condor(chain), "roi")
    rois = [s.roi for s in ranked if s.roi is not None]
    assert rois == sorted(rois, reverse=True)


def test_ranking_puts_unknown_values_last():
    """`None` یعنی نامعلوم یا نامحدود — نباید صفر فرض شود.

    اگر صفر فرض شود، لانگ کال (با سود نامحدود) ته لیست می‌افتد؛ برعکس
    واقعیت.
    """
    expiry = date.today() + timedelta(days=30)
    unlimited = StrategyPayoff(
        "long_call", UNDERLYING,
        [PositionLeg(Side.BUY, 1, 60, 1000, "call", 750, expiry)],
        expiry, underlying_price=750,
    )
    bounded = StrategyPayoff(
        "spread", UNDERLYING,
        [
            PositionLeg(Side.BUY, 1, 60, 1000, "call", 750, expiry),
            PositionLeg(Side.SELL, 1, 30, 1000, "call", 800, expiry),
        ],
        expiry, underlying_price=750,
    )
    ranked = rank_strategies([unlimited, bounded], "max_profit")
    assert ranked[-1] is unlimited, "مقدار نامعلوم باید آخر بیاید"


def test_unknown_rank_key_is_rejected(chain, scanner):
    with pytest.raises(ValueError, match="ناشناخته"):
        rank_strategies(scanner.scan_iron_condor(chain), "bogus_metric")


def test_all_rank_keys_are_usable(chain, scanner):
    """هر معیار اعلام‌شده باید واقعاً کار کند."""
    condors = scanner.scan_iron_condor(chain)
    if not condors:
        pytest.skip("ساختاری برای رتبه‌بندی نیست")
    for key in RANK_KEYS:
        assert len(rank_strategies(condors, key)) == len(condors)


def test_ranking_by_liquidity_prefers_tighter_spreads(chain, scanner):
    ranked = rank_strategies(scanner.scan_iron_condor(chain), "liquidity_score")
    scores = [s.liquidity_score for s in ranked if s.liquidity_score is not None]
    assert scores == sorted(scores, reverse=True)


# ======================================================================
# اسپردهای عمودی
# ======================================================================
VERTICAL_SCANS = (
    "bull_call_spread",
    "bear_call_spread",
    "bull_put_spread",
    "bear_put_spread",
)


@pytest.mark.parametrize("kind", VERTICAL_SCANS)
def test_vertical_spreads_found_in_the_real_chain(chain, scanner, kind):
    found = getattr(scanner, f"scan_{kind}")(chain)
    assert found, f"{kind} روی زنجیره‌ی واقعی پیدا نشد"
    for structure in found:
        assert structure.strategy_type == kind
        assert len(structure.legs) == 2


@pytest.mark.parametrize("kind", VERTICAL_SCANS)
def test_vertical_spreads_have_one_long_and_one_short(chain, scanner, kind):
    """اسپرد عمودی دقیقاً یک پایه‌ی خرید و یک پایه‌ی فروش دارد."""
    for structure in getattr(scanner, f"scan_{kind}")(chain):
        longs = [leg for leg in structure.legs if leg.is_long]
        shorts = [leg for leg in structure.legs if not leg.is_long]
        assert len(longs) == 1 and len(shorts) == 1


@pytest.mark.parametrize("kind", VERTICAL_SCANS)
def test_vertical_legs_share_type_and_expiry(chain, scanner, kind):
    """هر دو پایه یک نوع و یک سررسید — وگرنه ساختار دیگری است."""
    expected_type = "call" if "call" in kind else "put"
    for structure in getattr(scanner, f"scan_{kind}")(chain):
        assert {leg.option_type for leg in structure.legs} == {expected_type}
        assert len({leg.expiry for leg in structure.legs}) == 1


@pytest.mark.parametrize(
    "kind, long_is_lower",
    [
        ("bull_call_spread", True),
        ("bear_call_spread", False),
        ("bull_put_spread", True),
        ("bear_put_spread", False),
    ],
)
def test_vertical_leg_direction_matches_the_strategy(
    chain, scanner, kind, long_is_lower
):
    """جهتِ اشتباه یعنی ساختار دقیقاً برعکسِ اسمش عمل می‌کند."""
    for structure in getattr(scanner, f"scan_{kind}")(chain):
        long_leg = next(leg for leg in structure.legs if leg.is_long)
        short_leg = next(leg for leg in structure.legs if not leg.is_long)
        if long_is_lower:
            assert long_leg.strike < short_leg.strike, kind
        else:
            assert long_leg.strike > short_leg.strike, kind


@pytest.mark.parametrize("kind", VERTICAL_SCANS)
def test_vertical_profit_and_loss_are_both_bounded(chain, scanner, kind):
    """این چیزی است که اسپرد را از پوزیشن تک‌پایه متمایز می‌کند.

    خرید کالِ تنها سود نامحدود دارد؛ اسپرد سقف‌دار است. اگر یکی از این دو
    `None` (نامحدود) برگردد، پایه‌ها اشتباه بسته شده‌اند.
    """
    for structure in getattr(scanner, f"scan_{kind}")(chain):
        assert structure.max_profit is not None, kind
        assert structure.max_loss is not None, kind


@pytest.mark.parametrize("kind", VERTICAL_SCANS)
def test_vertical_value_never_exceeds_the_spread_width(chain, scanner, kind):
    """قید ریاضیِ اسپرد عمودی: ارزشش هرگز از عرض × اندازه بیشتر نیست.

    نقضش یعنی مظنه‌ها ناسازگارند (بازار رقیق) — نه فرصت آربیتراژ.
    """
    for structure in getattr(scanner, f"scan_{kind}")(chain):
        ceiling = structure.metadata["max_spread_value"]
        assert abs(structure.net_premium) <= ceiling
        assert structure.max_profit <= ceiling + 1e-6
        assert abs(structure.max_loss) <= ceiling + 1e-6


@pytest.mark.parametrize("kind", VERTICAL_SCANS)
def test_vertical_max_profit_is_positive(chain, scanner, kind):
    """ساختاری که در بهترین حالت هم سود نمی‌دهد، پیشنهاد نمی‌شود."""
    for structure in getattr(scanner, f"scan_{kind}")(chain):
        assert structure.max_profit > 0


def test_debit_spread_max_loss_equals_its_cost(chain, scanner):
    """در اسپرد بدهکار، بیشترین زیان همان پولی است که پرداخته‌اید."""
    for structure in scanner.scan_bull_call_spread(chain):
        if not structure.metadata["is_debit"]:
            continue
        assert structure.max_loss == pytest.approx(-structure.net_premium, rel=1e-6)


def test_debit_spread_profit_is_width_minus_cost(chain, scanner):
    """قید بسته‌ی اسپرد بدهکار: سود = عرض − هزینه."""
    for structure in scanner.scan_bull_call_spread(chain):
        if not structure.metadata["is_debit"]:
            continue
        expected = structure.metadata["max_spread_value"] - structure.net_premium
        assert structure.max_profit == pytest.approx(expected, rel=1e-6)


def test_credit_spread_profit_equals_the_credit(chain, scanner):
    """در اسپرد بستانکار، بیشترین سود همان اعتباری است که گرفته‌اید."""
    for structure in scanner.scan_bull_put_spread(chain):
        if structure.metadata["is_debit"]:
            continue
        assert structure.max_profit == pytest.approx(-structure.net_premium, rel=1e-6)


def test_spread_width_respects_the_filter(chain):
    """اسپرد بیش از حد پهن عملاً یک پوزیشن تک‌پایه است با هزینه‌ی بیشتر."""
    narrow = StrategyScanner(
        ScanFilters(min_open_interest=10, max_spread_width_pct=0.05)
    )
    wide = StrategyScanner(
        ScanFilters(min_open_interest=10, max_spread_width_pct=0.50)
    )
    spot = chain.spot_price

    tight = narrow.scan_bull_call_spread(chain, limit=100)
    loose = wide.scan_bull_call_spread(chain, limit=100)

    assert len(tight) <= len(loose)
    for structure in tight:
        assert structure.metadata["spread_width"] / spot <= 0.05 + 1e-9


def test_vertical_needs_two_strikes(scanner):
    """یک استرایک، اسپرد نمی‌سازد."""
    expiry = date.today() + timedelta(days=30)
    chain = _chain(_contract("call", 750, expiry), spot=750.0)
    assert scanner.scan_bull_call_spread(chain) == []


def test_vertical_never_pairs_across_expiries(scanner):
    """دو سررسید متفاوت یک اسپرد عمودی نمی‌سازند (آن تقویمی است)."""
    near = date.today() + timedelta(days=30)
    far = date.today() + timedelta(days=60)
    chain = _chain(
        _contract("call", 700, near),
        _contract("call", 800, far),
        spot=750.0,
    )
    assert scanner.scan_bull_call_spread(chain) == []


def test_vertical_ignores_the_other_option_type(scanner):
    """اسپرد کال نباید از پوت پایه بگیرد."""
    expiry = date.today() + timedelta(days=30)
    chain = _chain(
        _contract("call", 700, expiry),
        _contract("put", 800, expiry),
        spot=750.0,
    )
    assert scanner.scan_bull_call_spread(chain) == []
    assert scanner.scan_bull_put_spread(chain) == []


def test_inconsistent_quotes_are_rejected(scanner):
    """اگر پرمیوم از عرض اسپرد بیشتر باشد، داده خراب است نه آربیتراژ.

    اینجا کالِ ۷۰۰ ارزان‌تر از کالِ ۸۰۰ مظنه خورده — که در بازار سالم
    ممکن نیست. ساختار باید رد شود، نه اینکه ROI نجومی گزارش کند.
    """
    expiry = date.today() + timedelta(days=30)
    chain = _chain(
        # عرض ۱۰ ⇒ سقف ارزش ۱۰×۱۰۰۰ = ۱۰٬۰۰۰، ولی هزینه ۵۰٬۰۰۰ می‌شود
        _contract("call", 750, expiry, bid=5.0, ask=60.0),
        _contract("call", 760, expiry, bid=5.0, ask=6.0),
        spot=750.0,
    )
    for structure in scanner.scan_bull_call_spread(chain):
        ceiling = structure.metadata["max_spread_value"]
        assert abs(structure.net_premium) <= ceiling


# ======================================================================
# اسپرد تقویمی
# ======================================================================
def test_calendar_spread_found_in_the_real_chain(chain, scanner):
    found = scanner.scan_calendar_spread(chain)
    assert found, "اسپرد تقویمی روی زنجیره‌ی واقعی پیدا نشد"
    for structure in found:
        assert structure.strategy_type == "calendar_spread"
        assert len(structure.legs) == 2


def test_calendar_legs_share_strike_but_not_expiry(chain, scanner):
    """تعریف ساختار: هم‌استرایک، دو سررسید."""
    for structure in scanner.scan_calendar_spread(chain):
        assert len({leg.strike for leg in structure.legs}) == 1
        assert len({leg.expiry for leg in structure.legs}) == 2
        assert len({leg.option_type for leg in structure.legs}) == 1


def test_calendar_sells_near_and_buys_far(chain, scanner):
    """جهت مهم است: نزدیک فروخته و دور خریده می‌شود، نه برعکس."""
    for structure in scanner.scan_calendar_spread(chain):
        short_leg = next(leg for leg in structure.legs if not leg.is_long)
        long_leg = next(leg for leg in structure.legs if leg.is_long)
        assert short_leg.expiry < long_leg.expiry


def test_calendar_is_always_a_debit(chain, scanner):
    """سررسید دورتر ارزش زمانی بیشتری دارد، پس ورود هزینه دارد.

    بستانکار بودن یعنی مظنه‌ها ناسازگارند، نه یک فرصت.
    """
    for structure in scanner.scan_calendar_spread(chain):
        assert structure.net_premium > 0


def test_calendar_reports_unknown_instead_of_a_wrong_number(chain, scanner):
    """قلب صداقتِ این اسکنر.

    منحنی سود این کلاس بر ارزش ذاتیِ سررسید بنا شده. برای اسپرد تقویمی
    آن مدل غلط است: وقتی پایه‌ی نزدیک منقضی می‌شود پایه‌ی دور هنوز ارزش
    زمانی دارد — و همان، کلِ سود ساختار است. صفر فرض کردنش یک اسپرد سالم
    را «زیان کامل» نشان می‌دهد، که بدتر از نگفتن است.
    """
    found = scanner.scan_calendar_spread(chain)
    assert found

    for structure in found:
        assert structure.single_expiry is False
        assert structure.max_profit is None
        assert structure.max_loss is None
        assert structure.roi is None
        assert structure.risk_reward is None
        assert structure.breakevens == []
        assert structure.profit_zone is None

        # ولی هزینه‌ی ورود دقیق است و باید بماند
        assert structure.net_cost > 0
        assert structure.metadata["payoff_is_approximate"] is True
        assert structure.metadata["approximation_note"]


def test_calendar_is_sorted_by_cost_not_roi(chain, scanner):
    """ROI اینجا `None` است، پس مرتب‌سازی با آن بی‌معنا بود."""
    found = scanner.scan_calendar_spread(chain, limit=10)
    costs = [s.net_cost for s in found]
    assert costs == sorted(costs)


def test_calendar_respects_the_minimum_gap(chain):
    """دو سررسید نزدیک‌به‌هم ارزش زمانیِ معناداری اختلاف ندارند."""
    strict = StrategyScanner(
        ScanFilters(min_open_interest=10, min_calendar_gap_days=90)
    )
    loose = StrategyScanner(
        ScanFilters(min_open_interest=10, min_calendar_gap_days=1)
    )
    assert len(strict.scan_calendar_spread(chain, limit=100)) <= len(
        loose.scan_calendar_spread(chain, limit=100)
    )
    for structure in strict.scan_calendar_spread(chain, limit=100):
        assert structure.metadata["gap_days"] >= 90


def test_calendar_needs_two_expiries_at_the_same_strike(scanner):
    expiry = date.today() + timedelta(days=30)
    chain = _chain(
        _contract("call", 700, expiry),
        _contract("call", 800, expiry),
        spot=750.0,
    )
    assert scanner.scan_calendar_spread(chain) == []


def test_calendar_metadata_records_both_expiries(chain, scanner):
    for structure in scanner.scan_calendar_spread(chain):
        meta = structure.metadata
        assert meta["near_expiry"] < meta["far_expiry"]
        assert meta["gap_days"] > 0
        assert meta["option_type"] in ("call", "put")


# ======================================================================
# فهرست ساختارها — تنها منبع حقیقت
# ======================================================================
def test_scan_all_covers_every_declared_kind(chain, scanner):
    """اگر اسکنری به `SCAN_KINDS` اضافه شود ولی متدش نباشد، اینجا می‌شکند."""
    result = scanner.scan_all(chain)
    assert set(result) == set(SCAN_KINDS)


def test_every_declared_kind_has_a_scanner_method():
    for name in SCAN_KINDS:
        assert hasattr(StrategyScanner, f"scan_{name}"), name


def test_every_kind_has_a_persian_label():
    """برچسب خالی در UI یعنی کلید انگلیسی به کاربر نشان داده می‌شود."""
    for name, label in SCAN_KINDS.items():
        assert label.strip(), name
        assert label != name

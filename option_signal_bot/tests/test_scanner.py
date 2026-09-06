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
    """ترکیب سررسیدها ساختار دیگری است (تقویمی)، نه اینها."""
    for group in scanner.scan_all(chain).values():
        for strategy in group:
            expiries = {leg.expiry for leg in strategy.legs if leg.is_option}
            assert len(expiries) == 1


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
    chain = _chain()
    assert scanner.scan_all(chain) == {
        "long_straddle": [], "collar": [], "iron_condor": []
    }


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

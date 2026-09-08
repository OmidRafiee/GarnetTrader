"""تست‌های محاسبه‌ی سود/زیان ساختارهای چندپایه.

اعداد این تست‌ها **دستی** حساب شده‌اند تا اگر منطق عوض شود، تست
شکست بخورد نه اینکه با آن هم‌راه شود.

تمرکز ویژه روی واحدها: خطای `contract_size` بی‌صدا همه‌چیز را ۱۰۰۰
برابر غلط می‌کند.
"""

from __future__ import annotations

from datetime import date

import pytest

from signals.signal_model import Side
from strategies.payoff import PositionLeg, StrategyPayoff, underlying_leg

EXPIRY = date(2026, 9, 30)
SIZE = 1_000


def _opt(side, price, kind, strike, qty=1, size=SIZE, **kw):
    return PositionLeg(
        side=side, quantity=qty, entry_price=price, contract_size=size,
        option_type=kind, strike=strike, expiry=EXPIRY, **kw
    )


# ======================================================================
# لانگ استردل
# ======================================================================
@pytest.fixture
def straddle():
    """کال و پوت استرایک ۷۵۰، پرمیوم ۶۰ و ۴۰ → هزینه ۱۰۰ در واحد."""
    return StrategyPayoff(
        "long_straddle", "خودرو",
        [_opt(Side.BUY, 60, "call", 750), _opt(Side.BUY, 40, "put", 750)],
        EXPIRY, underlying_price=750,
    )


def test_straddle_profits_on_large_rise(straddle):
    """صعود شدید: کال ذاتی می‌گیرد، پوت بی‌ارزش می‌شود."""
    # در ۹۵۰: کال ذاتی ۲۰۰ − پرمیوم ۱۰۰ = ۱۰۰ در واحد × ۱۰۰۰
    assert straddle.payoff(950) == pytest.approx(100_000)


def test_straddle_profits_on_large_fall(straddle):
    """نزول شدید: پوت ذاتی می‌گیرد، کال بی‌ارزش می‌شود."""
    # در ۵۵۰: پوت ذاتی ۲۰۰ − پرمیوم ۱۰۰ = ۱۰۰ در واحد
    assert straddle.payoff(550) == pytest.approx(100_000)


def test_straddle_max_loss_is_at_the_strike(straddle):
    """بدترین حالت: قیمت دقیقاً روی استرایک بماند، هر دو پایه بی‌ارزش."""
    assert straddle.payoff(750) == pytest.approx(-100_000)
    assert straddle.max_loss == pytest.approx(-100_000)


def test_straddle_breakevens_are_strike_plus_minus_premium(straddle):
    assert straddle.lower_breakeven == pytest.approx(650, abs=2)
    assert straddle.upper_breakeven == pytest.approx(850, abs=2)


def test_straddle_max_profit_is_unlimited_not_zero(straddle):
    """سود نامحدود باید `None` باشد.

    اگر صفر برگردد، مرتب‌سازی بر اساس سود، استردل را ته لیست می‌گذارد —
    برعکس واقعیت.
    """
    assert straddle.max_profit is None


def test_straddle_net_cost_counts_contract_size_once(straddle):
    """ضرب دوباره در اندازه‌ی قرارداد، خطای ۱۰۰۰ برابری می‌سازد."""
    assert straddle.net_cost == pytest.approx(100 * SIZE)


# ======================================================================
# کالر
# ======================================================================
@pytest.fixture
def collar():
    """سهم ۷۵۰ + پوت ۷۰۰ (۲۰) + فروش کال ۸۰۰ (۲۵)."""
    return StrategyPayoff(
        "collar", "خودرو",
        [
            underlying_leg("خودرو", 750, 1, SIZE),
            _opt(Side.BUY, 20, "put", 700),
            _opt(Side.SELL, 25, "call", 800),
        ],
        EXPIRY, underlying_price=750,
    )


def test_collar_caps_profit_in_a_rising_market(collar):
    """سقف سود: بالای استرایک کال، سود ثابت می‌ماند."""
    at_cap = collar.payoff(800)
    assert collar.payoff(900) == pytest.approx(at_cap)
    assert collar.payoff(2000) == pytest.approx(at_cap)


def test_collar_floors_loss_in_a_falling_market(collar):
    """کف حفاظت: زیر استرایک پوت، زیان ثابت می‌ماند."""
    at_floor = collar.payoff(700)
    assert collar.payoff(500) == pytest.approx(at_floor)
    assert collar.payoff(0) == pytest.approx(at_floor)


def test_collar_max_profit_and_loss_are_bounded(collar):
    """کالر هر دو طرف محدود است — نه سود نامحدود، نه زیان نامحدود."""
    # سقف: (۸۰۰−۷۵۰ سهم) + ۵ اعتبار خالص = ۵۵ در واحد
    assert collar.max_profit == pytest.approx(55_000)
    # کف: (۷۰۰−۷۵۰) + ۵ = −۴۵ در واحد
    assert collar.max_loss == pytest.approx(-45_000)


def test_collar_short_call_premium_offsets_put_cost(collar):
    """پرمیوم کال فروخته باید هزینه‌ی پوت را کم کند.

    ۲۰ پرداخت − ۲۵ دریافت = ۵ **بستانکار**.
    """
    assert collar.net_premium == pytest.approx(-5 * SIZE)
    assert collar.net_credit == pytest.approx(5 * SIZE)


def test_collar_breakeven_accounts_for_net_credit(collar):
    """با اعتبار خالص ۵، سر به سر زیر قیمت ورود سهم است."""
    assert collar.lower_breakeven == pytest.approx(745, abs=2)


# ======================================================================
# آیرون کاندور
# ======================================================================
@pytest.fixture
def condor():
    """خرید پوت ۶۵۰(۱۰) / فروش پوت ۷۰۰(۲۵) / فروش کال ۸۰۰(۲۵) / خرید کال ۸۵۰(۱۰).

    اعتبار خالص: ۲۵+۲۵−۱۰−۱۰ = ۳۰ در واحد.
    """
    return StrategyPayoff(
        "iron_condor", "خودرو",
        [
            _opt(Side.BUY, 10, "put", 650),
            _opt(Side.SELL, 25, "put", 700),
            _opt(Side.SELL, 25, "call", 800),
            _opt(Side.BUY, 10, "call", 850),
        ],
        EXPIRY, underlying_price=750,
    )


def test_condor_max_profit_inside_the_zone(condor):
    """بین دو استرایک فروخته‌شده، همه‌ی اعتبار حفظ می‌شود."""
    assert condor.payoff(750) == pytest.approx(30_000)
    assert condor.max_profit == pytest.approx(30_000)


def test_condor_profit_at_short_strikes_is_still_max(condor):
    """دقیقاً روی استرایک‌های فروخته‌شده، هنوز حداکثر سود است."""
    assert condor.payoff(700) == pytest.approx(30_000)
    assert condor.payoff(800) == pytest.approx(30_000)


def test_condor_max_loss_outside_the_wings(condor):
    """بیرون از بال‌ها: عرض اسپرد منهای اعتبار."""
    # عرض ۵۰ − اعتبار ۳۰ = ۲۰ زیان در واحد
    assert condor.payoff(600) == pytest.approx(-20_000)
    assert condor.payoff(900) == pytest.approx(-20_000)
    assert condor.max_loss == pytest.approx(-20_000)


def test_condor_loss_is_capped_by_long_wings(condor):
    """بال‌های خریداری‌شده زیان را محدود می‌کنند."""
    assert condor.payoff(0) == pytest.approx(-20_000)
    assert condor.payoff(5000) == pytest.approx(-20_000)


def test_condor_breakevens_are_short_strikes_plus_credit(condor):
    assert condor.lower_breakeven == pytest.approx(670, abs=2)
    assert condor.upper_breakeven == pytest.approx(830, abs=2)


def test_condor_profit_zone_matches_breakevens(condor):
    zone = condor.profit_zone
    assert zone is not None
    assert zone[0] == pytest.approx(670, abs=2)
    assert zone[1] == pytest.approx(830, abs=2)


def test_condor_risk_reward_and_roi(condor):
    assert condor.risk_reward == pytest.approx(30_000 / 20_000)
    # ساختار بستانکار با ریسک تعریف‌شده: سرمایه = حداکثر زیان
    assert condor.required_capital == pytest.approx(20_000)
    assert condor.roi == pytest.approx(1.5)


def test_condor_net_credit_is_positive(condor):
    assert condor.net_credit == pytest.approx(30_000)
    assert condor.net_premium == pytest.approx(-30_000)


# ======================================================================
# واحدها و داده‌ی ناقص
# ======================================================================
def test_quantity_scales_payoff_linearly():
    """۵ قرارداد باید دقیقاً ۵ برابر یک قرارداد بدهد."""
    one = StrategyPayoff("long_call", "خودرو", [_opt(Side.BUY, 60, "call", 750)],
                         EXPIRY, underlying_price=750)
    five = StrategyPayoff("long_call", "خودرو",
                          [_opt(Side.BUY, 60, "call", 750, qty=5)],
                          EXPIRY, underlying_price=750)
    assert five.payoff(900) == pytest.approx(5 * one.payoff(900))


def test_contract_size_is_not_double_counted():
    """اندازه‌ی قرارداد فقط **یک بار** ضرب می‌شود."""
    leg = _opt(Side.BUY, 60, "call", 750, size=1_000)
    assert leg.units == 1_000
    assert leg.entry_cost == pytest.approx(60_000)


def test_different_contract_size_changes_result_proportionally():
    """اندازه‌ی اشتباه نباید بی‌صدا رد شود؛ اثرش باید دیده شود."""
    small = StrategyPayoff("x", "y", [_opt(Side.BUY, 60, "call", 750, size=100)],
                           EXPIRY, underlying_price=750)
    big = StrategyPayoff("x", "y", [_opt(Side.BUY, 60, "call", 750, size=1000)],
                         EXPIRY, underlying_price=750)
    assert big.payoff(900) == pytest.approx(10 * small.payoff(900))


def test_commission_reduces_payoff():
    """کمیسیون باید از سود کم شود، نه نادیده گرفته."""
    free = StrategyPayoff("x", "y", [_opt(Side.BUY, 60, "call", 750)],
                          EXPIRY, underlying_price=750, commission_rate=0.0)
    charged = StrategyPayoff("x", "y", [_opt(Side.BUY, 60, "call", 750)],
                             EXPIRY, underlying_price=750, commission_rate=0.005)
    assert charged.payoff(900) < free.payoff(900)
    assert charged.commission == pytest.approx(60_000 * 0.005)


def test_commission_defaults_to_zero_not_guessed():
    """نرخ کمیسیون واقعی در پروژه نیست؛ عدد جعلی وارد نمی‌کنیم."""
    s = StrategyPayoff("x", "y", [_opt(Side.BUY, 60, "call", 750)], EXPIRY)
    assert s.commission == 0.0


def test_missing_quotes_do_not_crash_liquidity():
    """پایه‌ی بدون مظنه → نقدشوندگی نامعلوم، نه صفر."""
    leg = _opt(Side.BUY, 60, "call", 750, bid=None, ask=None)
    s = StrategyPayoff("x", "y", [leg], EXPIRY, underlying_price=750)
    assert s.liquidity_score is None


def test_liquidity_uses_the_worst_leg():
    """یک پایه‌ی بی‌نقد کل ساختار را غیرقابل اجرا می‌کند."""
    good = _opt(Side.BUY, 60, "call", 750, bid=59, ask=61, open_interest=1000)
    bad = _opt(Side.BUY, 40, "put", 750, bid=20, ask=60, open_interest=5)
    s = StrategyPayoff("x", "y", [good, bad], EXPIRY, underlying_price=750)

    only_good = StrategyPayoff("x", "y", [good], EXPIRY, underlying_price=750)
    assert s.liquidity_score < only_good.liquidity_score


def test_naked_short_call_has_unlimited_loss():
    """فروش کال بدون پوشش: زیان نامحدود، نه یک عدد."""
    s = StrategyPayoff("short_call", "خودرو", [_opt(Side.SELL, 60, "call", 750)],
                       EXPIRY, underlying_price=750)
    assert s.max_loss is None


def test_covered_call_loss_is_bounded():
    """کاوردکال: سهم پایه، کال فروخته را می‌پوشاند."""
    s = StrategyPayoff(
        "covered_call", "خودرو",
        [underlying_leg("خودرو", 750, 1, SIZE), _opt(Side.SELL, 25, "call", 800)],
        EXPIRY, underlying_price=750,
    )
    assert s.max_loss is not None
    assert s.max_profit == pytest.approx(75_000)  # (800-750)+25


def test_order_plan_lists_every_leg_without_executing(condor):
    """نقشه‌ی سفارش فقط توصیف است؛ هیچ سفارشی ثبت نمی‌شود."""
    plan = condor.order_plan()
    assert len(plan) == 4
    assert [p["action"] for p in plan] == ["BUY", "SELL", "SELL", "BUY"]
    assert all("limit_price" in p for p in plan)


def test_multi_leg_structures_declare_leg_risk(condor, straddle):
    """کارگزاری سفارش چندپایه‌ی اتمیک ندارد؛ باید صریح اعلام شود."""
    assert condor.has_leg_risk is True
    assert straddle.has_leg_risk is True

    single = StrategyPayoff("long_call", "خودرو", [_opt(Side.BUY, 60, "call", 750)],
                            EXPIRY)
    assert single.has_leg_risk is False


def test_to_dict_is_serialisable(condor):
    import json

    data = condor.to_dict()
    json.dumps(data, ensure_ascii=False)  # نباید خطا بدهد
    assert data["strategy_type"] == "iron_condor"
    assert data["max_profit"] == pytest.approx(30_000)

"""تست کارمزد و مالیات، و فیلد درجه‌یکِ `contract_size`.

**قرارداد مرکزی این فایل: پیش‌فرض صفر است.**

نرخ واقعی کارمزد آپشن بورس تهران در پروژه نیست و حدس زده نمی‌شود — همان
قاعده‌ای که برای داده‌ی بازار رعایت می‌شود. پس مهم‌ترین تست این است که
با نرخ صفر، **هیچ عددی** تغییر نکند: یک نرخ حدسی، دقتِ کاذب می‌سازد که
از نبودش بدتر است.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from risk.fees import NO_FEES, FeeSchedule
from risk.risk_calculator import RiskCalculator, RiskLimits
from signals.signal_model import (
    DEFAULT_CONTRACT_SIZE,
    OptionType,
    Side,
    Signal,
)

FEES = FeeSchedule(buy_rate=0.001, sell_rate=0.001, sell_tax_rate=0.0005)


def _signal(side=Side.BUY, premium=100.0, qty=0, **kwargs) -> Signal:
    return Signal(
        symbol="ضخود7136",
        option_type=OptionType.CALL,
        side=side,
        strike=750.0,
        expiry=date.today() + timedelta(days=30),
        suggested_price=premium,
        suggested_qty=qty,
        reason="تست",
        strategy_name="s",
        **kwargs,
    )


# ======================================================================
# پیش‌فرض صفر
# ======================================================================
def test_default_schedule_is_zero():
    assert NO_FEES.is_zero is True
    assert FeeSchedule().is_zero is True


def test_zero_fees_cost_nothing():
    assert NO_FEES.entry_cost(1_000_000, True) == 0.0
    assert NO_FEES.exit_cost(1_000_000, True) == 0.0
    assert NO_FEES.round_trip_cost(1_000_000) == 0.0


def test_zero_fees_leave_breakeven_untouched():
    assert NO_FEES.breakeven_premium(100.0) == 100.0


def test_zero_fees_describe_is_empty():
    """متن خالی یعنی چیزی به سیگنال اضافه نمی‌شود."""
    assert NO_FEES.describe() == ""


def test_zero_fees_change_no_risk_number():
    """مهم‌ترین تست: با پیش‌فرض پروژه، همه‌ی اعداد باید دقیقاً مثل قبل باشند."""
    limits = RiskLimits(account_equity=1_000_000_000.0)
    signal = _signal()

    without = RiskCalculator(limits).evaluate(signal, 1_000)
    explicit = RiskCalculator(limits, NO_FEES).evaluate(signal, 1_000)

    assert without.suggested_qty == explicit.suggested_qty
    assert without.take_profit == explicit.take_profit
    assert without.stop_loss == explicit.stop_loss
    assert without.round_trip_fees == 0.0


# ======================================================================
# محاسبه‌ی کارمزد
# ======================================================================
def test_round_trip_rate_sums_both_sides():
    assert FEES.round_trip_rate == pytest.approx(0.001 + 0.001 + 0.0005)


def test_buy_entry_pays_only_the_buy_rate():
    assert FEES.entry_cost(1_000_000, is_buy=True) == pytest.approx(1_000.0)


def test_sell_entry_pays_the_sell_rate_and_tax():
    """مالیات فقط سمت فروش است."""
    assert FEES.entry_cost(1_000_000, is_buy=False) == pytest.approx(1_500.0)


def test_exit_side_is_inverted():
    """خریدار برای خروج **می‌فروشد** — پس مالیات فروش می‌دهد.

    وارونگی سمت، اشتباه رایجی است که کارمزد را کم‌برآورد می‌کند.
    """
    assert FEES.exit_cost(1_000_000, was_buy=True) == pytest.approx(1_500.0)
    assert FEES.exit_cost(1_000_000, was_buy=False) == pytest.approx(1_000.0)


def test_round_trip_is_entry_plus_exit():
    notional = 1_000_000
    assert FEES.round_trip_cost(notional, True) == pytest.approx(
        FEES.entry_cost(notional, True) + FEES.exit_cost(notional, True)
    )


def test_round_trip_is_symmetric_for_both_directions():
    """رفت و برگشت هر دو سمت را می‌پیماید، پس جمعش باید یکی باشد."""
    assert FEES.round_trip_cost(1_000_000, True) == pytest.approx(
        FEES.round_trip_cost(1_000_000, False)
    )


def test_negative_notional_is_treated_as_magnitude():
    """موقعیت بستانکار notional منفی دارد؛ کارمزدش منفی نمی‌شود."""
    assert FEES.entry_cost(-1_000_000, True) > 0


def test_per_order_fee_is_added_to_each_leg():
    schedule = FeeSchedule(per_order=5_000.0)
    assert schedule.entry_cost(0, True) == pytest.approx(5_000.0)
    assert schedule.round_trip_cost(0, True) == pytest.approx(10_000.0)


def test_per_order_alone_is_not_zero_schedule():
    assert FeeSchedule(per_order=1.0).is_zero is False


# ======================================================================
# سر به سر و حد سود
# ======================================================================
def test_buyer_breakeven_is_above_the_entry_premium():
    """خریدار باید بالاتر بفروشد تا کارمزد را هم پوشش بدهد."""
    assert FEES.breakeven_premium(100.0, is_buy=True) == pytest.approx(100.25)


def test_seller_breakeven_is_below_the_entry_premium():
    assert FEES.breakeven_premium(100.0, is_buy=False) == pytest.approx(99.75)


def test_breakeven_of_zero_premium_is_zero():
    assert FEES.breakeven_premium(0.0) == 0.0


def test_take_profit_is_shifted_to_survive_fees():
    """حد سودِ «۷۰٪» باید **پس از** کارمزد ۷۰٪ بدهد.

    بدون این، در عمل کمتر می‌شد و کاربر نمی‌فهمید چرا.
    """
    gross = 100.0 * 1.70
    net = FEES.net_take_profit(100.0, 70.0, is_buy=True)
    assert net > gross
    assert net == pytest.approx(gross + 100.0 * FEES.round_trip_rate)


def test_seller_take_profit_shifts_downward():
    """فروشنده با خرید ارزان‌تر سود می‌کند، پس هدفش پایین‌تر می‌رود."""
    gross = 100.0 * 0.30
    net = FEES.net_take_profit(100.0, 70.0, is_buy=False)
    assert net < gross


def test_zero_fees_leave_take_profit_exact():
    assert NO_FEES.net_take_profit(100.0, 70.0, True) == pytest.approx(170.0)


# ======================================================================
# اثر روی محاسبه‌ی ریسک
# ======================================================================
def test_fees_shift_the_take_profit_in_risk_output():
    limits = RiskLimits(account_equity=1_000_000_000.0, take_profit_pct=70.0)
    plain = RiskCalculator(limits).evaluate(_signal(), 1_000)
    charged = RiskCalculator(limits, FEES).evaluate(_signal(), 1_000)
    assert charged.take_profit > plain.take_profit


def test_fees_do_not_move_the_stop_loss():
    """حد ضرر روی درصدِ افت پرمیوم است، نه سود؛ کارمزد جایش را عوض نمی‌کند."""
    limits = RiskLimits(account_equity=1_000_000_000.0)
    plain = RiskCalculator(limits).evaluate(_signal(), 1_000)
    charged = RiskCalculator(limits, FEES).evaluate(_signal(), 1_000)
    assert charged.stop_loss == plain.stop_loss


def test_fees_are_reported_on_the_suggestion():
    limits = RiskLimits(account_equity=1_000_000_000.0)
    result = RiskCalculator(limits, FEES).evaluate(_signal(), 1_000)
    assert result.round_trip_fees > 0


def test_fees_reduce_or_hold_the_position_size():
    """کارمزد پولی است که در هر حالت می‌رود، پس ریسک هر قرارداد بیشتر است."""
    limits = RiskLimits(account_equity=10_000_000.0, max_contracts=10_000)
    plain = RiskCalculator(limits).evaluate(_signal(), 1_000)
    charged = RiskCalculator(limits, FEES).evaluate(_signal(), 1_000)
    assert charged.suggested_qty <= plain.suggested_qty


def test_fee_note_appears_in_the_signal_text():
    """اگر اعداد شامل کارمزدند، کاربر باید بداند."""
    limits = RiskLimits(account_equity=1_000_000_000.0)
    notes = RiskCalculator(limits, FEES).evaluate(_signal(), 1_000).notes
    assert "کارمزد" in notes


def test_no_fee_note_when_rates_are_zero():
    limits = RiskLimits(account_equity=1_000_000_000.0)
    notes = RiskCalculator(limits).evaluate(_signal(), 1_000).notes
    assert "کارمزد" not in notes


def test_applied_signal_carries_the_fee_total():
    limits = RiskLimits(account_equity=1_000_000_000.0)
    enriched = RiskCalculator(limits, FEES).apply(_signal(), 1_000)
    assert enriched is not None
    assert enriched.metadata["round_trip_fees"] > 0


def test_applied_signal_omits_fees_when_zero():
    """کلیدِ صفر فقط شلوغی است؛ نبودش یعنی نرخی تنظیم نشده."""
    limits = RiskLimits(account_equity=1_000_000_000.0)
    enriched = RiskCalculator(limits).apply(_signal(), 1_000)
    assert enriched is not None
    assert "round_trip_fees" not in enriched.metadata


# ======================================================================
# `contract_size` به‌عنوان فیلد درجه‌یک
# ======================================================================
def test_contract_size_is_a_first_class_field():
    """در `metadata` بودنش خطرناک بود: اشتباهش ارزش را ۱۰۰۰ برابر غلط می‌کند."""
    signal = _signal(qty=3, contract_size=500)
    assert signal.contract_size == 500
    assert signal.units_per_contract == 500
    assert signal.notional == pytest.approx(100.0 * 3 * 500)


def test_missing_contract_size_falls_back_to_the_tehran_default():
    """`None` یعنی «استراتژی نگفت» — با صفر فرق دارد (صفر = ارزش صفر)."""
    signal = _signal(qty=3)
    assert signal.contract_size is None
    assert signal.units_per_contract == DEFAULT_CONTRACT_SIZE
    assert signal.notional == pytest.approx(100.0 * 3 * DEFAULT_CONTRACT_SIZE)


def test_legacy_metadata_contract_size_is_honored():
    """رکوردهای موجود دیتابیس اندازه را در `metadata` دارند."""
    signal = _signal(qty=2, metadata={"contract_size": 250})
    assert signal.contract_size == 250
    assert signal.notional == pytest.approx(100.0 * 2 * 250)


def test_explicit_field_wins_over_metadata():
    signal = _signal(qty=1, contract_size=100, metadata={"contract_size": 999})
    assert signal.contract_size == 100


def test_unreadable_legacy_value_does_not_crash():
    signal = _signal(qty=1, metadata={"contract_size": "بزرگ"})
    assert signal.contract_size is None
    assert signal.units_per_contract == DEFAULT_CONTRACT_SIZE


def test_zero_contract_size_uses_the_default_not_zero():
    """اندازه‌ی صفر یعنی داده خراب است؛ ارزش صفر گزارش کردن گمراه‌کننده است."""
    signal = _signal(qty=2, contract_size=0)
    assert signal.units_per_contract == DEFAULT_CONTRACT_SIZE


def test_contract_size_survives_json_round_trip():
    signal = _signal(qty=2, contract_size=500)
    restored = Signal.from_dict(json.loads(signal.to_json()))
    assert restored.contract_size == 500
    assert restored.notional == signal.notional


def test_strategies_set_the_field_from_the_real_chain(
    market_data, option_chain, recorded_symbol
):
    """اندازه باید از زنجیره‌ی واقعی بیاید، نه از عددِ ثابت."""
    from datetime import datetime

    from strategies.base_strategy import StrategyContext
    from strategies.directional_strategy import DirectionalStrategy

    context = StrategyContext(
        underlying=recorded_symbol,
        quote=market_data.get_quote(recorded_symbol),
        history=market_data.get_history(recorded_symbol, 90),
        chain=option_chain.get_chain(recorded_symbol),
        now=datetime.now(),
    )
    sizes = {c.contract_size for c in context.chain.contracts}

    for signal in DirectionalStrategy().generate(context):
        assert signal.contract_size in sizes
        assert signal.contract_size == signal.metadata["contract_size"]


def test_risk_calculator_sets_the_field():
    limits = RiskLimits(account_equity=1_000_000_000.0)
    enriched = RiskCalculator(limits).apply(_signal(), 500)
    assert enriched is not None
    assert enriched.contract_size == 500


# ======================================================================
# wiring
# ======================================================================
def test_fees_are_zero_by_default_in_config():
    import bootstrap
    from config.loader import default_settings

    assert bootstrap.build_risk_calculator(default_settings()).fees.is_zero


def test_fees_reach_the_calculator_from_config():
    import bootstrap
    from config.loader import default_settings

    settings = default_settings()
    settings["risk"]["fees"]["buy_rate"] = 0.002
    assert bootstrap.build_risk_calculator(settings).fees.buy_rate == 0.002


def test_fees_key_is_not_an_unknown_key_warning(caplog):
    """هشدارِ «کلید ناشناخته» برای گرفتن غلط‌املایی است، نه کلید درست."""
    import bootstrap
    from config.loader import default_settings

    with caplog.at_level("WARNING"):
        bootstrap.build_risk_calculator(default_settings())
    assert "fees" not in caplog.text

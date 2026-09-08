"""تست شرط مالکیت سهم برای Covered Call.

**چرا این شرط مهم است**

Covered Call یعنی فروش کال روی سهمی که **داری**. بدون سهم، همان معامله یک
کالِ لخت است: سود محدود به پرمیوم، زیان نامحدود. این دو یک استراتژی با
پارامتر مختلف نیستند — دو پروفایل ریسکِ کاملاً متفاوت‌اند، و سیگنالی که
این تفاوت را نبیند خطرناک است.

تست‌ها روی زنجیره‌ی **واقعیِ ضبط‌شده‌ی** TSETMC اجرا می‌شوند.
"""

from __future__ import annotations

from datetime import datetime

from signals.signal_generator import GeneratorConfig, SignalGenerator
from strategies.base_strategy import StrategyContext
from strategies.neutral_strategy import NeutralStrategy


def _context(market_data, option_chain, symbol, holding):
    """context واقعی، با مالکیتِ داده‌شده."""
    return StrategyContext(
        underlying=symbol,
        quote=market_data.get_quote(symbol),
        history=market_data.get_history(symbol, 90),
        chain=option_chain.get_chain(symbol),
        now=datetime.now(),
        underlying_holding=holding,
    )


def _covered_calls(strategy, context):
    """فقط سیگنال‌های Covered Call از خروجی استراتژی."""
    return [
        s
        for s in strategy.generate(context)
        if (s.metadata or {}).get("structure") == "covered_call"
    ]


# ----------------------------------------------------------------------
# قرارداد `StrategyContext`
# ----------------------------------------------------------------------
def test_holding_defaults_to_unknown(market_data, option_chain, recorded_symbol):
    """پیش‌فرض باید «نامعلوم» باشد، نه صفر.

    اگر پیش‌فرض صفر بود، هر context بدون کارگزاری یعنی «سهم نداری» و همه‌ی
    سیگنال‌های Covered Call بی‌صدا حذف می‌شدند.
    """
    context = StrategyContext(
        underlying=recorded_symbol,
        quote=market_data.get_quote(recorded_symbol),
        history=market_data.get_history(recorded_symbol, 30),
        chain=option_chain.get_chain(recorded_symbol),
    )
    assert context.underlying_holding is None


# ----------------------------------------------------------------------
# رفتار استراتژی
# ----------------------------------------------------------------------
# در نمونه‌ی ضبط‌شده هیچ نمادی «رنج» نیست، پس `generate` هیچ‌وقت به
# Covered Call نمی‌رسد. برای تستِ **مسیر مثبت** دو راه بود: ساختن تاریخچه‌ی
# مصنوعیِ رنج (که این پروژه ممنوع کرده — گاردِ تست هم داریم)، یا صدا زدنِ
# مستقیم `_covered_call` روی زنجیره‌ی واقعی و کنار گذاشتنِ همان یک گیت.
# دومی انتخاب شد: زنجیره، استرایک و اندازه‌ی قرارداد همه واقعی می‌مانند و
# فقط شرطی که موضوع این تست نیست دور زده می‌شود.
def _covered_call_direct(strategy, context):
    """`_covered_call` را مستقیم صدا می‌زند و گیتِ «رنج» را دور می‌زند."""
    strategy._is_range_bound = lambda _context: True  # type: ignore[method-assign]
    return strategy._covered_call(context, iv=0.5, realized=0.35, iv_ratio=1.6)


def test_zero_holding_blocks_the_signal(market_data, option_chain, recorded_symbol):
    """با صفر سهم، Covered Call نباید صادر شود — آن یک کالِ لخت است."""
    context = _context(market_data, option_chain, recorded_symbol, holding=0)
    assert _covered_call_direct(NeutralStrategy(), context) == []


def test_insufficient_holding_blocks_the_signal(
    market_data, option_chain, recorded_symbol
):
    """کمتر از یک قرارداد سهم هم کافی نیست.

    اندازه‌ی قرارداد در تهران معمولاً ۱۰۰۰ سهم است؛ ۹۹۹ سهم هیچ قراردادی
    را پوشش نمی‌دهد.
    """
    context = _context(market_data, option_chain, recorded_symbol, holding=999)
    assert _covered_call_direct(NeutralStrategy(), context) == []


def test_unknown_holding_still_emits_with_a_warning(
    market_data, option_chain, recorded_symbol
):
    """نامعلوم ≠ ندارد. رفتار قبلی پروژه حفظ می‌شود، ولی با هشدار."""
    context = _context(market_data, option_chain, recorded_symbol, holding=None)
    signals = _covered_call_direct(NeutralStrategy(), context)

    assert signals, "نامعلوم نباید سیگنال را حذف کند"
    for signal in signals:
        assert signal.metadata["holding_verified"] is False
        assert signal.metadata["max_covered_contracts"] is None
        assert "بررسی نشد" in signal.reason


def test_unknown_holding_can_be_rejected_when_strict(
    market_data, option_chain, recorded_symbol
):
    """حالت سخت‌گیرانه: نامعلوم هم رد شود."""
    strategy = NeutralStrategy(params={"skip_covered_call_if_holding_unknown": True})
    context = _context(market_data, option_chain, recorded_symbol, holding=None)
    assert _covered_call_direct(strategy, context) == []


def test_requirement_can_be_turned_off(market_data, option_chain, recorded_symbol):
    """کسی که می‌داند چه می‌کند باید بتواند شرط را خاموش کند."""
    strategy = NeutralStrategy(params={"require_underlying_holding": False})
    context = _context(market_data, option_chain, recorded_symbol, holding=0)
    signals = _covered_call_direct(strategy, context)

    assert signals, "با خاموش بودن شرط باید سیگنال بدهد"
    for signal in signals:
        assert signal.metadata["underlying_holding"] == 0
        assert signal.metadata["max_covered_contracts"] == 0


def test_sufficient_holding_records_coverage(
    market_data, option_chain, recorded_symbol
):
    """مالکیت کافی: سیگنال با تأیید و تعداد قرارداد قابل پوشش."""
    strategy = NeutralStrategy()
    context = _context(market_data, option_chain, recorded_symbol, holding=50_000)

    signals = _covered_call_direct(strategy, context)
    assert signals, "روی زنجیره‌ی واقعی باید یک کال قابل انتخاب باشد"

    for signal in signals:
        meta = signal.metadata
        assert meta["holding_verified"] is True
        assert meta["underlying_holding"] == 50_000
        expected = 50_000 // meta["shares_per_contract"]
        assert meta["max_covered_contracts"] == expected
        assert meta["max_covered_contracts"] > 0
        assert "مالکیت تأیید شد" in signal.reason


def test_shares_per_contract_comes_from_the_real_chain(
    market_data, option_chain, recorded_symbol
):
    """اندازه‌ی قرارداد باید از زنجیره بیاید، نه از یک عددِ حدسی.

    اگر ۱۰۰۰ را hard-code می‌کردیم، روی نمادی با اندازه‌ی متفاوت شرط
    مالکیت بی‌صدا غلط می‌شد.
    """
    chain = option_chain.get_chain(recorded_symbol)
    real_sizes = {c.contract_size for c in chain.contracts}

    strategy = NeutralStrategy()
    context = _context(market_data, option_chain, recorded_symbol, holding=50_000)
    signals = _covered_call_direct(strategy, context)

    assert signals
    assert signals[0].metadata["shares_per_contract"] in real_sizes


def test_holding_does_not_affect_straddle(market_data, option_chain, recorded_symbol):
    """شرط مالکیت فقط Covered Call را می‌بندد.

    استردل سهم پایه نمی‌خواهد؛ اگر مالکیت آن را هم می‌بست، یک شرط درست
    به یک خرابیِ سراسری تبدیل می‌شد.
    """
    strategy = NeutralStrategy()
    with_shares = strategy.generate(
        _context(market_data, option_chain, recorded_symbol, holding=50_000)
    )
    without = strategy.generate(
        _context(market_data, option_chain, recorded_symbol, holding=0)
    )

    kinds = lambda sigs: {  # noqa: E731
        (s.metadata or {}).get("structure") for s in sigs
    } - {"covered_call"}
    assert kinds(with_shares) == kinds(without)


# ----------------------------------------------------------------------
# لایه‌ی `SignalGenerator`
# ----------------------------------------------------------------------
def test_generator_passes_holding_into_context(
    market_data, option_chain, recorded_symbol
):
    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        config=GeneratorConfig(symbols=[recorded_symbol]),
        holdings_provider=lambda: {recorded_symbol: 12_000},
    )
    assert generator.build_context(recorded_symbol).underlying_holding == 12_000


def test_symbol_absent_from_holdings_is_zero_not_unknown(
    market_data, option_chain, recorded_symbol
):
    """لیست دارایی را دیده‌ایم و این نماد در آن نیست ⇒ واقعاً صفر است."""
    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        holdings_provider=lambda: {"نماد_دیگر": 5_000},
    )
    assert generator.build_context(recorded_symbol).underlying_holding == 0


def test_without_provider_holding_is_unknown(
    market_data, option_chain, recorded_symbol
):
    generator = SignalGenerator(
        market_data=market_data, option_chain=option_chain, strategies=[]
    )
    assert generator.build_context(recorded_symbol).underlying_holding is None


def test_provider_failure_yields_unknown_not_zero(
    market_data, option_chain, recorded_symbol
):
    """قطعی کارگزاری نباید به «سهم نداری» تبدیل شود.

    اگر می‌شد، یک تایم‌اوت گذرا همه‌ی سیگنال‌های Covered Call را بی‌صدا
    حذف می‌کرد و کاربر هیچ‌وقت نمی‌فهمید چرا.
    """

    def boom() -> dict[str, int]:
        raise RuntimeError("توکن منقضی")

    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        holdings_provider=boom,
    )
    assert generator.build_context(recorded_symbol).underlying_holding is None


def test_holdings_are_read_once_per_pass(market_data, option_chain, recorded_symbol):
    """یک پاسخ همه‌ی دارایی‌ها را دارد؛ هر نماد یک درخواست، اسراف است."""
    calls = {"n": 0}

    def provider() -> dict[str, int]:
        calls["n"] += 1
        return {recorded_symbol: 1_000}

    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        holdings_provider=provider,
    )
    generator.build_context(recorded_symbol)
    generator.build_context(recorded_symbol)
    assert calls["n"] == 1


def test_cache_resets_between_passes(market_data, option_chain, recorded_symbol):
    """کاربر می‌تواند وسط دو پاس سهم بخرد؛ کش نباید آن را پنهان کند."""
    calls = {"n": 0}

    def provider() -> dict[str, int]:
        calls["n"] += 1
        return {recorded_symbol: 1_000}

    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[],
        config=GeneratorConfig(symbols=[recorded_symbol]),
        holdings_provider=provider,
    )
    generator.run_once()
    generator.run_once()
    assert calls["n"] == 2


# ----------------------------------------------------------------------
# لایه‌ی wiring
# ----------------------------------------------------------------------
def test_no_provider_without_account_source():
    import bootstrap

    assert bootstrap.build_holdings_provider(None) is None


def test_no_provider_when_broker_cannot_report_shares():
    """آداپتری که دارایی سهم نمی‌دهد باید «نامعلوم» بدهد، نه «خالی»."""
    import bootstrap

    class _Blind:
        name = "blind"
        supports_share_positions = False

    assert bootstrap.build_holdings_provider(_Blind()) is None


def test_provider_keys_on_symbol_name_and_isin():
    import bootstrap
    from brokers.base import SharePosition

    class _Account:
        name = "fake"
        supports_share_positions = True

        def get_share_positions(self):
            return [
                SharePosition(
                    symbol_isin="IRO1IKCO0008",
                    symbol_name="خودرو",
                    quantity=25_000,
                )
            ]

    provider = bootstrap.build_holdings_provider(_Account())
    holdings = provider()
    assert holdings["خودرو"] == 25_000
    assert holdings["IRO1IKCO0008"] == 25_000


def test_gate_blocks_a_signal_that_would_otherwise_be_emitted(
    market_data, option_chain, recorded_symbol
):
    """محکم‌ترین تست: همان ورودی، فقط مالکیت عوض می‌شود.

    تست‌های منفیِ دیگر می‌توانند به دلیلِ دیگری (رنج نبودن، IV ارزان) خالی
    برگردند و بی‌صدا بی‌معنا شوند. اینجا می‌دانیم سیگنال با سهم **هست**،
    پس صفر شدنش بدون سهم فقط می‌تواند کارِ شرط مالکیت باشد.
    """
    context_with = _context(market_data, option_chain, recorded_symbol, 50_000)
    context_without = _context(market_data, option_chain, recorded_symbol, 0)

    assert _covered_call_direct(NeutralStrategy(), context_with)
    assert _covered_call_direct(NeutralStrategy(), context_without) == []

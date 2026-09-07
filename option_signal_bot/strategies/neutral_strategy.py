"""استراتژی‌های خنثی نسبت به جهت بازار (به‌صورت سیگنال، نه سفارش).

- Covered Call: بازار رنج + IV گران ⇒ پیشنهاد فروش Call روی سهم موجود در پورتفو.
- Long Straddle: IV ارزان نسبت به نوسان تاریخی ⇒ پیشنهاد خرید همزمان Call و Put ATM.

نکته مهم: سیگنال Covered Call فقط معنا دارد اگر کاربر سهم پایه را داشته
باشد. بدون سهم، همان معامله یک **کالِ لخت** است: سود محدود به پرمیوم،
زیان نامحدود. این دو یک استراتژی با پارامتر مختلف نیستند.

مالکیت از `StrategyContext.underlying_holding` خوانده می‌شود — یک **عدد**
که لایه‌ی wiring از کارگزاری پر می‌کند، نه دسترسیِ استراتژی به حساب.
سه حالت دارد و تفاوت‌شان تصمیم‌ساز است:

    `None` → نامعلوم (کارگزاری خاموش) ⇒ سیگنال با هشدار، مثل قبل
    `0` یا کمتر از یک قرارداد ⇒ سیگنال **صادر نمی‌شود**
    کافی ⇒ سیگنال با تأیید مالکیت و تعداد قرارداد قابل پوشش
"""

from __future__ import annotations

import logging
from typing import Any

from signals.signal_model import Side, Signal
from strategies.base_strategy import BaseStrategy, StrategyContext
from strategies.directional_strategy import sma
from strategies.registry import register_strategy

logger = logging.getLogger(__name__)


@register_strategy
class NeutralStrategy(BaseStrategy):
    """بر اساس مقایسه IV با نوسان تاریخی، سیگنال Covered Call یا Straddle می‌دهد."""

    name = "neutral_iv_spread"

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "trend_window": 20,
            # حداکثر انحراف قیمت از میانگین که «رنج» تلقی می‌شود (درصد)
            "range_threshold_pct": 3.0,
            # IV باید چند برابر نوسان تاریخی باشد تا «گران» حساب شود
            "rich_iv_ratio": 1.30,
            # و چند برابر تا «ارزان» حساب شود
            "cheap_iv_ratio": 0.80,
            "covered_call_moneyness": 0.08,
            "min_days_to_expiry": 14,
            "max_days_to_expiry": 60,
            "min_open_interest": 50,
            "enable_covered_call": True,
            "enable_straddle": True,
            # Covered Call فقط وقتی سهم پایه را داری معنا دارد. بدون سهم،
            # همان معامله یک کالِ لخت است: زیان نامحدود. پس اگر مالکیت
            # معلوم باشد و کافی نباشد، سیگنال صادر نمی‌شود.
            "require_underlying_holding": True,
            # وقتی مالکیت **نامعلوم** است (کارگزاری خاموش)، سیگنال با
            # هشدار صادر می‌شود — رفتار قبلی پروژه. `True` یعنی
            # سخت‌گیرانه: نامعلوم هم رد شود.
            "skip_covered_call_if_holding_unknown": False,
        }

    def generate(self, context: StrategyContext) -> list[Signal]:
        realized = context.realized_vol(self.params["trend_window"])
        if realized <= 0:
            return []

        atm_call = self.select_contract(context, "call", moneyness=0.0,
                                        min_open_interest=self.params["min_open_interest"])
        if atm_call is None:
            return []

        atm_iv = context.implied_vol(atm_call)
        if atm_iv is None:
            return []

        iv_ratio = atm_iv / realized
        signals: list[Signal] = []

        if self.params["enable_covered_call"] and iv_ratio >= self.params["rich_iv_ratio"]:
            signals.extend(self._covered_call(context, atm_iv, realized, iv_ratio))
        elif self.params["enable_straddle"] and iv_ratio <= self.params["cheap_iv_ratio"]:
            signals.extend(self._straddle(context, atm_call, atm_iv, realized, iv_ratio))

        return signals

    # ------------------------------------------------------------------
    def _covered_call(
        self, context: StrategyContext, iv: float, realized: float, iv_ratio: float
    ) -> list[Signal]:
        """فروش Call با استرایک بالاتر، مشروط به رنج بودن بازار و مالکیت سهم."""
        if not self._is_range_bound(context):
            return []

        contract = self.select_contract(
            context,
            "call",
            moneyness=self.params["covered_call_moneyness"],
            min_open_interest=self.params["min_open_interest"],
        )
        if contract is None:
            return []

        # شرط مالکیت، **قبل از** ساخت سیگنال. Covered Call بدون سهم یک کالِ
        # لخت است: سود محدود به پرمیوم، زیان نامحدود. این دو یک استراتژی
        # با پارامتر مختلف نیستند، دو پروفایل ریسکِ متفاوت‌اند.
        holding = context.underlying_holding
        needed = contract.contract_size  # سهمِ لازم برای یک قرارداد

        if holding is None:
            # نامعلوم: کارگزاری خاموش است یا دارایی سهم را نمی‌دهد.
            if self.params["skip_covered_call_if_holding_unknown"]:
                return []
            holding_note = (
                "⚠️ مالکیت سهم پایه **بررسی نشد** (اتصال کارگزاری خاموش است). "
                f"برای یک قرارداد به {needed:,} سهم نیاز است؛ بدون سهم این معامله "
                "کالِ لخت با زیان نامحدود است."
            )
            max_contracts = None
        elif self.params["require_underlying_holding"] and holding < needed:
            logger.info(
                "Covered Call روی %s رد شد: %s سهم داری، برای یک قرارداد %s لازم است.",
                context.underlying,
                f"{holding:,}",
                f"{needed:,}",
            )
            return []
        else:
            max_contracts = holding // needed if needed else 0
            holding_note = (
                f"✅ مالکیت تأیید شد: {holding:,} سهم پایه "
                f"(پوشش {max_contracts:,} قرارداد)."
            )

        premium = contract.bid or contract.mid_price or 0.0
        yield_pct = premium / context.spot * 100.0 if context.spot else 0.0
        reason = (
            f"Covered Call روی {context.underlying}: بازار در محدوده رنج و IV گران است "
            f"(IV {iv * 100:.1f}٪ در برابر نوسان تاریخی "
            f"{realized * 100:.1f}٪، نسبت {iv_ratio:.2f}). "
            f"پرمیوم دریافتی ≈ {yield_pct:.2f}٪ قیمت پایه. "
            f"{holding_note}"
        )
        return [
            self.build_signal(
                context,
                contract=contract,
                side=Side.SELL,
                reason=reason,
                confidence=round(min((iv_ratio - 1.0), 1.0), 2),
                metadata={
                    "structure": "covered_call",
                    "requires_underlying_holding": True,
                    "underlying_holding": holding,
                    "holding_verified": holding is not None,
                    "shares_per_contract": needed,
                    "max_covered_contracts": max_contracts,
                    "implied_vol": round(iv, 4),
                    "realized_vol": round(realized, 4),
                    "iv_ratio": round(iv_ratio, 3),
                },
            )
        ]

    def _straddle(
        self,
        context: StrategyContext,
        atm_call,
        iv: float,
        realized: float,
        iv_ratio: float,
    ) -> list[Signal]:
        """خرید همزمان Call و Put ATM؛ دو سیگنال با شناسه ساختار مشترک."""
        atm_put = context.chain.nearest_strike("put", atm_call.strike, atm_call.expiry)
        if atm_put is None or not atm_put.mid_price:
            return []

        total_premium = (atm_call.ask or 0.0) + (atm_put.ask or 0.0)
        breakeven_pct = total_premium / context.spot * 100.0 if context.spot else 0.0
        reason = (
            f"Long Straddle روی {context.underlying}: IV ارزان است "
            f"(IV {iv * 100:.1f}٪ در برابر نوسان تاریخی "
            f"{realized * 100:.1f}٪، نسبت {iv_ratio:.2f}). "
            f"مجموع پرمیوم {total_premium:,.0f} ⇒ برای سوددهی نیاز به حرکت بیش از "
            f"{breakeven_pct:.2f}٪ در قیمت پایه. هر دو پا باید همزمان ثبت شوند."
        )
        meta = {
            "structure": "long_straddle",
            "structure_id": f"straddle-{atm_call.expiry}-{atm_call.strike:.0f}",
            "total_premium": round(total_premium, 1),
            "breakeven_move_pct": round(breakeven_pct, 3),
            "implied_vol": round(iv, 4),
            "realized_vol": round(realized, 4),
        }
        confidence = round(min((1.0 - iv_ratio), 1.0), 2)
        return [
            self.build_signal(
                context, atm_call, Side.BUY, reason, confidence, {**meta, "leg": "call"}
            ),
            self.build_signal(
                context, atm_put, Side.BUY, reason, confidence, {**meta, "leg": "put"}
            ),
        ]

    def _is_range_bound(self, context: StrategyContext) -> bool:
        """قیمت نسبت به میانگین متحرک، از آستانه بیشتر فاصله نگرفته باشد."""
        mean = sma(context.closes, self.params["trend_window"])
        if not mean:
            return False
        deviation_pct = abs(context.spot - mean) / mean * 100.0
        return deviation_pct <= self.params["range_threshold_pct"]

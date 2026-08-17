"""استراتژی جهت‌دار: خرید Call/Put بر اساس تکنیکال نماد پایه.

نسخه حداقلی و آگاهانه ساده: تقاطع دو میانگین متحرک + تأیید مومنتوم.
هدف این فایل نمایش «شکل» یک استراتژی است، نه یک لبه معاملاتی واقعی.
"""

from __future__ import annotations

from typing import Any

from signals.signal_model import Side, Signal
from strategies.base_strategy import BaseStrategy, StrategyContext
from strategies.registry import register_strategy


def sma(values: list[float], window: int) -> float | None:
    """میانگین متحرک ساده؛ None اگر داده کافی نباشد."""
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / window


@register_strategy
class DirectionalStrategy(BaseStrategy):
    """اگر روند صعودی بود Call، اگر نزولی بود Put پیشنهاد می‌دهد."""

    name = "directional_ma_cross"

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "fast_window": 5,
            "slow_window": 20,
            "momentum_window": 10,
            # حداقل فاصله نسبی دو میانگین برای پرهیز از نویز بازار رنج (درصد)
            "min_separation_pct": 0.5,
            # حداقل مومنتوم لازم روی پنجره اخیر (درصد)
            "min_momentum_pct": 2.0,
            # چقدر OTM باشد؛ 0.05 یعنی ۵٪ بالاتر از قیمت پایه برای Call
            "target_moneyness": 0.05,
            "min_days_to_expiry": 14,
            "max_days_to_expiry": 90,
            "min_open_interest": 50,
        }

    def generate(self, context: StrategyContext) -> list[Signal]:
        closes = context.closes
        params = self.params

        fast = sma(closes, params["fast_window"])
        slow = sma(closes, params["slow_window"])
        if fast is None or slow is None or slow <= 0:
            return []

        separation_pct = (fast - slow) / slow * 100.0
        momentum_pct = self._momentum_pct(closes, params["momentum_window"])
        if momentum_pct is None:
            return []

        direction = self._direction(separation_pct, momentum_pct)
        if direction is None:
            return []

        option_type = "call" if direction == "bullish" else "put"
        # برای Call استرایک بالاتر و برای Put استرایک پایین‌تر از قیمت پایه
        moneyness = params["target_moneyness"] * (1 if direction == "bullish" else -1)

        contract = self.select_contract(
            context,
            option_type=option_type,
            moneyness=moneyness,
            min_open_interest=params["min_open_interest"],
        )
        if contract is None:
            return []

        iv = context.implied_vol(contract)
        reason = (
            f"روند {'صعودی' if direction == 'bullish' else 'نزولی'} روی {context.underlying}: "
            f"MA{params['fast_window']} نسبت به MA{params['slow_window']} "
            f"{separation_pct:+.2f}٪ و مومنتوم {params['momentum_window']} روزه {momentum_pct:+.2f}٪. "
            f"قیمت پایه {context.spot:,.0f}."
        )
        if iv:
            reason += f" IV تخمینی نماد: {iv * 100:.1f}٪."

        signal = self.build_signal(
            context,
            contract=contract,
            side=Side.BUY,
            reason=reason,
            confidence=self._confidence(separation_pct, momentum_pct),
            metadata={
                "separation_pct": round(separation_pct, 3),
                "momentum_pct": round(momentum_pct, 3),
                "implied_vol": round(iv, 4) if iv else None,
                "realized_vol": round(context.realized_vol(), 4),
            },
        )
        return [signal]

    # ------------------------------------------------------------------
    @staticmethod
    def _momentum_pct(closes: list[float], window: int) -> float | None:
        if len(closes) <= window:
            return None
        past = closes[-window - 1]
        return (closes[-1] - past) / past * 100.0 if past > 0 else None

    def _direction(self, separation_pct: float, momentum_pct: float) -> str | None:
        min_sep = self.params["min_separation_pct"]
        min_mom = self.params["min_momentum_pct"]
        if separation_pct >= min_sep and momentum_pct >= min_mom:
            return "bullish"
        if separation_pct <= -min_sep and momentum_pct <= -min_mom:
            return "bearish"
        return None

    def _confidence(self, separation_pct: float, momentum_pct: float) -> float:
        """اعتماد ساده ۰..۱ بر پایه قدرت سیگنال نسبت به آستانه‌ها."""
        sep_ratio = abs(separation_pct) / max(self.params["min_separation_pct"], 1e-9)
        mom_ratio = abs(momentum_pct) / max(self.params["min_momentum_pct"], 1e-9)
        return round(min((sep_ratio + mom_ratio) / 6.0, 1.0), 2)

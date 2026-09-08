"""لانگ استردل — خرید همزمان کال و پوت روی یک استرایک و یک سررسید.

**چه وقت سود می‌دهد:** وقتی بازار **حرکت بزرگ** کند، فرقی نمی‌کند بالا
یا پایین. جهت مهم نیست، بزرگی حرکت مهم است.

**چه وقت ضرر می‌دهد:** وقتی بازار آرام بماند. هر روز که می‌گذرد ارزش
زمانی هر دو پایه آب می‌رود (theta منفی مضاعف).

**شرط ورود که این استراتژی را از قمار جدا می‌کند:**

استردل وقتی منطقی است که نوسان **ضمنی** (IV، آنچه بازار برای آینده
قیمت‌گذاری کرده) از نوسان **تاریخی** (RV، آنچه واقعاً اتفاق افتاده)
ارزان‌تر باشد. یعنی بازار حرکت را دست‌کم گرفته.

اگر IV گران باشد، شما نوسان را گران می‌خرید و حتی حرکت بزرگ هم ممکن
است سود ندهد. به همین دلیل `max_iv_to_rv_ratio` پیش‌فرض **زیر ۱** است.
"""

from __future__ import annotations

from typing import Any

from data.option_chain_client import OptionContract
from signals.signal_model import Side
from strategies.multi_leg import Leg, MultiLegStrategy
from strategies.registry import register_strategy


@register_strategy
class LongStraddleStrategy(MultiLegStrategy):
    """خرید کال و پوت هم‌استرایک — شرط روی حرکت بزرگ، بدون جهت."""

    name = "long_straddle"

    expected_legs = 2

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            # IV باید از RV ارزان‌تر باشد؛ بالای ۱ یعنی نوسان گران است
            "max_iv_to_rv_ratio": 0.95,
            # پنجره‌ی محاسبه‌ی نوسان تاریخی
            "rv_window": 30,
            # استرایک باید تا این حد به قیمت پایه نزدیک باشد (نسبی)
            "max_moneyness_gap": 0.03,
            # زمان لازم است تا حرکت اتفاق بیفتد؛ سررسید خیلی نزدیک
            # یعنی theta شما را قبل از حرکت می‌خورد
            "min_days_to_expiry": 21,
            "max_days_to_expiry": 90,
            "min_open_interest": 50,
            # حداکثر اسپرد نسبی هر پایه؛ استردل دو بار اسپرد می‌پردازد
            "max_relative_spread": 0.25,
        }

    # ------------------------------------------------------------------
    def build_legs(self, context) -> list[Leg]:
        params = self.params
        spot = context.spot
        if spot <= 0:
            return []

        realized = context.realized_vol(params["rv_window"])
        if realized <= 0:
            # بدون نوسان تاریخی نمی‌شود گفت IV ارزان است یا گران
            return []

        pair = self._find_atm_pair(context, spot)
        if pair is None:
            return []
        call, put = pair

        # میانگین IV دو پایه — همان چیزی که واقعاً می‌پردازید
        ivs = [context.implied_vol(call), context.implied_vol(put)]
        known = [v for v in ivs if v]
        if len(known) < 2:
            # بدون IV هر دو پایه، قضاوت ارزانی ممکن نیست
            return []

        implied = sum(known) / len(known)
        ratio = implied / realized
        if ratio > params["max_iv_to_rv_ratio"]:
            return []

        self._last_reason = (
            f"نوسان ضمنی {implied:.0%} در برابر تاریخی {realized:.0%} "
            f"(نسبت {ratio:.2f}) — بازار حرکت را ارزان قیمت‌گذاری کرده. "
            f"استرایک {call.strike:,.0f} روی قیمت پایه {spot:,.0f}."
        )
        self._last_ratio = ratio

        return [
            Leg(call, Side.BUY, role="کال"),
            Leg(put, Side.BUY, role="پوت"),
        ]

    # ------------------------------------------------------------------
    def _find_atm_pair(
        self, context, spot: float
    ) -> tuple[OptionContract, OptionContract] | None:
        """نزدیک‌ترین جفت کال/پوت هم‌استرایک و هم‌سررسید به قیمت پایه."""
        params = self.params
        today = context.today()

        usable = [
            c
            for c in context.chain.contracts
            if params["min_days_to_expiry"]
            <= (c.expiry - today).days
            <= params["max_days_to_expiry"]
            and c.open_interest >= params["min_open_interest"]
            and self._spread_ok(c, params["max_relative_spread"])
        ]
        if not usable:
            return None

        calls = {(c.strike, c.expiry): c for c in usable if c.option_type == "call"}
        puts = {(c.strike, c.expiry): c for c in usable if c.option_type == "put"}

        # فقط استرایک‌هایی که **هر دو** پایه دارند
        common = sorted(
            set(calls) & set(puts), key=lambda k: (abs(k[0] - spot), k[1])
        )
        if not common:
            return None

        strike, expiry = common[0]
        if abs(strike - spot) / spot > params["max_moneyness_gap"]:
            # نزدیک‌ترین استرایک هم خیلی دور است؛ این دیگر استردل نیست
            return None
        return calls[(strike, expiry)], puts[(strike, expiry)]

    @staticmethod
    def _spread_ok(contract: OptionContract, max_spread: float) -> bool:
        """اسپرد پهن یعنی ورود و خروج گران؛ استردل دو برابر متحمل می‌شود."""
        bid, ask = contract.bid, contract.ask
        if not bid or not ask or bid <= 0:
            return False
        mid = (bid + ask) / 2
        return mid > 0 and (ask - bid) / mid <= max_spread

    # ------------------------------------------------------------------
    def explain(self, context, legs, leg) -> str:
        net = self.net_debit(legs)
        breakeven_up = legs[0].contract.strike + net
        breakeven_down = legs[0].contract.strike - net
        return (
            f"لانگ استردل روی {context.underlying}: {getattr(self, '_last_reason', '')} "
            f"هزینه خالص {net:,.0f}؛ سر به سر بالای {breakeven_up:,.0f} "
            f"یا زیر {breakeven_down:,.0f}. پایه: {leg.role}."
        )

    def confidence(self, context, legs) -> float | None:
        """هرچه IV نسبت به RV ارزان‌تر، اطمینان بیشتر.

        نسبت ۰.۵ (نوسان نصف قیمت) اطمینان کامل، و نسبت برابر با سقف
        مجاز، اطمینان صفر.
        """
        ratio = getattr(self, "_last_ratio", None)
        if ratio is None:
            return None
        ceiling = self.params["max_iv_to_rv_ratio"]
        if ceiling <= 0.5:
            return 0.5
        return max(0.0, min(1.0, (ceiling - ratio) / (ceiling - 0.5)))

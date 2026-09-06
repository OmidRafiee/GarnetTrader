"""کالر — سهم پایه + خرید پوت محافظ + فروش کال پوشش‌دهنده.

**برای چه کسی:** کسی که **سهم پایه را دارد** و می‌خواهد از افت شدید
محافظت شود، ولی حاضر است سقف سود را واگذار کند تا هزینه‌ی محافظت را
بدهد.

پرمیوم کال فروخته‌شده هزینه‌ی پوت خریداری‌شده را کم می‌کند. اگر بیشتر
از آن باشد، کالر **بستانکار** است: محافظت رایگان به‌اضافه‌ی کمی پول.

⚠️ **پیش‌نیاز واقعی:** باید سهم پایه را داشته باشید. این استراتژی
سیگنالِ «سهم بخر» نمی‌دهد؛ فرض می‌کند دارید. پروژه موجودی سهم شما را
نمی‌داند، پس این را در `metadata` صریح اعلام می‌کند تا کاربر بداند.
"""

from __future__ import annotations

from typing import Any

from data.option_chain_client import OptionContract
from signals.signal_model import Side
from strategies.multi_leg import Leg, MultiLegStrategy
from strategies.registry import register_strategy


@register_strategy
class CollarStrategy(MultiLegStrategy):
    """محافظت از سهم موجود، با واگذاری سقف سود."""

    name = "collar"

    #: فقط دو پایه‌ی **اختیار**؛ سهم پایه از قبل در اختیار کاربر است
    expected_legs = 2

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            # پوت چقدر زیر قیمت پایه باشد (کف حفاظت)
            "protection_pct": 0.10,
            # کال چقدر بالای قیمت پایه باشد (سقف سود)
            "cap_pct": 0.10,
            # حداکثر هزینه‌ی خالص محافظت، نسبت به قیمت پایه.
            # صفر یعنی فقط کالر رایگان یا بستانکار.
            "max_net_cost_pct": 0.02,
            "min_days_to_expiry": 21,
            "max_days_to_expiry": 90,
            "min_open_interest": 50,
            "max_relative_spread": 0.30,
        }

    # ------------------------------------------------------------------
    def build_legs(self, context) -> list[Leg]:
        params = self.params
        spot = context.spot
        if spot <= 0:
            return []

        target_put = spot * (1 - params["protection_pct"])
        target_call = spot * (1 + params["cap_pct"])
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
            return []

        best: tuple[float, OptionContract, OptionContract] | None = None
        for expiry in {c.expiry for c in usable}:
            same = [c for c in usable if c.expiry == expiry]
            puts = [c for c in same if c.option_type == "put" and c.strike < spot]
            calls = [c for c in same if c.option_type == "call" and c.strike > spot]
            if not puts or not calls:
                continue

            put = min(puts, key=lambda c: abs(c.strike - target_put))
            call = min(calls, key=lambda c: abs(c.strike - target_call))

            # هزینه‌ی خالص: پوت را می‌خریم (ask) و کال را می‌فروشیم (bid)
            net = (put.ask or 0.0) - (call.bid or 0.0)
            if net > spot * params["max_net_cost_pct"]:
                continue
            if best is None or net < best[0]:
                best = (net, put, call)

        if best is None:
            return []

        net_cost, put, call = best
        self._last = {
            "net_cost": net_cost,
            "put_strike": put.strike,
            "call_strike": call.strike,
            "spot": spot,
        }
        return [
            Leg(put, Side.BUY, role="پوت محافظ"),
            Leg(call, Side.SELL, role="کال فروخته"),
        ]

    @staticmethod
    def _spread_ok(contract: OptionContract, max_spread: float) -> bool:
        bid, ask = contract.bid, contract.ask
        if not bid or not ask or bid <= 0:
            return False
        mid = (bid + ask) / 2
        return mid > 0 and (ask - bid) / mid <= max_spread

    # ------------------------------------------------------------------
    def explain(self, context, legs, leg) -> str:
        last = getattr(self, "_last", {})
        net = last.get("net_cost", 0.0)
        kind = "بستانکار" if net < 0 else "هزینه"
        return (
            f"کالر روی {context.underlying}: کف حفاظت "
            f"{last.get('put_strike', 0):,.0f} و سقف سود "
            f"{last.get('call_strike', 0):,.0f} "
            f"({kind} خالص {abs(net):,.0f} در هر واحد). "
            f"⚠️ نیازمند داشتن سهم پایه. پایه: {leg.role}."
        )

    def confidence(self, context, legs) -> float | None:
        """کالر ارزان‌تر، مطمئن‌تر.

        بستانکار (هزینه‌ی منفی) بهترین حالت است؛ هزینه‌ی برابر با سقف
        مجاز، اطمینان صفر.
        """
        last = getattr(self, "_last", None)
        if not last:
            return None
        ceiling = context.spot * self.params["max_net_cost_pct"]
        if ceiling <= 0:
            return None
        net = last["net_cost"]
        return max(0.0, min(1.0, (ceiling - net) / (2 * ceiling)))

    def generate(self, context):
        """سیگنال‌ها را با هشدار «نیازمند سهم پایه» برچسب می‌زند."""
        signals = super().generate(context)
        for signal in signals:
            signal.metadata["requires_underlying_shares"] = True
            signal.metadata["strategy_type"] = "collar"
        return signals

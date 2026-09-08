"""پایه‌ی استراتژی‌های چندپایه (Straddle، Collar، Spread و …).

**مسئله‌ای که این ماژول حل می‌کند**

مدل `Signal` یک قرارداد است. یک استراتژی دوپایه مثل لانگ استردل، دو
`Signal` تولید می‌کند — و بدون گروه‌بندی، هیچ چیز نمی‌داند این دو یک
پوزیشن‌اند. نتیجه‌اش دو خطای واقعی است:

1. **اجرای ناقص:** فیلترهای `SignalGenerator` (اعتماد، ریسک، تکراری) روی
   هر سیگنال جدا اعمال می‌شوند. اگر یک پایه رد شود و دیگری بماند،
   استردل تبدیل به یک کال تنها می‌شود — که پروفایل ریسکش **کاملاً**
   فرق دارد.
2. **اندازه‌گیری ناسازگار:** ریسک هر پایه را جدا حساب می‌کند، پس ممکن
   است ۵ کال و ۳ پوت پیشنهاد شود. آن دیگر استردل نیست.

راه‌حل: هر پایه یک `leg_group_id` مشترک و `leg_count` می‌گیرد.
`SignalGenerator` گروه را **اتمی** رد یا قبول می‌کند و تعداد را روی
کمینه‌ی پایه‌ها یکسان می‌کند.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from data.option_chain_client import OptionContract
from signals.signal_model import Side, Signal
from strategies.base_strategy import BaseStrategy, StrategyContext


@dataclass(frozen=True)
class Leg:
    """یک پایه از استراتژی چندپایه."""

    contract: OptionContract
    side: Side
    #: نسبت این پایه به پایه‌های دیگر (۱ = یک قرارداد به ازای هر واحد)
    ratio: int = 1
    #: توضیح نقش این پایه، برای خواندن انسان
    role: str = ""

    @property
    def is_long(self) -> bool:
        return self.side is Side.BUY


class MultiLegStrategy(BaseStrategy):
    """پایه‌ی استراتژی‌هایی که چند قرارداد را با هم پیشنهاد می‌دهند.

    زیرکلاس‌ها `build_legs()` را پیاده می‌کنند و این کلاس گروه‌بندی،
    برچسب‌گذاری و ساخت `Signal` را انجام می‌دهد.
    """

    #: تعداد پایه‌ی مورد انتظار — برای اعتبارسنجی خروجی زیرکلاس
    expected_legs: int = 2

    def build_legs(self, context: StrategyContext) -> list[Leg]:
        """پایه‌های استراتژی؛ لیست خالی یعنی شرایط برقرار نیست."""
        raise NotImplementedError

    def generate(self, context: StrategyContext) -> list[Signal]:
        legs = self.build_legs(context)
        if not legs:
            return []

        if len(legs) != self.expected_legs:
            # این یک باگ در زیرکلاس است، نه شرایط بازار
            raise ValueError(
                f"{self.name} باید {self.expected_legs} پایه بدهد، "
                f"نه {len(legs)}."
            )

        group_id = uuid.uuid4().hex[:12]
        net_debit = self.net_debit(legs)

        signals = []
        for index, leg in enumerate(legs, start=1):
            metadata: dict[str, Any] = {
                "leg_group_id": group_id,
                "leg_count": len(legs),
                "leg_index": index,
                "leg_role": leg.role,
                "leg_ratio": leg.ratio,
                # هزینه‌ی خالص کل ساختار — ریسک باید روی این حساب کند،
                # نه روی پرمیوم تک‌پایه
                "net_debit": round(net_debit, 2),
                "strategy_kind": "multi_leg",
            }
            signals.append(
                self.build_signal(
                    context,
                    leg.contract,
                    leg.side,
                    reason=self.explain(context, legs, leg),
                    confidence=self.confidence(context, legs),
                    metadata=metadata,
                )
            )
        return signals

    # ------------------------------------------------------------------
    @staticmethod
    def net_debit(legs: list[Leg]) -> float:
        """هزینه‌ی خالص ورود به کل ساختار.

        مثبت = بدهکاری (پول می‌دهید)، منفی = بستانکاری (پول می‌گیرید).
        خریدار سمت ask را می‌پردازد و فروشنده سمت bid را می‌گیرد؛
        استفاده از mid هزینه را خوش‌بینانه نشان می‌دهد.
        """
        total = 0.0
        for leg in legs:
            contract = leg.contract
            if leg.is_long:
                price = contract.ask or contract.mid_price or 0.0
                total += price * leg.ratio
            else:
                price = contract.bid or contract.mid_price or 0.0
                total -= price * leg.ratio
        return total

    @staticmethod
    def max_loss(legs: list[Leg], net_debit: float) -> float | None:
        """حداکثر زیان ساختار، اگر قابل محاسبه باشد.

        `None` یعنی زیان **نامحدود** (مثلاً پایه‌ی فروش بدون پوشش) —
        که با «صفر» یکی نیست و نباید با آن اشتباه شود.
        """
        naked_short = any(
            not leg.is_long
            and not any(
                other.is_long and other.contract.option_type == leg.contract.option_type
                for other in legs
            )
            for leg in legs
        )
        if naked_short:
            return None
        return max(net_debit, 0.0)

    def explain(self, context: StrategyContext, legs: list[Leg], leg: Leg) -> str:
        """توضیح یک پایه در بافت کل ساختار."""
        return f"{self.name}: پایه {leg.role}"

    def confidence(self, context: StrategyContext, legs: list[Leg]) -> float | None:
        return None

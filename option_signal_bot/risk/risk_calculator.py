"""محاسبه position sizing و stop-loss **پیشنهادی**.

خروجی این ماژول فقط برای درج در متن سیگنال است؛ هیچ سفارشی اجرا نمی‌شود و
هیچ ارجاعی به لایه execution وجود ندارد.
"""

from __future__ import annotations

from dataclasses import dataclass

from risk.fees import NO_FEES, FeeSchedule
from signals.signal_model import Side, Signal


@dataclass(frozen=True)
class RiskLimits:
    """حدود ریسک حساب؛ معمولاً از `config/settings.yaml` خوانده می‌شود."""

    account_equity: float = 1_000_000_000.0  # ریال
    risk_per_trade_pct: float = 1.0  # درصد از دارایی که در یک معامله ریسک می‌شود
    max_position_pct: float = 5.0  # سقف درصد دارایی در یک پوزیشن
    max_contracts: int = 50
    stop_loss_pct: float = 35.0  # درصد افت پرمیوم برای حد ضرر
    take_profit_pct: float = 70.0  # درصد رشد پرمیوم برای حد سود


@dataclass(frozen=True)
class RiskSuggestion:
    """نتیجه محاسبه ریسک برای یک سیگنال."""

    suggested_qty: int
    stop_loss: float | None
    take_profit: float | None
    max_loss: float
    notes: str
    #: کارمزد رفت و برگشتِ کل موقعیت. صفر یعنی نرخی تنظیم نشده — نه
    #: اینکه کارمزدی وجود ندارد.
    round_trip_fees: float = 0.0

    @property
    def is_tradable(self) -> bool:
        """اگر حتی یک قرارداد هم از حدود ریسک عبور کند، سیگنال باید کنار گذاشته شود."""
        return self.suggested_qty > 0


class RiskCalculator:
    """اندازه پوزیشن و حدود پیشنهادی را از پرمیوم و حدود ریسک حساب می‌سازد."""

    def __init__(
        self,
        limits: RiskLimits | None = None,
        fees: FeeSchedule | None = None,
    ) -> None:
        self.limits = limits or RiskLimits()
        #: نرخ کارمزد. پیش‌فرض **صفر** است و حدس زده نمی‌شود؛ تا کاربر
        #: نرخ ندهد، همه‌ی اعداد دقیقاً مثل قبل می‌مانند.
        self.fees = fees or NO_FEES

    def evaluate(self, signal: Signal, contract_size: int = 1_000) -> RiskSuggestion:
        """محاسبه پیشنهاد ریسک برای یک سیگنال.

        منطق: بیشترین زیان قابل قبول در هر معامله = دارایی × درصد ریسک.
        برای خرید آپشن، زیان هر قرارداد تا حد ضرر = پرمیوم × درصد حد ضرر × اندازه قرارداد.

        اگر نرخ کارمزد تنظیم شده باشد، حد سود طوری جابه‌جا می‌شود که
        درصد هدف **پس از** کارمزد به دست بیاید — وگرنه حد سودِ «۷۰٪» در
        عمل کمتر می‌شد و کاربر نمی‌فهمید چرا.
        """
        limits = self.limits
        premium = max(signal.suggested_price, 0.0)
        if premium <= 0 or contract_size <= 0:
            return RiskSuggestion(0, None, None, 0.0, "پرمیوم نامعتبر است.")

        cost_per_contract = premium * contract_size
        risk_budget = limits.account_equity * limits.risk_per_trade_pct / 100.0
        loss_fraction = limits.stop_loss_pct / 100.0

        is_buy = signal.side is Side.BUY
        if is_buy:
            # خریدار: زیان محدود به پرمیوم؛ حد ضرر روی درصدی از پرمیوم
            risk_per_contract = cost_per_contract * loss_fraction
            stop_loss = round(premium * (1 - loss_fraction), 1)
        else:
            # فروشنده: زیان نظری نامحدود؛ محافظه‌کارانه دو برابر پرمیوم فرض می‌شود
            risk_per_contract = cost_per_contract * 2.0
            stop_loss = round(premium * (1 + loss_fraction), 1)

        # حد سود شامل کارمزد؛ با نرخ صفر دقیقاً همان عدد قبلی است.
        take_profit = round(
            self.fees.net_take_profit(premium, limits.take_profit_pct, is_buy), 1
        )
        # کارمزد به ریسک هر قرارداد اضافه می‌شود: پولی است که در هر حالت
        # از دست می‌رود، پس در اندازه‌گیری باید دیده شود.
        risk_per_contract += self.fees.round_trip_cost(cost_per_contract, is_buy)

        qty_by_risk = int(risk_budget // max(risk_per_contract, 1e-9))
        exposure_cap = limits.account_equity * limits.max_position_pct / 100.0
        qty_by_exposure = int(exposure_cap // cost_per_contract)
        qty = max(min(qty_by_risk, qty_by_exposure, limits.max_contracts), 0)

        notes = self._explain(qty, qty_by_risk, qty_by_exposure, limits)
        fee_note = self.fees.describe()
        if fee_note:
            notes = f"{notes} {fee_note}"

        return RiskSuggestion(
            suggested_qty=qty,
            stop_loss=stop_loss if qty > 0 else None,
            take_profit=take_profit if qty > 0 else None,
            max_loss=round(qty * risk_per_contract, 0),
            notes=notes,
            round_trip_fees=round(
                self.fees.round_trip_cost(qty * cost_per_contract, is_buy), 0
            ),
        )

    def apply(self, signal: Signal, contract_size: int = 1_000) -> Signal | None:
        """سیگنال را با اعداد ریسک برمی‌گرداند، یا None اگر از حدود ریسک عبور کند."""
        suggestion = self.evaluate(signal, contract_size)
        if not suggestion.is_tradable:
            return None
        enriched = signal.with_risk(
            suggested_qty=suggestion.suggested_qty,
            stop_loss=suggestion.stop_loss,
            take_profit=suggestion.take_profit,
        )
        enriched.contract_size = contract_size
        enriched.metadata.update(
            {
                "contract_size": contract_size,
                "max_loss": suggestion.max_loss,
                "risk_notes": suggestion.notes,
            }
        )
        if suggestion.round_trip_fees:
            enriched.metadata["round_trip_fees"] = suggestion.round_trip_fees
        return enriched

    @staticmethod
    def _explain(
        qty: int, qty_by_risk: int, qty_by_exposure: int, limits: RiskLimits
    ) -> str:
        if qty == 0:
            return "حتی یک قرارداد هم از حدود ریسک عبور می‌کند؛ سیگنال صرف‌نظر شد."
        if qty == limits.max_contracts:
            return f"محدود شده به سقف {limits.max_contracts} قرارداد."
        if qty == qty_by_exposure and qty_by_exposure < qty_by_risk:
            return f"محدود شده به سقف {limits.max_position_pct}٪ ارزش پوزیشن."
        return f"اندازه‌گیری بر پایه ریسک {limits.risk_per_trade_pct}٪ دارایی."

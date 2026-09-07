"""محاسبه‌ی سود/زیان و معیارهای ساختارهای چندپایه.

**قواعد واحد — مهم‌ترین بخش این ماژول**

اشتباه در واحدها بی‌صدا همه‌ی اعداد را ۱۰۰۰ برابر غلط می‌کند. پس:

| اصطلاح | معنی | مثال |
|---|---|---|
| `premium` | پرمیوم **هر واحد** (همان عددی که در تابلو می‌بینید) | ۶۲ |
| `contract_size` | تعداد واحد در هر قرارداد | ۱۰۰۰ |
| `quantity` | تعداد **قرارداد** | ۵ |
| premium per contract | `premium × contract_size` | ۶۲,۰۰۰ |
| total premium | `premium × contract_size × quantity` | ۳۱۰,۰۰۰ |

`OptionContract.bid/ask/last_price` در این پروژه **هر واحد** است
(از TSETMC همین‌طور می‌آید)، پس ضرب در `contract_size` **یک بار** و
فقط اینجا انجام می‌شود.

**کمیسیون:** بورس ایران کارمزد درصدی دارد، ولی نرخ دقیق در پروژه
نیست. پس پیش‌فرض **صفر** است و اگر کاربر در تنظیمات نرخ بدهد اعمال
می‌شود — عدد جعلی وارد نمی‌کنیم.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Any

from data.option_chain_client import OptionContract
from signals.signal_model import Side

#: نرخ کمیسیون ناشناخته است مگر کاربر بدهد؛ حدس نمی‌زنیم
DEFAULT_COMMISSION_RATE = 0.0


@dataclass(frozen=True)
class PositionLeg:
    """یک پایه‌ی قابل محاسبه — قرارداد آپشن یا خودِ دارایی پایه."""

    side: Side
    quantity: int
    #: قیمت ورود **هر واحد**
    entry_price: float
    contract_size: int = 1_000
    #: `None` یعنی این پایه خودِ سهم پایه است، نه اختیار
    option_type: str | None = None
    strike: float | None = None
    expiry: date | None = None
    symbol: str = ""
    role: str = ""
    #: داده‌ی نقدشوندگی، برای امتیازدهی
    bid: float | None = None
    ask: float | None = None
    open_interest: int = 0
    volume: int = 0
    #: کد یکتای TSETMC — برای گرفتن عمق مظنه‌ی همین پایه
    ins_code: str = ""

    @property
    def is_long(self) -> bool:
        return self.side is Side.BUY

    @property
    def is_option(self) -> bool:
        return self.option_type is not None

    @property
    def units(self) -> int:
        """تعداد کل واحد (نه قرارداد)."""
        return self.quantity * self.contract_size

    @property
    def entry_cost(self) -> float:
        """هزینه‌ی ورود این پایه. مثبت = پرداخت، منفی = دریافت."""
        cost = self.entry_price * self.units
        return cost if self.is_long else -cost

    @property
    def bid_ask_spread(self) -> float | None:
        """اسپرد مطلق؛ `None` اگر یک طرف مظنه نباشد."""
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask <= 0:
            return None
        return self.ask - self.bid

    @property
    def relative_spread(self) -> float | None:
        """اسپرد نسبت به میانگین مظنه — معیار اصلی نقدشوندگی."""
        spread = self.bid_ask_spread
        if spread is None:
            return None
        mid = (self.bid + self.ask) / 2
        return None if mid <= 0 else spread / mid

    def intrinsic_at(self, underlying_price: float) -> float:
        """ارزش ذاتی **هر واحد** در سررسید."""
        if not self.is_option:
            return underlying_price
        if self.strike is None:
            return 0.0
        if self.option_type == "call":
            return max(underlying_price - self.strike, 0.0)
        return max(self.strike - underlying_price, 0.0)

    def payoff_at(self, underlying_price: float) -> float:
        """سود/زیان این پایه در سررسید، **بدون** کمیسیون.

        برای اختیار: (ارزش ذاتی − پرمیوم پرداختی) × واحد، با علامت جهت.
        برای سهم پایه: (قیمت − قیمت ورود) × واحد.
        """
        value = self.intrinsic_at(underlying_price)
        per_unit = value - self.entry_price
        total = per_unit * self.units
        return total if self.is_long else -total


@dataclass
class StrategyPayoff:
    """ساختار کامل یک استراتژی، با همه‌ی معیارهای قابل محاسبه.

    مقادیری که از داده‌ی موجود قابل محاسبه نیستند **`None`** می‌مانند،
    نه صفر. صفر یعنی «محاسبه شد و صفر بود»؛ `None` یعنی «نمی‌دانیم».
    """

    strategy_type: str
    underlying: str
    legs: list[PositionLeg]
    expiration: date | None = None
    commission_rate: float = DEFAULT_COMMISSION_RATE
    underlying_price: float | None = None
    #: وجه تضمین لازم، اگر از کارگزاری آمده باشد
    required_margin: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def quantity(self) -> int:
        """تعداد قرارداد ساختار (کمینه‌ی پایه‌ها)."""
        return min((leg.quantity for leg in self.legs), default=0)

    @property
    def contract_multiplier(self) -> int:
        """اندازه‌ی قرارداد؛ اگر پایه‌ها یکسان نباشند، بزرگ‌ترین."""
        sizes = {leg.contract_size for leg in self.legs if leg.is_option}
        return max(sizes) if sizes else 1_000

    @property
    def net_premium(self) -> float:
        """پرمیوم خالص اختیارها. مثبت = بدهکار، منفی = بستانکار."""
        return sum(leg.entry_cost for leg in self.legs if leg.is_option)

    @property
    def net_credit(self) -> float | None:
        """اعتبار دریافتی؛ `None` اگر ساختار بدهکار باشد."""
        premium = self.net_premium
        return -premium if premium < 0 else None

    @property
    def net_cost(self) -> float:
        """هزینه‌ی خالص کل ساختار، شامل سهم پایه و کمیسیون."""
        return sum(leg.entry_cost for leg in self.legs) + self.commission

    @property
    def entry_cost(self) -> float:
        """هم‌معنی `net_cost` — برای هم‌خوانی با اصطلاح رایج."""
        return self.net_cost

    @property
    def commission(self) -> float:
        """کمیسیون کل، روی **ارزش مطلق** همه‌ی پایه‌ها.

        نرخ پیش‌فرض صفر است چون نرخ واقعی در پروژه نیست؛ حدس نمی‌زنیم.
        """
        if self.commission_rate <= 0:
            return 0.0
        turnover = sum(abs(leg.entry_price * leg.units) for leg in self.legs)
        return turnover * self.commission_rate

    @property
    def required_capital(self) -> float | None:
        """سرمایه‌ی لازم برای ورود.

        سه حالت، به ترتیب اولویت:

        1. ساختار بدهکار → هزینه‌ی خالص.
        2. وجه تضمین واقعی از کارگزاری، اگر داده شده باشد.
        3. ساختار بستانکار با **ریسک تعریف‌شده** (مثل آیرون کاندور):
           حداکثر زیان همان سرمایه‌ای است که واقعاً در خطر است.

        اگر هیچ‌کدام، `None` — وجه تضمین را حدس نمی‌زنیم.
        """
        cost = self.net_cost
        if cost > 0:
            return cost
        if self.required_margin is not None:
            return self.required_margin

        # ساختار بستانکار با زیان محدود: بیشترین چیزی که می‌توانید
        # از دست بدهید، همان سرمایه‌ی درگیر است.
        loss = self.max_loss
        if loss is not None and loss < 0:
            return abs(loss)
        return None

    # ------------------------------------------------------------------
    def payoff(self, underlying_price: float) -> float:
        """سود/زیان کل ساختار در یک قیمت مشخص دارایی پایه.

        `strategy.payoff(price)` — همان امضایی که خواسته شده.
        """
        total = sum(leg.payoff_at(underlying_price) for leg in self.legs)
        return total - self.commission

    def payoff_curve(
        self, low: float | None = None, high: float | None = None, points: int = 41
    ) -> list[tuple[float, float]]:
        """منحنی سود/زیان روی بازه‌ای از قیمت پایه."""
        center = self.underlying_price or self._center_strike()
        if center <= 0:
            return []
        low = low if low is not None else center * 0.6
        high = high if high is not None else center * 1.4
        if points < 2:
            points = 2
        step = (high - low) / (points - 1)
        return [
            (low + i * step, self.payoff(low + i * step)) for i in range(points)
        ]

    def _center_strike(self) -> float:
        strikes = [leg.strike for leg in self.legs if leg.strike]
        return sum(strikes) / len(strikes) if strikes else 0.0

    # ------------------------------------------------------------------
    @property
    def max_profit(self) -> float | None:
        """حداکثر سود؛ `None` یعنی **نامحدود**.

        `None` عمداً با صفر فرق دارد: لانگ کال سود نامحدود دارد، ولی
        صفر یعنی هیچ سودی ممکن نیست.
        """
        if self._has_unlimited_upside():
            return None
        return self._extreme(maximize=True)

    @property
    def max_loss(self) -> float | None:
        """حداکثر زیان (عدد منفی)؛ `None` یعنی **نامحدود**."""
        if self._has_unlimited_downside():
            return None
        return self._extreme(maximize=False)

    def _has_unlimited_upside(self) -> bool:
        """کال خریداری‌شده‌ی بی‌پوشش → سود نامحدود در بالا."""
        long_calls = sum(
            leg.quantity for leg in self.legs
            if leg.is_option and leg.option_type == "call" and leg.is_long
        )
        short_calls = sum(
            leg.quantity for leg in self.legs
            if leg.is_option and leg.option_type == "call" and not leg.is_long
        )
        long_underlying = any(
            not leg.is_option and leg.is_long for leg in self.legs
        )
        return long_calls > short_calls or (long_underlying and short_calls == 0)

    def _has_unlimited_downside(self) -> bool:
        """کال فروخته‌شده‌ی بی‌پوشش → زیان نامحدود در بالا.

        پوت فروخته‌شده زیانش محدود است (قیمت زیر صفر نمی‌رود)، پس
        نامحدود حساب نمی‌شود.
        """
        short_calls = sum(
            leg.quantity for leg in self.legs
            if leg.is_option and leg.option_type == "call" and not leg.is_long
        )
        long_calls = sum(
            leg.quantity for leg in self.legs
            if leg.is_option and leg.option_type == "call" and leg.is_long
        )
        covered = sum(
            leg.quantity for leg in self.legs if not leg.is_option and leg.is_long
        )
        return short_calls > long_calls + covered

    def _extreme(self, maximize: bool) -> float:
        """بیشینه/کمینه‌ی سود روی نقاط شکست.

        تابع سود تکه‌ای-خطی است، پس اکسترمم فقط روی استرایک‌ها یا در
        دو انتها رخ می‌دهد — نمونه‌برداری از همان نقاط کافی و دقیق است.
        """
        strikes = sorted({leg.strike for leg in self.legs if leg.strike})
        if not strikes:
            center = self.underlying_price or 0.0
            probes = [0.0, center * 2] if center else [0.0]
        else:
            span = max(strikes) - min(strikes) or max(strikes) * 0.5
            probes = [0.0, *strikes, max(strikes) + span]
            # نقاط میانی، چون اکسترمم ممکن است بین دو استرایک باشد
            probes += [(a + b) / 2 for a, b in pairwise(strikes)]

        values = [self.payoff(p) for p in probes]
        return max(values) if maximize else min(values)

    @property
    def breakevens(self) -> list[float]:
        """نقاط سر به سر، با جستجوی تغییر علامت روی منحنی."""
        center = self.underlying_price or self._center_strike()
        if center <= 0:
            return []

        points = self.payoff_curve(center * 0.3, center * 1.7, points=201)
        found: list[float] = []
        for (p1, v1), (p2, v2) in pairwise(points):
            if v1 == 0:
                found.append(p1)
            elif v1 * v2 < 0:
                # درون‌یابی خطی روی همان تکه
                found.append(p1 + (p2 - p1) * abs(v1) / (abs(v1) + abs(v2)))
        return [round(p, 2) for p in found]

    @property
    def lower_breakeven(self) -> float | None:
        points = self.breakevens
        return min(points) if points else None

    @property
    def upper_breakeven(self) -> float | None:
        points = self.breakevens
        return max(points) if points else None

    def distance_to_breakeven(self) -> float | None:
        """نزدیک‌ترین فاصله‌ی نسبی تا سر به سر — چقدر بازار باید حرکت کند."""
        spot = self.underlying_price
        if not spot or spot <= 0:
            return None
        points = self.breakevens
        if not points:
            return None
        return min(abs(p - spot) / spot for p in points)

    @property
    def roi(self) -> float | None:
        """بازده نسبت به سرمایه‌ی لازم؛ `None` اگر سرمایه معلوم نباشد."""
        capital = self.required_capital
        profit = self.max_profit
        if not capital or capital <= 0 or profit is None:
            return None
        return profit / capital

    @property
    def risk_reward(self) -> float | None:
        """نسبت سود به زیان؛ `None` اگر یک طرف نامحدود باشد."""
        profit, loss = self.max_profit, self.max_loss
        if profit is None or loss is None or loss == 0:
            return None
        return profit / abs(loss)

    @property
    def profit_zone(self) -> tuple[float, float] | None:
        """بازه‌ای از قیمت پایه که ساختار در آن سودده است."""
        points = self.breakevens
        if len(points) < 2:
            return None
        return (min(points), max(points))

    # ------------------------------------------------------------------
    @property
    def liquidity_score(self) -> float | None:
        """امتیاز نقدشوندگی ۰..۱ — کمینه‌ی پایه‌ها.

        عمداً کمینه، نه میانگین: یک پایه‌ی بی‌نقد کل ساختار را غیرقابل
        اجرا می‌کند، حتی اگر بقیه عالی باشند.
        """
        scores = []
        for leg in self.legs:
            if not leg.is_option:
                continue
            spread = leg.relative_spread
            if spread is None:
                return None  # داده ناقص؛ ادعای نقدشوندگی نمی‌کنیم
            spread_score = max(0.0, 1.0 - spread / 0.5)
            oi_score = min(1.0, leg.open_interest / 500) if leg.open_interest else 0.0
            scores.append(0.6 * spread_score + 0.4 * oi_score)
        return min(scores) if scores else None

    @property
    def min_open_interest(self) -> int:
        return min(
            (leg.open_interest for leg in self.legs if leg.is_option), default=0
        )

    @property
    def max_relative_spread(self) -> float | None:
        spreads = [
            leg.relative_spread for leg in self.legs if leg.is_option
        ]
        known = [s for s in spreads if s is not None]
        return max(known) if known else None

    # ------------------------------------------------------------------
    def order_plan(self) -> list[dict[str, Any]]:
        """نقشه‌ی سفارش — **فقط برای نمایش، هیچ سفارشی ارسال نمی‌شود**.

        این پروژه لایه‌ی اجرا را صدا نمی‌زند؛ خروجی اینجا برای این است
        که کاربر بداند چه سفارش‌هایی باید دستی ثبت کند.
        """
        return [
            {
                "action": "BUY" if leg.is_long else "SELL",
                "instrument": "UNDERLYING" if not leg.is_option else leg.option_type.upper(),
                "symbol": leg.symbol,
                "strike": leg.strike,
                "expiry": leg.expiry.isoformat() if leg.expiry else None,
                "quantity": leg.quantity,
                "contract_size": leg.contract_size,
                "limit_price": leg.entry_price,
                "role": leg.role,
                "ins_code": leg.ins_code,
            }
            for leg in self.legs
        ]

    @property
    def has_leg_risk(self) -> bool:
        """آیا اجرای جداگانه‌ی پایه‌ها ریسک دارد؟

        همیشه `True` برای ساختار چندپایه: کارگزاری ایزی‌تریدر سفارش
        چندپایه‌ی اتمیک ندارد (در کشف API چنین endpointای پیدا نشد)، پس
        بین ثبت پایه‌ها قیمت می‌تواند حرکت کند.
        """
        return len([leg for leg in self.legs if leg.is_option]) > 1

    def to_dict(self) -> dict[str, Any]:
        """خروجی قابل استفاده‌ی بقیه‌ی پروژه (API، UI، ذخیره‌سازی)."""
        return {
            "strategy_type": self.strategy_type,
            "underlying": self.underlying,
            "underlying_price": self.underlying_price,
            "expiration": self.expiration.isoformat() if self.expiration else None,
            "quantity": self.quantity,
            "contract_multiplier": self.contract_multiplier,
            "legs": self.order_plan(),
            "net_premium": round(self.net_premium, 2),
            "net_credit": round(self.net_credit, 2) if self.net_credit else None,
            "net_cost": round(self.net_cost, 2),
            "commission": round(self.commission, 2),
            "max_profit": round(self.max_profit, 2) if self.max_profit is not None else None,
            "max_loss": round(self.max_loss, 2) if self.max_loss is not None else None,
            "breakevens": self.breakevens,
            "lower_breakeven": self.lower_breakeven,
            "upper_breakeven": self.upper_breakeven,
            "distance_to_breakeven": self.distance_to_breakeven(),
            "profit_zone": self.profit_zone,
            "roi": self.roi,
            "risk_reward": self.risk_reward,
            "required_capital": self.required_capital,
            "required_margin": self.required_margin,
            "liquidity_score": self.liquidity_score,
            "min_open_interest": self.min_open_interest,
            "max_relative_spread": self.max_relative_spread,
            "has_leg_risk": self.has_leg_risk,
            **self.metadata,
        }


# ----------------------------------------------------------------------
# کمکی‌ها برای ساخت پایه از قرارداد واقعی زنجیره
# ----------------------------------------------------------------------
def leg_from_contract(
    contract: OptionContract,
    side: Side,
    quantity: int = 1,
    role: str = "",
) -> PositionLeg:
    """یک `PositionLeg` از قرارداد واقعی زنجیره می‌سازد.

    قیمت ورود واقع‌بینانه انتخاب می‌شود: خریدار `ask` می‌پردازد و
    فروشنده `bid` می‌گیرد. استفاده از `mid` هزینه را خوش‌بینانه نشان
    می‌دهد و ROI را غیرواقعی بالا می‌برد.
    """
    if side is Side.BUY:
        price = contract.ask or contract.mid_price or contract.last_price or 0.0
    else:
        price = contract.bid or contract.mid_price or contract.last_price or 0.0

    return PositionLeg(
        side=side,
        quantity=quantity,
        entry_price=float(price),
        contract_size=contract.contract_size,
        option_type=contract.option_type,
        strike=contract.strike,
        expiry=contract.expiry,
        symbol=contract.symbol,
        role=role,
        bid=contract.bid,
        ask=contract.ask,
        open_interest=contract.open_interest,
        volume=contract.volume,
        ins_code=contract.ins_code,
    )


def underlying_leg(
    symbol: str, price: float, quantity: int, contract_size: int, side: Side = Side.BUY
) -> PositionLeg:
    """پایه‌ی خودِ سهم پایه (برای کاوردکال و کالر)."""
    return PositionLeg(
        side=side,
        quantity=quantity,
        entry_price=price,
        contract_size=contract_size,
        option_type=None,
        symbol=symbol,
        role="سهم پایه",
    )

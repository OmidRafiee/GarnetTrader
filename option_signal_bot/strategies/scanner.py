"""اسکنر ساختارهای چندپایه روی زنجیره‌ی **واقعی** آپشن.

از `OptionChainClient` موجود پروژه تغذیه می‌شود؛ هیچ داده‌ی ساختگی
نمی‌سازد و هیچ سفارشی ثبت نمی‌کند.

**فیلترها عمداً سخت‌گیرند.** بازار واقعی پر از قراردادهای مرده است:
بدون مظنه، بدون موقعیت باز، با اسپرد ۱۰۰٪. ساختاری که یک پایه‌اش
غیرقابل معامله باشد، روی کاغذ سودده به نظر می‌رسد و در عمل اجرا
نمی‌شود — که بدتر از پیدا نکردن آن است.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from itertools import combinations

from data.option_chain_client import OptionChain, OptionContract
from signals.signal_model import Side
from strategies.payoff import (
    DEFAULT_COMMISSION_RATE,
    StrategyPayoff,
    leg_from_contract,
    underlying_leg,
)

logger = logging.getLogger(__name__)


@dataclass
class ScanFilters:
    """گیت‌های اعتبار و نقدشوندگی.

    مقادیر پیش‌فرض محافظه‌کارند: بهتر است چند ساختار کمتر پیدا شود تا
    ساختاری پیشنهاد شود که نمی‌توان اجرا کرد.
    """

    min_days_to_expiry: int = 7
    max_days_to_expiry: int = 120
    min_open_interest: int = 50
    #: اسپرد نسبی بیشتر از این، یعنی ورود و خروج گران است
    max_relative_spread: float = 0.35
    #: هر دو طرف مظنه لازم است؛ یک‌طرفه یعنی قیمت اجرا نامعلوم
    require_both_quotes: bool = True
    min_volume: int = 0
    commission_rate: float = DEFAULT_COMMISSION_RATE
    #: عرض اسپرد عمودی نسبت به قیمت پایه. اسپرد بیش از حد پهن عملاً یک
    #: پوزیشن تک‌پایه است با هزینه‌ی یک پایه‌ی اضافه.
    max_spread_width_pct: float = 0.30
    #: فاصله‌ی حداقلِ دو سررسید در اسپرد تقویمی (روز). اگر دو سررسید
    #: تقریباً هم‌زمان باشند، ساختار ارزش زمانی معناداری ندارد.
    min_calendar_gap_days: int = 14

    def accepts(self, contract: OptionContract, today: date) -> bool:
        """آیا این قرارداد قابل استفاده در یک ساختار است؟"""
        days = (contract.expiry - today).days
        if not self.min_days_to_expiry <= days <= self.max_days_to_expiry:
            return False
        if contract.open_interest < self.min_open_interest:
            return False
        if contract.volume < self.min_volume:
            return False
        if contract.strike <= 0:
            return False

        bid, ask = contract.bid, contract.ask
        if self.require_both_quotes:
            if not bid or not ask or bid <= 0 or ask <= 0:
                return False
            if ask < bid:
                # مظنه‌ی معکوس یعنی داده خراب است
                return False
            mid = (bid + ask) / 2
            if mid <= 0 or (ask - bid) / mid > self.max_relative_spread:
                return False
        elif not (bid or ask or contract.last_price):
            return False
        return True


class StrategyScanner:
    """پیدا کردن ساختارهای معتبر در زنجیره‌ی واقعی."""

    def __init__(self, filters: ScanFilters | None = None) -> None:
        self.filters = filters or ScanFilters()

    # ------------------------------------------------------------------
    def _usable(self, chain: OptionChain, today: date) -> list[OptionContract]:
        return [c for c in chain.contracts if self.filters.accepts(c, today)]

    @staticmethod
    def _by_expiry(
        contracts: list[OptionContract],
    ) -> dict[date, list[OptionContract]]:
        """گروه‌بندی بر اساس سررسید.

        هر ساختار باید **یک سررسید** داشته باشد؛ ترکیب سررسیدها ساختار
        دیگری است (تقویمی) با پروفایل ریسک متفاوت.
        """
        groups: dict[date, list[OptionContract]] = {}
        for contract in contracts:
            groups.setdefault(contract.expiry, []).append(contract)
        return groups

    # ------------------------------------------------------------------
    def scan_long_straddle(
        self, chain: OptionChain, today: date | None = None, limit: int = 20
    ) -> list[StrategyPayoff]:
        """خرید کال و پوت هم‌استرایک، هم‌سررسید."""
        today = today or date.today()
        spot = chain.spot_price
        results: list[StrategyPayoff] = []

        for expiry, contracts in self._by_expiry(self._usable(chain, today)).items():
            calls = {c.strike: c for c in contracts if c.option_type == "call"}
            puts = {c.strike: c for c in contracts if c.option_type == "put"}

            for strike in sorted(set(calls) & set(puts)):
                call, put = calls[strike], puts[strike]
                legs = [
                    leg_from_contract(call, Side.BUY, role="کال"),
                    leg_from_contract(put, Side.BUY, role="پوت"),
                ]
                payoff = StrategyPayoff(
                    strategy_type="long_straddle",
                    underlying=chain.underlying,
                    legs=legs,
                    expiration=expiry,
                    underlying_price=spot,
                    commission_rate=self.filters.commission_rate,
                    metadata={
                        "strike": strike,
                        "call_symbol": call.symbol,
                        "put_symbol": put.symbol,
                    },
                )
                if payoff.net_cost <= 0:
                    # استردل همیشه بدهکار است؛ عدد غیرمنطقی یعنی داده خراب
                    continue
                results.append(payoff)

        results.sort(key=lambda s: abs((s.metadata.get("strike") or 0) - spot))
        return results[:limit]

    # ------------------------------------------------------------------
    def scan_collar(
        self,
        chain: OptionChain,
        today: date | None = None,
        limit: int = 20,
        shares_owned: int | None = None,
    ) -> list[StrategyPayoff]:
        """سهم پایه + پوت محافظ (خرید) + کال پوشش‌دهنده (فروش).

        Args:
            shares_owned: تعداد **قرارداد** معادل سهم موجود. اگر ندهید
                یک قرارداد فرض می‌شود؛ این عدد ساختار را مقیاس می‌کند،
                نه شکلش را.
        """
        today = today or date.today()
        spot = chain.spot_price
        if spot <= 0:
            return []

        quantity = shares_owned or 1
        results: list[StrategyPayoff] = []

        for expiry, contracts in self._by_expiry(self._usable(chain, today)).items():
            # پوت زیر قیمت پایه = محافظت، کال بالای قیمت پایه = سقف سود
            puts = sorted(
                (c for c in contracts if c.option_type == "put" and c.strike < spot),
                key=lambda c: -c.strike,
            )
            calls = sorted(
                (c for c in contracts if c.option_type == "call" and c.strike > spot),
                key=lambda c: c.strike,
            )
            if not puts or not calls:
                continue

            for put in puts[:4]:
                for call in calls[:4]:
                    size = call.contract_size or put.contract_size
                    legs = [
                        underlying_leg(chain.underlying, spot, quantity, size),
                        leg_from_contract(put, Side.BUY, quantity, "پوت محافظ"),
                        leg_from_contract(call, Side.SELL, quantity, "کال فروخته"),
                    ]
                    payoff = StrategyPayoff(
                        strategy_type="collar",
                        underlying=chain.underlying,
                        legs=legs,
                        expiration=expiry,
                        underlying_price=spot,
                        commission_rate=self.filters.commission_rate,
                        metadata={
                            "put_symbol": put.symbol,
                            "call_symbol": call.symbol,
                            "put_strike": put.strike,
                            "call_strike": call.strike,
                            # کف حفاظت و سقف سود، همان چیزی که کاربر می‌پرسد
                            "lower_protection": put.strike,
                            "upper_profit_cap": call.strike,
                            "cost_of_protection": round(
                                (put.ask or 0) - (call.bid or 0), 2
                            ),
                        },
                    )
                    results.append(payoff)

        # کالری بهتر است که هزینه‌ی حفاظتش کمتر باشد
        results.sort(key=lambda s: s.metadata.get("cost_of_protection", 0))
        return results[:limit]

    # ------------------------------------------------------------------
    def scan_iron_condor(
        self, chain: OptionChain, today: date | None = None, limit: int = 20
    ) -> list[StrategyPayoff]:
        """خرید پوت / فروش پوت / فروش کال / خرید کال.

        ترتیب استرایک الزامی است:
        `long_put < short_put < short_call < long_call`
        """
        today = today or date.today()
        spot = chain.spot_price
        results: list[StrategyPayoff] = []

        for expiry, contracts in self._by_expiry(self._usable(chain, today)).items():
            puts = sorted(
                (c for c in contracts if c.option_type == "put" and c.strike < spot),
                key=lambda c: c.strike,
            )
            calls = sorted(
                (c for c in contracts if c.option_type == "call" and c.strike > spot),
                key=lambda c: c.strike,
            )
            if len(puts) < 2 or len(calls) < 2:
                continue

            # نزدیک‌ترین‌ها به قیمت پایه، برای مهار انفجار ترکیبی
            for long_put, short_put in combinations(puts[-5:], 2):
                if long_put.strike >= short_put.strike:
                    continue
                for short_call, long_call in combinations(calls[:5], 2):
                    if short_call.strike >= long_call.strike:
                        continue
                    if not (
                        long_put.strike
                        < short_put.strike
                        < short_call.strike
                        < long_call.strike
                    ):
                        continue

                    legs = [
                        leg_from_contract(long_put, Side.BUY, role="لانگ پوت"),
                        leg_from_contract(short_put, Side.SELL, role="شورت پوت"),
                        leg_from_contract(short_call, Side.SELL, role="شورت کال"),
                        leg_from_contract(long_call, Side.BUY, role="لانگ کال"),
                    ]
                    payoff = StrategyPayoff(
                        strategy_type="iron_condor",
                        underlying=chain.underlying,
                        legs=legs,
                        expiration=expiry,
                        underlying_price=spot,
                        commission_rate=self.filters.commission_rate,
                        metadata={
                            "long_put_strike": long_put.strike,
                            "short_put_strike": short_put.strike,
                            "short_call_strike": short_call.strike,
                            "long_call_strike": long_call.strike,
                            "put_spread_width": short_put.strike - long_put.strike,
                            "call_spread_width": long_call.strike - short_call.strike,
                        },
                    )
                    # آیرون کاندور باید **بستانکار** باشد؛ بدهکار یعنی
                    # ساختار معیوب است و سودی ندارد
                    if payoff.net_credit is None:
                        continue
                    results.append(payoff)

        results.sort(key=lambda s: -(s.roi or 0))
        return results[:limit]

    # ------------------------------------------------------------------
    # اسپردهای عمودی (هم‌سررسید، دو استرایک)
    # ------------------------------------------------------------------
    def _vertical_spreads(
        self,
        chain: OptionChain,
        option_type: str,
        long_is_lower: bool,
        strategy_type: str,
        today: date,
        limit: int,
        max_width_pct: float,
    ) -> list[StrategyPayoff]:
        """موتور مشترک هر چهار اسپرد عمودی.

        چهار ساختار (bull call، bear call، bull put، bear put) فقط در دو
        چیز فرق دارند: نوع قرارداد، و اینکه پایه‌ی خریداری‌شده استرایک
        پایین‌تر است یا بالاتر. نوشتنشان جدا یعنی چهار نسخه از همان منطق
        که با هم واگرا می‌شوند.

        Args:
            long_is_lower: `True` یعنی استرایک پایین‌تر خریده می‌شود.
            max_width_pct: عرض اسپرد نسبت به قیمت پایه. اسپرد بیش از حد
                پهن، عملاً یک پوزیشن تک‌پایه با هزینه‌ی بیشتر است.
        """
        spot = chain.spot_price
        results: list[StrategyPayoff] = []

        for expiry, contracts in self._by_expiry(self._usable(chain, today)).items():
            strikes = sorted(
                (c for c in contracts if c.option_type == option_type),
                key=lambda c: c.strike,
            )
            if len(strikes) < 2:
                continue

            for lower, upper in combinations(strikes, 2):
                if lower.strike >= upper.strike:
                    continue
                width = upper.strike - lower.strike
                if spot > 0 and width / spot > max_width_pct:
                    continue

                long_leg, short_leg = (
                    (lower, upper) if long_is_lower else (upper, lower)
                )
                legs = [
                    leg_from_contract(long_leg, Side.BUY, role="لانگ"),
                    leg_from_contract(short_leg, Side.SELL, role="شورت"),
                ]
                # سقف ارزش اسپرد: عرض × اندازه‌ی قرارداد. اسپرد عمودی
                # هیچ‌وقت بیش از این نمی‌ارزد.
                ceiling = width * max(leg.contract_size for leg in legs)

                payoff = StrategyPayoff(
                    strategy_type=strategy_type,
                    underlying=chain.underlying,
                    legs=legs,
                    expiration=expiry,
                    underlying_price=spot,
                    commission_rate=self.filters.commission_rate,
                    metadata={
                        "long_strike": long_leg.strike,
                        "short_strike": short_leg.strike,
                        "spread_width": width,
                        "max_spread_value": ceiling,
                        "is_debit": None,  # پایین‌تر پر می‌شود
                    },
                )

                # گیت درستی: هزینه‌ی یک اسپرد بدهکار نمی‌تواند از عرضش
                # بیشتر باشد، و یک اسپرد بستانکار نمی‌تواند اعتباری بیشتر
                # از عرض بگیرد. هر دو حالت یعنی مظنه‌ها ناسازگارند (بازار
                # رقیق)، نه اینکه فرصت آربیتراژ پیدا شده باشد.
                if abs(payoff.net_premium) > ceiling:
                    continue
                # سودِ صفر یا منفی یعنی ساختار بی‌معنا است
                if payoff.max_profit is not None and payoff.max_profit <= 0:
                    continue

                payoff.metadata["is_debit"] = payoff.net_premium > 0
                results.append(payoff)

        results.sort(key=lambda s: -(s.roi or 0))
        return results[:limit]

    def scan_bull_call_spread(
        self, chain: OptionChain, today: date | None = None, limit: int = 20
    ) -> list[StrategyPayoff]:
        """خرید کالِ پایین‌تر + فروش کالِ بالاتر. صعودی، بدهکار.

        سود و زیان هر دو **محدود**اند — همین چیزی است که آن را از خرید
        کالِ تنها متمایز می‌کند: هزینه‌ی کمتر، در ازای سقف سود.
        """
        return self._vertical_spreads(
            chain,
            option_type="call",
            long_is_lower=True,
            strategy_type="bull_call_spread",
            today=today or date.today(),
            limit=limit,
            max_width_pct=self.filters.max_spread_width_pct,
        )

    def scan_bear_call_spread(
        self, chain: OptionChain, today: date | None = None, limit: int = 20
    ) -> list[StrategyPayoff]:
        """فروش کالِ پایین‌تر + خرید کالِ بالاتر. نزولی/خنثی، بستانکار."""
        return self._vertical_spreads(
            chain,
            option_type="call",
            long_is_lower=False,
            strategy_type="bear_call_spread",
            today=today or date.today(),
            limit=limit,
            max_width_pct=self.filters.max_spread_width_pct,
        )

    def scan_bull_put_spread(
        self, chain: OptionChain, today: date | None = None, limit: int = 20
    ) -> list[StrategyPayoff]:
        """فروش پوتِ بالاتر + خرید پوتِ پایین‌تر. صعودی/خنثی، بستانکار."""
        return self._vertical_spreads(
            chain,
            option_type="put",
            long_is_lower=True,
            strategy_type="bull_put_spread",
            today=today or date.today(),
            limit=limit,
            max_width_pct=self.filters.max_spread_width_pct,
        )

    def scan_bear_put_spread(
        self, chain: OptionChain, today: date | None = None, limit: int = 20
    ) -> list[StrategyPayoff]:
        """خرید پوتِ بالاتر + فروش پوتِ پایین‌تر. نزولی، بدهکار."""
        return self._vertical_spreads(
            chain,
            option_type="put",
            long_is_lower=False,
            strategy_type="bear_put_spread",
            today=today or date.today(),
            limit=limit,
            max_width_pct=self.filters.max_spread_width_pct,
        )

    # ------------------------------------------------------------------
    # اسپرد تقویمی (هم‌استرایک، دو سررسید)
    # ------------------------------------------------------------------
    def scan_calendar_spread(
        self, chain: OptionChain, today: date | None = None, limit: int = 20
    ) -> list[StrategyPayoff]:
        """فروش سررسید نزدیک + خرید سررسید دور، هم‌استرایک.

        ⚠️ **محدودیت مهم و عمدی — این را جدی بگیرید.**

        بقیه‌ی ساختارهای این اسکنر یک سررسید دارند، پس منحنی سود در
        سررسید دقیق است. اسپرد تقویمی این‌طور نیست: وقتی پایه‌ی نزدیک
        منقضی می‌شود، پایه‌ی دور **هنوز ارزش زمانی دارد** — و کل سودِ این
        ساختار همان است.

        `StrategyPayoff` ارزش ذاتیِ سررسید را حساب می‌کند، پس برای این
        ساختار `max_profit` و `roi` را **دست‌کم** می‌گیرد (ارزش زمانیِ
        باقی‌مانده را صفر فرض می‌کند). محاسبه‌ی درستش به قیمت‌گذاری
        پایه‌ی دور در تاریخ سررسید نزدیک نیاز دارد، یعنی یک فرض IV آینده.

        پس این اسکنر:
        * `net_debit` را می‌دهد (هزینه‌ی ورود — این دقیق است)،
        * ساختارها را بر اساس **هزینه**، نه ROI، مرتب می‌کند،
        * و با `payoff_is_approximate: True` علامت می‌زند تا هیچ‌کس این
          ROI را با ROI ساختارهای دیگر مقایسه نکند.

        عددِ خوش‌بینانه‌ی حدسی ندادن، بهتر از عددی است که قابل مقایسه به
        نظر بیاید ولی نباشد.
        """
        today = today or date.today()
        spot = chain.spot_price
        usable = self._usable(chain, today)
        results: list[StrategyPayoff] = []

        for option_type in ("call", "put"):
            # گروه‌بندی بر اساس استرایک، چون این ساختار **بین** سررسیدها است
            by_strike: dict[float, list[OptionContract]] = {}
            for contract in usable:
                if contract.option_type == option_type:
                    by_strike.setdefault(contract.strike, []).append(contract)

            for strike, contracts in by_strike.items():
                if len(contracts) < 2:
                    continue
                ordered = sorted(contracts, key=lambda c: c.expiry)

                for near, far in combinations(ordered, 2):
                    gap = (far.expiry - near.expiry).days
                    if gap < self.filters.min_calendar_gap_days:
                        continue

                    legs = [
                        leg_from_contract(near, Side.SELL, role="شورت نزدیک"),
                        leg_from_contract(far, Side.BUY, role="لانگ دور"),
                    ]
                    payoff = StrategyPayoff(
                        strategy_type="calendar_spread",
                        underlying=chain.underlying,
                        legs=legs,
                        # سررسیدِ **نزدیک**: تاریخی که در آن تصمیم گرفته
                        # می‌شود. پایه‌ی دور بعد از آن هم زنده است.
                        expiration=near.expiry,
                        underlying_price=spot,
                        commission_rate=self.filters.commission_rate,
                        # دو سررسید ⇒ منحنی سررسید معنا ندارد. معیارهای
                        # وابسته به آن `None` می‌شوند، نه عددِ غلط.
                        single_expiry=False,
                        metadata={
                            "strike": strike,
                            "option_type": option_type,
                            "near_expiry": near.expiry.isoformat(),
                            "far_expiry": far.expiry.isoformat(),
                            "gap_days": gap,
                            # پرچم صداقت: منحنی سود این ساختار تقریبی است
                            "payoff_is_approximate": True,
                            "approximation_note": (
                                "سود در سررسید نزدیک به ارزش زمانیِ باقی‌مانده‌ی "
                                "پایه‌ی دور بستگی دارد، که اینجا صفر فرض شده. "
                                "پس max_profit و ROI دست‌کم گرفته شده‌اند و با "
                                "ساختارهای هم‌سررسید قابل مقایسه نیستند."
                            ),
                        },
                    )

                    # یک اسپرد تقویمیِ درست **بدهکار** است: سررسید دورتر
                    # همیشه ارزش زمانی بیشتری دارد. بستانکار بودن یعنی
                    # مظنه‌ها ناسازگارند (بازار رقیق)، نه یک فرصت.
                    if payoff.net_premium <= 0:
                        continue

                    results.append(payoff)

        # بر اساس **هزینه** مرتب می‌شود، نه ROI: ROI اینجا دست‌کم گرفته
        # شده و مرتب‌سازی با آن گمراه‌کننده است.
        results.sort(key=lambda s: s.net_cost)
        return results[:limit]

    # ------------------------------------------------------------------
    def scan_all(
        self, chain: OptionChain, today: date | None = None, limit: int = 10
    ) -> dict[str, list[StrategyPayoff]]:
        """همه‌ی اسکنرها روی یک زنجیره.

        از `SCAN_KINDS` ساخته می‌شود، نه از یک فهرست دستی: افزودن اسکنر
        تازه نباید نیاز به ویرایش این تابع داشته باشد (وگرنه یک روز
        اسکنری اضافه می‌شود که هیچ‌جا دیده نمی‌شود).
        """
        return {
            name: getattr(self, f"scan_{name}")(chain, today, limit)
            for name in SCAN_KINDS
        }


# ----------------------------------------------------------------------
# فهرست ساختارها — تنها منبع حقیقت
# ----------------------------------------------------------------------
#: نام اسکنر → برچسب فارسی. کلیدها همان پسوند متدهای `scan_*` هستند، و
#: `scan_all` هم از همین فهرست ساخته می‌شود. پس افزودن یک ساختار تازه =
#: یک متد `scan_x` + یک ورودی اینجا، و داشبورد خودکار نشانش می‌دهد.
SCAN_KINDS: dict[str, str] = {
    "long_straddle": "لانگ استردل",
    "collar": "کالر",
    "iron_condor": "آیرون کاندور",
    "bull_call_spread": "اسپرد صعودی کال",
    "bear_call_spread": "اسپرد نزولی کال",
    "bull_put_spread": "اسپرد صعودی پوت",
    "bear_put_spread": "اسپرد نزولی پوت",
    "calendar_spread": "اسپرد تقویمی",
}


# ----------------------------------------------------------------------
# رتبه‌بندی
# ----------------------------------------------------------------------
#: معیارهای مرتب‌سازی. `True` یعنی بزرگ‌تر بهتر است.
RANK_KEYS: dict[str, bool] = {
    "roi": True,
    "max_profit": True,
    "max_loss": True,  # کمتر منفی = بهتر، پس بزرگ‌تر بهتر است
    "risk_reward": True,
    "liquidity_score": True,
    "min_open_interest": True,
    "max_relative_spread": False,  # کمتر بهتر
    "distance_to_breakeven": False,  # نزدیک‌تر بهتر
    "net_credit": True,
    "required_capital": False,  # کمتر بهتر
}


def rank_strategies(
    strategies: list[StrategyPayoff],
    key: str = "roi",
    descending: bool | None = None,
) -> list[StrategyPayoff]:
    """مرتب‌سازی ساختارها بر اساس یک معیار.

    ساختارهایی که مقدارشان `None` است (نامعلوم یا نامحدود) **آخر**
    می‌آیند، نه اینکه صفر فرض شوند — «نمی‌دانیم» با «صفر» یکی نیست.
    """
    if key not in RANK_KEYS:
        raise ValueError(
            f"معیار ناشناخته: «{key}». موجود: {', '.join(sorted(RANK_KEYS))}"
        )
    bigger_is_better = RANK_KEYS[key] if descending is None else descending

    def value(strategy: StrategyPayoff):
        raw = getattr(strategy, key, None)
        if callable(raw):
            raw = raw()
        return raw

    known = [s for s in strategies if value(s) is not None]
    unknown = [s for s in strategies if value(s) is None]
    known.sort(key=value, reverse=bigger_is_better)
    return known + unknown

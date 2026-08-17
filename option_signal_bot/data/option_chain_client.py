"""اینترفیس زنجیره آپشن: استرایک‌ها، سررسیدها و پرمیوم لحظه‌ای.

`MockOptionChainClient` زنجیره‌ای واقع‌نما (با اسکیو نوسان و اسپرد مظنه) می‌سازد
تا استراتژی‌ها و بک‌تست بدون اتصال به TSETMC قابل اجرا باشند.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from data.market_data_client import MarketDataClient
from pricing.black_scholes import bs_price, years_to_expiry

# اندازه استاندارد قرارداد آپشن در بورس تهران (تعداد سهم پایه در هر قرارداد)
DEFAULT_CONTRACT_SIZE = 1_000


@dataclass(frozen=True)
class OptionContract:
    """یک نماد آپشن در زنجیره."""

    symbol: str
    underlying: str
    option_type: str  # "call" | "put"
    strike: float
    expiry: date
    bid: float | None = None
    ask: float | None = None
    last_price: float | None = None
    open_interest: int = 0
    volume: int = 0
    contract_size: int = DEFAULT_CONTRACT_SIZE

    @property
    def mid_price(self) -> float | None:
        """میانه مظنه خرید/فروش؛ اگر نبود، آخرین معامله."""
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2.0
        return self.last_price

    def days_to_expiry(self, today: date | None = None) -> int:
        return max((self.expiry - (today or date.today())).days, 0)

    def time_to_expiry(self, today: date | None = None) -> float:
        """زمان تا سررسید بر حسب سال."""
        return years_to_expiry(self.days_to_expiry(today))

    def is_liquid(self, min_open_interest: int = 1, min_volume: int = 0) -> bool:
        return self.open_interest >= min_open_interest and self.volume >= min_volume


@dataclass(frozen=True)
class OptionChain:
    """زنجیره کامل آپشن یک نماد پایه در یک لحظه."""

    underlying: str
    spot_price: float
    as_of: datetime
    contracts: tuple[OptionContract, ...]

    def expiries(self) -> list[date]:
        return sorted({c.expiry for c in self.contracts})

    def filter(
        self,
        option_type: str | None = None,
        expiry: date | None = None,
        min_days: int | None = None,
        max_days: int | None = None,
    ) -> list[OptionContract]:
        """فیلتر ساده روی نوع، سررسید و بازه روز باقی‌مانده."""
        today = self.as_of.date()
        result = list(self.contracts)
        if option_type:
            result = [c for c in result if c.option_type == option_type.lower()]
        if expiry:
            result = [c for c in result if c.expiry == expiry]
        if min_days is not None:
            result = [c for c in result if c.days_to_expiry(today) >= min_days]
        if max_days is not None:
            result = [c for c in result if c.days_to_expiry(today) <= max_days]
        return result

    def nearest_strike(
        self, option_type: str, target_strike: float, expiry: date | None = None
    ) -> OptionContract | None:
        """نزدیک‌ترین استرایک به مقدار هدف (مثلاً برای انتخاب ATM)."""
        candidates = self.filter(option_type=option_type, expiry=expiry)
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(c.strike - target_strike))


class OptionChainClient(ABC):
    """قرارداد دریافت زنجیره آپشن."""

    @abstractmethod
    def get_chain(self, underlying: str) -> OptionChain:
        """زنجیره آپشن نماد پایه در لحظه فعلی."""

    @abstractmethod
    def get_contract(self, option_symbol: str) -> OptionContract | None:
        """یک نماد آپشن مشخص (برای رفرش پرمیوم قبل از صدور سیگنال)."""


class MockOptionChainClient(OptionChainClient):
    """زنجیره مصنوعی حول قیمت پایه، با پرمیوم برگرفته از Black-Scholes."""

    def __init__(
        self,
        market_data: MarketDataClient,
        strikes_per_side: int = 4,
        expiry_days: tuple[int, ...] = (21, 49, 90),
        base_vol: float = 0.55,
        risk_free_rate: float = 0.25,
        spread_pct: float = 0.03,
    ) -> None:
        self.market_data = market_data
        self.strikes_per_side = strikes_per_side
        self.expiry_days = expiry_days
        self.base_vol = base_vol
        self.risk_free_rate = risk_free_rate
        self.spread_pct = spread_pct

    @staticmethod
    def _strike_step(spot: float) -> float:
        """گام استرایک را به مقدار گردِ متناسب با سطح قیمت انتخاب می‌کند."""
        for threshold, step in ((1_000, 50), (5_000, 100), (20_000, 500)):
            if spot < threshold:
                return float(step)
        return 1_000.0

    def _implied_vol(self, strike: float, spot: float) -> float:
        """اسکیو ساده: هر چه از ATM دورتر، نوسان ضمنی بالاتر."""
        moneyness = abs(strike / spot - 1.0)
        return self.base_vol * (1.0 + 0.6 * moneyness)

    def get_chain(self, underlying: str) -> OptionChain:
        quote = self.market_data.get_quote(underlying)
        spot = quote.reference_price
        step = self._strike_step(spot)
        atm = round(spot / step) * step
        today = date.today()

        contracts: list[OptionContract] = []
        for expiry_index, days in enumerate(self.expiry_days):
            expiry = today + timedelta(days=days)
            tte = years_to_expiry(days)
            for offset in range(-self.strikes_per_side, self.strikes_per_side + 1):
                strike = atm + offset * step
                if strike <= 0:
                    continue
                for option_type in ("call", "put"):
                    sigma = self._implied_vol(strike, spot)
                    fair = bs_price(
                        spot, strike, tte, self.risk_free_rate, sigma, option_type
                    )
                    fair = max(fair, 1.0)
                    half_spread = fair * self.spread_pct / 2.0
                    contracts.append(
                        OptionContract(
                            symbol=self._mock_symbol(
                                underlying, option_type, expiry_index, offset
                            ),
                            underlying=underlying,
                            option_type=option_type,
                            strike=float(strike),
                            expiry=expiry,
                            bid=round(fair - half_spread, 1),
                            ask=round(fair + half_spread, 1),
                            last_price=round(fair, 1),
                            # نمادهای نزدیک ATM نقدشوندگی بیشتری دارند
                            open_interest=max(500 - abs(offset) * 90, 20),
                            volume=max(300 - abs(offset) * 60, 5),
                        )
                    )
        return OptionChain(
            underlying=underlying,
            spot_price=spot,
            as_of=quote.timestamp,
            contracts=tuple(contracts),
        )

    @staticmethod
    def _mock_symbol(
        underlying: str, option_type: str, expiry_index: int, offset: int
    ) -> str:
        prefix = "ض" if option_type == "call" else "ط"
        return f"{prefix}{underlying[:3]}-{expiry_index}{offset:+d}"

    def get_contract(self, option_symbol: str) -> OptionContract | None:
        # در حالت mock نمی‌دانیم نماد به کدام پایه تعلق دارد، پس همه پایه‌ها را می‌گردیم.
        base_prices = getattr(self.market_data, "base_prices", {})
        for underlying in base_prices or {}:
            for contract in self.get_chain(underlying).contracts:
                if contract.symbol == option_symbol:
                    return contract
        return None

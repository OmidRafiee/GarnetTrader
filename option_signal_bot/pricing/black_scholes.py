"""قیمت‌گذاری آپشن اروپایی با مدل Black-Scholes-Merton + محاسبه گریک‌ها.

آپشن‌های بورس تهران اروپایی هستند (اعمال فقط در سررسید)، پس این مدل مناسب است.
هیچ وابستگی خارجی لازم نیست؛ تابع توزیع نرمال با `math.erf` پیاده شده تا
اجرای `--dry-run` بدون نصب scipy هم کار کند.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal

OptionKind = Literal["call", "put"]

_SQRT_2 = math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

# تعداد روزهای تقویمی سال، برای تبدیل تتا به «به‌ازای هر روز»
DAYS_PER_YEAR = 365.0


def norm_cdf(x: float) -> float:
    """تابع توزیع تجمعی نرمال استاندارد."""
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


def norm_pdf(x: float) -> float:
    """تابع چگالی نرمال استاندارد."""
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


@dataclass(frozen=True)
class Greeks:
    """گریک‌های آپشن.

    قراردادهای واحد (برای خوانایی در متن سیگنال):
      - delta, gamma: بر حسب واحد تغییر قیمت دارایی پایه
      - theta: تغییر قیمت آپشن به‌ازای گذشت **یک روز** تقویمی
      - vega: تغییر قیمت آپشن به‌ازای **۱ درصد** تغییر نوسان
      - rho: تغییر قیمت آپشن به‌ازای **۱ درصد** تغییر نرخ بهره
    """

    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def _normalize_kind(option_type: str) -> OptionKind:
    kind = option_type.strip().lower()
    if kind in ("c", "call"):
        return "call"
    if kind in ("p", "put"):
        return "put"
    raise ValueError(f"نوع آپشن نامعتبر است: {option_type!r}")


def _intrinsic(spot: float, strike: float, kind: OptionKind) -> float:
    """ارزش ذاتی؛ برای حالت‌های مرزی (سررسید رسیده یا نوسان صفر)."""
    return max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)


def d1_d2(
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    sigma: float,
    dividend_yield: float = 0.0,
) -> tuple[float, float]:
    """محاسبه d1 و d2 مدل Black-Scholes."""
    if time_to_expiry <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        raise ValueError("پارامترهای ورودی برای d1/d2 باید مثبت باشند.")
    vol_sqrt_t = sigma * math.sqrt(time_to_expiry)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend_yield + 0.5 * sigma * sigma) * time_to_expiry
    ) / vol_sqrt_t
    return d1, d1 - vol_sqrt_t


def bs_price(
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    sigma: float,
    option_type: str = "call",
    dividend_yield: float = 0.0,
) -> float:
    """قیمت نظری آپشن اروپایی.

    Args:
        spot: قیمت لحظه‌ای دارایی پایه
        strike: قیمت اعمال
        time_to_expiry: زمان تا سررسید بر حسب **سال**
        rate: نرخ بهره بدون ریسک (اعشاری، مثلاً 0.25)
        sigma: نوسان سالانه (اعشاری، مثلاً 0.45)
        option_type: "call" یا "put"
        dividend_yield: بازده تقسیم سود پیوسته (برای بورس تهران معمولاً 0)
    """
    kind = _normalize_kind(option_type)
    if time_to_expiry <= 0 or sigma <= 0:
        return _intrinsic(spot, strike, kind)

    d1, d2 = d1_d2(spot, strike, time_to_expiry, rate, sigma, dividend_yield)
    disc = math.exp(-rate * time_to_expiry)
    carry = math.exp(-dividend_yield * time_to_expiry)

    if kind == "call":
        return spot * carry * norm_cdf(d1) - strike * disc * norm_cdf(d2)
    return strike * disc * norm_cdf(-d2) - spot * carry * norm_cdf(-d1)


def bs_greeks(
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    sigma: float,
    option_type: str = "call",
    dividend_yield: float = 0.0,
) -> Greeks:
    """محاسبه Delta, Gamma, Theta, Vega, Rho برای آپشن اروپایی."""
    kind = _normalize_kind(option_type)

    if time_to_expiry <= 0 or sigma <= 0:
        # در سررسید فقط دلتای پله‌ای معنا دارد و بقیه گریک‌ها صفرند.
        itm = _intrinsic(spot, strike, kind) > 0
        delta = (1.0 if kind == "call" else -1.0) if itm else 0.0
        return Greeks(delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    d1, d2 = d1_d2(spot, strike, time_to_expiry, rate, sigma, dividend_yield)
    sqrt_t = math.sqrt(time_to_expiry)
    disc = math.exp(-rate * time_to_expiry)
    carry = math.exp(-dividend_yield * time_to_expiry)
    pdf_d1 = norm_pdf(d1)

    gamma = carry * pdf_d1 / (spot * sigma * sqrt_t)
    vega_raw = spot * carry * pdf_d1 * sqrt_t

    if kind == "call":
        delta = carry * norm_cdf(d1)
        theta_raw = (
            -spot * carry * pdf_d1 * sigma / (2 * sqrt_t)
            - rate * strike * disc * norm_cdf(d2)
            + dividend_yield * spot * carry * norm_cdf(d1)
        )
        rho_raw = strike * time_to_expiry * disc * norm_cdf(d2)
    else:
        delta = -carry * norm_cdf(-d1)
        theta_raw = (
            -spot * carry * pdf_d1 * sigma / (2 * sqrt_t)
            + rate * strike * disc * norm_cdf(-d2)
            - dividend_yield * spot * carry * norm_cdf(-d1)
        )
        rho_raw = -strike * time_to_expiry * disc * norm_cdf(-d2)

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta_raw / DAYS_PER_YEAR,  # به‌ازای هر روز
        vega=vega_raw / 100.0,  # به‌ازای ۱٪ نوسان
        rho=rho_raw / 100.0,  # به‌ازای ۱٪ نرخ بهره
    )


def years_to_expiry(days: float) -> float:
    """تبدیل «تعداد روز تا سررسید» به کسری از سال."""
    return max(days, 0.0) / DAYS_PER_YEAR

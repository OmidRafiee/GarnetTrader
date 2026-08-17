"""استخراج نوسان ضمنی (IV) از پرمیوم بازار.

روش: نیوتن-رافسون با استفاده از Vega، و در صورت واگرایی، سقوط به تنصیف (bisection)
که همیشه همگرا می‌شود چون قیمت آپشن نسبت به sigma یکنوا صعودی است.
"""

from __future__ import annotations

import math

from pricing.black_scholes import bs_price, d1_d2, norm_pdf

# بازه جست‌وجوی معقول برای نوسان سالانه در بازار ایران (۰.۱٪ تا ۵۰۰٪)
MIN_SIGMA = 1e-4
MAX_SIGMA = 5.0


def _price_bounds_ok(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    option_type: str,
) -> bool:
    """بررسی این‌که پرمیوم بازار در بازه بدون آربیتراژ مدل قرار دارد."""
    if market_price <= 0 or time_to_expiry <= 0:
        return False
    lo = bs_price(spot, strike, time_to_expiry, rate, MIN_SIGMA, option_type)
    hi = bs_price(spot, strike, time_to_expiry, rate, MAX_SIGMA, option_type)
    return lo <= market_price <= hi


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    option_type: str = "call",
    dividend_yield: float = 0.0,
    tolerance: float = 1e-6,
    max_iterations: int = 100,
) -> float | None:
    """نوسان ضمنی را برمی‌گرداند، یا None اگر قابل استخراج نباشد.

    Args:
        market_price: پرمیوم مشاهده‌شده در بازار (هم‌واحد با قیمت پایه)
        time_to_expiry: زمان تا سررسید بر حسب سال

    Returns:
        نوسان سالانه اعشاری (مثلاً 0.62) یا None برای قیمت‌های خارج از بازه مدل.
    """
    if not _price_bounds_ok(
        market_price, spot, strike, time_to_expiry, rate, option_type
    ):
        return None

    sigma = _initial_guess(market_price, spot, strike, time_to_expiry)

    # مرحله ۱: نیوتن-رافسون
    for _ in range(max_iterations):
        price = bs_price(
            spot, strike, time_to_expiry, rate, sigma, option_type, dividend_yield
        )
        diff = price - market_price
        if abs(diff) < tolerance:
            return sigma
        d1, _ = d1_d2(spot, strike, time_to_expiry, rate, sigma, dividend_yield)
        vega = (
            spot
            * math.exp(-dividend_yield * time_to_expiry)
            * norm_pdf(d1)
            * math.sqrt(time_to_expiry)
        )
        if vega < 1e-10:
            break
        sigma -= diff / vega
        if not (MIN_SIGMA < sigma < MAX_SIGMA):
            break

    # مرحله ۲: تنصیف به‌عنوان پشتیبان
    return _bisect(
        market_price,
        spot,
        strike,
        time_to_expiry,
        rate,
        option_type,
        dividend_yield,
        tolerance,
        max_iterations,
    )


def _initial_guess(
    market_price: float, spot: float, strike: float, time_to_expiry: float
) -> float:
    """حدس اولیه Brenner-Subrahmanyam برای آپشن نزدیک ATM."""
    guess = (
        math.sqrt(2 * math.pi / time_to_expiry) * market_price / max(spot, 1e-9)
        if time_to_expiry > 0
        else 0.5
    )
    return min(max(guess, 0.05), 2.0)


def _bisect(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    option_type: str,
    dividend_yield: float,
    tolerance: float,
    max_iterations: int,
) -> float | None:
    low, high = MIN_SIGMA, MAX_SIGMA
    for _ in range(max_iterations):
        mid = 0.5 * (low + high)
        price = bs_price(
            spot, strike, time_to_expiry, rate, mid, option_type, dividend_yield
        )
        if abs(price - market_price) < tolerance:
            return mid
        if price < market_price:
            low = mid
        else:
            high = mid
    result = 0.5 * (low + high)
    return result if MIN_SIGMA < result < MAX_SIGMA else None


def realized_volatility(closes: list[float], periods_per_year: float = 250.0) -> float:
    """نوسان تاریخی (سالانه‌شده) از سری قیمت پایانی، برای مقایسه با IV."""
    if len(closes) < 3:
        return 0.0
    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i] > 0 and closes[i - 1] > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance * periods_per_year)

"""تست‌های قیمت‌گذاری Black-Scholes، گریک‌ها و نوسان ضمنی."""

from __future__ import annotations

import math

import pytest

from pricing.black_scholes import bs_greeks, bs_price, norm_cdf, years_to_expiry
from pricing.implied_volatility import implied_volatility, realized_volatility

SPOT = 2_500.0
STRIKE = 2_500.0
TTE = 0.25
RATE = 0.25
SIGMA = 0.55


def test_norm_cdf_symmetry():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.5) + norm_cdf(-1.5) == pytest.approx(1.0)


def test_put_call_parity():
    """C - P = S - K·e^{-rT} باید دقیقاً برقرار باشد."""
    call = bs_price(SPOT, STRIKE, TTE, RATE, SIGMA, "call")
    put = bs_price(SPOT, STRIKE, TTE, RATE, SIGMA, "put")
    expected = SPOT - STRIKE * math.exp(-RATE * TTE)
    assert call - put == pytest.approx(expected, rel=1e-9)


def test_price_is_monotonic_in_volatility():
    low = bs_price(SPOT, STRIKE, TTE, RATE, 0.30, "call")
    high = bs_price(SPOT, STRIKE, TTE, RATE, 0.90, "call")
    assert high > low > 0


def test_price_at_expiry_is_intrinsic():
    assert bs_price(3_000, 2_500, 0.0, RATE, SIGMA, "call") == pytest.approx(500.0)
    assert bs_price(3_000, 2_500, 0.0, RATE, SIGMA, "put") == pytest.approx(0.0)
    assert bs_price(2_000, 2_500, 0.0, RATE, SIGMA, "put") == pytest.approx(500.0)


def test_deep_itm_call_approaches_discounted_intrinsic():
    price = bs_price(10_000, 2_500, TTE, RATE, 0.20, "call")
    floor = 10_000 - 2_500 * math.exp(-RATE * TTE)
    assert price == pytest.approx(floor, rel=1e-3)


def test_greeks_signs_and_ranges():
    call = bs_greeks(SPOT, STRIKE, TTE, RATE, SIGMA, "call")
    put = bs_greeks(SPOT, STRIKE, TTE, RATE, SIGMA, "put")

    assert 0.0 < call.delta < 1.0
    assert -1.0 < put.delta < 0.0
    assert call.gamma > 0 and put.gamma > 0
    assert call.vega > 0 and put.vega > 0
    assert call.theta < 0  # ارزش زمانی خریدار Call با گذشت زمان کم می‌شود
    assert call.rho > 0 and put.rho < 0


def test_gamma_and_vega_match_between_call_and_put():
    call = bs_greeks(SPOT, STRIKE, TTE, RATE, SIGMA, "call")
    put = bs_greeks(SPOT, STRIKE, TTE, RATE, SIGMA, "put")
    assert call.gamma == pytest.approx(put.gamma)
    assert call.vega == pytest.approx(put.vega)


def test_delta_matches_numeric_derivative():
    epsilon = 0.01
    up = bs_price(SPOT + epsilon, STRIKE, TTE, RATE, SIGMA, "call")
    down = bs_price(SPOT - epsilon, STRIKE, TTE, RATE, SIGMA, "call")
    numeric_delta = (up - down) / (2 * epsilon)
    assert bs_greeks(SPOT, STRIKE, TTE, RATE, SIGMA, "call").delta == pytest.approx(
        numeric_delta, rel=1e-4
    )


def test_greeks_at_expiry_are_flat():
    greeks = bs_greeks(3_000, 2_500, 0.0, RATE, SIGMA, "call")
    assert greeks.delta == 1.0
    assert greeks.gamma == greeks.vega == greeks.theta == 0.0


def test_invalid_option_type_raises():
    with pytest.raises(ValueError):
        bs_price(SPOT, STRIKE, TTE, RATE, SIGMA, "straddle")


@pytest.mark.parametrize("option_type", ["call", "put"])
@pytest.mark.parametrize("strike", [2_000.0, 2_500.0, 3_200.0])
def test_implied_volatility_roundtrip(option_type: str, strike: float):
    """IV استخراج‌شده از قیمت مدل باید همان sigma ورودی را برگرداند."""
    price = bs_price(SPOT, strike, TTE, RATE, SIGMA, option_type)
    iv = implied_volatility(price, SPOT, strike, TTE, RATE, option_type)
    assert iv is not None
    assert iv == pytest.approx(SIGMA, abs=1e-4)


def test_implied_volatility_rejects_impossible_price():
    # قیمتی بالاتر از خودِ دارایی پایه با هیچ نوسانی سازگار نیست.
    assert implied_volatility(SPOT * 2, SPOT, STRIKE, TTE, RATE, "call") is None
    assert implied_volatility(0.0, SPOT, STRIKE, TTE, RATE, "call") is None


def test_realized_volatility_is_positive_for_moving_series():
    closes = [1_000 * (1.01 ** i) if i % 2 else 1_000 * (0.99 ** i) for i in range(40)]
    assert realized_volatility(closes) > 0


def test_realized_volatility_handles_short_series():
    assert realized_volatility([100.0]) == 0.0


def test_years_to_expiry():
    assert years_to_expiry(365) == pytest.approx(1.0)
    assert years_to_expiry(-5) == 0.0

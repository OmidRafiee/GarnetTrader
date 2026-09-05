"""ابزار مشترک تست‌ها — همه روی داده‌ی **واقعیِ ضبط‌شده**.

این پروژه داده‌ی ساختگی ندارد. تست‌ها روی همان پاسخی اجرا می‌شوند که
TSETMC واقعاً داده و یک بار با `scripts/record_fixtures.py` ضبط شده،
پس هم واقعی‌اند و هم بدون شبکه و بدون ساعت بازار کار می‌کنند.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data.tsetmc_market_data_client import TsetmcMarketDataClient
from data.tsetmc_option_chain_client import (
    FilePayloadSource,
    TsetmcOptionChainClient,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CHAIN_FIXTURE = FIXTURE_DIR / "tsetmc_option_market_watch.json"
HISTORY_DIR = FIXTURE_DIR / "history"

#: نمادهایی که تاریخچه‌شان ضبط شده (به `record_fixtures.py` نگاه کنید)
RECORDED_SYMBOLS = ("خودرو", "شستا", "اهرم")


def _require_fixtures() -> None:
    if not CHAIN_FIXTURE.exists():
        pytest.skip(
            f"نمونه‌ی ضبط‌شده نیست: {CHAIN_FIXTURE}\n"
            "با scripts/record_fixtures.py بسازیدش."
        )


@pytest.fixture
def payload_source() -> FilePayloadSource:
    """منبع پاسخ ضبط‌شده‌ی دیده‌بان بازار آپشن."""
    _require_fixtures()
    return FilePayloadSource(CHAIN_FIXTURE)


@pytest.fixture
def market_data(payload_source) -> TsetmcMarketDataClient:
    """کلاینت داده‌ی پایه روی نمونه‌ی ضبط‌شده — بدون شبکه."""
    return TsetmcMarketDataClient(payload_source, history_dir=HISTORY_DIR)


@pytest.fixture
def option_chain(payload_source) -> TsetmcOptionChainClient:
    """زنجیره‌ی آپشن روی نمونه‌ی ضبط‌شده — بدون شبکه."""
    return TsetmcOptionChainClient(payload_source)


@pytest.fixture
def recorded_symbol() -> str:
    """یک نماد که هم آپشن دارد و هم تاریخچه‌اش ضبط شده."""
    _require_fixtures()
    for symbol in RECORDED_SYMBOLS:
        payload = json.loads(CHAIN_FIXTURE.read_text(encoding="utf-8"))
        rows = payload.get("instrumentOptMarketWatch") or []
        if any(row.get("lval30_UA") == symbol for row in rows):
            return symbol
    pytest.skip("هیچ نماد ضبط‌شده‌ای در نمونه پیدا نشد.")

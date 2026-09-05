"""تست‌های کلاینت‌های TSETMC روی یک پاسخ **واقعی ضبط‌شده** (بدون شبکه).

fixture در `tests/fixtures/tsetmc_option_market_watch.json` نمونه واقعی endpoint
دیده‌بان بازار آپشن است. اگر TSETMC ساختار پاسخ را عوض کند، این تست‌ها می‌شکنند —
که دقیقاً هدف است. برای به‌روزرسانی نمونه: `python scripts/fetch_tsetmc_sample.py`
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from data.tsetmc_market_data_client import TsetmcMarketDataClient
from data.tsetmc_option_chain_client import (
    ChainQualityReport,
    DataQualityRules,
    FilePayloadSource,
    HttpPayloadSource,
    PayloadSource,
    TsetmcOptionChainClient,
    parse_tsetmc_date,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tsetmc_option_market_watch.json"
UNDERLYING = "خودرو"


def _row_for(payload, underlying=UNDERLYING):
    """اولین ردیف مربوط به نماد پایه‌ی مورد نظر.

    قبلاً `[0]` استفاده می‌شد، ولی ترتیب ردیف‌ها در پاسخ TSETMC تضمینی
    نیست و با هر بار ضبط دوباره عوض می‌شود. انتخاب بر اساس نماد، تست را
    به ترتیب دلخواه بازار وابسته نمی‌کند.
    """
    rows = payload["instrumentOptMarketWatch"]
    return next(r for r in rows if r.get("lval30_UA") == underlying)


class CountingSource(PayloadSource):
    """منبعی که تعداد fetch را می‌شمارد — برای تست کش."""

    source_name = "counting"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def fetch(self) -> dict:
        self.calls += 1
        return self.payload


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def client() -> TsetmcOptionChainClient:
    return TsetmcOptionChainClient(FilePayloadSource(FIXTURE), cache_ttl_seconds=float("inf"))


# ----------------------------------------------------------------------
# نگاشت فیلدها
# ----------------------------------------------------------------------
def test_fixture_exists_and_has_expected_shape(payload):
    rows = payload["instrumentOptMarketWatch"]
    assert rows, "fixture خالی است"
    required = {
        "strikePrice", "endDate", "contractSize", "lval30_UA", "uaInsCode",
        "lVal18AFC_C", "lVal18AFC_P", "pMeDem_C", "pMeOf_C", "oP_C",
        "qTotTran5J_C", "pDrCotVal_UA",
    }
    assert required <= set(rows[0]), f"فیلدهای گمشده: {required - set(rows[0])}"


def test_parse_tsetmc_date():
    assert parse_tsetmc_date("20260819") == date(2026, 8, 19)
    assert parse_tsetmc_date(20260819) == date(2026, 8, 19)


@pytest.mark.parametrize("bad", ["2026819", "abcdefgh", ""])
def test_parse_tsetmc_date_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_tsetmc_date(bad)


def test_chain_maps_real_fields(client, payload):
    row = _row_for(payload)
    chain = client.get_chain(UNDERLYING)

    call = next(c for c in chain.contracts if c.symbol == row["lVal18AFC_C"])
    assert call.option_type == "call"
    assert call.underlying == UNDERLYING
    assert call.strike == float(row["strikePrice"])
    assert call.expiry == parse_tsetmc_date(row["endDate"])
    assert call.bid == float(row["pMeDem_C"])
    assert call.ask == float(row["pMeOf_C"])
    assert call.open_interest == int(row["oP_C"])
    assert call.volume == int(row["qTotTran5J_C"])
    assert call.contract_size == int(row["contractSize"])
    # میانه مظنه باید بین bid و ask بیفتد
    assert call.bid <= call.mid_price <= call.ask


def test_chain_spot_comes_from_payload(client, payload):
    row = _row_for(payload)
    chain = client.get_chain(UNDERLYING)
    assert chain.spot_price == float(row["pDrCotVal_UA"])


def test_chain_contains_both_calls_and_puts(client):
    chain = client.get_chain(UNDERLYING)
    kinds = {c.option_type for c in chain.contracts}
    assert kinds == {"call", "put"}


def test_zero_quote_means_no_quote_not_zero_price(client):
    """در TSETMC مقدار صفر یعنی «مظنه‌ای نیست»؛ نباید به قیمت صفر تبدیل شود."""
    chain = client.get_chain(UNDERLYING)
    for contract in chain.contracts:
        assert contract.bid is None or contract.bid > 0
        assert contract.ask is None or contract.ask > 0
        assert contract.last_price is None or contract.last_price > 0


# ----------------------------------------------------------------------
# گیت‌های کیفیت داده
# ----------------------------------------------------------------------
def test_contract_without_any_quote_is_dropped(tmp_path):
    """قرارداد بدون هیچ مظنه‌ای باید حذف شود.

    شرط لازم را خودمان می‌سازیم: در نمونه‌ی ضبط‌شده ممکن است همه‌ی
    قراردادهای یک نماد مظنه داشته باشند، و آن‌وقت تست چیزی را نمی‌سنجد.
    وابسته‌کردن تست به شانسِ داده‌ی آن روز، تست را بی‌ارزش می‌کند.
    """
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = payload["instrumentOptMarketWatch"]
    targets = [r for r in rows if r.get("lval30_UA") == UNDERLYING]
    assert targets, "نماد پایه در نمونه نیست"

    # مظنه‌ی چند ردیف را خالی کن تا گیت کیفیت چیزی برای حذف داشته باشد
    for row in targets[:3]:
        # pClosing هم fallback آخرین قیمت است؛ بدون آن قرارداد حذف نمی‌شود
        for key in ("pMeDem_C", "pMeOf_C", "pDrCotVal_C", "pClosing_C",
                    "pMeDem_P", "pMeOf_P", "pDrCotVal_P", "pClosing_P"):
            row[key] = 0

    doctored = tmp_path / "chain.json"
    doctored.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    strict = TsetmcOptionChainClient(
        FilePayloadSource(doctored),
        cache_ttl_seconds=float("inf"),
        quality=DataQualityRules(require_quote=True, max_relative_spread=None),
    )
    loose = TsetmcOptionChainClient(
        FilePayloadSource(doctored),
        cache_ttl_seconds=float("inf"),
        quality=DataQualityRules(require_quote=False, max_relative_spread=None),
    )
    strict_chain = strict.get_chain(UNDERLYING)
    loose_chain = loose.get_chain(UNDERLYING)

    assert len(strict_chain.contracts) < len(loose_chain.contracts)
    assert strict.last_quality_report.dropped_no_quote > 0


def test_wide_spread_is_dropped():
    tight = TsetmcOptionChainClient(
        FilePayloadSource(FIXTURE),
        cache_ttl_seconds=float("inf"),
        quality=DataQualityRules(max_relative_spread=0.01),
    )
    tight.get_chain(UNDERLYING)
    assert tight.last_quality_report.dropped_wide_spread > 0


def test_min_days_to_expiry_gate():
    far = TsetmcOptionChainClient(
        FilePayloadSource(FIXTURE),
        cache_ttl_seconds=float("inf"),
        quality=DataQualityRules(min_days_to_expiry=10_000),
    )
    chain = far.get_chain(UNDERLYING)
    assert chain.contracts == ()
    assert far.last_quality_report.dropped_near_expiry > 0


def test_quality_report_counts_add_up(client):
    client.get_chain(UNDERLYING)
    report = client.last_quality_report
    assert report.total == report.accepted + report.dropped
    assert "قرارداد پذیرفته شد" in report.summary()


def test_empty_report_summary():
    assert "0/0" in ChainQualityReport().summary()


# ----------------------------------------------------------------------
# کش و خطاها
# ----------------------------------------------------------------------
def test_payload_is_fetched_once_within_ttl(payload):
    source = CountingSource(payload)
    client = TsetmcOptionChainClient(source, cache_ttl_seconds=float("inf"))
    client.get_chain(UNDERLYING)
    client.get_chain(UNDERLYING)
    client.available_underlyings()
    assert source.calls == 1


def test_refresh_invalidates_cache(payload):
    source = CountingSource(payload)
    client = TsetmcOptionChainClient(source, cache_ttl_seconds=float("inf"))
    client.get_chain(UNDERLYING)
    client.refresh()
    client.get_chain(UNDERLYING)
    assert source.calls == 2


def test_unknown_underlying_fails_loudly(client):
    with pytest.raises(ValueError) as excinfo:
        client.get_chain("نماد_ناموجود")
    # پیام خطا باید نمادهای موجود را نشان بدهد تا اشتباه تنظیمات سریع پیدا شود
    assert UNDERLYING in str(excinfo.value)


def test_bad_payload_shape_raises():
    source = CountingSource({"چیز_دیگری": []})
    with pytest.raises(ValueError):
        TsetmcOptionChainClient(source).get_chain(UNDERLYING)


def test_missing_fixture_file_raises():
    with pytest.raises(FileNotFoundError):
        TsetmcOptionChainClient(FilePayloadSource("این-فایل-نیست.json")).get_chain(UNDERLYING)


def test_get_contract_by_symbol(client, payload):
    symbol = payload["instrumentOptMarketWatch"][0]["lVal18AFC_C"]
    contract = client.get_contract(symbol)
    assert contract is not None and contract.symbol == symbol
    assert client.get_contract("نماد_ناموجود") is None


def test_source_name_reports_fixture(client):
    assert client.source_name == "fixture"
    assert HttpPayloadSource().source_name == "tsetmc"


# ----------------------------------------------------------------------
# کلاینت داده پایه (نگاشت نماد و قیمت لحظه‌ای، بدون درخواست شبکه)
# ----------------------------------------------------------------------
def test_market_data_resolves_ins_code_from_option_payload(payload):
    market = TsetmcMarketDataClient(source=CountingSource(payload))
    row = _row_for(payload)
    assert market.resolve_ins_code(UNDERLYING) == row["uaInsCode"]
    assert UNDERLYING in market.available_symbols()


def test_market_data_quote_without_extra_request(payload):
    source = CountingSource(payload)
    market = TsetmcMarketDataClient(source=source)
    quote = market.get_quote(UNDERLYING)
    row = _row_for(payload)
    assert quote.last_price == float(row["pDrCotVal_UA"])
    assert quote.symbol == UNDERLYING
    assert source.calls == 1  # قیمت پایه از همان پاسخ زنجیره می‌آید


def test_market_data_unknown_symbol_fails_loudly(payload):
    market = TsetmcMarketDataClient(source=CountingSource(payload))
    with pytest.raises(ValueError):
        market.get_quote("نماد_ناموجود")
    with pytest.raises(ValueError):
        market.resolve_ins_code("نماد_ناموجود")


def test_candle_mapper_skips_broken_rows():
    good = {
        "dEven": 20260817, "priceFirst": 614, "priceMax": 620,
        "priceMin": 609, "pClosing": 614, "qTotTran5J": 100,
    }
    candle = TsetmcMarketDataClient._to_candle(good)
    assert candle is not None
    assert candle.date == date(2026, 8, 17)
    assert (candle.open, candle.high, candle.low, candle.close) == (614.0, 620.0, 609.0, 614.0)

    assert TsetmcMarketDataClient._to_candle({"dEven": 20260817, "pClosing": 0}) is None
    assert TsetmcMarketDataClient._to_candle({"pClosing": 100}) is None
    assert TsetmcMarketDataClient._to_candle({"dEven": "بد", "pClosing": 100}) is None

"""تست‌های لایه آداپتر کارگزاری.

تمرکز روی همان چیزی است که این لایه را خطرناک می‌کند: پاسخ کارگزاری
**مستند نیست** و هر آپدیت وب‌اپ می‌تواند شکلش را عوض کند. پس تست‌ها
بیشتر از «مسیر خوش‌بینانه»، روی رفتار در برابر پاسخ خراب تمرکز دارند.

نمونه‌های JSON از شکل واقعی کشف‌شده ساخته شده‌اند
(`docs/broker-emofid.md`)، نه از حدس.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from brokers import (
    AccountBalance,
    AccountDataSource,
    BrokerAuthError,
    BrokerError,
    BrokerUnavailableError,
    UnderlyingLimit,
)
from brokers.emofid import EmofidAccountClient

# ----------------------------------------------------------------------
# نمونه‌های پاسخ، با شکل واقعی کشف‌شده
# ----------------------------------------------------------------------
POSITION_ROW = {
    "customerIsin": "IRT1XXXX0001",
    "symbolIsin": "IRO9IKCO6K41",
    "symbolName": "ضخود7136",
    "side": 1,
    "executedQuantity": 5,
    "openSellQuantity": 0,
    "openBuyQuantity": 2,
    "contractRequiredMargin": 1_200_000,
    "cashSettlementDate": "2026-09-30T00:00:00",
    "physicalSettlementDate": "2026-10-02T00:00:00",
    "cefo": False,
    "totalMargin": 6_000_000,
    "strikePrice": 750,
    "baseIsin": "IRO1IKCO0008",
    "createDateTime": "2026-09-01T10:00:00",
    "modifyDate": "2026-09-02T11:00:00",
    "id": "pos-1",
    "strategyInvolveQuantity": 0,
    "buyAveragePrice": 61,
    "sellAveragePrice": 0,
    "closedPositionProfitLoss": 0,
}

CONTRACT_PAYLOAD = {
    "id": "c-1",
    "contractIsin": "IRO9IKCO6K41",
    "symbolIsin": "IRO9IKCO6K41",
    "startDate": "2026-06-01T00:00:00",
    "endDate": "2026-09-30T00:00:00",
    "contractSize": 1000,
    "baseIsin": "IRO1IKCO0008",
    "initialMargin": 1_500_000,
    "requiredMargin": 1_200_000,
    "strikePrice": 750,
    "maintenanceMargin": 900_000,
    "openPositions": 12_000,
    "maxCOP": 500,
    "maxCAOP": 1000,
    "cashSettlementDate": "2026-09-30T00:00:00",
    "physicalSettlementDate": "2026-10-02T00:00:00",
    "maxBrokerOP": 5000,
    "maxMarketOP": 50_000,
    "maxOrders": 100,
    "cefo": False,
}

LIMIT_PAYLOAD = {
    "baseIsin": "IRO1IKCO0008",
    "maxOpenPosition": 1000,
    "lowLimitOpenPosition": 0,
    "sumOpenPositions": 400,
    "isRequestAllowed": True,
}


# شکل از سشن واقعیِ کشف‌شده می‌آید (GET /easy/api/money).
MONEY_PAYLOAD = {
    "t0": 5_000_000,
    "t1": 7_000_000,
    "t2": 12_000_000,
    "buyPowerT0": 4_500_000,
    "buyPowerT1": 6_500_000,
    "buyPowerT2": 11_000_000,
    "blockT2": 500_000,
    "withdrawBlockT2": 0,
    "marginBlock": 3_000_000,
    "credit": 0,
    "avandCredit": 0,
    "walletWithdrawBalanceT0": 0,
    "warrantValueCredit": 0,
    "hamiBalance": 0,
    "block": 900_000,
}


class FakeClient(EmofidAccountClient):
    """کلاینت با شبکه‌ی جعلی، تا تست به حساب واقعی نیاز نداشته باشد."""

    def __init__(self, responses: dict[str, object], **kwargs):
        super().__init__(token="test-token", **kwargs)
        self.responses = responses
        self.calls: list[str] = []

    def _get(self, path, params=None):  # type: ignore[override]
        self.calls.append(path)
        if path not in self.responses:
            raise AssertionError(f"مسیر پیش‌بینی‌نشده: {path}")
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        return value


# ----------------------------------------------------------------------
# مسیر درست
# ----------------------------------------------------------------------
def test_positions_map_to_domain_model():
    client = FakeClient({"/option/api/Positions": [POSITION_ROW]})
    positions = client.get_positions()

    assert len(positions) == 1
    position = positions[0]
    assert position.symbol_name == "ضخود7136"
    assert position.quantity == 5
    assert position.is_long is True
    assert position.strike_price == 750
    assert position.cash_settlement_date == date(2026, 9, 30)
    assert position.is_open is True


def test_short_position_is_not_long():
    """`side` غیر از ۱ یعنی فروش — و فروش است که وجه تضمین می‌خواهد."""
    row = {**POSITION_ROW, "side": 2}
    client = FakeClient({"/option/api/Positions": [row]})
    assert client.get_positions()[0].is_long is False


def test_contract_spec_maps_all_fields():
    client = FakeClient({"/option/api/Contracts/IRO9IKCO6K41/symbol": CONTRACT_PAYLOAD})
    spec = client.get_contract_spec("IRO9IKCO6K41")

    assert spec.contract_size == 1000
    assert spec.strike_price == 750
    assert spec.end_date == date(2026, 9, 30)
    assert spec.initial_margin == 1_500_000
    assert spec.max_customer_open_position == 500


def test_underlying_limit_computes_remaining_capacity():
    client = FakeClient(
        {"/option/api/contracts/underlying-asset/IRO1IKCO0008": LIMIT_PAYLOAD}
    )
    limit = client.get_underlying_limit("IRO1IKCO0008", date(2026, 9, 30))

    assert limit.remaining_capacity == 600
    assert limit.is_request_allowed is True


def test_remaining_capacity_never_negative():
    """اگر مجموع از سقف رد شده باشد، ظرفیت صفر است نه منفی.

    عدد منفی در محاسبه‌ی تعداد قرارداد، بی‌صدا به سفارش معکوس تبدیل می‌شود.
    """
    limit = UnderlyingLimit(
        base_isin="X", max_open_position=100, sum_open_positions=150
    )
    assert limit.remaining_capacity == 0


# ----------------------------------------------------------------------
# پاسخ خراب — مهم‌ترین بخش
# ----------------------------------------------------------------------
def test_missing_required_field_raises_loudly():
    """نبود فیلد ضروری یعنی شکل API عوض شده؛ باید بلند خطا بدهد.

    برگرداندن صفر بدترین کار است: `strike=0` بی‌صدا هر محاسبه‌ای را
    خراب می‌کند بدون اینکه چیزی خراب به نظر برسد.
    """
    row = {k: v for k, v in POSITION_ROW.items() if k != "strikePrice"}
    client = FakeClient({"/option/api/Positions": [row]})

    with pytest.raises(BrokerError, match="strikePrice"):
        client.get_positions()


def test_zero_contract_size_is_rejected():
    """اندازه قرارداد صفر یعنی خطای ۱۰۰۰ برابری در ارزش موقعیت."""
    payload = {**CONTRACT_PAYLOAD, "contractSize": 0}
    client = FakeClient({"/option/api/Contracts/X/symbol": payload})

    with pytest.raises(BrokerError, match="اندازه قرارداد"):
        client.get_contract_spec("X")


def test_wrong_shape_is_rejected():
    """اگر آرایه انتظار داریم و شیء آمد، یعنی API عوض شده."""
    client = FakeClient({"/option/api/Positions": {"unexpected": "shape"}})
    with pytest.raises(BrokerError, match="آرایه"):
        client.get_positions()


def test_unparseable_date_does_not_crash_the_whole_read():
    """تاریخ خراب نباید کل خواندن پوزیشن‌ها را بخواباند.

    برخلاف عدد، تاریخِ نبوده تصمیم‌ساز نیست؛ `None` قابل قبول است.
    """
    row = {**POSITION_ROW, "cashSettlementDate": "not-a-date"}
    client = FakeClient({"/option/api/Positions": [row]})

    position = client.get_positions()[0]
    assert position.cash_settlement_date is None
    assert position.quantity == 5  # بقیه‌ی فیلدها سالم مانده‌اند


# ----------------------------------------------------------------------
# احراز هویت
# ----------------------------------------------------------------------
def test_client_without_token_is_not_authenticated():
    assert EmofidAccountClient(token=None).is_authenticated() is False


def test_request_without_token_raises_auth_error():
    client = EmofidAccountClient(token=None)
    with pytest.raises(BrokerAuthError, match="توکن"):
        client.get_positions()


def test_bearer_prefix_is_added_once():
    assert EmofidAccountClient(token="abc")._token == "Bearer abc"
    assert EmofidAccountClient(token="Bearer abc")._token == "Bearer abc"
    assert EmofidAccountClient(token="bearer abc")._token == "bearer abc"


def test_missing_session_file_gives_actionable_error(tmp_path):
    with pytest.raises(BrokerAuthError, match="3-discover-api"):
        EmofidAccountClient.from_session_file(tmp_path / "nope.json")


def test_session_file_without_token_is_rejected(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"origins": []}), encoding="utf-8")
    with pytest.raises(BrokerAuthError, match="توکن"):
        EmofidAccountClient.from_session_file(path)


def test_token_is_extracted_from_session_file(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps(
            {
                "origins": [
                    {
                        "origin": "https://easytrader.ir",
                        "localStorage": [
                            {"name": "theme", "value": "dark"},
                            {
                                "name": "auth_token",
                                "value": json.dumps({"access_token": "SECRET123"}),
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client = EmofidAccountClient.from_session_file(path)
    assert client.is_authenticated()
    assert client._token == "Bearer SECRET123"


# ----------------------------------------------------------------------
# ایمنی
# ----------------------------------------------------------------------
def test_adapter_implements_the_contract():
    """آداپتر باید همان قراردادی را پیاده کند که هسته می‌شناسد."""
    assert issubclass(EmofidAccountClient, AccountDataSource)


def test_broker_layer_has_no_write_operations():
    """این لایه فقط خواندن است.

    اگر روزی ثبت سفارش اضافه شد، باید در یک قرارداد **جداگانه** باشد تا
    «خواندن حساب» ناخواسته اجازه‌ی نوشتن نگیرد.
    """
    forbidden = ("def submit_order", "def place_order", "def cancel_order", "def modify_order")
    for module in ("brokers/base.py", "brokers/emofid.py"):
        source = Path(module).read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in source, f"{module} نباید {name} داشته باشد"


def test_broker_layer_never_imports_execution():
    for module in ("brokers/base.py", "brokers/emofid.py", "brokers/__init__.py"):
        source = Path(module).read_text(encoding="utf-8")
        assert "import execution" not in source
        assert "from execution" not in source


def test_only_get_requests_are_issued():
    """هیچ متد HTTP نوشتنی در آداپتر نباشد."""
    source = Path("brokers/emofid.py").read_text(encoding="utf-8")
    for verb in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"', "method=\"POST\""):
        assert verb not in source, f"آداپتر نباید {verb} بزند"


# ----------------------------------------------------------------------
# خطاهای شبکه
# ----------------------------------------------------------------------
def test_transient_failure_surfaces_as_unavailable():
    client = FakeClient(
        {"/option/api/Positions": BrokerUnavailableError("شبکه قطع است")}
    )
    with pytest.raises(BrokerUnavailableError):
        client.get_positions()


def test_auth_error_is_distinct_from_unavailable():
    """تفکیک مهم است: یکی با تلاش مجدد درست می‌شود، دیگری نه."""
    assert issubclass(BrokerAuthError, BrokerError)
    assert issubclass(BrokerUnavailableError, BrokerError)
    assert not issubclass(BrokerAuthError, BrokerUnavailableError)


def test_empty_isin_is_rejected_before_any_request():
    client = FakeClient({})
    with pytest.raises(ValueError):
        client.get_contract_spec("")
    with pytest.raises(ValueError):
        client.get_underlying_limit("", date(2026, 9, 30))
    assert client.calls == [], "نباید درخواستی زده می‌شد"

# ----------------------------------------------------------------------
# احراز هویت کوکی‌محور — شکل واقعی فایل سشن ایزی‌تریدر
# ----------------------------------------------------------------------
def _session_with_cookies(tmp_path, cookies):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")
    return path


def test_session_with_only_cookies_is_accepted(tmp_path):
    """فایل سشن واقعی ایزی‌تریدر توکن ندارد، فقط کوکی.

    توکن Bearer با OIDC در لحظه ساخته می‌شود و ذخیره نمی‌شود. پس نبودِ
    توکن نباید خطای «سشن نامعتبر» بدهد.
    """
    path = _session_with_cookies(tmp_path, [
        {"name": ".AspNetCore.Identity.Application", "value": "COOKIEVAL",
         "domain": "login.emofid.com"},
    ])
    client = EmofidAccountClient.from_session_file(path)
    assert client.is_authenticated()


def test_analytics_cookies_are_not_forwarded(tmp_path):
    """کوکی آنالیتیکس نه لازم است و نه باید بی‌دلیل جابه‌جا شود."""
    path = _session_with_cookies(tmp_path, [
        {"name": "sess", "value": "KEEP", "domain": "login.emofid.com"},
        {"name": "MUID", "value": "DROP", "domain": ".bing.com"},
        {"name": "CLID", "value": "DROP", "domain": "www.clarity.ms"},
    ])
    client = EmofidAccountClient.from_session_file(path)
    assert "KEEP" in client._cookie_header
    assert "DROP" not in client._cookie_header


def test_session_with_neither_token_nor_cookies_is_rejected(tmp_path):
    path = _session_with_cookies(tmp_path, [])
    with pytest.raises(BrokerAuthError, match="کوکی"):
        EmofidAccountClient.from_session_file(path)


def test_cookie_only_401_explains_the_missing_token():
    """۴۰۱ با کوکی تنها باید بگوید توکن لازم است، نه «دوباره لاگین کن».

    آزموده شد که /option/api/* هدر authorization می‌خواهد؛ پیام باید
    کاربر را به همان‌جا ببرد وگرنه بی‌جهت دوباره لاگین می‌کند.
    """
    import urllib.error

    class Cookie401(EmofidAccountClient):
        def __init__(self):
            super().__init__(token=None, cookie_header="sess=x", retries=1)
            self._opener = self

        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    with pytest.raises(BrokerAuthError, match="authorization"):
        Cookie401().get_positions()


# ----------------------------------------------------------------------
# موجودی حساب
# ----------------------------------------------------------------------
def test_balance_maps_all_fields():
    client = FakeClient({"/easy/api/money": MONEY_PAYLOAD})
    balance = client.get_balance()

    assert balance.cash_t0 == 5_000_000
    assert balance.cash_t2 == 12_000_000
    assert balance.buy_power_t2 == 11_000_000
    assert balance.blocked == 900_000
    assert balance.margin_blocked == 3_000_000
    assert balance.raw is MONEY_PAYLOAD


def test_equity_prefers_t2_buy_power():
    """محافظه‌کارانه‌ترین تعریف: قدرت خرید T+۲."""
    client = FakeClient({"/easy/api/money": MONEY_PAYLOAD})
    assert client.get_balance().equity == 11_000_000


def test_equity_falls_back_when_buy_power_missing():
    """بعضی حساب‌ها `buyPowerT2` ندارند؛ نقدِ T+۲ جایگزین درستی است."""
    payload = {k: v for k, v in MONEY_PAYLOAD.items() if k != "buyPowerT2"}
    client = FakeClient({"/easy/api/money": payload})
    assert client.get_balance().equity == 12_000_000


def test_equity_never_negative():
    """دارایی منفی به `RiskCalculator` تعداد قرارداد بی‌معنا می‌دهد."""
    payload = {**MONEY_PAYLOAD, "buyPowerT2": -5_000_000, "t2": -1, "t0": -1}
    assert FakeClient({"/easy/api/money": payload}).get_balance().equity == 0.0


def test_balance_tolerates_missing_optional_fields():
    """نبودِ `credit` یعنی «اعتباری نیست»، نه «شکل API عوض شده»."""
    client = FakeClient({"/easy/api/money": {"t0": 1_000}})
    balance = client.get_balance()
    assert balance.cash_t0 == 1_000
    assert balance.credit == 0.0
    assert balance.equity == 1_000


def test_balance_tolerates_unreadable_numbers():
    payload = {**MONEY_PAYLOAD, "credit": "n/a", "marginBlock": None}
    balance = FakeClient({"/easy/api/money": payload}).get_balance()
    assert balance.credit == 0.0 and balance.margin_blocked == 0.0


def test_balance_rejects_non_object_response():
    """آرایه به‌جای شیء یعنی شکل API عوض شده — باید بلند باشد."""
    client = FakeClient({"/easy/api/money": [1, 2, 3]})
    with pytest.raises(BrokerError, match="money"):
        client.get_balance()


def test_empty_balance_has_zero_equity():
    assert AccountBalance().equity == 0.0


# ----------------------------------------------------------------------
# دارایی سهم (شرط Covered Call)
# ----------------------------------------------------------------------
PERFORMANCE_PAYLOAD = {
    "items": [
        {
            "symbolIsin": "IRO1IKCO0008",
            "symbolName": "خودرو",
            "asset": 25_000,
            "totalBuyAveragePrice": 2_300,
        },
        {
            # سهمی که فروخته شده — `asset` صفر. باید فیلتر شود، وگرنه
            # Covered Call روی سهمی که نداری تأیید می‌شود.
            "symbolIsin": "IRO1SIPA0009",
            "symbolName": "خساپا",
            "asset": 0,
            "totalBuyAveragePrice": 1_800,
        },
    ]
}


def test_share_positions_map_to_domain_model():
    client = FakeClient({"/assetmodule/api/performance": PERFORMANCE_PAYLOAD})
    positions = client.get_share_positions()

    assert len(positions) == 1
    assert positions[0].symbol_name == "خودرو"
    assert positions[0].quantity == 25_000
    assert positions[0].average_price == 2_300


def test_zero_asset_rows_are_dropped():
    """سهمِ فروخته‌شده در کارنامه می‌ماند ولی دارایی نیست."""
    client = FakeClient({"/assetmodule/api/performance": PERFORMANCE_PAYLOAD})
    assert all(p.quantity > 0 for p in client.get_share_positions())
    assert "خساپا" not in {p.symbol_name for p in client.get_share_positions()}


def test_share_positions_reject_malformed_payload():
    with pytest.raises(BrokerError, match="performance"):
        FakeClient({"/assetmodule/api/performance": []}).get_share_positions()

    with pytest.raises(BrokerError, match="items"):
        FakeClient(
            {"/assetmodule/api/performance": {"items": "nope"}}
        ).get_share_positions()


def test_share_positions_skip_junk_rows():
    payload = {"items": ["junk", None, {}, {"asset": 5, "symbolName": "x"}]}
    positions = FakeClient(
        {"/assetmodule/api/performance": payload}
    ).get_share_positions()
    assert len(positions) == 1 and positions[0].quantity == 5


def test_emofid_declares_share_position_support():
    """مصرف‌کننده با همین فلگ «نمی‌دانم» را از «نداری» تشخیص می‌دهد."""
    assert EmofidAccountClient.supports_share_positions is True


def test_base_contract_defaults_to_no_share_support():
    """آداپتری که پیاده نکرده، نباید ادعای دانستن کند."""
    assert AccountDataSource.supports_share_positions is False

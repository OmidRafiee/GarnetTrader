"""تست‌های ابزار کشف API — تمرکز روی **تضمین لو نرفتن راز**.

این تست‌ها یک HAR ساختگی با توکن و کد ملی و موجودی جعلی می‌سازند و بررسی می‌کنند
که هیچ‌کدام از این مقادیر در خروجی نباشند.
"""

from __future__ import annotations

import json

import pytest

from discovery.api_inventory import (
    NUM_MASK,
    REDACTED,
    ApiCall,
    ApiInventory,
    endpoint_key,
    extract_signalr_targets,
    looks_sensitive,
    redact_header_value,
    redact_url,
    sketch,
    sketch_json_text,
)

# مقادیر «حساس» جعلی که نباید در هیچ خروجی ظاهر شوند
FAKE_TOKEN = "eyJhbGciOiJIUzI1NiwidHlwIjoiSldUIn0.SUPERSECRET.signature"
FAKE_NATIONAL_ID = "0071234567"
FAKE_BALANCE = 987654321
FAKE_ACCOUNT = "12345678"


# ----------------------------------------------------------------------
# اسکلت نوع‌ها: هیچ مقداری عبور نمی‌کند
# ----------------------------------------------------------------------
def test_sketch_replaces_values_with_types():
    assert sketch({"balance": FAKE_BALANCE, "symbol": "ضخود6053", "ok": True}) == {
        "balance": "number",
        "symbol": "string",
        "ok": "bool",
    }


def test_sketch_handles_nested_and_lists():
    data = {
        "positions": [
            {"symbol": "خودرو", "qty": 1000, "avg": 612.5},
            {"symbol": "شستا", "qty": 2000, "avg": 2890.0},
        ],
        "meta": {"page": 1, "next": None},
        "empty": [],
    }
    assert sketch(data) == {
        "positions": [{"symbol": "string", "qty": "number", "avg": "number"}],
        "meta": {"page": "number", "next": "null"},
        "empty": ["<empty>"],
    }


def test_sketch_stops_at_max_depth():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
    assert "..." in json.dumps(sketch(deep, max_depth=3))


def test_no_secret_value_survives_sketching():
    payload = {
        "accessToken": FAKE_TOKEN,
        "nationalId": FAKE_NATIONAL_ID,
        "balance": FAKE_BALANCE,
        "accountNumber": FAKE_ACCOUNT,
    }
    rendered = json.dumps(sketch(payload), ensure_ascii=False)
    for secret in (FAKE_TOKEN, FAKE_NATIONAL_ID, str(FAKE_BALANCE), FAKE_ACCOUNT):
        assert secret not in rendered
    # ولی نام فیلدها باید بمانند، چون برای نوشتن آداپتر لازم‌اند
    assert "accessToken" in rendered and "balance" in rendered


def test_sketch_json_text_returns_none_for_non_json():
    assert sketch_json_text("<html>سلام</html>") is None
    assert sketch_json_text(None) is None
    assert sketch_json_text('{"a": 1}') == {"a": "number"}


# ----------------------------------------------------------------------
# پاک‌سازی هدر و URL
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    ["Authorization", "authorization", "Cookie", "X-Auth-Token", "apikey", "X-Refresh-Token"],
)
def test_sensitive_headers_are_redacted(name):
    assert FAKE_TOKEN not in redact_header_value(name, FAKE_TOKEN)


def test_auth_scheme_is_kept_but_token_is_not():
    """نوع احراز هویت برای نوشتن آداپتر لازم است؛ خودِ توکن نه."""
    result = redact_header_value("Authorization", f"Bearer {FAKE_TOKEN}")
    assert result == f"Bearer {REDACTED}"
    assert FAKE_TOKEN not in result


def test_harmless_header_passes_through():
    assert redact_header_value("Content-Type", "application/json") == "application/json"


@pytest.mark.parametrize(
    "key,expected",
    [
        ("password", True), ("accessToken", True), ("nationalCode", True),
        ("customer_id", True), ("otpCode", True), ("mobileNumber", True),
        ("symbol", False), ("page", False), ("market", False),
    ],
)
def test_looks_sensitive(key, expected):
    assert looks_sensitive(key) is expected


def test_redact_url_masks_sensitive_query_values():
    url = f"https://api.example.com/v1/orders?token={FAKE_TOKEN}&type=option&page=2"
    result = redact_url(url)
    assert FAKE_TOKEN not in result
    assert REDACTED in result
    # مقادیر کوتاه و بی‌خطر مفیدند و می‌مانند
    assert "type=option" in result and "page=2" in result


def test_redact_url_masks_long_numbers_in_path():
    result = redact_url(f"https://api.example.com/accounts/{FAKE_NATIONAL_ID}/positions")
    assert FAKE_NATIONAL_ID not in result
    assert NUM_MASK in result



def test_redact_url_masks_short_broker_account_codes():
    """کد حساب ۵ رقمی هم باید ماسک شود، نه فقط کد ملی ۱۰ رقمی.

    با آستانه‌ی قبلی (۶ رقم) یک کد حساب ۵ رقمی از فیلتر رد می‌شد و در
    گزارش قابل اشتراک می‌نشست. کدهای حساب/مشتری کارگزاری‌های ایرانی
    معمولاً ۵-۶ رقمی‌اند.
    """
    for account in ("1234", "12345", "123456"):
        result = redact_url(f"https://api.example.com/accounts/{account}/positions")
        assert account not in result, f"کد {account} ماسک نشد: {result}"
        assert NUM_MASK in result


def test_redact_url_keeps_short_structural_numbers():
    """۱ تا ۳ رقم ساختار مفید است، نه شناسه؛ نباید ماسک شود."""
    for path in ("/api/Instrument/GetInstrumentOptionMarketWatch/1",
                 "/api/MarketData/GetMarketOverview/2",
                 "/api/v2/orders"):
        result = redact_url("https://api.example.com" + path)
        assert NUM_MASK not in result, f"بی‌جهت ماسک شد: {result}"

def test_endpoint_key_ignores_query():
    key_a = endpoint_key("get", "https://a.com/v1/x?page=1")
    key_b = endpoint_key("GET", "https://a.com/v1/x?page=2")
    assert key_a == key_b == "GET https://a.com/v1/x"


# ----------------------------------------------------------------------
# SignalR
# ----------------------------------------------------------------------
def test_extract_signalr_targets():
    frame = (
        '{"type":1,"target":"OnQuoteChanged","arguments":[{"symbol":"ضخود6053","bid":34}]}\x1e'
        '{"type":1,"target":"OnOrderStatus","arguments":[{"id":"12345678"}]}\x1e'
    )
    assert extract_signalr_targets(frame) == ["OnQuoteChanged", "OnOrderStatus"]


def test_socket_frames_record_targets_not_arguments():
    inventory = ApiInventory()
    frame = (
        '{"type":1,"target":"OnQuoteChanged",'
        f'"arguments":[{{"balance":{FAKE_BALANCE},"token":"{FAKE_TOKEN}"}}]}}'
    )
    inventory.add_socket_frame("wss://push.example.com/hub", frame, sent=False)

    rendered = inventory.to_markdown() + inventory.to_json()
    assert "OnQuoteChanged" in rendered
    assert FAKE_TOKEN not in rendered
    assert str(FAKE_BALANCE) not in rendered


def test_handshake_frame_without_target_is_tolerated():
    inventory = ApiInventory()
    inventory.add_socket_frame("wss://x/hub", '{"protocol":"json","version":1}\x1e', sent=True)
    inventory.add_socket_frame("wss://x/hub", "not-json-at-all", sent=False)
    socket = next(iter(inventory.sockets.values()))
    assert socket.frames_sent == 1 and socket.frames_received == 1
    assert socket.sent_targets == [] and socket.received_targets == []


# ----------------------------------------------------------------------
# فهرست و ادغام
# ----------------------------------------------------------------------
def _call(**kwargs) -> ApiCall:
    base = {"method": "GET", "url": "https://api.example.com/v1/positions"}
    return ApiCall(**{**base, **kwargs})


def test_repeated_calls_are_merged_and_counted():
    inventory = ApiInventory()
    inventory.add_call(_call(status=200))
    inventory.add_call(_call(status=200))
    assert len(inventory.calls) == 1
    assert next(iter(inventory.calls.values())).count == 2


def test_merge_prefers_successful_response_schema():
    inventory = ApiInventory()
    inventory.add_call(_call(status=401, response_schema=None))
    inventory.add_call(_call(status=200, response_schema={"qty": "number"}))
    merged = next(iter(inventory.calls.values()))
    assert merged.status == 200
    assert merged.response_schema == {"qty": "number"}


def test_merge_unions_auth_headers_and_query_keys():
    inventory = ApiInventory()
    inventory.add_call(_call(auth_headers=["Authorization"], query_keys=["page"]))
    inventory.add_call(_call(auth_headers=["Cookie"], query_keys=["size"]))
    merged = next(iter(inventory.calls.values()))
    assert merged.auth_headers == ["Authorization", "Cookie"]
    assert merged.query_keys == ["page", "size"]


def test_inventory_reports_hosts_and_auth_schemes():
    inventory = ApiInventory()
    inventory.add_call(_call(auth_headers=["Authorization"]))
    inventory.add_call(_call(url="https://push.example.com/hub/negotiate", method="POST"))
    assert inventory.hosts() == {"api.example.com": 1, "push.example.com": 1}
    assert inventory.auth_schemes() == ["Authorization"]


def test_markdown_and_json_are_renderable():
    inventory = ApiInventory()
    inventory.add_call(
        _call(
            status=200,
            content_type="application/json",
            auth_headers=["Authorization"],
            query_keys=["page"],
            request_schema={"filter": "string"},
            response_schema={"positions": [{"symbol": "string"}]},
        )
    )
    markdown = inventory.to_markdown()
    assert "Endpoint" in markdown and "positions" in markdown
    parsed = json.loads(inventory.to_json())
    assert parsed["calls"][0]["response_schema"] == {"positions": [{"symbol": "string"}]}


# ----------------------------------------------------------------------
# مسیر HAR به‌صورت end-to-end
# ----------------------------------------------------------------------
def _fake_har() -> dict:
    return {
        "log": {
            "entries": [
                {
                    "_resourceType": "xhr",
                    "request": {
                        "method": "GET",
                        "url": f"https://api.emofid.test/v1/accounts/{FAKE_ACCOUNT}/balance?token={FAKE_TOKEN}",
                        "headers": [
                            {"name": "Authorization", "value": f"Bearer {FAKE_TOKEN}"},
                            {"name": "Content-Type", "value": "application/json"},
                        ],
                    },
                    "response": {
                        "status": 200,
                        "content": {
                            "mimeType": "application/json",
                            "text": json.dumps(
                                {"balance": FAKE_BALANCE, "nationalId": FAKE_NATIONAL_ID}
                            ),
                        },
                    },
                },
                {
                    "_resourceType": "script",
                    "request": {
                        "method": "GET",
                        "url": "https://cdn.emofid.test/main.js",
                        "headers": [],
                    },
                    "response": {"status": 200, "content": {"mimeType": "application/javascript"}},
                },
                {
                    "_resourceType": "websocket",
                    "request": {
                        "method": "GET",
                        "url": "wss://push.emofid.test/hub",
                        "headers": [],
                    },
                    "response": {"status": 101, "content": {}},
                    "_webSocketMessages": [
                        {"type": "send", "data": '{"protocol":"json","version":1}\x1e'},
                        {
                            "type": "receive",
                            "data": '{"type":1,"target":"OnQuote","arguments":[{"bid":34}]}\x1e',
                        },
                    ],
                },
            ]
        }
    }


def test_har_pipeline_extracts_shape_and_hides_secrets():
    sys_path_marker = __import__("sys").path  # noqa: F841 - conftest مسیر را تنظیم کرده
    from scripts.har_to_inventory import build_inventory

    inventory = build_inventory(_fake_har())
    rendered = inventory.to_markdown() + inventory.to_json()

    # ساختار کشف شده
    assert "balance" in rendered and "nationalId" in rendered
    assert "OnQuote" in rendered
    assert "Authorization" in inventory.auth_schemes()

    # هیچ مقدار حساسی نمانده
    for secret in (FAKE_TOKEN, FAKE_NATIONAL_ID, str(FAKE_BALANCE), FAKE_ACCOUNT):
        assert secret not in rendered, f"مقدار حساس لو رفت: {secret}"

    # فایل‌های استاتیک فیلتر شده‌اند
    assert "main.js" not in rendered


def test_har_keep_static_flag():
    from scripts.har_to_inventory import build_inventory

    assert "main.js" in build_inventory(_fake_har(), keep_static=True).to_json()

# ----------------------------------------------------------------------
# بستن پنجره‌ی مرورگر نباید کار کاربر را دور بریزد
# ----------------------------------------------------------------------
def test_capture_loop_survives_a_closed_browser():
    """بستن پنجره راه طبیعی پایان دادن است و نباید همه چیز را از بین ببرد.

    باگ واقعی: `page.wait_for_timeout()` هنگام بسته شدن مرورگر
    `TargetClosedError` می‌داد و **قبل از** بررسی `context.pages` بالا
    می‌آمد. چون فقط `KeyboardInterrupt` گرفته می‌شد، اسکریپت کرش می‌کرد و
    گزارش endpointها هم نوشته نمی‌شد — یعنی کل سشن لاگین کاربر هدر می‌رفت.

    این تست ساختار محافظ را چک می‌کند، نه رفتار زنده را (که به مرورگر
    واقعی نیاز دارد و در تست‌های سریع جا نمی‌شود).
    """
    from pathlib import Path

    source = Path("scripts/emofid_login.py").read_text(encoding="utf-8")

    loop_start = source.index("deadline = time.monotonic()")
    loop_end = source.index("context.storage_state", loop_start)
    loop_body = source[loop_start:loop_end]

    assert "except KeyboardInterrupt" in loop_body
    assert "except Exception" in loop_body, (
        "حلقه‌ی ضبط باید بسته شدن مرورگر را هم بگیرد، نه فقط Ctrl+C"
    )


def test_closing_hint_warns_session_is_lost():
    """کاربر باید بداند بستن پنجره فایل سشن را از بین می‌برد.

    گزارش نجات پیدا می‌کند ولی سشن نه — Playwright برای خواندنش به
    مرورگر زنده نیاز دارد. اگر این را نگوییم، کاربر پنجره را می‌بندد و
    بعد نمی‌فهمد چرا `broker.enabled` کار نمی‌کند.
    """
    from pathlib import Path

    source = Path("scripts/emofid_login.py").read_text(encoding="utf-8")
    assert "Ctrl+C" in source
    assert "فایل سشن از دست می‌رود" in source or "سشن از دست" in source

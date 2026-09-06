"""تست‌های لایه API داشبورد.

تمرکز روی چیزهایی است که می‌توانند بی‌صدا خراب شوند:

* نوشتن روی `settings.yaml` نباید بقیه‌ی کلیدها را پاک کند یا فارسی را
  خراب کند (یک بار با `Set-Content` همین اتفاق افتاد و PyYAML فایل را
  نخواند).
* پارامتر ناشناخته باید رد شود، نه اینکه در yaml بنشیند و بی‌اثر بماند.
* داشبورد نباید هیچ راهی به لایه‌ی `execution` داشته باشد.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """کلاینت تست با `settings.yaml` موقت، تا تنظیمات واقعی دست‌نخورده بماند."""
    from web import api as web_api

    example = Path(web_api.EXAMPLE_PATH)
    settings = tmp_path / "settings.yaml"
    settings.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(web_api, "SETTINGS_PATH", settings)
    with TestClient(web_api.app) as test_client:
        test_client.settings_path = settings  # type: ignore[attr-defined]
        yield test_client


def _load(path: Path) -> dict:
    return yaml.safe_load(open(path, encoding="utf-8")) or {}


# ----------------------------------------------------------------------
# خواندن
# ----------------------------------------------------------------------
def test_status_reports_data_source(client):
    """منبع داده باید در وضعیت بیاید؛ کاربر با همین تشخیص می‌دهد mock است یا نه."""
    body = client.get("/api/status").json()
    assert "market_data_provider" in body
    assert "option_chain_provider" in body
    assert "signal_count" in body


def test_status_exposes_trading_calendar_fields(client):
    """کلیدهای تقویم باید همیشه باشند، حتی وقتی شبکه نیست و مقدارشان None است."""
    body = client.get("/api/status").json()
    for key in ("today_jalali", "next_trading_day", "known_holidays"):
        assert key in body


def test_strategies_expose_defaults_and_current(client):
    body = client.get("/api/strategies").json()
    names = [s["name"] for s in body["strategies"]]
    assert "directional_ma_cross" in names
    for strategy in body["strategies"]:
        assert strategy["defaults"], "پارامترهای پیش‌فرض باید برگردند"
        # هر پیش‌فرض باید در مقدار فعلی هم حاضر باشد
        assert set(strategy["defaults"]) <= set(strategy["params"])


def test_signals_endpoint_returns_computed_fields(client):
    body = client.get("/api/signals?limit=5").json()
    assert "total" in body
    for signal in body["signals"]:
        # این دو property هستند و در asdict نمی‌آیند؛ باید دستی اضافه شوند
        assert "days_to_expiry" in signal
        assert "notional" in signal


# ----------------------------------------------------------------------
# نوشتن
# ----------------------------------------------------------------------
def test_risk_update_preserves_the_rest_of_the_file(client):
    """نوشتن یک کلید نباید بقیه‌ی تنظیمات را قربانی کند."""
    path = client.settings_path
    before = _load(path)

    response = client.put("/api/risk", json={"max_contracts": 7})
    assert response.status_code == 200

    after = _load(path)
    assert after["risk"]["max_contracts"] == 7
    assert after["risk"]["account_equity"] == before["risk"]["account_equity"]
    for key in ("general", "market_data", "option_chain", "strategies", "signals"):
        assert key in after, f"بخش «{key}» پس از نوشتن گم شد"


def test_symbols_update_keeps_persian_readable(client):
    """نماد فارسی باید سالم برگردد و فایل با PyYAML خوانا بماند."""
    path = client.settings_path
    response = client.put("/api/symbols", json={"symbols": ["خودرو", "شستا"]})
    assert response.status_code == 200

    after = _load(path)
    assert after["market_data"]["symbols"] == ["خودرو", "شستا"]


def test_strategy_toggle_round_trips(client):
    response = client.put("/api/strategies/directional_ma_cross", json={"enabled": False})
    assert response.status_code == 200

    body = client.get("/api/strategies").json()
    target = next(s for s in body["strategies"] if s["name"] == "directional_ma_cross")
    assert target["enabled"] is False


def test_strategy_param_is_written_where_the_strategy_reads_it(client):
    """پارامتر باید زیر کلید `params` بنشیند، نه مسطح.

    `create_strategies` فقط `entry["params"]` را به استراتژی پاس می‌دهد.
    نوشتن مسطح بی‌صدا بی‌اثر است: داشبورد «ذخیره شد» می‌گوید ولی رفتار
    استراتژی تغییر نمی‌کند — بدترین نوع خرابی.
    """
    from strategies.registry import create_strategies

    response = client.put(
        "/api/strategies/directional_ma_cross", json={"params": {"min_momentum_pct": 4.25}}
    )
    assert response.status_code == 200

    stored = _load(client.settings_path)["strategies"]["directional_ma_cross"]
    assert stored["params"]["min_momentum_pct"] == 4.25

    # و مهم‌تر: واقعاً به استراتژی ساخته‌شده می‌رسد
    built = create_strategies({"directional_ma_cross": stored})
    assert built, "استراتژی ساخته نشد"
    assert built[0].params["min_momentum_pct"] == 4.25


def test_strategy_params_reflect_stored_values_in_get(client):
    """GET باید مقدار ذخیره‌شده را نشان بدهد، نه پیش‌فرض را."""
    client.put(
        "/api/strategies/directional_ma_cross", json={"params": {"min_momentum_pct": 9.5}}
    )
    body = client.get("/api/strategies").json()
    target = next(s for s in body["strategies"] if s["name"] == "directional_ma_cross")
    assert target["params"]["min_momentum_pct"] == 9.5
    # کلید ساختاری `params` نباید خودش به‌عنوان یک پارامتر ظاهر شود
    assert "params" not in target["params"]


# ----------------------------------------------------------------------
# اعتبارسنجی
# ----------------------------------------------------------------------
def test_unknown_strategy_is_rejected(client):
    assert client.put("/api/strategies/does_not_exist", json={"enabled": True}).status_code == 404


def test_unknown_param_is_rejected_not_silently_stored(client):
    """پارامتر اشتباه باید خطا بدهد.

    اگر بی‌صدا در yaml بنشیند، کاربر فکر می‌کند تنظیمش اعمال شده در حالی
    که استراتژی هیچ‌وقت آن را نمی‌خواند.
    """
    response = client.put(
        "/api/strategies/directional_ma_cross", json={"params": {"totally_bogus": 1}}
    )
    assert response.status_code == 400
    stored = _load(client.settings_path)["strategies"].get("directional_ma_cross") or {}
    assert "totally_bogus" not in stored


def test_empty_symbol_list_is_rejected(client):
    assert client.put("/api/symbols", json={"symbols": []}).status_code == 400
    assert client.put("/api/symbols", json={"symbols": ["  "]}).status_code == 400


def test_empty_risk_patch_is_rejected(client):
    assert client.put("/api/risk", json={}).status_code == 400


# ----------------------------------------------------------------------
# حساب کارگزاری
# ----------------------------------------------------------------------
def test_account_is_disabled_by_default(client):
    """اتصال به حساب باید صریحاً روشن شود، نه اینکه پیش‌فرض باشد."""
    body = client.get("/api/account").json()
    assert body["enabled"] is False
    assert body["positions"] == []
    assert body["reason"], "باید دلیل خاموش بودن را بگوید"


def test_account_reports_broker_failure_without_crashing(client, monkeypatch):
    """سشن منقضی نباید داشبورد را بخواباند؛ پیام روشن باید بدهد."""
    import yaml

    path = client.settings_path
    data = yaml.safe_load(open(path, encoding="utf-8"))
    data["broker"] = {"enabled": True, "session_file": "var/does-not-exist.json"}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    body = client.get("/api/account").json()
    assert body["enabled"] is True
    assert body["reason"]  # پیام خطا هست
    assert body["positions"] == []


# ----------------------------------------------------------------------
# ایمنی
# ----------------------------------------------------------------------
def test_web_layer_cannot_reach_execution():
    """داشبورد نباید هیچ مسیری به ثبت سفارش داشته باشد.

    گارد AST سراسری در `test_signal_generator.py` کل مخزن را می‌پاید و
    `web/` را هم پوشش می‌دهد؛ این تست همان قاعده را صریح و موضعی می‌کند.
    """
    source = Path(importlib.import_module("web.api").__file__).read_text(encoding="utf-8")
    assert "import execution" not in source
    assert "from execution" not in source

# ----------------------------------------------------------------------
# انتخاب منبع داده
# ----------------------------------------------------------------------
def test_datasource_lists_only_real_providers(client):
    body = client.get("/api/datasource").json()
    assert "tsetmc" in body["available_market_data"]
    assert "tsetmc" in body["available_option_chain"]
    # داده‌ی ساختگی حذف شده؛ نباید در گزینه‌ها باشد
    assert "mock" not in body["available_market_data"]
    assert "mock" not in body["available_option_chain"]


def test_datasource_change_round_trips(client):
    response = client.put("/api/datasource", json={"market_data_provider": "pytse"})
    assert response.status_code == 200
    assert _load(client.settings_path)["market_data"]["provider"] == "pytse"


def test_unknown_provider_is_rejected(client):
    response = client.put("/api/datasource", json={"option_chain_provider": "nope"})
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


def test_enrichment_requires_broker_to_be_enabled(client):
    """غنی‌سازی بدون کارگزاری بی‌معناست و باید صریح رد شود.

    اگر بی‌صدا پذیرفته شود، کاربر فکر می‌کند وجه تضمین از کارگزاری
    می‌آید در حالی که هیچ‌وقت نمی‌آید.
    """
    response = client.put("/api/datasource", json={"enrich_with_broker": True})
    assert response.status_code == 400
    assert "حساب" in response.json()["detail"]


def test_enrichment_allowed_once_broker_is_on(client):
    import yaml

    path = client.settings_path
    data = yaml.safe_load(open(path, encoding="utf-8"))
    data["broker"] = {"enabled": True, "token": "x"}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.put("/api/datasource", json={"enrich_with_broker": True})
    assert response.status_code == 200
    assert _load(path)["option_chain"]["enrich_with_broker"] is True


def test_enrich_limit_is_bounded(client):
    """سقف بالا لازم است: هر واحد یک درخواست شبکه در هر پاس است."""
    assert client.put("/api/datasource", json={"enrich_limit": 5000}).status_code == 400
    assert client.put("/api/datasource", json={"enrich_limit": -1}).status_code == 400
    assert client.put("/api/datasource", json={"enrich_limit": 10}).status_code == 200


def test_broker_token_is_never_returned(client):
    """توکن نباید از هیچ endpointی برگردد."""
    client.put("/api/broker", json={"enabled": True, "token": "SECRET_TOKEN_123"})

    for path in ("/api/datasource", "/api/status", "/api/account"):
        assert "SECRET_TOKEN_123" not in client.get(path).text, f"توکن در {path} لو رفت"

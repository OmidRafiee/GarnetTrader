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

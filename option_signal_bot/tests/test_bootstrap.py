"""تست‌های لایه تنظیمات، رجیستری استراتژی، wiring و ذخیره‌سازی."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import bootstrap
from config.loader import build_dataclass, deep_merge, default_settings, load_settings, section
from notifiers.console_notifier import ConsoleNotifier
from risk.risk_calculator import RiskLimits
from signals.signal_model import OptionType, Side, Signal
from storage.signal_log import SignalLog
from strategies.base_strategy import BaseStrategy
from strategies.registry import available_strategies, create_strategies, create_strategy


@pytest.fixture
def settings() -> dict:
    return default_settings()


# ----------------------------------------------------------------------
# تنظیمات
# ----------------------------------------------------------------------
def test_deep_merge_keeps_untouched_siblings():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    merged = deep_merge(base, {"a": {"y": 99}})
    assert merged == {"a": {"x": 1, "y": 99}, "b": 3}
    assert base["a"]["y"] == 2  # ورودی تغییر نمی‌کند


def test_deep_merge_replaces_lists():
    merged = deep_merge({"symbols": ["الف", "ب"]}, {"symbols": ["ج"]})
    assert merged["symbols"] == ["ج"]


def test_load_settings_falls_back_to_defaults_when_missing():
    loaded = load_settings(Path("این-مسیر-وجود-ندارد.yaml"))
    assert loaded == default_settings()


def test_section_always_returns_dict():
    assert section({"risk": None}, "risk") == {}
    assert section({}, "ناموجود") == {}
    assert section({"risk": {"max_contracts": 3}}, "risk")["max_contracts"] == 3


def test_build_dataclass_ignores_unknown_keys():
    limits = build_dataclass(
        RiskLimits, {"max_contracts": 7, "کلید_اشتباه": 1}, "risk"
    )
    assert limits.max_contracts == 7
    assert limits.risk_per_trade_pct == RiskLimits().risk_per_trade_pct


def test_default_settings_keeps_execution_disabled():
    assert default_settings()["execution"]["enabled"] is False


# ----------------------------------------------------------------------
# رجیستری استراتژی‌ها
# ----------------------------------------------------------------------
def test_builtin_strategies_are_registered():
    names = available_strategies()
    assert "directional_ma_cross" in names
    assert "neutral_iv_spread" in names


def test_empty_config_activates_all_strategies():
    assert len(create_strategies({})) == len(available_strategies())


def test_disabled_strategy_is_skipped():
    strategies = create_strategies(
        {"directional_ma_cross": {"enabled": False}, "neutral_iv_spread": {"enabled": True}}
    )
    assert [s.name for s in strategies] == ["neutral_iv_spread"]


def test_unknown_strategy_is_ignored_not_fatal():
    strategies = create_strategies({"استراتژی_ناموجود": {"enabled": True}})
    assert strategies == []


def test_params_from_config_override_defaults():
    strategy = create_strategies(
        {"directional_ma_cross": {"params": {"fast_window": 3}}}
    )[0]
    assert strategy.params["fast_window"] == 3
    assert strategy.params["slow_window"] == 20  # پیش‌فرض حفظ می‌شود


def test_create_unknown_strategy_raises():
    with pytest.raises(ValueError):
        create_strategy("هیچ")


def test_registry_rejects_nameless_strategy():
    from strategies.registry import register_strategy

    class Nameless(BaseStrategy):
        def generate(self, context):
            return []

    with pytest.raises(ValueError):
        register_strategy(Nameless)


# ----------------------------------------------------------------------
# wiring
# ----------------------------------------------------------------------
def test_create_app_in_dry_run_uses_console_only(settings):
    app = bootstrap.create_app(settings, dry_run=True)
    assert [type(n) for n in app.notifiers] == [ConsoleNotifier]
    assert app.signal_log is None  # dry-run روی دیسک نمی‌نویسد
    assert app.generator.strategies
    app.close()


def test_create_app_wires_risk_limits_from_settings(settings):
    settings["risk"] = {"max_contracts": 3}
    app = bootstrap.create_app(settings, dry_run=True)
    assert app.generator.risk_calculator.limits.max_contracts == 3
    app.close()


def test_unknown_provider_fails_loudly(settings):
    settings["market_data"]["provider"] = "کارگزاری_ناموجود"
    with pytest.raises(ValueError):
        bootstrap.build_market_data(settings)


def test_option_chain_receives_config(settings):
    settings["option_chain"] = {"provider": "mock", "strikes_per_side": 2, "base_vol": 0.4}
    market_data = bootstrap.build_market_data(settings)
    chain_client = bootstrap.build_option_chain(settings, market_data)
    assert chain_client.strikes_per_side == 2
    assert chain_client.base_vol == 0.4


def test_telegram_is_skipped_without_token(settings):
    settings["notifiers"]["telegram"] = {"enabled": True, "bot_token": "<YOUR_TOKEN>"}
    # متغیرهای محیطی واقعی نباید نتیجه تست را عوض کنند
    saved = {k: os.environ.pop(k, None) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    try:
        notifiers = bootstrap.build_notifiers(settings, dry_run=False)
        assert all(n.name != "telegram" for n in notifiers)
    finally:
        os.environ.update({k: v for k, v in saved.items() if v is not None})


def test_notifiers_fall_back_to_console_when_all_disabled(settings):
    settings["notifiers"] = {"console": {"enabled": False}, "telegram": {"enabled": False}}
    notifiers = bootstrap.build_notifiers(settings, dry_run=False)
    assert [type(n) for n in notifiers] == [ConsoleNotifier]


def test_generator_never_holds_an_executor(settings):
    """قرارداد مایل‌استون ۱ در سطح wiring: چیزی به‌نام executor ساخته نمی‌شود."""
    app = bootstrap.create_app(settings, dry_run=True)
    assert not hasattr(app, "executor")
    assert not hasattr(app, "broker")
    app.close()


# ----------------------------------------------------------------------
# ذخیره‌سازی
# ----------------------------------------------------------------------
def _sample_signal() -> Signal:
    from datetime import date

    return Signal(
        symbol="ضخود-1",
        option_type=OptionType.CALL,
        side=Side.BUY,
        strike=2_500.0,
        expiry=date(2030, 1, 1),
        suggested_price=120.0,
        suggested_qty=5,
        reason="تست ذخیره‌سازی",
        strategy_name="manual",
    )


def test_signal_log_roundtrip_and_status_update():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "signals.db"
        with SignalLog(db_path=db, jsonl_path=Path(tmp) / "signals.jsonl") as log:
            signal = _sample_signal()
            log.save(signal)
            log.save(signal)  # ذخیره دوباره نباید رکورد تکراری بسازد

            assert log.count() == 1
            restored = log.all_signals()[0]
            assert restored.symbol == signal.symbol
            assert restored.suggested_qty == 5
            assert restored.status.value == "new"

            log.update_status(signal.signal_id, "expired")
            # هم ستون و هم payload باید به‌روز شده باشند
            assert log.all_signals()[0].status.value == "expired"

        assert db.exists()
        assert (Path(tmp) / "signals.jsonl").read_text(encoding="utf-8").count("\n") == 2


def test_signal_log_creates_missing_directories():
    with tempfile.TemporaryDirectory() as tmp:
        nested = Path(tmp) / "الف" / "ب" / "signals.db"
        with SignalLog(db_path=nested, jsonl_path=None) as log:
            log.save(_sample_signal())
            assert log.count() == 1
        assert nested.exists()

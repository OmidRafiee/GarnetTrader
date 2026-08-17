"""تست‌های ارکستریتور سیگنال، مدل سیگنال و جداسازی معماری از لایه اجرا."""

from __future__ import annotations

import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest

from data.market_data_client import MockMarketDataClient
from data.option_chain_client import MockOptionChainClient
from risk.risk_calculator import RiskCalculator, RiskLimits
from signals.signal_generator import GeneratorConfig, SignalGenerator
from signals.signal_model import OptionType, Side, Signal
from strategies.base_strategy import BaseStrategy
from strategies.directional_strategy import DirectionalStrategy
from strategies.neutral_strategy import NeutralStrategy

SYMBOL = "خودرو"


class AlwaysBuyAtmCall(BaseStrategy):
    """استراتژی قطعی برای تست: همیشه یک Call نزدیک ATM پیشنهاد می‌دهد."""

    name = "test_always_atm_call"

    def generate(self, context):
        contract = self.select_contract(context, "call", moneyness=0.0)
        if contract is None:
            return []
        return [self.build_signal(context, contract, Side.BUY, "تست", confidence=0.9)]


@pytest.fixture
def market_data() -> MockMarketDataClient:
    return MockMarketDataClient(seed=42)


@pytest.fixture
def option_chain(market_data) -> MockOptionChainClient:
    return MockOptionChainClient(market_data)


def make_generator(market_data, option_chain, strategies, **config_kwargs) -> SignalGenerator:
    return SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=strategies,
        risk_calculator=RiskCalculator(RiskLimits()),
        config=GeneratorConfig(symbols=[SYMBOL], **config_kwargs),
    )


# ----------------------------------------------------------------------
# جداسازی معماری
# ----------------------------------------------------------------------
def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


#: لایه سیگنال باید خالص بماند: نه اجرا، نه اطلاع‌رسانی، نه ذخیره‌سازی
FORBIDDEN_FOR_SIGNAL_LAYER = ("execution", "notifiers", "storage")

SIGNAL_LAYER_MODULES = [
    "signals/signal_generator.py",
    "signals/signal_model.py",
    "strategies/base_strategy.py",
    "strategies/directional_strategy.py",
    "strategies/neutral_strategy.py",
    "strategies/registry.py",
    "risk/risk_calculator.py",
]

IGNORED_DIRS = {"__pycache__", "venv", ".venv", "var", "build", "dist"}


def _project_modules(root: Path):
    """همه فایل‌های پایتون پروژه، بدون پوشه‌های محیط و خروجی."""
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if any(p in IGNORED_DIRS or p.startswith(".") for p in parts):
            continue
        yield path


@pytest.mark.parametrize("module_path", SIGNAL_LAYER_MODULES)
def test_signal_layer_stays_pure(module_path: str):
    """قرارداد مایل‌استون ۱: لایه سیگنال به اجرا/اطلاع‌رسانی/ذخیره‌سازی وابسته نیست."""
    root = Path(__file__).resolve().parent.parent
    modules = _imported_modules(root / module_path)
    offenders = [
        m for m in modules if m.split(".")[0] in FORBIDDEN_FOR_SIGNAL_LAYER
    ]
    assert offenders == [], f"{module_path} نباید {offenders} را import کند."


def test_only_execution_layer_imports_execution():
    """گارد سراسری: هیچ ماژول جدیدی هم نباید بی‌صدا به لایه اجرا وصل شود.

    این تست عمداً روی کل مخزن اجرا می‌شود تا با اضافه‌شدن فایل‌های آینده،
    خودکار آن‌ها را هم پوشش بدهد.
    """
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in _project_modules(root):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(("execution/", "tests/")):
            continue
        if any(m.split(".")[0] == "execution" for m in _imported_modules(path)):
            offenders.append(relative)
    assert offenders == [], f"این ماژول‌ها نباید execution را import کنند: {offenders}"


def test_generator_has_no_executor_attribute(market_data, option_chain):
    generator = make_generator(market_data, option_chain, [AlwaysBuyAtmCall()])
    names = [n for n in vars(generator) if "exec" in n.lower() or "broker" in n.lower()]
    assert names == []


# ----------------------------------------------------------------------
# تولید سیگنال
# ----------------------------------------------------------------------
def test_generator_produces_signal_with_risk_numbers(market_data, option_chain):
    generator = make_generator(market_data, option_chain, [AlwaysBuyAtmCall()])
    signals = generator.run_once()

    assert len(signals) == 1
    signal = signals[0]
    assert signal.option_type is OptionType.CALL
    assert signal.side is Side.BUY
    assert signal.underlying == SYMBOL
    assert signal.suggested_price > 0
    assert signal.suggested_qty > 0  # توسط RiskCalculator پر شده
    assert signal.stop_loss is not None and signal.stop_loss < signal.suggested_price
    assert signal.take_profit is not None and signal.take_profit > signal.suggested_price
    assert signal.strategy_name == AlwaysBuyAtmCall.name


def test_generator_deduplicates_within_window(market_data, option_chain):
    generator = make_generator(
        market_data, option_chain, [AlwaysBuyAtmCall()], dedupe_window_minutes=60
    )
    assert len(generator.run_once()) == 1
    assert generator.run_once() == []  # همان سیگنال، داخل پنجره ضدتکرار

    generator.reset_dedupe_cache()
    assert len(generator.run_once()) == 1


def test_generator_applies_min_confidence(market_data, option_chain):
    generator = make_generator(
        market_data, option_chain, [AlwaysBuyAtmCall()], min_confidence=0.99
    )
    assert generator.run_once() == []


def test_generator_survives_broken_strategy(market_data, option_chain):
    class BrokenStrategy(BaseStrategy):
        name = "broken"

        def generate(self, context):
            raise RuntimeError("خرابی عمدی")

    generator = make_generator(
        market_data, option_chain, [BrokenStrategy(), AlwaysBuyAtmCall()]
    )
    assert len(generator.run_once()) == 1  # استراتژی سالم همچنان کار می‌کند


def test_generator_rejects_signal_over_risk_limits(market_data, option_chain):
    tiny_account = RiskCalculator(RiskLimits(account_equity=1_000.0))
    generator = SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=[AlwaysBuyAtmCall()],
        risk_calculator=tiny_account,
        config=GeneratorConfig(symbols=[SYMBOL]),
    )
    assert generator.run_once() == []


def test_real_strategies_run_without_error(market_data, option_chain):
    """استراتژی‌های واقعی ممکن است سیگنال ندهند، اما نباید خطا بدهند."""
    generator = make_generator(
        market_data, option_chain, [DirectionalStrategy(), NeutralStrategy()]
    )
    signals = generator.run_once()
    assert isinstance(signals, list)
    for signal in signals:
        assert signal.suggested_qty > 0
        assert signal.reason


def test_strategy_context_has_history_and_chain(market_data, option_chain):
    generator = make_generator(market_data, option_chain, [])
    context = generator.build_context(SYMBOL)
    assert len(context.history) == generator.config.history_days
    assert context.spot > 0
    assert context.chain.contracts
    assert context.realized_vol() > 0


# ----------------------------------------------------------------------
# مدل سیگنال
# ----------------------------------------------------------------------
def test_signal_json_roundtrip(market_data, option_chain):
    generator = make_generator(market_data, option_chain, [AlwaysBuyAtmCall()])
    original = generator.run_once()[0]

    payload = json.loads(original.to_json())
    assert isinstance(payload["expiry"], str)
    assert payload["option_type"] == "call"

    restored = Signal.from_dict(payload)
    assert restored.symbol == original.symbol
    assert restored.strike == original.strike
    assert restored.expiry == original.expiry
    assert restored.created_at == original.created_at
    assert restored.option_type is OptionType.CALL


def test_signal_accepts_raw_strings():
    signal = Signal(
        symbol="ضخود-1",
        option_type="call",
        side="buy",
        strike=2_500.0,
        expiry=__import__("datetime").date.today(),
        suggested_price=100.0,
        suggested_qty=1,
        reason="تست",
        strategy_name="manual",
    )
    assert signal.option_type is OptionType.CALL
    assert signal.side is Side.BUY
    assert signal.valid_until > signal.created_at


def test_signal_expiry_and_summary(market_data, option_chain):
    generator = make_generator(market_data, option_chain, [AlwaysBuyAtmCall()])
    signal = generator.run_once()[0]

    assert not signal.is_expired()
    assert signal.is_expired(signal.created_at + timedelta(days=1))
    assert signal.symbol in signal.summary()
    assert signal.notional > 0

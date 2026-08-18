"""لایه wiring: از تنظیمات، اجزای برنامه را می‌سازد (Dependency Injection).

چرا جدا از `main.py`؟ تا هر entrypoint دیگری (داشبورد وب، اسکریپت بک‌تست،
نوتبوک تحلیل) بتواند همین اجزا را با یک خط بسازد و منطق wiring تکرار نشود.

برای افزودن یک provider یا notifier جدید، فقط یک ورودی به دیکشنری‌های
`MARKET_DATA_PROVIDERS`, `OPTION_CHAIN_PROVIDERS` یا `NOTIFIER_BUILDERS` اضافه کنید.

⚠️ این ماژول هم مثل بقیه، لایه `execution` را نمی‌سازد و صدا نمی‌زند.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from backtest.signal_backtester import SignalBacktester
from config.loader import build_dataclass, resolve_path, section
from data.market_data_client import (
    MarketDataClient,
    MockMarketDataClient,
    PytseMarketDataClient,
)
from data.option_chain_client import MockOptionChainClient, OptionChainClient
from data.tsetmc_market_data_client import TsetmcMarketDataClient
from data.tsetmc_option_chain_client import (
    DataQualityRules,
    FilePayloadSource,
    HttpPayloadSource,
    PayloadSource,
    TsetmcOptionChainClient,
)
from notifiers.base_notifier import BaseNotifier
from notifiers.console_notifier import ConsoleNotifier
from notifiers.telegram_notifier import TelegramNotifier
from risk.risk_calculator import RiskCalculator, RiskLimits
from signals.signal_generator import GeneratorConfig, SignalGenerator
from storage.signal_log import SignalLog
from strategies.registry import create_strategies

logger = logging.getLogger(__name__)

def _payload_source(config: dict[str, Any]) -> PayloadSource:
    """منبع پاسخ TSETMC از تنظیمات؛ `fixture_path` یعنی پخش پاسخ ضبط‌شده."""
    fixture_path = config.get("fixture_path")
    if fixture_path:
        return FilePayloadSource(resolve_path(fixture_path))
    return HttpPayloadSource(
        market=config.get("market", 0),
        timeout=config.get("timeout", 15),
        retries=config.get("retries", 3),
    )


#: نام provider در تنظیمات → سازنده کلاینت داده پایه
MARKET_DATA_PROVIDERS: dict[str, Callable[[dict[str, Any]], MarketDataClient]] = {
    "mock": lambda _config: MockMarketDataClient(),
    "pytse": lambda config: PytseMarketDataClient(
        allow_fallback=config.get("allow_fallback", True)
    ),
    "tsetmc": lambda config: TsetmcMarketDataClient(
        source=_payload_source(config),
        timeout=config.get("timeout", 20),
        retries=config.get("retries", 3),
        history_ttl_seconds=config.get("history_ttl_seconds", 900.0),
    ),
}


def _build_tsetmc_chain(config, market_data, _risk_free_rate) -> OptionChainClient:
    """زنجیره زنده از API عمومی TSETMC (بدون احراز هویت).

    اگر کلاینت داده پایه هم TSETMC باشد، **همان منبع** به اشتراک گذاشته می‌شود تا
    قیمت پایه و مظنه آپشن از یک پاسخ (و یک لحظه) بیایند و درخواست تکراری نرود.
    """
    shared = getattr(market_data, "source", None)
    if isinstance(shared, HttpPayloadSource):
        return TsetmcOptionChainClient(
            shared,
            cache_ttl_seconds=config.get("cache_ttl_seconds", 20.0),
            quality=build_dataclass(
                DataQualityRules, config.get("quality") or {}, "option_chain.quality"
            ),
        )
    return TsetmcOptionChainClient(
        HttpPayloadSource(
            market=config.get("market", 0),
            timeout=config.get("timeout", 15),
            retries=config.get("retries", 3),
        ),
        cache_ttl_seconds=config.get("cache_ttl_seconds", 20.0),
        quality=build_dataclass(
            DataQualityRules, config.get("quality") or {}, "option_chain.quality"
        ),
    )


def _build_fixture_chain(config, market_data, _risk_free_rate) -> OptionChainClient:
    """پخش مجدد یک پاسخ ضبط‌شده TSETMC — تست آفلاین و بازتولید یک روز خاص."""
    path = config.get("fixture_path")
    if not path:
        raise ValueError(
            "برای provider «fixture» باید مقدار option_chain.fixture_path تنظیم شود."
        )
    return TsetmcOptionChainClient(
        FilePayloadSource(resolve_path(path)),
        cache_ttl_seconds=float("inf"),  # فایل ثابت است؛ یک بار خوانده می‌شود
        quality=build_dataclass(
            DataQualityRules, config.get("quality") or {}, "option_chain.quality"
        ),
    )


#: نام provider در تنظیمات → سازنده کلاینت زنجیره آپشن
OPTION_CHAIN_PROVIDERS: dict[str, Callable[..., OptionChainClient]] = {
    "mock": lambda config, market_data, risk_free_rate: MockOptionChainClient(
        market_data,
        risk_free_rate=risk_free_rate,
        **_mock_chain_kwargs(config),
    ),
    "tsetmc": _build_tsetmc_chain,
    "fixture": _build_fixture_chain,
}


@dataclass
class AppContext:
    """اجزای ساخته‌شده برنامه، آماده استفاده در حلقه اصلی."""

    settings: dict[str, Any]
    market_data: MarketDataClient
    option_chain: OptionChainClient
    generator: SignalGenerator
    notifiers: list[BaseNotifier] = field(default_factory=list)
    signal_log: SignalLog | None = None

    def close(self) -> None:
        """آزادسازی منابع (اتصال SQLite)."""
        if self.signal_log is not None:
            self.signal_log.close()


# ----------------------------------------------------------------------
# سازنده‌های تک‌تک اجزا
# ----------------------------------------------------------------------
def _mock_chain_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    """کلیدهای مرتبط تنظیمات را به آرگومان‌های `MockOptionChainClient` نگاشت می‌کند."""
    kwargs: dict[str, Any] = {}
    for key in ("strikes_per_side", "base_vol", "spread_pct"):
        if key in config:
            kwargs[key] = config[key]
    if "expiry_days" in config:
        kwargs["expiry_days"] = tuple(config["expiry_days"])
    return kwargs


def _pick(registry: dict[str, Any], name: str, kind: str):
    """انتخاب provider از رجیستری با خطای خوانا در صورت نام اشتباه."""
    builder = registry.get(str(name).lower())
    if builder is None:
        raise ValueError(
            f"{kind} ناشناخته: «{name}». گزینه‌های موجود: {', '.join(sorted(registry))}"
        )
    return builder


def build_market_data(settings: dict[str, Any]) -> MarketDataClient:
    config = section(settings, "market_data")
    provider = config.get("provider", "mock")
    return _pick(MARKET_DATA_PROVIDERS, provider, "provider داده بازار")(config)


def build_option_chain(
    settings: dict[str, Any], market_data: MarketDataClient
) -> OptionChainClient:
    config = section(settings, "option_chain")
    provider = config.get("provider", "mock")
    risk_free_rate = section(settings, "market_data").get("risk_free_rate", 0.25)
    builder = _pick(OPTION_CHAIN_PROVIDERS, provider, "provider زنجیره آپشن")
    return builder(config, market_data, risk_free_rate)


def build_risk_calculator(settings: dict[str, Any]) -> RiskCalculator:
    limits = build_dataclass(RiskLimits, section(settings, "risk"), "risk")
    return RiskCalculator(limits)


def build_generator_config(settings: dict[str, Any]) -> GeneratorConfig:
    market = section(settings, "market_data")
    signals = section(settings, "signals")
    return build_dataclass(
        GeneratorConfig,
        {
            "symbols": list(market.get("symbols", [])),
            "risk_free_rate": market.get("risk_free_rate", 0.25),
            "history_days": market.get("history_days", 90),
            "dedupe_window_minutes": signals.get("dedupe_window_minutes", 60),
            "min_confidence": signals.get("min_confidence"),
            "signal_validity_minutes": signals.get("validity_minutes", 30),
        },
        "signals",
    )


def build_generator(
    settings: dict[str, Any],
    market_data: MarketDataClient,
    option_chain: OptionChainClient,
) -> SignalGenerator:
    return SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=create_strategies(section(settings, "strategies")),
        risk_calculator=build_risk_calculator(settings),
        config=build_generator_config(settings),
    )


def _build_console(config: dict[str, Any], as_json: bool) -> BaseNotifier | None:
    return ConsoleNotifier(as_json=as_json or config.get("as_json", False))


def _build_telegram(config: dict[str, Any], _as_json: bool) -> BaseNotifier | None:
    # متغیر محیطی بر مقدار فایل اولویت دارد تا توکن هرگز در گیت نیفتد.
    token = os.getenv("TELEGRAM_BOT_TOKEN") or str(config.get("bot_token", ""))
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or str(config.get("chat_id", ""))
    if not token or token.startswith("<"):
        logger.warning(
            "توکن تلگرام تنظیم نشده (TELEGRAM_BOT_TOKEN)؛ این کانال ساخته نشد."
        )
        return None
    return TelegramNotifier(bot_token=token, chat_id=chat_id)


#: نام کانال در تنظیمات → سازنده آن
NOTIFIER_BUILDERS: dict[str, Callable[[dict[str, Any], bool], BaseNotifier | None]] = {
    "console": _build_console,
    "telegram": _build_telegram,
}


def build_notifiers(
    settings: dict[str, Any], dry_run: bool = False, as_json: bool = False
) -> list[BaseNotifier]:
    """ساخت کانال‌های فعال. در حالت dry-run فقط کنسول ساخته می‌شود."""
    config = section(settings, "notifiers")
    if dry_run:
        return [ConsoleNotifier(as_json=as_json)]

    notifiers: list[BaseNotifier] = []
    for name, channel_config in config.items():
        channel_config = channel_config or {}
        if not channel_config.get("enabled", False):
            continue
        builder = NOTIFIER_BUILDERS.get(name)
        if builder is None:
            logger.warning("کانال اطلاع‌رسانی ناشناخته نادیده گرفته شد: %s", name)
            continue
        notifier = builder(channel_config, as_json)
        if notifier is not None:
            notifiers.append(notifier)

    if not notifiers:
        logger.warning("هیچ کانال فعالی نیست؛ کنسول به‌عنوان پشتیبان اضافه شد.")
        notifiers.append(ConsoleNotifier(as_json=as_json))
    return notifiers


def build_signal_log(settings: dict[str, Any], dry_run: bool = False) -> SignalLog | None:
    """در dry-run هیچ چیزی روی دیسک نوشته نمی‌شود."""
    config = section(settings, "storage")
    if dry_run or not config.get("enabled", True):
        return None
    jsonl = config.get("jsonl_path")
    return SignalLog(
        db_path=resolve_path(config.get("sqlite_path", "var/signals.db")),
        jsonl_path=resolve_path(jsonl) if jsonl else None,
    )


def build_backtester(
    settings: dict[str, Any],
    market_data: MarketDataClient,
    option_chain: OptionChainClient,
) -> SignalBacktester:
    config = section(settings, "backtest")
    return SignalBacktester(
        market_data=market_data,
        option_chain=option_chain,
        strategies=create_strategies(section(settings, "strategies")),
        horizon_days=config.get("horizon_days", 10),
        warmup_days=config.get("warmup_days", 30),
        step_days=config.get("step_days", 1),
        risk_free_rate=section(settings, "market_data").get("risk_free_rate", 0.25),
    )


# ----------------------------------------------------------------------
def create_app(
    settings: dict[str, Any], dry_run: bool = False, as_json: bool = False
) -> AppContext:
    """ساخت کل برنامه از تنظیمات — تنها تابعی که entrypoint‌ها لازم دارند."""
    market_data = build_market_data(settings)
    option_chain = build_option_chain(settings, market_data)
    return AppContext(
        settings=settings,
        market_data=market_data,
        option_chain=option_chain,
        generator=build_generator(settings, market_data, option_chain),
        notifiers=build_notifiers(settings, dry_run=dry_run, as_json=as_json),
        signal_log=build_signal_log(settings, dry_run=dry_run),
    )

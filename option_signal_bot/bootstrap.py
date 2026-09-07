"""لایه wiring: از تنظیمات، اجزای برنامه را می‌سازد (Dependency Injection).

چرا جدا از `main.py`؟ تا هر entrypoint دیگری (داشبورد وب، اسکریپت بک‌تست،
نوتبوک تحلیل) بتواند همین اجزا را با یک خط بسازد و منطق wiring تکرار نشود.

برای افزودن یک provider یا notifier جدید، فقط یک ورودی به دیکشنری‌های
`MARKET_DATA_PROVIDERS`, `OPTION_CHAIN_PROVIDERS` یا `NOTIFIER_BUILDERS` اضافه کنید.

⚠️ این ماژول هم مثل بقیه، لایه `execution` را نمی‌سازد و صدا نمی‌زند.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from backtest.signal_backtester import SignalBacktester
from config.loader import build_dataclass, resolve_path, section
from data.market_data_client import (
    MarketDataClient,
    PytseMarketDataClient,
)
from data.option_chain_client import OptionChainClient
from data.tsetmc_market_data_client import TsetmcMarketDataClient
from data.tsetmc_option_chain_client import (
    DataQualityRules,
    FilePayloadSource,
    HttpPayloadSource,
    PayloadSource,
    TsetmcOptionChainClient,
)
from market.trading_calendar import TradingCalendar
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
    trading_calendar: TradingCalendar | None = None
    #: آداپتر کارگزاری (فقط‌خواندنی) — `None` اگر خاموش یا در دسترس نباشد
    account_source: Any | None = None

    def close(self) -> None:
        """آزادسازی منابع (اتصال SQLite)."""
        if self.signal_log is not None:
            self.signal_log.close()


# ----------------------------------------------------------------------
# سازنده‌های تک‌تک اجزا
# ----------------------------------------------------------------------

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
    provider = config.get("provider", "tsetmc")
    return _pick(MARKET_DATA_PROVIDERS, provider, "provider داده بازار")(config)


def build_account_source(settings: dict[str, Any]):
    """آداپتر کارگزاری، اگر در تنظیمات فعال باشد — وگرنه `None`.

    شکست اینجا **خطا نمی‌دهد**: نبودِ داده‌ی حساب نباید کل ربات را
    بخواباند، چون سیگنال‌دهی به آن وابسته نیست. فقط لاگ هشدار می‌دهد.
    """
    broker = section(settings, "broker")
    if not broker.get("enabled"):
        return None

    try:
        from brokers.emofid import EmofidAccountClient

        common = {
            "base_url": broker.get("base_url", "https://api-mts.orbis.easytrader.ir"),
            "timeout": broker.get("timeout", 15),
            "retries": broker.get("retries", 3),
        }
        token = (broker.get("token") or "").strip()
        if token:
            return EmofidAccountClient(token=token, **common)
        return EmofidAccountClient.from_session_file(
            resolve_path(broker.get("session_file", "var/emofid/session.json")),
            **common,
        )
    except Exception as exc:  # noqa: BLE001 - نبود حساب نباید ربات را بخواباند
        logger.warning(
            "اتصال به کارگزاری برقرار نشد؛ ربات بدون داده‌ی حساب ادامه می‌دهد: %s", exc
        )
        return None


def build_option_chain(
    settings: dict[str, Any],
    market_data: MarketDataClient,
    account_source: Any | None = None,
) -> OptionChainClient:
    """زنجیره‌ی آپشن.

    Args:
        account_source: آداپتر کارگزاریِ **از قبل ساخته‌شده**. اگر داده نشود
            و غنی‌سازی خواسته شده باشد، خودش یکی می‌سازد. پاس‌دادنش باعث
            می‌شود در یک اجرا فقط یک سشن کارگزاری باز شود، نه چند تا.
    """
    config = section(settings, "option_chain")
    provider = config.get("provider", "tsetmc")
    risk_free_rate = section(settings, "market_data").get("risk_free_rate", 0.25)
    builder = _pick(OPTION_CHAIN_PROVIDERS, provider, "provider زنجیره آپشن")
    chain = builder(config, market_data, risk_free_rate)

    # غنی‌سازی با داده‌ی کارگزاری، اگر خواسته شده باشد.
    # عمداً *روی* منبع انتخاب‌شده می‌نشیند، نه به‌جایش: ایزی‌تریدر
    # مشخصات قرارداد را فقط تک‌به‌تک می‌دهد (~۱۳۸۶ درخواست برای کل
    # بازار)، پس نمی‌تواند منبع زنجیره باشد — ولی برای چند قرارداد
    # نزدیک به قیمت پایه، معتبرترین منبع است.
    if config.get("enrich_with_broker"):
        from data.enriched_option_chain import BrokerEnrichedOptionChain

        account = account_source or build_account_source(settings)
        if account is None:
            logger.warning(
                "enrich_with_broker روشن است ولی کارگزاری در دسترس نیست؛ "
                "زنجیره بدون غنی‌سازی استفاده می‌شود."
            )
        else:
            chain = BrokerEnrichedOptionChain(
                chain, account, enrich_limit=config.get("enrich_limit", 20)
            )
    return chain


def build_risk_calculator(
    settings: dict[str, Any], account_source: Any | None = None
) -> RiskCalculator:
    """`RiskCalculator` با دارایی حساب.

    اگر `risk.use_broker_equity` روشن باشد و آداپتر کارگزاری موجودی بدهد،
    آن عدد جای `account_equity` دستیِ yaml را می‌گیرد. عدد دستی به‌سرعت
    کهنه می‌شود و اندازه‌گیری ریسک را روی دارایی‌ای انجام می‌دهد که وجود
    ندارد.

    سه قید عمدی:

    * موجودی **صفر یا منفی** اعمال **نمی‌شود.** حساب صفر یعنی هر سیگنال
      صفر قرارداد می‌گیرد، که فرقی با نبودِ سیگنال ندارد ولی شبیه یک
      اشکال نرم‌افزاری به نظر می‌آید. در آن حالت مقدار yaml می‌ماند.
    * خطای شبکه کشنده نیست: به مقدار yaml برمی‌گردد و لاگ هشدار می‌دهد.
    * پیش‌فرض **خاموش** است، تا رفتار فعلی کسی بی‌خبر عوض نشود.
    """
    config = section(settings, "risk")
    limits = build_dataclass(
        RiskLimits, config, "risk", ignore={"use_broker_equity"}
    )

    if account_source is None or not config.get("use_broker_equity", False):
        return RiskCalculator(limits)

    try:
        equity = account_source.get_balance().equity
    except Exception as exc:  # نبود موجودی نباید ربات را بخواباند
        logger.warning(
            "موجودی کارگزاری خوانده نشد؛ `account_equity` تنظیمات استفاده می‌شود: %s",
            exc,
        )
        return RiskCalculator(limits)

    if equity <= 0:
        logger.warning(
            "موجودی کارگزاری %s است؛ مقدار yaml (%s) نگه داشته شد.",
            f"{equity:,.0f}",
            f"{limits.account_equity:,.0f}",
        )
        return RiskCalculator(limits)

    logger.info(
        "دارایی حساب از کارگزاری خوانده شد: %s ریال (جای %s در yaml).",
        f"{equity:,.0f}",
        f"{limits.account_equity:,.0f}",
    )
    return RiskCalculator(dataclasses.replace(limits, account_equity=equity))


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
    account_source: Any | None = None,
) -> SignalGenerator:
    return SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=create_strategies(section(settings, "strategies")),
        risk_calculator=build_risk_calculator(settings, account_source),
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


def build_trading_calendar(
    settings: dict[str, Any], market_data: MarketDataClient | None = None
) -> TradingCalendar:
    """تقویم معاملاتی؛ تعطیلات را از تاریخچه‌ی **واقعی** بازار یاد می‌گیرد.

    نماد مرجع باید پرمعامله باشد تا «نبودِ کندل» واقعاً یعنی تعطیلی، نه
    توقف نماد. پیش‌فرض «خودرو» است که عملاً هیچ‌وقت بسته نیست.

    شکست یادگیری خطا نمی‌دهد: تقویمِ فقط-آخرهفته از نداشتنِ تقویم بهتر است.
    """
    config = section(settings, "trading_calendar")
    calendar = TradingCalendar(
        holidays=[
            date.fromisoformat(str(d)) for d in config.get("extra_holidays", []) or []
        ],
        cache_path=resolve_path(config.get("cache_path", "var/trading_calendar.json")),
    )

    if market_data is not None and config.get("learn_from_market", True):
        learned = calendar.learn_from_client(
            market_data,
            symbol=config.get("reference_symbol", "خودرو"),
            days=int(config.get("learn_days", 365)),
        )
        if learned:
            calendar.save()
    return calendar


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
        adjust_corporate_actions=config.get("adjust_corporate_actions", True),
    )


# ----------------------------------------------------------------------
def create_app(
    settings: dict[str, Any], dry_run: bool = False, as_json: bool = False
) -> AppContext:
    """ساخت کل برنامه از تنظیمات — تنها تابعی که entrypoint‌ها لازم دارند."""
    market_data = build_market_data(settings)
    # تقویم روی خودِ کلاینت می‌نشیند تا `is_market_open` همه‌جا — CLI و
    # داشبورد — بدون تغییر امضا تعطیلات رسمی را ببیند.
    calendar = build_trading_calendar(settings, market_data)
    market_data.trading_calendar = calendar

    # یک بار ساخته می‌شود و بین غنی‌سازی زنجیره و دارایی حساب مشترک است،
    # تا یک اجرا بیش از یک سشن کارگزاری باز نکند.
    account_source = build_account_source(settings)
    option_chain = build_option_chain(settings, market_data, account_source)
    return AppContext(
        settings=settings,
        market_data=market_data,
        option_chain=option_chain,
        trading_calendar=calendar,
        account_source=account_source,
        generator=build_generator(settings, market_data, option_chain, account_source),
        notifiers=build_notifiers(settings, dry_run=dry_run, as_json=as_json),
        signal_log=build_signal_log(settings, dry_run=dry_run),
    )

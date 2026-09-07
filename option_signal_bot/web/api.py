"""لایه API داشبورد.

مرزهای این ماژول عمداً تنگ است:

* **هیچ سفارشی ثبت نمی‌شود.** این ماژول `execution` را import نمی‌کند و تست
  گارد سراسری موجود در پروژه آن را تضمین می‌کند.
* نوشتن فقط روی `config/settings.yaml` است، نه چیز دیگر.
* پاس رصد بازار از همان `run_cycle` در `main.py` می‌آید، نه یک نسخه‌ی موازی؛
  تا داشبورد و ترمینال هرگز دو روایت مختلف از یک پاس نگویند.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bootstrap import create_app
from config import force_utf8_stdio
from config.loader import PROJECT_ROOT, deep_merge, load_settings, resolve_path, section
from market.trading_calendar import format_jalali
from signals.signal_model import Signal
from storage.signal_log import SignalLog
from strategies.registry import available_strategies, get_strategy_class

# اینجا `main()` نداریم که اول کار صدایش بزنیم؛ سرور با
# `uvicorn web.api:app` بالا می‌آید. یک پاس رصد، سیگنال‌ها را به
# ConsoleNotifier می‌دهد که فارسی چاپ می‌کند، و stdout پیش‌فرض ویندوز
# cp1252 است — بدون این، خودِ پاس رصد با UnicodeEncodeError می‌افتد.
force_utf8_stdio()

logger = logging.getLogger("option_signal_bot.web")

STATIC_DIR = Path(__file__).parent / "static"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
EXAMPLE_PATH = PROJECT_ROOT / "config" / "settings.example.yaml"

app = FastAPI(title="GarnetTrader — داشبورد سیگنال", docs_url="/api/docs")

#: قفل، تا دو درخواست همزمان یک پاس رصد را دوبار اجرا نکنند
_scan_lock = asyncio.Lock()


# ----------------------------------------------------------------------
# کمکی‌ها
# ----------------------------------------------------------------------
def _settings() -> dict[str, Any]:
    """تنظیمات فعلی. اگر `settings.yaml` نبود، از فایل نمونه ساخته می‌شود."""
    if not SETTINGS_PATH.exists() and EXAMPLE_PATH.exists():
        SETTINGS_PATH.write_text(
            EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
        )
        logger.info("settings.yaml از فایل نمونه ساخته شد.")
    return load_settings(SETTINGS_PATH)


def _signal_log(settings: dict[str, Any]) -> SignalLog:
    storage = section(settings, "storage")
    return SignalLog(
        db_path=resolve_path(storage.get("sqlite_path", "var/signals.db")),
        jsonl_path=resolve_path(storage.get("jsonl_path", "var/signals.jsonl")),
    )


def _signal_dict(signal: Signal) -> dict[str, Any]:
    """سیگنال را برای UI سریالایز می‌کند، با دو مقدار محاسبه‌شده."""
    data: dict[str, Any] = json.loads(signal.to_json())
    # هر دو property هستند، نه متد
    data["days_to_expiry"] = signal.days_to_expiry
    data["notional"] = signal.notional
    return data


def _patch_settings(patch: dict[str, Any]) -> None:
    """ادغام عمیق `patch` در `settings.yaml`.

    کامنت‌های فارسی فایل با بازنویسی از دست می‌روند، ولی ساختار و همه‌ی
    مقادیر دیگر حفظ می‌شوند. نوشتن با UTF-8 بدون BOM انجام می‌شود، چون
    PyYAML فایل double-encode شده را نمی‌خواند.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - در requirements هست
        raise HTTPException(
            status_code=500, detail="PyYAML نصب نیست؛ ویرایش تنظیمات ممکن نیست."
        ) from exc

    current: dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        with SETTINGS_PATH.open("r", encoding="utf-8") as handle:
            current = yaml.safe_load(handle) or {}

    merged = deep_merge(current, patch)
    text = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, indent=2)
    SETTINGS_PATH.write_text(text, encoding="utf-8", newline="\n")
    logger.info("settings.yaml به‌روزرسانی شد: %s", list(patch))


# ----------------------------------------------------------------------
# مدل‌های ورودی
# ----------------------------------------------------------------------
class SymbolsUpdate(BaseModel):
    symbols: list[str] = Field(..., description="نمادهای پایه برای رصد")


class StrategyUpdate(BaseModel):
    enabled: bool | None = None
    params: dict[str, Any] | None = None


class RiskUpdate(BaseModel):
    account_equity: float | None = None
    risk_per_trade_pct: float | None = None
    max_position_pct: float | None = None
    max_contracts: int | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    #: دارایی از کارگزاری خوانده شود؟ (نیاز به broker.enabled)
    use_broker_equity: bool | None = None


# ----------------------------------------------------------------------
# سیگنال‌ها
# ----------------------------------------------------------------------
@app.get("/api/signals")
def get_signals(
    limit: int = 100,
    strategy: str | None = None,
    underlying: str | None = None,
) -> dict[str, Any]:
    """سیگنال‌های ذخیره‌شده، جدیدترین اول، با فیلتر اختیاری."""
    settings = _settings()
    with _signal_log(settings) as log:
        signals = log.all_signals(limit=None)

    if strategy:
        signals = [s for s in signals if s.strategy_name == strategy]
    if underlying:
        signals = [s for s in signals if s.underlying == underlying]

    signals.sort(key=lambda s: s.created_at, reverse=True)
    return {
        "total": len(signals),
        "signals": [_signal_dict(s) for s in signals[:limit]],
    }


@app.post("/api/scan")
async def run_scan() -> dict[str, Any]:
    """یک پاس رصد بازار.

    سفارشی ثبت نمی‌شود؛ فقط سیگنال تولید، ذخیره و به notifierها فرستاده
    می‌شود — دقیقاً همان کاری که `main.py --once` می‌کند.
    """
    if _scan_lock.locked():
        raise HTTPException(status_code=409, detail="یک پاس رصد در حال اجراست.")

    async with _scan_lock:
        settings = _settings()

        def _work() -> list[Signal]:
            # از run_cycle خودِ main.py استفاده می‌کنیم تا منطق پاس رصد
            # در دو جا تکرار (و با هم واگرا) نشود.
            from main import run_cycle

            context = create_app(settings, dry_run=False, as_json=False)
            try:
                return run_cycle(context)
            finally:
                context.close()

        try:
            signals = await asyncio.to_thread(_work)
        except Exception as exc:  # noqa: BLE001 - پیام خطا به UI برگردانده می‌شود
            logger.exception("پاس رصد ناموفق بود.")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "generated": len(signals),
        "signals": [_signal_dict(s) for s in signals],
    }


# ----------------------------------------------------------------------
# استراتژی‌ها
# ----------------------------------------------------------------------
@app.get("/api/strategies")
def get_strategies() -> dict[str, Any]:
    """استراتژی‌های ثبت‌شده، با پارامترهای پیش‌فرض و مقدار فعلی."""
    settings = _settings()
    configured = section(settings, "strategies")

    result = []
    for name in available_strategies():
        cls = get_strategy_class(name)
        defaults = cls.default_params() if cls else {}
        entry = dict(configured.get(name) or {})
        # `create_strategies` فقط `entry["params"]` را می‌خواند؛ هر کلید
        # دیگری در این سطح را استراتژی نمی‌بیند. پس فقط همان را بخوان،
        # وگرنه داشبورد مقداری را نشان می‌دهد که هیچ اثری ندارد.
        stored_params = entry.get("params") or {}
        doc = ""
        if cls and cls.__doc__:
            doc = cls.__doc__.strip().split("\n")[0]
        result.append(
            {
                "name": name,
                "enabled": entry.get("enabled", True),
                "doc": doc,
                "defaults": defaults,
                "params": {**defaults, **stored_params},
            }
        )
    return {"strategies": result}


@app.put("/api/strategies/{name}")
def update_strategy(name: str, update: StrategyUpdate) -> dict[str, Any]:
    """فعال/غیرفعال کردن یا تغییر پارامترهای یک استراتژی."""
    cls = get_strategy_class(name)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"استراتژی «{name}» وجود ندارد.")

    patch: dict[str, Any] = {}
    if update.enabled is not None:
        patch["enabled"] = update.enabled

    if update.params:
        known = set(cls.default_params())
        unknown = set(update.params) - known
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"پارامتر ناشناخته: {', '.join(sorted(unknown))}",
            )
        # زیر کلید `params` نوشته می‌شود، چون `create_strategies` فقط
        # همان را به استراتژی پاس می‌دهد. نوشتن مسطح، بی‌صدا بی‌اثر است.
        patch["params"] = dict(update.params)

    if not patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")

    _patch_settings({"strategies": {name: patch}})
    return {"ok": True, "strategy": name, "applied": patch}


# ----------------------------------------------------------------------
# نمادها
# ----------------------------------------------------------------------
@app.get("/api/symbols")
def get_symbols() -> dict[str, Any]:
    """نمادهای تحت رصد، و نمادهایی که واقعاً در بازار آپشن دارند."""
    settings = _settings()
    watched = section(settings, "market_data").get("symbols") or []

    available: list[str] = []
    error: str | None = None
    try:
        # کلاینت را خودمان نمی‌سازیم؛ از همان wiring در bootstrap استفاده
        # می‌کنیم تا اگر امضای سازنده عوض شد اینجا نشکند و تنظیمات (کش،
        # fixture، گیت‌های کیفیت) هم همان چیزی باشد که CLI استفاده می‌کند.
        context = create_app(settings, dry_run=False, as_json=False)
        try:
            chain = context.option_chain
            getter = getattr(chain, "available_underlyings", None)
            if getter is None:
                error = f"منبع زنجیره فعلی ({type(chain).__name__}) لیست نمادها را نمی‌دهد."
            else:
                available = sorted(getter())
        finally:
            context.close()
    except Exception as exc:  # noqa: BLE001 - شبکه ممکن است قطع باشد
        error = str(exc)
        logger.warning("دریافت نمادهای بازار ناموفق بود: %s", exc)

    return {"watched": watched, "available": available, "error": error}


@app.put("/api/symbols")
def update_symbols(update: SymbolsUpdate) -> dict[str, Any]:
    """جایگزینی لیست نمادهای تحت رصد."""
    cleaned = [s.strip() for s in update.symbols if s.strip()]
    if not cleaned:
        raise HTTPException(status_code=400, detail="لیست نمادها خالی است.")
    _patch_settings({"market_data": {"symbols": cleaned}})
    return {"ok": True, "symbols": cleaned}


# ----------------------------------------------------------------------
# ریسک و وضعیت
# ----------------------------------------------------------------------
@app.get("/api/risk")
def get_risk() -> dict[str, Any]:
    return section(_settings(), "risk")


@app.put("/api/risk")
def update_risk(update: RiskUpdate) -> dict[str, Any]:
    patch = {k: v for k, v in update.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")
    _patch_settings({"risk": patch})
    return {"ok": True, "applied": patch}


def _attach_depth(structures: dict[str, list[dict[str, Any]]]) -> None:
    """عمق مظنه‌ی هر پایه را به نقشه‌ی سفارش اضافه می‌کند (درجا).

    این جواب سؤالی است که مظنه‌ی تک‌سطحی نمی‌تواند بدهد: «اگر این حجم را
    بزنم، واقعاً چقدر پر می‌شود و با چه لغزشی؟» یک ساختار که روی کاغذ
    سودده است ولی پایه‌اش فقط ۱ قرارداد عمق دارد، اجرا نمی‌شود.

    خطا اینجا کشنده نیست: عمق یک افزونه است، و نبودش نباید کل اسکن را
    بی‌نتیجه کند. پایه‌ی بدون عمق فقط `depth: null` می‌گیرد.
    """
    from data.order_book import OrderBookClient

    client = OrderBookClient()
    for found in structures.values():
        for structure in found:
            for leg in structure.get("legs") or []:
                ins_code = leg.get("ins_code")
                quantity = int(leg.get("quantity") or 0)
                if not ins_code or quantity <= 0:
                    leg["depth"] = None
                    continue

                book = client.try_get_order_book(ins_code, leg.get("symbol", ""))
                if book is None:
                    leg["depth"] = None
                    continue

                side = "buy" if leg.get("action") == "BUY" else "sell"
                avg, filled = book.fill_price(side, quantity)
                leg["depth"] = {
                    "levels": len(book.asks if side == "buy" else book.bids),
                    "available": book.depth(side),
                    "fill_price": avg,
                    "filled_quantity": filled,
                    "fully_fillable": filled >= quantity,
                    "slippage": book.slippage(side, quantity),
                }


@app.get("/api/structures")
async def scan_structures(
    underlying: str,
    kind: str = "all",
    limit: int = 10,
    rank_by: str = "roi",
    min_open_interest: int = 50,
    with_depth: bool = False,
) -> dict[str, Any]:
    """اسکن ساختارهای چندپایه روی زنجیره‌ی **واقعی**.

    `with_depth=true` عمق مظنه‌ی هر پایه را هم می‌گیرد و می‌گوید سفارش
    واقعاً به چه قیمتی پر می‌شود. عمداً پیش‌فرض خاموش است: هر پایه یک
    درخواست جداگانه به TSETMC می‌خورد.

    ⚠️ خروجی فقط تحلیل و نقشه‌ی سفارش است؛ هیچ سفارشی ثبت نمی‌شود.
    """
    from strategies.scanner import RANK_KEYS, ScanFilters, StrategyScanner, rank_strategies

    if rank_by not in RANK_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"معیار ناشناخته: «{rank_by}». موجود: {', '.join(sorted(RANK_KEYS))}",
        )

    settings = _settings()

    def _work() -> dict[str, Any]:
        context = create_app(settings, dry_run=True, as_json=False)
        try:
            chain = context.option_chain.get_chain(underlying)
        finally:
            context.close()

        scanner = StrategyScanner(
            ScanFilters(min_open_interest=min_open_interest)
        )
        scans = {
            "long_straddle": scanner.scan_long_straddle,
            "collar": scanner.scan_collar,
            "iron_condor": scanner.scan_iron_condor,
        }
        wanted = scans if kind == "all" else {kind: scans.get(kind)}
        if None in wanted.values():
            raise HTTPException(
                status_code=400,
                detail=f"ساختار ناشناخته: «{kind}». موجود: {', '.join(scans)}, all",
            )

        result = {}
        for name, fn in wanted.items():
            found = rank_strategies(fn(chain, limit=limit * 3), rank_by)[:limit]
            result[name] = [s.to_dict() for s in found]

        if with_depth:
            _attach_depth(result)

        return {
            "underlying": underlying,
            "spot_price": chain.spot_price,
            "ranked_by": rank_by,
            "with_depth": with_depth,
            "structures": result,
        }

    try:
        return await asyncio.to_thread(_work)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - پیام به UI می‌رود
        logger.exception("اسکن ساختارها ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/structures/rank-keys")
def get_rank_keys() -> dict[str, Any]:
    """معیارهای قابل استفاده برای مرتب‌سازی."""
    from strategies.scanner import RANK_KEYS

    return {
        "keys": [
            {"key": k, "bigger_is_better": v} for k, v in sorted(RANK_KEYS.items())
        ]
    }


@app.get("/api/report")
def get_report(days: int | None = None) -> dict[str, Any]:
    """گزارش عملکرد سیگنال‌ها.

    `days=None` یعنی کل تاریخچه. `win_rate` وقتی هیچ سیگنالی نتیجه
    نگرفته `null` است، نه صفر — این دو یکی نیستند.
    """
    from storage.reporting import SignalReporter

    settings = _settings()
    storage = section(settings, "storage")
    path = resolve_path(storage.get("sqlite_path", "var/signals.db"))

    with SignalReporter(path) as reporter:
        return {
            "summary": reporter.summary(days),
            "by_strategy": [
                {
                    "strategy": s.strategy,
                    "total": s.total,
                    "wins": s.wins,
                    "losses": s.losses,
                    "pending": s.pending,
                    "win_rate": s.win_rate,
                    "avg_pnl_pct": s.avg_pnl_pct,
                    "best_pnl_pct": s.best_pnl_pct,
                    "worst_pnl_pct": s.worst_pnl_pct,
                }
                for s in reporter.by_strategy(days)
            ],
            "by_underlying": reporter.by_underlying(days),
            "daily": reporter.daily_counts(days or 30),
            "recent": reporter.recent(limit=50, days=days),
        }


@app.post("/api/report/evaluate")
async def evaluate_pending() -> dict[str, Any]:
    """نتیجه‌ی سیگنال‌های در انتظار را با **قیمت واقعی** بازار می‌سنجد.

    قیمت از TSETMC خوانده می‌شود. اگر نمادی قیمت نداشته باشد، رد می‌شود
    و نتیجه‌ی جعلی ثبت نمی‌شود.
    """
    from storage.reporting import SignalReporter, evaluate_signal

    settings = _settings()
    storage = section(settings, "storage")
    path = resolve_path(storage.get("sqlite_path", "var/signals.db"))

    def _work() -> dict[str, Any]:
        context = create_app(settings, dry_run=True, as_json=False)
        evaluated = skipped = 0
        try:
            with SignalReporter(path) as reporter:
                pending = reporter.pending_signals()
                for row in pending:
                    payload = json.loads(row["payload"])
                    underlying = payload.get("underlying")
                    symbol = payload.get("symbol")
                    if not underlying or not symbol:
                        skipped += 1
                        continue
                    try:
                        chain = context.option_chain.get_chain(underlying)
                        match = next(
                            (c for c in chain.contracts if c.symbol == symbol), None
                        )
                        price = match.last_price or match.bid if match else None
                    except Exception:  # noqa: BLE001 - نماد ممکن است دیگر نباشد
                        price = None

                    if not price:
                        skipped += 1
                        continue

                    outcome, pnl, hit_t, hit_s = evaluate_signal(payload, float(price))
                    reporter.record_outcome(
                        row["signal_id"], float(price), pnl, outcome, hit_t, hit_s
                    )
                    evaluated += 1
        finally:
            context.close()
        return {"evaluated": evaluated, "skipped": skipped}

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:  # noqa: BLE001 - پیام به UI می‌رود
        logger.exception("ارزیابی سیگنال‌ها ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/report/export")
def export_report(days: int | None = None) -> FileResponse:
    """خروجی CSV برای اکسل."""
    from storage.reporting import SignalReporter

    settings = _settings()
    storage = section(settings, "storage")
    path = resolve_path(storage.get("sqlite_path", "var/signals.db"))
    out = resolve_path("var/signal_report.csv")

    with SignalReporter(path) as reporter:
        count = reporter.export_csv(out, days)
    logger.info("گزارش CSV با %s ردیف ساخته شد.", count)

    return FileResponse(
        out, media_type="text/csv", filename=f"garnet-signals-{date.today()}.csv"
    )


class DataSourceUpdate(BaseModel):
    market_data_provider: str | None = None
    option_chain_provider: str | None = None
    enrich_with_broker: bool | None = None
    enrich_limit: int | None = None


class BrokerUpdate(BaseModel):
    enabled: bool | None = None
    token: str | None = None
    session_file: str | None = None


@app.get("/api/datasource")
def get_datasource() -> dict[str, Any]:
    """منبع داده‌ی فعلی و گزینه‌های موجود."""
    import bootstrap

    settings = _settings()
    market = section(settings, "market_data")
    chain = section(settings, "option_chain")
    return {
        "market_data_provider": market.get("provider"),
        "option_chain_provider": chain.get("provider"),
        "enrich_with_broker": bool(chain.get("enrich_with_broker")),
        "enrich_limit": chain.get("enrich_limit", 20),
        "available_market_data": sorted(bootstrap.MARKET_DATA_PROVIDERS),
        "available_option_chain": sorted(bootstrap.OPTION_CHAIN_PROVIDERS),
        "broker_enabled": bool(section(settings, "broker").get("enabled")),
    }


@app.put("/api/datasource")
def update_datasource(update: DataSourceUpdate) -> dict[str, Any]:
    """تغییر منبع داده از پنل.

    ⚠️ ایزی‌تریدر گزینه‌ی زنجیره نیست: مشخصات قرارداد را فقط تک‌به‌تک
    می‌دهد (~۱۳۸۶ درخواست برای کل بازار). به‌جایش `enrich_with_broker`
    را روشن کنید تا روی زنجیره‌ی TSETMC سوار شود.
    """
    import bootstrap

    market_patch: dict[str, Any] = {}
    chain_patch: dict[str, Any] = {}

    if update.market_data_provider is not None:
        name = update.market_data_provider.strip().lower()
        if name not in bootstrap.MARKET_DATA_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"provider ناشناخته: «{name}». "
                f"موجود: {', '.join(sorted(bootstrap.MARKET_DATA_PROVIDERS))}",
            )
        market_patch["provider"] = name

    if update.option_chain_provider is not None:
        name = update.option_chain_provider.strip().lower()
        if name not in bootstrap.OPTION_CHAIN_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"provider ناشناخته: «{name}». "
                f"موجود: {', '.join(sorted(bootstrap.OPTION_CHAIN_PROVIDERS))}",
            )
        chain_patch["provider"] = name

    if update.enrich_with_broker is not None:
        if update.enrich_with_broker and not section(_settings(), "broker").get("enabled"):
            raise HTTPException(
                status_code=400,
                detail="برای غنی‌سازی، اول اتصال کارگزاری را در تب «حساب» فعال کنید.",
            )
        chain_patch["enrich_with_broker"] = update.enrich_with_broker

    if update.enrich_limit is not None:
        if not 0 <= update.enrich_limit <= 200:
            raise HTTPException(
                status_code=400,
                detail="enrich_limit باید بین ۰ تا ۲۰۰ باشد؛ هر واحد یک درخواست شبکه است.",
            )
        chain_patch["enrich_limit"] = update.enrich_limit

    if not market_patch and not chain_patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")

    patch: dict[str, Any] = {}
    if market_patch:
        patch["market_data"] = market_patch
    if chain_patch:
        patch["option_chain"] = chain_patch
    _patch_settings(patch)
    return {"ok": True, "applied": patch}


@app.put("/api/broker")
def update_broker(update: BrokerUpdate) -> dict[str, Any]:
    """تنظیم اتصال حساب کارگزاری از خود پنل.

    ⚠️ توکن در `settings.yaml` ذخیره می‌شود که در `.gitignore` است. عمر
    کوتاهی دارد و باید هر چند ساعت تازه شود.
    """
    patch: dict[str, Any] = {}
    if update.enabled is not None:
        patch["enabled"] = update.enabled
    if update.token is not None:
        patch["token"] = update.token.strip()
    if update.session_file is not None:
        patch["session_file"] = update.session_file.strip()

    if not patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")

    _patch_settings({"broker": patch})
    # توکن هرگز برنمی‌گردد
    return {"ok": True, "applied": sorted(k for k in patch if k != "token")}


@app.get("/api/account")
def get_account() -> dict[str, Any]:
    """پوزیشن‌های واقعی حساب کارگزاری — **فقط خواندن**.

    پیش‌فرض خاموش است. اگر روشن نباشد یا سشن منقضی شده باشد، به‌جای
    خطای خام، وضعیت روشن با راهنمای رفع برمی‌گردد تا داشبورد نشکند.
    """
    settings = _settings()
    broker_cfg = section(settings, "broker")

    if not broker_cfg.get("enabled"):
        return {
            "enabled": False,
            "reason": "اتصال به حساب کارگزاری خاموش است. "
            "از همین صفحه «فعال باشد» را تیک بزنید و توکن را وارد کنید.",
            "positions": [],
        }

    try:
        from brokers.emofid import EmofidAccountClient

        common = {
            "base_url": broker_cfg.get("base_url", "https://api-mts.orbis.easytrader.ir"),
            "timeout": broker_cfg.get("timeout", 15),
            "retries": broker_cfg.get("retries", 3),
        }
        # توکن صریح مقدم است: API آپشن هدر authorization می‌خواهد و فایل
        # سشن (که فقط کوکی دارد) برای آن کافی نیست.
        token = (broker_cfg.get("token") or "").strip()
        if token:
            client = EmofidAccountClient(token=token, **common)
        else:
            client = EmofidAccountClient.from_session_file(
                resolve_path(broker_cfg.get("session_file", "var/emofid/session.json")),
                **common,
            )
        positions = client.get_positions()
        # موجودی جدا try می‌شود: اگر این endpoint در دسترس نباشد،
        # پوزیشن‌ها که خوانده شده‌اند نباید با آن از دست بروند.
        try:
            balance = client.get_balance()
        except Exception as exc:  # موجودی نباید پوزیشن‌ها را ببرد
            logger.warning("خواندن موجودی حساب ناموفق بود: %s", exc)
            balance = None
    except Exception as exc:  # noqa: BLE001 - پیام به UI می‌رود، داشبورد نمی‌خوابد
        logger.warning("خواندن حساب کارگزاری ناموفق بود: %s", exc)
        return {"enabled": True, "reason": str(exc), "positions": []}

    return {
        "enabled": True,
        "reason": None,
        "balance": (
            None
            if balance is None
            else {
                "equity": balance.equity,
                "cash_t0": balance.cash_t0,
                "cash_t1": balance.cash_t1,
                "cash_t2": balance.cash_t2,
                "buy_power_t0": balance.buy_power_t0,
                "buy_power_t2": balance.buy_power_t2,
                "blocked": balance.blocked,
                "margin_blocked": balance.margin_blocked,
                "credit": balance.credit,
            }
        ),
        "use_broker_equity": bool(
            section(settings, "risk").get("use_broker_equity", False)
        ),
        "positions": [
            {
                "symbol_name": p.symbol_name,
                "symbol_isin": p.symbol_isin,
                "quantity": p.quantity,
                "is_long": p.is_long,
                "strike_price": p.strike_price,
                "total_margin": p.total_margin,
                "buy_average_price": p.buy_average_price,
                "sell_average_price": p.sell_average_price,
                "closed_pnl": p.closed_pnl,
                "open_buy_quantity": p.open_buy_quantity,
                "open_sell_quantity": p.open_sell_quantity,
                "cash_settlement_date": (
                    p.cash_settlement_date.isoformat() if p.cash_settlement_date else None
                ),
            }
            for p in positions
        ],
    }


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    """وضعیت کلی برای نوار بالای داشبورد."""
    settings = _settings()
    with _signal_log(settings) as log:
        total = log.count()

    market_open: bool | None = None
    today_jalali: str | None = None
    next_trading_day: str | None = None
    known_holidays: int | None = None
    try:
        context = create_app(settings, dry_run=False, as_json=False)
        try:
            market_open = context.market_data.is_market_open()
            calendar = context.trading_calendar
            if calendar is not None:
                today = date.today()
                today_jalali = format_jalali(today)
                known_holidays = len(calendar.holidays)
                # وقتی بازار بسته است، «کِی باز می‌شود» مفیدترین چیزی است
                # که نوار وضعیت می‌تواند بگوید.
                if not market_open:
                    nxt = (
                        today
                        if calendar.is_trading_day(today)
                        else calendar.next_trading_day(today)
                    )
                    next_trading_day = f"{nxt.isoformat()} ({format_jalali(nxt)})"
        finally:
            context.close()
    except Exception as exc:  # نبود شبکه نباید داشبورد را بخواباند
        logger.warning("تشخیص وضعیت بازار ناموفق بود: %s", exc)

    return {
        "market_open": market_open,
        "signal_count": total,
        "market_data_provider": section(settings, "market_data").get("provider"),
        "option_chain_provider": section(settings, "option_chain").get("provider"),
        "settings_path": str(SETTINGS_PATH),
        "today_jalali": today_jalali,
        "next_trading_day": next_trading_day,
        "known_holidays": known_holidays,
    }


# ----------------------------------------------------------------------
# فایل‌های استاتیک
# ----------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

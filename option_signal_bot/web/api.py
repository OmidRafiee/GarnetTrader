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
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bootstrap import create_app
from config import force_utf8_stdio
from config.loader import PROJECT_ROOT, deep_merge, load_settings, resolve_path, section
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
async def run_scan(mock: bool = False) -> dict[str, Any]:
    """یک پاس رصد بازار.

    سفارشی ثبت نمی‌شود؛ فقط سیگنال تولید، ذخیره و به notifierها فرستاده
    می‌شود — دقیقاً همان کاری که `main.py --once` می‌کند.
    """
    if _scan_lock.locked():
        raise HTTPException(status_code=409, detail="یک پاس رصد در حال اجراست.")

    async with _scan_lock:
        settings = _settings()
        if mock:
            settings.setdefault("market_data", {})["provider"] = "mock"
            settings.setdefault("option_chain", {})["provider"] = "mock"

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


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    """وضعیت کلی برای نوار بالای داشبورد."""
    settings = _settings()
    with _signal_log(settings) as log:
        total = log.count()

    market_open: bool | None = None
    try:
        context = create_app(settings, dry_run=False, as_json=False)
        try:
            market_open = context.market_data.is_market_open()
        finally:
            context.close()
    except Exception as exc:  # noqa: BLE001 - نبود شبکه نباید داشبورد را بخواباند
        logger.warning("تشخیص وضعیت بازار ناموفق بود: %s", exc)

    return {
        "market_open": market_open,
        "signal_count": total,
        "market_data_provider": section(settings, "market_data").get("provider"),
        "option_chain_provider": section(settings, "option_chain").get("provider"),
        "settings_path": str(SETTINGS_PATH),
    }


# ----------------------------------------------------------------------
# فایل‌های استاتیک
# ----------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

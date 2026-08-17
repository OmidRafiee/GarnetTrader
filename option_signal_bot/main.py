"""نقطه ورود CLI ربات سیگنال‌دهی آپشن بورس تهران.

مسئولیت این فایل فقط سه چیز است: خواندن آرگومان‌ها، حلقه اجرا، و توزیع سیگنال.
ساخت اجزا در `bootstrap.py` و مقادیر پیش‌فرض در `config/loader.py` است.

حلقه اصلی: دیتا → استراتژی → سیگنال → notifier → لاگ.

اجرای سریع بدون هیچ تنظیمی:
    python main.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

from bootstrap import AppContext, build_backtester, create_app
from config.loader import load_settings, section
from notifiers.base_notifier import BaseNotifier
from signals.signal_model import Signal, SignalStatus
from storage.signal_log import SignalLog

logger = logging.getLogger("option_signal_bot")


# ----------------------------------------------------------------------
# حلقه اصلی
# ----------------------------------------------------------------------
def dispatch(
    signals: list[Signal], notifiers: list[BaseNotifier], signal_log: SignalLog | None
) -> None:
    """ارسال سیگنال‌ها به همه کانال‌ها و ثبت آن‌ها در لاگ ماندگار."""
    for signal in signals:
        delivered = sum(1 for notifier in notifiers if notifier.send(signal))
        if delivered:
            signal.status = SignalStatus.NOTIFIED
        if signal_log is not None:
            signal_log.save(signal)


def run_cycle(app: AppContext) -> list[Signal]:
    """یک پاس کامل رصد بازار."""
    signals = app.generator.run_once()
    if not signals:
        logger.info("در این پاس شرایط هیچ استراتژی برقرار نبود.")
    dispatch(signals, app.notifiers, app.signal_log)
    return signals


def run_loop(app: AppContext, once: bool, interval: int | None) -> None:
    """حلقه دائمی رصد بازار تا وقتی کاربر متوقفش کند."""
    general = section(app.settings, "general")
    interval = interval or general.get("poll_interval_seconds", 300)
    only_when_open = general.get("run_only_when_market_open", False)

    while True:
        if only_when_open and not app.market_data.is_market_open():
            logger.info("بازار بسته است؛ پاس بعدی %s ثانیه دیگر.", interval)
        else:
            run_cycle(app)
        if once:
            return
        time.sleep(interval)


def run_backtest(settings: dict[str, Any]) -> None:
    """گزارش کیفیت سیگنال روی داده تاریخی (بدون اجرای هیچ سفارشی)."""
    app = create_app(settings, dry_run=True)
    backtester = build_backtester(settings, app.market_data, app.option_chain)
    report = backtester.run(
        symbols=list(section(settings, "market_data").get("symbols", [])),
        days=section(settings, "backtest").get("history_days", 180),
    )
    print("\n=== گزارش بک‌تست کیفیت سیگنال ===")
    print(report.summary())


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="option_signal_bot",
        description="ربات سیگنال‌دهی دستی معاملات آپشن بورس تهران (بدون اجرای سفارش)",
    )
    parser.add_argument("--config", type=Path, default=None, help="مسیر فایل settings.yaml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="یک پاس با داده mock، خروجی فقط کنسول، بدون تلگرام و بدون ذخیره‌سازی",
    )
    parser.add_argument("--once", action="store_true", help="فقط یک پاس اجرا و خروج")
    parser.add_argument("--interval", type=int, default=None, help="فاصله پاس‌ها به ثانیه")
    parser.add_argument("--symbols", nargs="+", default=None, help="بازنویسی لیست نمادها")
    parser.add_argument("--mock", action="store_true", help="اجبار به استفاده از داده mock")
    parser.add_argument("--json", action="store_true", help="چاپ سیگنال‌ها به‌صورت JSON")
    parser.add_argument("--backtest", action="store_true", help="اجرای بک‌تست کیفیت سیگنال")
    parser.add_argument("--log-level", default=None, help="DEBUG | INFO | WARNING | ERROR")
    return parser.parse_args(argv)


def apply_cli_overrides(settings: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """اعمال فلگ‌های CLI روی تنظیمات (اولویت CLI بر فایل)."""
    if args.symbols:
        settings["market_data"]["symbols"] = args.symbols
    if args.dry_run or args.mock:
        settings["market_data"]["provider"] = "mock"
        settings["option_chain"]["provider"] = "mock"
    return settings


def configure_logging(settings: dict[str, Any], cli_level: str | None) -> None:
    level = cli_level or section(settings, "general").get("log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = apply_cli_overrides(load_settings(args.config), args)
    configure_logging(settings, args.log_level)

    if args.backtest:
        run_backtest(settings)
        return 0

    app = create_app(settings, dry_run=args.dry_run, as_json=args.json)
    try:
        if args.dry_run:
            print("حالت آزمایشی (dry-run): داده mock، خروجی کنسول، بدون ثبت سفارش.\n")
            signals = run_cycle(app)
            print(f"\nتعداد سیگنال تولیدشده: {len(signals)}")
            return 0
        run_loop(app, once=args.once, interval=args.interval)
    except KeyboardInterrupt:
        logger.info("اجرا با درخواست کاربر متوقف شد.")
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

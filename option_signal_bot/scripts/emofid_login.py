"""اسکلت لاگین با مرورگر **قابل‌مشاهده** + کشف فهرست APIهای پلتفرم.

این اسکریپت رمز شما را نمی‌داند و نمی‌خواهد. کاری که می‌کند:

    ۱. یک پنجره‌ی واقعی Chrome باز می‌کند و به آدرس پلتفرم می‌رود
    ۲. **شما** خودتان نام کاربری، رمز و OTP را در همان پنجره وارد می‌کنید
    ۳. در تمام مدت، ترافیک شبکه را رصد و «شکل» APIها را ثبت می‌کند
       (نام فیلدها و نوعشان — هیچ مقداری از داده‌های حساب ذخیره نمی‌شود)
    ۴. در پایان: گزارش فهرست API + وضعیت سشن را ذخیره می‌کند

⚠️ نکات مهم:
    - این اسکریپت هیچ فرمی را خودکار پر نمی‌کند و هیچ اعتبارنامه‌ای ذخیره نمی‌کند.
    - فایل سشن (`--session-file`) معادل دسترسی به حساب شماست. زیر `var/` ذخیره
      می‌شود که در `.gitignore` است. آن را هرگز جایی نفرستید.
    - هیچ سفارشی ثبت نمی‌شود؛ این ابزار فقط رصد می‌کند.

پیش‌نیاز (یک بار):
    pip install playwright
    # نیازی به `playwright install` نیست اگر Chrome سیستم را داشته باشید

اجرا:
    python scripts/emofid_login.py
    python scripts/emofid_login.py --url https://easytrader.ir --minutes 15
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import force_utf8_stdio
from discovery.api_inventory import (  # noqa: E402
    SENSITIVE_HEADERS,
    ApiCall,
    ApiInventory,
    looks_sensitive,
    redact_url,
    sketch_json_text,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://easytrader.ir"
DEFAULT_OUT_DIR = PROJECT_ROOT / "var" / "emofid"
#: فقط ترافیک داده‌ای؛ عکس و فونت و CSS نویز است
CAPTURED_TYPES = {"xhr", "fetch", "websocket", "eventsource", "other"}


def _auth_header_names(headers: dict[str, str]) -> list[str]:
    """نام هدرهایی که احراز هویت حمل می‌کنند (مقدارشان هرگز خوانده نمی‌شود)."""
    return sorted(
        name
        for name in headers
        if name.lower() in SENSITIVE_HEADERS or looks_sensitive(name)
    )


def _query_keys(url: str) -> list[str]:
    from urllib.parse import parse_qsl, urlsplit

    return [k for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)]


def capture(url: str, minutes: float, session_file: Path, out_dir: Path) -> ApiInventory:
    """مرورگر را باز می‌کند، منتظر لاگین دستی می‌ماند و ترافیک را ثبت می‌کند."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "Playwright نصب نیست. اجرا کنید:  pip install playwright\n"
            "(اگر Chrome سیستم را دارید، به `playwright install` نیازی نیست)"
        ) from None

    inventory = ApiInventory(title=f"فهرست API کشف‌شده — {url}")
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(
            storage_state=str(session_file) if session_file.exists() else None,
            viewport={"width": 1440, "height": 900},
        )

        def on_response(response) -> None:
            request = response.request
            if request.resource_type not in CAPTURED_TYPES:
                return
            try:
                headers = request.all_headers()
            except Exception:  # noqa: BLE001 - درخواست ممکن است از بین رفته باشد
                headers = {}
            content_type = (response.headers or {}).get("content-type", "").split(";")[0]

            response_schema = None
            if "json" in content_type:
                try:
                    response_schema = sketch_json_text(response.text())
                except Exception:  # noqa: BLE001 - بدنه ممکن است در دسترس نباشد
                    response_schema = None

            # بدنه‌ی درخواست ممکن است gzip یا باینری باشد؛ playwright هنگام
            # decode آن UnicodeDecodeError می‌دهد. بدون این محافظ، آن استثنا
            # کل هندلر را می‌کشد و آن endpoint هرگز ثبت نمی‌شود.
            try:
                request_schema = sketch_json_text(request.post_data)
            except Exception:  # noqa: BLE001 - بدنه‌ی غیرمتنی یا از بین رفته
                request_schema = None

            inventory.add_call(
                ApiCall(
                    method=request.method,
                    url=redact_url(request.url),
                    status=response.status,
                    resource_type=request.resource_type,
                    content_type=content_type,
                    auth_headers=_auth_header_names(headers),
                    query_keys=_query_keys(request.url),
                    request_schema=request_schema,
                    response_schema=response_schema,
                )
            )

        def on_websocket(ws) -> None:
            print(f"   🔌 اتصال realtime: {redact_url(ws.url)}")
            ws.on("framesent", lambda payload: inventory.add_socket_frame(ws.url, payload, True))
            ws.on(
                "framereceived",
                lambda payload: inventory.add_socket_frame(ws.url, payload, False),
            )

        context.on("response", on_response)
        page = context.new_page()
        page.on("websocket", on_websocket)

        print(f"\nمرورگر باز شد → {url}")
        print("=" * 68)
        print("خودتان در همان پنجره لاگین کنید (نام کاربری، رمز، OTP).")
        print("این اسکریپت هیچ فرمی را پر نمی‌کند و رمزی ذخیره نمی‌کند.")
        print("بعد از لاگین، صفحاتی را که برایمان مهم است باز کنید:")
        print("   • دیده‌بان / تابلوی بازار آپشن")
        print("   • پوزیشن‌ها (سبد دارایی)")
        print("   • موجودی حساب")
        print("   • عمق مظنه یک نماد آپشن")
        print("=" * 68)
        print(f"ضبط تا {minutes:.0f} دقیقه ادامه دارد. برای پایان زودتر، Ctrl+C.\n")

        page.goto(url, wait_until="domcontentloaded")

        deadline = time.monotonic() + minutes * 60
        try:
            while time.monotonic() < deadline:
                page.wait_for_timeout(1000)
                if not context.pages:  # کاربر مرورگر را بست
                    break
        except KeyboardInterrupt:
            print("\nضبط با درخواست شما پایان یافت.")

        # ذخیره وضعیت سشن برای اجرای بعدی (حساس!)
        try:
            context.storage_state(path=str(session_file))
            print(f"\nوضعیت سشن ذخیره شد: {session_file}")
            print("⚠️ این فایل معادل دسترسی به حساب شماست؛ آن را جایی نفرستید.")
        except Exception as exc:  # noqa: BLE001
            print(f"ذخیره سشن ناموفق بود: {exc}")

        context.close()
        browser.close()

    return inventory


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(
        description="لاگین دستی در مرورگر قابل‌مشاهده + کشف فهرست APIها (بدون ثبت داده حساب)"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="آدرس پلتفرم")
    parser.add_argument("--minutes", type=float, default=10.0, help="مدت ضبط")
    parser.add_argument(
        "--session-file",
        type=Path,
        default=DEFAULT_OUT_DIR / "session.json",
        help="مسیر ذخیره وضعیت سشن (حساس، زیر var/)",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="پوشه گزارش")
    args = parser.parse_args(argv)

    inventory = capture(args.url, args.minutes, args.session_file, args.out_dir)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.out_dir / "api_inventory.md"
    json_path = args.out_dir / "api_inventory.json"
    md_path.write_text(inventory.to_markdown(), encoding="utf-8")
    json_path.write_text(inventory.to_json(), encoding="utf-8")

    print(f"\n{'=' * 68}")
    print(f"endpoint یکتا: {len(inventory.calls)} | اتصال سوکت: {len(inventory.sockets)}")
    for host, count in inventory.hosts().items():
        print(f"   {host:40} {count:>4} فراخوان")
    auth = inventory.auth_schemes()
    print(f"هدرهای احراز هویت: {', '.join(auth) if auth else '—'}")
    print(f"\nگزارش: {md_path}")
    print(f"        {json_path}")
    print("این گزارش مقدار داده‌ها را ندارد، ولی قبل از اشتراک یک مرور چشمی بکنید.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

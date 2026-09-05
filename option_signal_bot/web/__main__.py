"""اجرای داشبورد:  python -m web

پیش‌فرض روی `127.0.0.1` گوش می‌دهد، یعنی فقط از همین کامپیوتر در دسترس است.
داشبورد هیچ احراز هویتی ندارد و تنظیمات را می‌نویسد، پس نباید روی شبکه باز
شود. اگر واقعاً لازم شد، `--host` را صریح بدهید.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from threading import Timer

from config import force_utf8_stdio


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(description="داشبورد وب ربات سیگنال")
    parser.add_argument("--host", default="127.0.0.1", help="پیش‌فرض: فقط همین کامپیوتر")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true", help="مرورگر را باز نکن")
    parser.add_argument("--reload", action="store_true", help="حالت توسعه")
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn نصب نیست. اجرا کنید:\n"
            "    .venv\\Scripts\\python.exe -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print(f"\nداشبورد: {url}")
    print("برای توقف: Ctrl+C")
    if args.host != "127.0.0.1":
        print(
            "\n⚠️  روی شبکه باز شده و داشبورد احراز هویت ندارد؛ "
            "هر کسی به این آدرس برسد می‌تواند تنظیمات را عوض کند."
        )
    print()

    if not args.no_browser and not args.reload:
        Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "web.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

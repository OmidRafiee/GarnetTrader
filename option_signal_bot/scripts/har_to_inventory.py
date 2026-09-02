"""تبدیل یک فایل HAR (خروجی DevTools) به فهرست API — بدون نیاز به هیچ نصبی.

این مسیر جایگزین `emofid_login.py` است، برای وقتی که نمی‌خواهید Playwright نصب کنید
یا مرورگر را به اسکریپت بسپارید:

    ۱. در Chrome، DevTools → تب Network را باز کنید
    ۲. گزینه‌ی «Preserve log» را تیک بزنید
    ۳. لاگین کنید و صفحات مورد نظر (پوزیشن، موجودی، دیده‌بان) را باز کنید
    ۴. راست‌کلیک روی لیست درخواست‌ها → «Save all as HAR with content»
    ۵. اجرا:  python scripts/har_to_inventory.py مسیر/فایل.har

⚠️ فایل HAR خام **حاوی توکن و داده‌های حساب شماست**. آن را جایی نفرستید؛
فقط گزارش خروجی این اسکریپت (که مقدارها را ندارد) قابل اشتراک است.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

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
DEFAULT_OUT_DIR = PROJECT_ROOT / "var" / "emofid"
#: پسوندهایی که داده نیستند و فقط گزارش را شلوغ می‌کنند
STATIC_SUFFIXES = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff",
    ".woff2", ".ttf", ".eot", ".ico", ".map", ".webp", ".mp4",
)


def is_static(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(STATIC_SUFFIXES)


def auth_header_names(headers: list[dict]) -> list[str]:
    names = []
    for header in headers:
        name = str(header.get("name", ""))
        if name.lower() in SENSITIVE_HEADERS or looks_sensitive(name):
            if name not in names:
                names.append(name)
    return sorted(names)


def build_inventory(har: dict, keep_static: bool = False) -> ApiInventory:
    """ساخت فهرست از ورودی‌های HAR (فقط شکل داده، بدون مقدار)."""
    inventory = ApiInventory(title="فهرست API کشف‌شده از HAR")
    entries = (har.get("log") or {}).get("entries") or []

    for entry in entries:
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        url = request.get("url", "")
        if not url or (not keep_static and is_static(url)):
            continue

        content = response.get("content") or {}
        content_type = str(content.get("mimeType", "")).split(";")[0]
        response_schema = (
            sketch_json_text(content.get("text")) if "json" in content_type else None
        )
        post = request.get("postData") or {}

        inventory.add_call(
            ApiCall(
                method=request.get("method", "GET"),
                url=redact_url(url),
                status=response.get("status"),
                resource_type=entry.get("_resourceType", ""),
                content_type=content_type,
                auth_headers=auth_header_names(request.get("headers") or []),
                query_keys=[k for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)],
                request_schema=sketch_json_text(post.get("text")),
                response_schema=response_schema,
            )
        )

    # HAR فریم‌های وب‌سوکت را در `_webSocketMessages` نگه می‌دارد
    for entry in entries:
        messages = entry.get("_webSocketMessages")
        if not messages:
            continue
        url = (entry.get("request") or {}).get("url", "")
        for message in messages:
            payload = message.get("data")
            if isinstance(payload, str):
                inventory.add_socket_frame(url, payload, message.get("type") == "send")

    return inventory


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(description="HAR → فهرست API (بدون مقدار داده)")
    parser.add_argument("har", type=Path, help="مسیر فایل .har")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--keep-static", action="store_true", help="نگه‌داشتن js/css/تصاویر")
    args = parser.parse_args(argv)

    if not args.har.exists():
        raise SystemExit(f"فایل پیدا نشد: {args.har}")

    har = json.loads(args.har.read_text(encoding="utf-8", errors="replace"))
    inventory = build_inventory(har, keep_static=args.keep_static)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.out_dir / "api_inventory.md"
    json_path = args.out_dir / "api_inventory.json"
    md_path.write_text(inventory.to_markdown(), encoding="utf-8")
    json_path.write_text(inventory.to_json(), encoding="utf-8")

    print(f"endpoint یکتا: {len(inventory.calls)} | اتصال سوکت: {len(inventory.sockets)}")
    for host, count in inventory.hosts().items():
        print(f"   {host:40} {count:>4} فراخوان")
    auth = inventory.auth_schemes()
    print(f"هدرهای احراز هویت: {', '.join(auth) if auth else '—'}")
    print(f"\nگزارش: {md_path}\n        {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

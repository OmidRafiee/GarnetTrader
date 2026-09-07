"""ضبط یک پاسخ واقعی TSETMC برای استفاده به‌عنوان fixture آفلاین.

چرا؟ تا تست‌ها بدون شبکه اجرا شوند و اگر TSETMC ساختار پاسخ را عوض کرد،
با شکست تست معلوم شود، نه با سیگنال غلط.

اجرا:
    python scripts/fetch_tsetmc_sample.py                    # فقط گزارش وضعیت بازار
    python scripts/fetch_tsetmc_sample.py --save-fixture      # به‌روزرسانی fixture تست
    python scripts/fetch_tsetmc_sample.py --save-full var/samples/full.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import force_utf8_stdio
from data.tsetmc_option_chain_client import (
    PAYLOAD_KEY,
    HttpPayloadSource,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "tsetmc_option_market_watch.json"
#: نماد پایه‌ای که ردیف‌هایش در fixture نگه داشته می‌شود
FIXTURE_UNDERLYING = "خودرو"
FIXTURE_ROWS = 6


def summarize(rows: list[dict]) -> None:
    """گزارش کوتاه از وضعیت بازار آپشن."""
    print(f"تعداد ردیف استرایک: {len(rows)}")
    underlyings = Counter(r.get("lval30_UA", "?") for r in rows)
    print(f"نماد پایه دارای آپشن: {len(underlyings)}")
    for name, count in underlyings.most_common():
        print(f"   {name:14} {count:>4} ردیف")
    expiries = sorted({str(r.get("endDate")) for r in rows})
    print(f"سررسیدها: {', '.join(expiries)}")
    no_quote = sum(
        1
        for r in rows
        if not r.get("pMeDem_C") and not r.get("pMeOf_C") and not r.get("pDrCotVal_C")
    )
    print(f"کال بدون هیچ مظنه/معامله: {no_quote} از {len(rows)}")


def build_fixture(rows: list[dict]) -> list[dict]:
    """ردیف‌های نمونه + یک ردیف بی‌مظنه، برای تست گیت‌های کیفیت داده."""
    picked = [r for r in rows if r.get("lval30_UA") == FIXTURE_UNDERLYING][:FIXTURE_ROWS]
    if not picked:
        raise SystemExit(f"نماد {FIXTURE_UNDERLYING} در پاسخ پیدا نشد.")

    dead = next(
        (
            r
            for r in rows
            if not r.get("pMeDem_P") and not r.get("pMeOf_P") and not r.get("pDrCotVal_P")
        ),
        None,
    )
    if dead is None:
        # اگر امروز چنین ردیفی نبود، یکی می‌سازیم تا تست گیت کیفیت پایدار بماند
        dead = dict(picked[-1])
        dead.update(
            {
                "pMeDem_P": 0,
                "pMeOf_P": 0,
                "pDrCotVal_P": 0,
                "pClosing_P": 0,
                "lVal18AFC_P": str(dead["lVal18AFC_P"]) + "X",
            }
        )
    return [*picked, dead]


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(description="ضبط نمونه پاسخ TSETMC")
    parser.add_argument("--market", type=int, default=0, help="0=همه، 1=بورس، 2=فرابورس")
    parser.add_argument("--save-fixture", action="store_true", help="به‌روزرسانی fixture تست")
    parser.add_argument("--save-full", type=Path, default=None, help="ذخیره پاسخ کامل")
    args = parser.parse_args(argv)

    payload = HttpPayloadSource(market=args.market).fetch()
    rows = payload.get(PAYLOAD_KEY) or []
    if not rows:
        raise SystemExit("پاسخ خالی بود؛ ساختار API را بررسی کنید.")

    summarize(rows)

    if args.save_full:
        args.save_full.parent.mkdir(parents=True, exist_ok=True)
        args.save_full.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"\nپاسخ کامل ذخیره شد: {args.save_full}")

    if args.save_fixture:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(
            json.dumps({PAYLOAD_KEY: build_fixture(rows)}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"fixture به‌روز شد: {FIXTURE_PATH}")
        print("حالا تست‌ها را اجرا کنید: pytest -q")

    return 0


if __name__ == "__main__":
    sys.exit(main())

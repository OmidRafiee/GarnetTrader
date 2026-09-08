"""گزارش دوره‌ای خودکار (روزانه / هفتگی).

**چرا لازم است**

گزارش عملکرد در داشبورد هست، ولی کسی که هر روز داشبورد را باز نمی‌کند،
هیچ‌وقت نمی‌فهمد یک استراتژی ماه‌ها است پول از دست می‌دهد. گزارشِ
فرستاده‌شده، گزارشی است که خوانده می‌شود.

**زمان‌بندی بدون scheduler**

پروژه سرویس یا cron ندارد و روی لپ‌تاپ اجرا می‌شود؛ لپ‌تاپ خوابیده یعنی
هر زمان‌بندیِ دقیقی از دست می‌رود. پس منطق «آیا موعدش رسیده؟» بر پایه‌ی
**آخرین ارسال** است، نه ساعت دیوار:

    اگر از آخرین گزارش به اندازه‌ی یک دوره گذشته → بفرست

نتیجه‌اش این است که اگر ربات دو روز خاموش بوده، بعد از روشن شدن **یک**
گزارش می‌آید (نه دو تا، نه صفر). گزارشِ دیرشده از گزارشِ ازدست‌رفته
بهتر است، و سیلِ گزارش‌های عقب‌افتاده از هر دو بدتر.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DAILY = "daily"
WEEKLY = "weekly"

_PERIOD_DAYS = {DAILY: 1, WEEKLY: 7}
_PERIOD_LABEL = {DAILY: "روزانه", WEEKLY: "هفتگی"}


@dataclass
class ReportSchedule:
    """آخرین زمان ارسال هر دوره، با تداوم روی دیسک.

    بدون تداوم، هر ری‌استارت یک گزارش تکراری می‌فرستاد — و ربات‌هایی که
    زیاد ری‌استارت می‌شوند، کاربر را غرق می‌کردند.
    """

    path: Path | None = None

    def __post_init__(self) -> None:
        self._sent: dict[str, str] = {}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._sent = {
                        k: str(v) for k, v in data.items() if isinstance(v, str)
                    }
            except (OSError, ValueError) as exc:
                logger.warning("زمان‌بندی گزارش خوانده نشد: %s", exc)

    def last_sent(self, period: str) -> datetime | None:
        raw = self._sent.get(period)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def mark_sent(self, period: str, now: datetime | None = None) -> None:
        self._sent[period] = (now or datetime.now()).isoformat(timespec="seconds")
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._sent, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("زمان‌بندی گزارش ذخیره نشد: %s", exc)

    def is_due(self, period: str, now: datetime | None = None) -> bool:
        """آیا موعد این دوره رسیده؟

        اولین اجرا **موعد نیست**: بلافاصله پس از نصب، یک گزارش خالی
        فرستادن فقط سردرگمی می‌سازد. پس اولین بار فقط زمان ثبت می‌شود.
        """
        days = _PERIOD_DAYS.get(period)
        if days is None:
            raise ValueError(f"دوره‌ی ناشناخته: «{period}»")

        now = now or datetime.now()
        previous = self.last_sent(period)
        if previous is None:
            self.mark_sent(period, now)
            return False
        return (now - previous) >= timedelta(days=days)


def format_report(
    period: str,
    metrics: dict[str, Any],
    by_strategy: list[dict[str, Any]] | None = None,
    signal_count: int = 0,
) -> str:
    """متن گزارش دوره‌ای.

    `None` در معیارها «نامعلوم» چاپ می‌شود، نه صفر — همان قراردادی که
    کل پروژه رعایت می‌کند.
    """
    label = _PERIOD_LABEL.get(period, period)

    def num(value: Any, digits: int = 2, suffix: str = "", signed: bool = True) -> str:
        if value is None:
            return "نامعلوم"
        sign = "+" if signed else ""
        return f"{value:{sign}.{digits}f}{suffix}"

    lines = [f"📊 گزارش {label} عملکرد سیگنال‌ها", ""]
    lines.append(f"سیگنال جدید در این دوره: {signal_count}")

    total = metrics.get("total") or 0
    if not total:
        lines.append("هیچ سیگنالی در این دوره ارزیابی نشده.")
        return "\n".join(lines)

    lines += [
        f"ارزیابی‌شده: {total} (برد {metrics.get('wins', 0)} / "
        f"باخت {metrics.get('losses', 0)})",
        f"نرخ برد: {num(metrics.get('win_rate_pct'), 1, '٪', signed=False)}",
        f"انتظار ریاضی: {num(metrics.get('expectancy_pct'), 2, '٪')}",
        f"ضریب سود: {num(metrics.get('profit_factor'), 2, signed=False)}",
        f"حداکثر افت: {num(metrics.get('max_drawdown_pct'), 2, '٪', signed=False)}",
    ]

    streak = metrics.get("longest_losing_streak")
    if streak:
        lines.append(f"بلندترین زنجیره باخت: {streak}")

    for row in by_strategy or []:
        if not row.get("total"):
            continue
        # `win_rate` نسبت است (۰..۱) نه درصد؛ `None` یعنی هنوز نتیجه‌ای نیست
        rate = row.get("win_rate")
        rate_text = "نامعلوم" if rate is None else f"{rate * 100:.0f}٪"
        lines.append(
            f"  └ {row.get('strategy', '?')}: {row['total']} سیگنال، "
            f"نرخ برد {rate_text}، "
            f"میانگین {num(row.get('avg_pnl_pct'), 2, '٪')}"
        )

    lines += [
        "",
        "⚠️ این گزارش فقط اطلاع‌رسانی است؛ هیچ سفارشی ثبت نشده و نمی‌شود.",
    ]
    return "\n".join(lines)

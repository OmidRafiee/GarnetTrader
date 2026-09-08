"""معیارهای حرفه‌ای عملکرد — یک پیاده‌سازی، دو مصرف‌کننده.

دو جا به همین اعداد نیاز دارند:

* `backtest/signal_backtester.py` — روی داده‌ی **تاریخی**
* `storage/reporting.py` — روی نتیجه‌ی **واقعیِ** سیگنال‌های صادرشده

نوشتنشان جدا یعنی دو نسخه که با هم واگرا می‌شوند، و بعد گزارش زنده و
بک‌تست دو عدد مختلف برای «شارپ» می‌دهند بدون اینکه معلوم باشد کدام درست
است. پس منطق اینجاست و هر دو صدایش می‌زنند.

**قرارداد `None`:** هر معیاری که نمونه‌ی کافی نداشته باشد `None`
برمی‌گرداند، نه صفر. صفر یعنی «حساب شد و صفر بود»؛ `None` یعنی
«نمی‌دانیم». قِلب کردن این دو، گزارشی می‌سازد که مطمئن به نظر می‌رسد و
نیست.
"""

from __future__ import annotations

import math
from typing import Any


def median(values: list[float]) -> float | None:
    """میانه؛ برخلاف میانگین، یک مقدار پرت آن را جابه‌جا نمی‌کند."""
    ordered = sorted(values)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def stdev(values: list[float]) -> float | None:
    """انحراف معیار نمونه‌ای؛ `None` اگر کمتر از دو مقدار باشد.

    با یک نمونه، پراکندگی تعریف نشده است. صفر برگرداندن یعنی ادعای
    «بی‌ریسک»، که از یک نمونه قابل نتیجه‌گیری نیست.
    """
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def average_win(values: list[float]) -> float | None:
    wins = [v for v in values if v > 0]
    return average(wins)


def average_loss(values: list[float]) -> float | None:
    """میانگین بازده بازنده‌ها (عدد منفی)."""
    losses = [v for v in values if v < 0]
    return average(losses)


def expectancy(values: list[float]) -> float | None:
    """انتظار ریاضی هر معامله.

    مهم‌ترین عدد گزارش. نرخ برد بالا با زیان‌های بزرگ می‌تواند انتظار
    **منفی** بدهد، و نرخ برد ۳۰٪ با بردهای بزرگ می‌تواند سودده باشد. پس
    نرخ برد به‌تنهایی گمراه‌کننده است.

    ریاضیاتش با میانگین ساده یکی است؛ نامِ جدا برای این است که در گزارش
    معنایش روشن باشد.
    """
    return average(values)


def profit_factor(values: list[float]) -> float | None:
    """مجموع بردها ÷ قدرمطلق مجموع زیان‌ها. بزرگ‌تر از ۱ یعنی سودده.

    `None` اگر هیچ زیانی نباشد: نسبت بی‌نهایت است و عدد دادن یعنی ادعای
    اطمینان از نمونه‌ای که هنوز زیان ندیده.
    """
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses <= 0:
        return None
    return gains / losses


def sharpe(values: list[float]) -> float | None:
    """شارپِ **هر معامله** — عمداً سالانه‌سازی **نشده**.

    سالانه‌کردن به تعداد معامله در سال نیاز دارد که به بازه و تنظیمات
    وابسته است؛ ضرب در عددی حدسی، شارپ را دلبخواه بزرگ می‌کند. نرخ بدون
    ریسک هم صفر گرفته شده، چون این بازده‌ها برای یک افق چندروزه‌اند نه
    بازده سرمایه در یک سال.
    """
    spread = stdev(values)
    if spread is None or spread <= 0:
        return None
    return (sum(values) / len(values)) / spread


def sortino(values: list[float]) -> float | None:
    """مثل شارپ، ولی فقط نوسانِ **سمت زیان** را جریمه می‌کند.

    نوسانِ رو به سود ریسک نیست. برای الگویی با بردهای بزرگ و زیان‌های
    کوچک، سورتینو منصفانه‌تر است.
    """
    if not values:
        return None
    downside = [v for v in values if v < 0]
    if not downside:
        return None
    deviation = math.sqrt(sum(v**2 for v in downside) / len(downside))
    if deviation <= 0:
        return None
    return (sum(values) / len(values)) / deviation


def equity_curve(values: list[float]) -> list[float]:
    """بازده تجمعی (جمع ساده‌ی درصدها)، به ترتیب ورودی.

    جمع ساده و نه مرکب: هر سیگنال یک معامله‌ی مستقل با اندازه‌ی ثابت
    است، نه سرمایه‌گذاری مجدد سود.

    ⚠️ ترتیب ورودی باید **زمانی** باشد؛ این تابع مرتب نمی‌کند.
    """
    total = 0.0
    curve = []
    for value in values:
        total += value
        curve.append(total)
    return curve


def max_drawdown(values: list[float]) -> float | None:
    """بیشترین افت از قله‌ی منحنی تجمعی، به‌صورت **اندازه‌ی مثبت**.

    میانگین مثبت، مسیر رسیدن به آن را پنهان می‌کند. افت ۴۰٪ در میانه‌ی
    راه یعنی در عمل خیلی‌ها قبل از پایان بازه بیرون می‌آمدند — عددی که
    میانگین کاملاً از دید پنهان می‌کند.
    """
    curve = equity_curve(values)
    if not curve:
        return None
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, peak - value)
    return worst


def longest_losing_streak(values: list[float]) -> int:
    """بلندترین زنجیره‌ی متوالیِ زیان — سنجه‌ی تحملِ لازم.

    مقدار صفر (نه سود نه زیان) زنجیره را **نمی‌شکند** و جزوش هم نمی‌شود.
    """
    longest = current = 0
    for value in values:
        if value > 0:
            current = 0
        elif value < 0:
            current += 1
            longest = max(longest, current)
    return longest


def summarize(values: list[float]) -> dict[str, Any]:
    """همه‌ی معیارها در یک دیکشنری — برای API، CSV و گزارش متنی.

    Args:
        values: بازده هر معامله (درصد)، به ترتیب **زمانی**.
    """
    total = len(values)
    wins = sum(1 for v in values if v > 0)
    losses = sum(1 for v in values if v < 0)
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        # نرخ برد روی معاملاتی که **نتیجه گرفته‌اند**، نه کل
        "win_rate_pct": (wins / (wins + losses) * 100.0)
        if (wins + losses)
        else None,
        "expectancy_pct": expectancy(values),
        "avg_return_pct": average(values),
        "median_return_pct": median(values),
        "stdev_return_pct": stdev(values),
        "avg_win_pct": average_win(values),
        "avg_loss_pct": average_loss(values),
        "profit_factor": profit_factor(values),
        "sharpe_per_signal": sharpe(values),
        "sortino_per_signal": sortino(values),
        "max_drawdown_pct": max_drawdown(values),
        "longest_losing_streak": longest_losing_streak(values),
        "best_return_pct": max(values) if values else None,
        "worst_return_pct": min(values) if values else None,
    }

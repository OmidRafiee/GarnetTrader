"""هشدار سلامت سیستم — تشخیص خرابی‌هایی که **بی‌صدا**اند.

**مسئله**

بدترین خرابی این ربات، خرابیِ پرسروصدا نیست. آن را در لاگ می‌بینید.
خرابیِ خطرناک این است:

* منبع داده قطع شده و هر پاس با خطا رد می‌شود، ولی ربات همچنان بالاست.
* یک استراتژی هر بار استثنا می‌دهد و `SignalGenerator` (درست) آن را
  می‌بلعد تا بقیه بمانند — پس هیچ‌کس نمی‌فهمد آن استراتژی مرده است.
* ده روز است هیچ سیگنالی نیامده. این می‌تواند «بازار شرایط نداشت» باشد،
  یا «فیلترها را سخت‌گیرانه‌تر از حد کردیم». تفاوتشان مهم است و از
  بیرون قابل تشخیص نیست.

در همه‌ی این‌ها ربات «سالم» به نظر می‌رسد: پروسه بالاست، خطایی به کاربر
نمی‌رسد، و سکوت با آرامش اشتباه گرفته می‌شود.

**رویکرد**

هر بررسی یک `HealthCheck` است که وضعیت و پیام برمی‌گرداند. هیچ بررسی‌ای
خودش پیام نمی‌فرستد — تصمیمِ اطلاع‌رسانی جای دیگری است. این جداسازی
باعث می‌شود بررسی‌ها بدون شبکه تست‌شدنی باشند.

**ضدهرزنامه:** هشداری که هر ۵ دقیقه تکرار شود، خوانده نمی‌شود. پس هر
هشدار یک `key` دارد و تا `cooldown` دوباره فرستاده نمی‌شود.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: وضعیت‌ها، به ترتیب شدت
OK = "ok"
WARNING = "warning"
CRITICAL = "critical"

_SEVERITY = {OK: 0, WARNING: 1, CRITICAL: 2}

_ICON = {OK: "✅", WARNING: "⚠️", CRITICAL: "🚨"}


@dataclass(frozen=True)
class HealthCheck:
    """نتیجه‌ی یک بررسی."""

    key: str
    status: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_alert(self) -> bool:
        return self.status != OK

    @property
    def severity(self) -> int:
        return _SEVERITY.get(self.status, 0)

    def format(self) -> str:
        return f"{_ICON.get(self.status, '')} {self.message}"


# ----------------------------------------------------------------------
# بررسی‌ها
# ----------------------------------------------------------------------
def check_signal_drought(
    last_signal_at: datetime | None,
    now: datetime,
    warning_days: int = 3,
    critical_days: int = 7,
    trading_days_only: int | None = None,
) -> HealthCheck:
    """سکوت طولانی: هیچ سیگنالی در چند روز گذشته.

    Args:
        trading_days_only: اگر داده شود، **روز معاملاتی** شمرده می‌شود نه
            روز تقویمی. بدون این، نوروز (۴ روز تعطیل) همیشه هشدار
            می‌داد — هشداری که درست نیست و اعتبار بقیه را هم می‌برد.
    """
    if last_signal_at is None:
        return HealthCheck(
            "signal_drought",
            WARNING,
            "هنوز هیچ سیگنالی ثبت نشده است.",
        )

    days = trading_days_only
    if days is None:
        days = max((now - last_signal_at).days, 0)

    detail = {"days": days, "last_signal_at": last_signal_at.isoformat()}
    if days >= critical_days:
        return HealthCheck(
            "signal_drought",
            CRITICAL,
            f"{days} روز معاملاتی است هیچ سیگنالی تولید نشده. "
            "یا شرایط بازار برقرار نیست، یا فیلترها بیش از حد سخت‌گیرانه‌اند.",
            detail,
        )
    if days >= warning_days:
        return HealthCheck(
            "signal_drought",
            WARNING,
            f"{days} روز معاملاتی است سیگنالی نیامده.",
            detail,
        )
    return HealthCheck("signal_drought", OK, f"آخرین سیگنال {days} روز پیش.", detail)


def check_data_source(
    consecutive_failures: int,
    warning_threshold: int = 2,
    critical_threshold: int = 5,
) -> HealthCheck:
    """قطعی منبع داده.

    شمارش **متوالی** است، نه کل: یک تایم‌اوت گذرا طبیعی است، ولی پنج
    شکست پشت‌سرهم یعنی منبع داده واقعاً قطع است.
    """
    detail = {"consecutive_failures": consecutive_failures}
    if consecutive_failures >= critical_threshold:
        return HealthCheck(
            "data_source",
            CRITICAL,
            f"منبع داده {consecutive_failures} بار پشت‌سرهم شکست خورد. "
            "ربات بالاست ولی داده‌ای نمی‌گیرد.",
            detail,
        )
    if consecutive_failures >= warning_threshold:
        return HealthCheck(
            "data_source",
            WARNING,
            f"منبع داده {consecutive_failures} بار پشت‌سرهم شکست خورد.",
            detail,
        )
    return HealthCheck("data_source", OK, "منبع داده در دسترس است.", detail)


def check_strategy_errors(
    error_counts: dict[str, int],
    threshold: int = 3,
) -> HealthCheck:
    """استراتژی‌ای که مرتب خطا می‌دهد.

    `SignalGenerator` عمداً خطای یک استراتژی را می‌بلعد تا بقیه بمانند —
    رفتار درستی است، ولی یعنی یک استراتژیِ **کاملاً مرده** هم بی‌صدا
    نادیده گرفته می‌شود. این بررسی همان سکوت را می‌شکند.
    """
    broken = {name: n for name, n in error_counts.items() if n >= threshold}
    if not broken:
        return HealthCheck("strategy_errors", OK, "همه‌ی استراتژی‌ها سالم اجرا شدند.")

    listed = "، ".join(f"{name} ({n} خطا)" for name, n in sorted(broken.items()))
    return HealthCheck(
        "strategy_errors",
        CRITICAL if len(broken) > 1 else WARNING,
        f"استراتژی با خطای مکرر: {listed}. سیگنال‌هایشان تولید نمی‌شود.",
        {"broken": broken},
    )


def check_stale_quotes(
    missing_quote_ratio: float | None,
    warning_ratio: float = 0.8,
) -> HealthCheck:
    """زنجیره‌ای که تقریباً هیچ مظنه‌ای ندارد.

    در روز تعطیل این طبیعی است (دیده شد: صفرِ مطلق). ولی در ساعت بازار
    یعنی چیزی در نگاشت داده شکسته — و ساختارها بی‌صدا خالی برمی‌گردند.
    """
    if missing_quote_ratio is None:
        return HealthCheck("stale_quotes", OK, "وضعیت مظنه‌ها بررسی نشد.")

    detail = {"missing_quote_ratio": round(missing_quote_ratio, 3)}
    if missing_quote_ratio >= warning_ratio:
        return HealthCheck(
            "stale_quotes",
            WARNING,
            f"{missing_quote_ratio * 100:.0f}٪ قراردادها مظنه‌ی دوطرفه ندارند. "
            "در ساعت بازار این یعنی مشکل داده؛ در تعطیلات طبیعی است.",
            detail,
        )
    return HealthCheck("stale_quotes", OK, "مظنه‌ها در دسترس‌اند.", detail)


# ----------------------------------------------------------------------
# ضدهرزنامه
# ----------------------------------------------------------------------
class AlertThrottle:
    """جلوی تکرار یک هشدار را تا مدت مشخص می‌گیرد.

    هشداری که هر ۵ دقیقه بیاید، خوانده نمی‌شود — و بعد هشدارِ واقعی هم
    در همان سیل گم می‌شود. پس هر `key` تا `cooldown` یک بار می‌آید.

    **تشدید وضعیت، cooldown را می‌شکند:** اگر هشدار از `warning` به
    `critical` برود، همان لحظه فرستاده می‌شود. سکوت درباره‌ی بدتر شدن،
    دقیقاً همان چیزی است که این ماژول قرار بود جلویش را بگیرد.
    """

    def __init__(
        self, path: str | Path | None = None, cooldown_hours: float = 6.0
    ) -> None:
        self.path = Path(path) if path else None
        self.cooldown = timedelta(hours=cooldown_hours)
        self._sent: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._sent = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError) as exc:
            logger.warning("حالت ضدهرزنامه خوانده نشد: %s", exc)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._sent, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("حالت ضدهرزنامه ذخیره نشد: %s", exc)

    def should_send(self, check: HealthCheck, now: datetime | None = None) -> bool:
        """آیا این هشدار باید فرستاده شود؟"""
        if not check.is_alert:
            return False

        now = now or datetime.now()
        previous = self._sent.get(check.key)
        if previous is None:
            return True

        # تشدید همیشه رد می‌شود از فیلتر
        if check.severity > _SEVERITY.get(str(previous.get("status")), 0):
            return True

        try:
            sent_at = datetime.fromisoformat(str(previous.get("sent_at")))
        except (TypeError, ValueError):
            return True
        return (now - sent_at) >= self.cooldown

    def record(self, check: HealthCheck, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self._sent[check.key] = {
            "status": check.status,
            "sent_at": now.isoformat(timespec="seconds"),
        }
        self._save()

    def clear(self, key: str) -> None:
        """وقتی مشکل حل شد، تاریخچه پاک می‌شود تا بار بعد فوراً هشدار برود."""
        if self._sent.pop(key, None) is not None:
            self._save()


# ----------------------------------------------------------------------
class HealthMonitor:
    """بررسی‌ها را اجرا می‌کند و شمارنده‌های متوالی را نگه می‌دارد.

    عمداً چیزی نمی‌فرستد: خروجی‌اش لیستی از `HealthCheck` است و
    تصمیم اطلاع‌رسانی جای دیگری گرفته می‌شود.
    """

    def __init__(self, thresholds: dict[str, Any] | None = None) -> None:
        config = thresholds or {}
        self.drought_warning_days = int(config.get("drought_warning_days", 3))
        self.drought_critical_days = int(config.get("drought_critical_days", 7))
        self.data_warning_failures = int(config.get("data_warning_failures", 2))
        self.data_critical_failures = int(config.get("data_critical_failures", 5))
        self.strategy_error_threshold = int(config.get("strategy_error_threshold", 3))
        self.missing_quote_ratio = float(config.get("missing_quote_ratio", 0.8))

        #: شکست‌های **متوالی** منبع داده
        self.consecutive_data_failures = 0
        #: خطای هر استراتژی از شروع اجرا
        self.strategy_errors: dict[str, int] = {}

    # -- ثبت رویداد ---------------------------------------------------
    def record_data_failure(self) -> None:
        self.consecutive_data_failures += 1

    def record_data_success(self) -> None:
        """شمارنده را صفر می‌کند — «متوالی» فقط با این معنا دارد."""
        self.consecutive_data_failures = 0

    def record_strategy_error(self, name: str) -> None:
        self.strategy_errors[name] = self.strategy_errors.get(name, 0) + 1

    def record_strategy_success(self, name: str) -> None:
        self.strategy_errors.pop(name, None)

    # -- اجرا ---------------------------------------------------------
    def run(
        self,
        last_signal_at: datetime | None = None,
        now: datetime | None = None,
        trading_days_since_signal: int | None = None,
        missing_quote_ratio: float | None = None,
    ) -> list[HealthCheck]:
        now = now or datetime.now()
        return [
            check_signal_drought(
                last_signal_at,
                now,
                self.drought_warning_days,
                self.drought_critical_days,
                trading_days_since_signal,
            ),
            check_data_source(
                self.consecutive_data_failures,
                self.data_warning_failures,
                self.data_critical_failures,
            ),
            check_strategy_errors(
                self.strategy_errors, self.strategy_error_threshold
            ),
            check_stale_quotes(missing_quote_ratio, self.missing_quote_ratio),
        ]

    @staticmethod
    def format_report(checks: list[HealthCheck], include_ok: bool = False) -> str:
        """گزارش متنی. پیش‌فرض فقط هشدارها، تا پیام کوتاه بماند."""
        shown = checks if include_ok else [c for c in checks if c.is_alert]
        if not shown:
            return "✅ همه‌چیز سالم است."
        return "\n".join(c.format() for c in shown)

"""پایه انتزاعی همه کانال‌های اطلاع‌رسانی سیگنال."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from signals.signal_model import Side, Signal

logger = logging.getLogger(__name__)


class BaseNotifier(ABC):
    """قرارداد ارسال سیگنال به یک کانال (تلگرام، کنسول، ...)."""

    name: str = "base"

    @abstractmethod
    def send(self, signal: Signal) -> bool:
        """ارسال یک سیگنال؛ True اگر موفق بود."""

    def send_many(self, signals: list[Signal]) -> int:
        """ارسال گروهی؛ تعداد ارسال‌های موفق را برمی‌گرداند."""
        sent = 0
        for signal in signals:
            try:
                if self.send(signal):
                    sent += 1
            except Exception:  # خرابی یک کانال، حلقه اصلی را نکشد
                logger.exception("ارسال سیگنال %s از کانال %s شکست خورد.", signal.symbol, self.name)
        return sent

    def send_text(self, text: str) -> bool:
        """پیام متنی آزاد (خطاها، شروع/پایان اجرا). پیش‌فرض: بدون عملیات."""
        logger.debug("[%s] %s", self.name, text)
        return True

    # ------------------------------------------------------------------
    @staticmethod
    def format_signal(signal: Signal) -> str:
        """قالب متنی مشترک سیگنال؛ کانال‌ها می‌توانند override کنند."""
        action = "🟢 خرید" if signal.side is Side.BUY else "🔴 فروش"
        kind = "Call" if signal.option_type.value == "call" else "Put"
        lines = [
            f"{action} {kind} | {signal.symbol}",
            f"استراتژی: {signal.strategy_name}",
            f"نماد پایه: {signal.underlying or '-'}"
            + (f" @ {signal.underlying_price:,.0f}" if signal.underlying_price else ""),
            f"استرایک: {signal.strike:,.0f}",
            f"سررسید: {signal.expiry} ({signal.days_to_expiry} روز)",
            f"پرمیوم پیشنهادی: {signal.suggested_price:,.0f}",
            f"تعداد پیشنهادی: {signal.suggested_qty} قرارداد",
        ]
        if signal.stop_loss is not None:
            lines.append(f"حد ضرر پیشنهادی: {signal.stop_loss:,.0f}")
        if signal.take_profit is not None:
            lines.append(f"حد سود پیشنهادی: {signal.take_profit:,.0f}")
        if signal.confidence is not None:
            lines.append(f"اعتماد: {signal.confidence:.2f}")
        data_source = signal.metadata.get("data_source")
        if data_source:
            lines.append(f"منبع داده: {data_source}")
        lines.extend(
            [
                f"دلیل: {signal.reason}",
                f"اعتبار تا: {signal.valid_until:%Y-%m-%d %H:%M}",
                "",
                "⚠️ این فقط یک سیگنال است؛ ثبت سفارش را خودتان دستی انجام دهید.",
            ]
        )
        return "\n".join(lines)

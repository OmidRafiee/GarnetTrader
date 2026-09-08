"""ارسال سیگنال به یک ربات تلگرام.

عمداً از HTTP خالص (`urllib`) استفاده می‌کنیم تا این کانال بدون نصب
`python-telegram-bot` هم کار کند؛ Bot API فقط یک درخواست POST ساده است.
اگر بعداً به قابلیت‌های پیشرفته‌تر نیاز شد، همین کلاس را جایگزین کنید.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from notifiers.base_notifier import BaseNotifier
from notifiers.telegram_commands import MuteState
from signals.signal_model import Signal

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 10


class TelegramNotifier(BaseNotifier):
    """ارسال پیام سیگنال به یک chat_id مشخص.

    Args:
        bot_token: توکن ربات از BotFather (از فایل تنظیمات یا متغیر محیطی)
        chat_id: شناسه چت/کانال مقصد
        disabled: اگر True باشد، پیام‌ها فقط لاگ می‌شوند (برای تست بدون توکن)
    """

    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        disabled: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
        mute: MuteState | None = None,
        ack_buttons: bool = True,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        # بدون توکن یا chat_id معتبر، کانال خودش را خاموش می‌کند تا حلقه اصلی نشکند.
        self.disabled = disabled or not bot_token or not chat_id
        #: وضعیت `/mute`، مشترک با `TelegramCommandBot`.
        #:
        #: عمداً از `disabled` جداست: `disabled` یعنی «این کانال قابل
        #: استفاده نیست» (توکن نداریم) ولی `mute` یعنی «کاربر موقتاً
        #: نمی‌خواهد بشنود». یکی گرفتنشان باعث می‌شد `/unmute` روی کانالی
        #: که توکن ندارد هم ادعای موفقیت کند.
        self.mute = mute
        #: دکمه‌های «اجرا کردم / رد کردم / دیدم» زیر هر سیگنال.
        #: خاموش‌کردنی است، چون در گروه‌های پرعضو هر کسی می‌تواند بزندش.
        self.ack_buttons = ack_buttons

    @property
    def muted(self) -> bool:
        return self.mute is not None and self.mute.muted

    def send(self, signal: Signal) -> bool:
        """سیگنال را با دکمه‌های تأیید دریافت می‌فرستد.

        دکمه‌ها به **خودِ همان پیام** چسبیده‌اند، پس پاسخ کاربر بدون ابهام
        به یک `signal_id` مشخص گره می‌خورد. جایگزینش این بود که کاربر
        شناسه را دستی تایپ کند — که عملاً یعنی هیچ‌وقت تأیید نمی‌گیریم.
        """
        markup = None
        if self.ack_buttons:
            from storage.acknowledgement import build_keyboard

            markup = build_keyboard(signal.signal_id)
        return self.send_text(self.format_signal(signal), reply_markup=markup)

    def send_text(self, text: str, reply_markup: dict | None = None) -> bool:
        if self.disabled:
            logger.info("[telegram/خاموش] %s", text.replace("\n", " | "))
            return False
        if self.muted:
            # رصد و ثبت ادامه دارد؛ فقط پیام نمی‌رود. سیگنال در دیتابیس
            # هست، پس `/signals` بعداً نشانش می‌دهد.
            logger.info("[telegram/mute] %s", text.replace("\n", " | "))
            return False
        payload: dict[str, object] = {"chat_id": self.chat_id, "text": text}
        if reply_markup is not None:
            # تلگرام این فیلد را به‌صورت JSON در بدنه‌ی form می‌خواهد
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self._post("sendMessage", payload)

    # ------------------------------------------------------------------
    def _post(self, method: str, payload: dict[str, object]) -> bool:
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/{method}"
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
                if not body.get("ok"):
                    logger.error("تلگرام درخواست را نپذیرفت: %s", body)
                    return False
                return True
        except urllib.error.URLError as exc:
            logger.error("ارسال به تلگرام ناموفق بود: %s", exc)
            return False
        except (ValueError, OSError) as exc:  # پاسخ نامعتبر یا خطای شبکه
            logger.error("خطای غیرمنتظره در ارسال تلگرام: %s", exc)
            return False

    def check_connection(self) -> bool:
        """اعتبارسنجی توکن با `getMe` — برای اجرای اولیه مفید است."""
        if self.disabled:
            return False
        return self._post("getMe", {})

"""دستورهای تلگرام: `/signals`, `/status`, `/mute`, `/unmute`, `/report`.

**چرا long-polling و نه webhook**

webhook به یک آدرس عمومی HTTPS نیاز دارد. این ربات روی لپ‌تاپ کاربر اجرا
می‌شود، پس long-polling (`getUpdates`) تنها راهی است که بدون دامنه، بدون
گواهی و بدون باز کردن پورت کار می‌کند.

**مرزهای امنیتی — مهم‌ترین بخش این ماژول**

۱. **هیچ دستوری سفارش ثبت نمی‌کند.** این ماژول `execution` را import
   نمی‌کند و گارد AST پروژه آن را تضمین می‌کند. تنها دستور «نویسنده»،
   `/mute` است که فقط جلوی *ارسال اعلان* را می‌گیرد.

۲. **فقط `chat_id` پیکربندی‌شده پذیرفته می‌شود.** توکن ربات اگر لو برود،
   هر کسی می‌تواند به ربات پیام بدهد. بدون این فیلتر، یک غریبه می‌توانست
   وضعیت حساب و سیگنال‌های شما را بخواند.

۳. **`offset` نگه داشته می‌شود** تا یک دستور دوبار اجرا نشود.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


@dataclass
class CommandContext:
    """داده‌ای که دستورها می‌توانند بخوانند — عمداً همه‌اش «تابع».

    هیچ کلاینت واقعی‌ای اینجا نمی‌نشیند: این لایه نباید بداند دیتابیس و
    بازار چطور کار می‌کنند، و مهم‌تر، نباید بتواند چیزی جز همین‌ها را صدا
    بزند. تابع‌ها را `bootstrap` پر می‌کند.
    """

    #: آخرین سیگنال‌ها → لیست دیکشنری خوانا
    recent_signals: Callable[[int], list[dict[str, Any]]] | None = None
    #: وضعیت کلی → دیکشنری
    status: Callable[[], dict[str, Any]] | None = None
    #: خلاصه‌ی عملکرد → دیکشنری معیارها
    performance: Callable[[], dict[str, Any]] | None = None


@dataclass
class MuteState:
    """وضعیت خاموشی اعلان‌ها، با تداوم روی دیسک.

    روی دیسک ذخیره می‌شود چون در غیر این صورت هر ری‌استارت ربات، `/mute`
    را بی‌صدا لغو می‌کرد — و کاربر فکر می‌کرد خاموش است.
    """

    path: Path | None = None
    _muted: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._muted = bool(data.get("muted", False))
            except (OSError, ValueError) as exc:
                logger.warning("وضعیت mute خوانده نشد: %s", exc)

    @property
    def muted(self) -> bool:
        return self._muted

    def set(self, muted: bool) -> None:
        self._muted = bool(muted)
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"muted": self._muted}), encoding="utf-8"
            )
        except OSError as exc:
            # نتوانستن ذخیره، خودِ خاموشی را باطل نمی‌کند — فقط تا
            # ری‌استارت بعدی می‌ماند. ولی کاربر باید بداند.
            logger.warning("وضعیت mute ذخیره نشد: %s", exc)


class TelegramCommandBot:
    """دستورهای تلگرام را می‌خواند و جواب می‌دهد.

    Args:
        bot_token: توکن ربات
        allowed_chat_id: **تنها** چتی که دستورهایش پذیرفته می‌شود
        context: تابع‌های خواندن داده
        mute: وضعیت خاموشی مشترک با notifier
        state_path: مسیر ذخیره‌ی `offset` (تا دستور تکرار نشود)
    """

    def __init__(
        self,
        bot_token: str,
        allowed_chat_id: str,
        context: CommandContext | None = None,
        mute: MuteState | None = None,
        state_path: str | Path | None = None,
        timeout: int = 10,
        ack_store: Any | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_chat_id = str(allowed_chat_id).strip()
        self.context = context or CommandContext()
        self.mute = mute or MuteState()
        self.timeout = timeout
        self._state_path = Path(state_path) if state_path else None
        self._offset = self._load_offset()
        #: محل ثبت تأیید دریافت. `None` یعنی دکمه‌ها ثبت نمی‌شوند —
        #: ولی همچنان به تلگرام جواب داده می‌شود، وگرنه دکمه برای کاربر
        #: تا ابد در حال چرخیدن می‌ماند.
        self.ack_store = ack_store

        self.commands: dict[str, Callable[[list[str]], str]] = {
            "start": self._cmd_help,
            "help": self._cmd_help,
            "signals": self._cmd_signals,
            "status": self._cmd_status,
            "report": self._cmd_report,
            "mute": self._cmd_mute,
            "unmute": self._cmd_unmute,
        }

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.allowed_chat_id)

    # -- تداوم offset -------------------------------------------------
    def _load_offset(self) -> int:
        if self._state_path is None or not self._state_path.exists():
            return 0
        try:
            return int(
                json.loads(self._state_path.read_text(encoding="utf-8")).get(
                    "offset", 0
                )
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("offset تلگرام خوانده نشد: %s", exc)
            return 0

    def _save_offset(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"offset": self._offset}), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("offset تلگرام ذخیره نشد: %s", exc)

    # -- HTTP ---------------------------------------------------------
    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/{method}"
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            if not body.get("ok"):
                logger.error("تلگرام %s را نپذیرفت: %s", method, body)
                return None
            return body
        except (urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("درخواست %s تلگرام ناموفق بود: %s", method, exc)
            return None

    def send_text(self, text: str) -> bool:
        return (
            self._call("sendMessage", {"chat_id": self.allowed_chat_id, "text": text})
            is not None
        )

    # -- حلقه ---------------------------------------------------------
    def poll_once(self, limit: int = 20) -> int:
        """یک بار `getUpdates` و پاسخ به دستورها.

        Returns:
            تعداد دستورهای اجراشده.
        """
        if not self.enabled:
            return 0

        body = self._call(
            "getUpdates",
            {"offset": self._offset, "limit": limit, "timeout": 0},
        )
        if body is None:
            return 0

        handled = 0
        for update in body.get("result") or []:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                # حتی اگر پیام را رد کنیم، offset جلو می‌رود؛ وگرنه یک
                # پیام غریبه حلقه را برای همیشه گیر می‌اندازد.
                self._offset = max(self._offset, update_id + 1)

            # دکمه‌های inline از راه `callback_query` می‌آیند، نه `message`
            if update.get("callback_query"):
                if self._handle_callback(update["callback_query"]):
                    handled += 1
                continue

            reply = self._handle_update(update)
            if reply is not None:
                self.send_text(reply)
                handled += 1

        self._save_offset()
        return handled

    def _handle_callback(self, query: dict[str, Any]) -> bool:
        """پاسخ به دکمه‌ی تأیید دریافت.

        **همیشه** `answerCallbackQuery` صدا زده می‌شود، حتی وقتی تأیید را
        رد می‌کنیم: تلگرام تا آن پاسخ نرسد، دکمه را برای کاربر در حال
        چرخیدن نگه می‌دارد. سکوت اینجا یعنی رابط خراب به نظر می‌رسد.
        """
        from storage.acknowledgement import ACTION_LABELS, parse_callback

        query_id = str(query.get("id") or "")
        chat_id = str(((query.get("message") or {}).get("chat") or {}).get("id", ""))

        # همان مرز امنیتیِ دستورها: توکن لو رفته نباید داده‌ی ما را بنویسد
        if chat_id != self.allowed_chat_id:
            logger.warning("callback از چت غیرمجاز %s نادیده گرفته شد.", chat_id)
            self._answer_callback(query_id, "این چت مجاز نیست.")
            return False

        parsed = parse_callback(query.get("data", ""))
        if parsed is None:
            self._answer_callback(query_id, "دکمه‌ی ناشناخته.")
            return False

        action, signal_id = parsed
        if self.ack_store is None:
            self._answer_callback(query_id, "ثبت تأیید فعال نیست.")
            return False

        try:
            self.ack_store.record(signal_id, action, source="telegram")
        except Exception as exc:  # ثبت نشدن تأیید نباید حلقه را بخواباند
            logger.warning("ثبت تأیید %s ناموفق بود: %s", signal_id, exc)
            self._answer_callback(query_id, "ثبت نشد؛ دوباره تلاش کنید.")
            return False

        self._answer_callback(query_id, f"ثبت شد: {ACTION_LABELS.get(action, action)}")
        return True

    def _answer_callback(self, query_id: str, text: str) -> None:
        """به تلگرام می‌گوید دکمه دیده شد (وگرنه تا ابد می‌چرخد)."""
        if query_id:
            self._call("answerCallbackQuery", {"callback_query_id": query_id, "text": text})

    def _handle_update(self, update: dict[str, Any]) -> str | None:
        """متن پاسخ، یا `None` اگر این پیام به ما مربوط نیست."""
        message = update.get("message") or update.get("edited_message") or {}
        chat_id = str((message.get("chat") or {}).get("id", "")).strip()
        text = str(message.get("text") or "").strip()

        if not text.startswith("/"):
            return None

        # مرز امنیتی: توکن لو رفته نباید به داده‌ی کاربر برسد
        if chat_id != self.allowed_chat_id:
            logger.warning("دستور تلگرام از چت غیرمجاز %s نادیده گرفته شد.", chat_id)
            return None

        # `/signals@MyBot 5` → نام دستور و آرگومان‌ها
        parts = text.split()
        name = parts[0].lstrip("/").split("@")[0].lower()
        args = parts[1:]

        handler = self.commands.get(name)
        if handler is None:
            return f"دستور ناشناخته: /{name}\n\n{self._cmd_help([])}"

        try:
            return handler(args)
        except Exception as exc:  # یک دستور خراب نباید حلقه را بخواباند
            logger.exception("اجرای دستور /%s شکست خورد.", name)
            return f"اجرای /{name} با خطا مواجه شد: {exc}"

    # -- دستورها ------------------------------------------------------
    def _cmd_help(self, _args: list[str]) -> str:
        return (
            "دستورهای موجود:\n"
            "/signals [تعداد] — آخرین سیگنال‌ها (پیش‌فرض ۵)\n"
            "/status — وضعیت بازار و منبع داده\n"
            "/report — خلاصه‌ی عملکرد سیگنال‌ها\n"
            "/mute — خاموش کردن اعلان‌ها\n"
            "/unmute — روشن کردن اعلان‌ها\n\n"
            "⚠️ این ربات هیچ سفارشی ثبت نمی‌کند و راهی به لایه‌ی اجرا ندارد."
        )

    def _cmd_signals(self, args: list[str]) -> str:
        if self.context.recent_signals is None:
            return "خواندن سیگنال‌ها در دسترس نیست."

        limit = 5
        if args:
            try:
                limit = max(1, min(int(args[0]), 20))
            except ValueError:
                return f"تعداد نامعتبر: «{args[0]}»"

        signals = self.context.recent_signals(limit)
        if not signals:
            return "هنوز سیگنالی ثبت نشده."

        lines = [f"آخرین {len(signals)} سیگنال:"]
        for item in signals:
            side = "خرید" if item.get("side") == "buy" else "فروش"
            kind = "Call" if item.get("option_type") == "call" else "Put"
            lines.append(
                f"• {side} {kind} {item.get('symbol', '?')} "
                f"| اعمال {_money(item.get('strike'))} "
                f"| پرمیوم {_money(item.get('suggested_price'))} "
                f"| {item.get('strategy_name', '?')}"
            )
        return "\n".join(lines)

    def _cmd_status(self, _args: list[str]) -> str:
        if self.context.status is None:
            return "وضعیت در دسترس نیست."

        data = self.context.status()
        market = data.get("market_open")
        market_text = (
            "باز" if market is True else "بسته" if market is False else "نامعلوم"
        )
        lines = [
            f"بازار: {market_text}",
            f"سیگنال ذخیره‌شده: {_money(data.get('signal_count'))}",
            f"منبع داده: {data.get('market_data_provider')}"
            f"+{data.get('option_chain_provider')}",
        ]
        if data.get("today_jalali"):
            lines.append(f"امروز: {data['today_jalali']}")
        if data.get("next_trading_day"):
            lines.append(f"روز معاملاتی بعدی: {data['next_trading_day']}")
        lines.append(f"اعلان‌ها: {'خاموش 🔇' if self.mute.muted else 'روشن 🔔'}")
        return "\n".join(lines)

    def _cmd_report(self, _args: list[str]) -> str:
        if self.context.performance is None:
            return "گزارش عملکرد در دسترس نیست."

        m = self.context.performance()
        if not m.get("total"):
            return "هیچ سیگنالی هنوز ارزیابی نشده."

        def num(value: Any, suffix: str = "") -> str:
            # `None` یعنی نمونه کافی نبود — نه صفر
            return "نامعلوم" if value is None else f"{value:+.2f}{suffix}"

        return "\n".join(
            [
                f"سیگنال ارزیابی‌شده: {m['total']}"
                f" (برد {m.get('wins', 0)} / باخت {m.get('losses', 0)})",
                f"نرخ برد: {num(m.get('win_rate_pct'), '٪')}",
                f"انتظار ریاضی: {num(m.get('expectancy_pct'), '٪')}",
                f"ضریب سود: {num(m.get('profit_factor'))}",
                f"حداکثر افت: {num(m.get('max_drawdown_pct'), '٪')}",
            ]
        )

    def _cmd_mute(self, _args: list[str]) -> str:
        self.mute.set(True)
        return (
            "🔇 اعلان‌ها خاموش شد.\n"
            "رصد بازار و ثبت سیگنال ادامه دارد؛ فقط پیام فرستاده نمی‌شود.\n"
            "با /unmute روشن کنید."
        )

    def _cmd_unmute(self, _args: list[str]) -> str:
        self.mute.set(False)
        return "🔔 اعلان‌ها روشن شد."


def _money(value: Any) -> str:
    """عدد با جداکننده‌ی هزار؛ `None` را «—» نشان می‌دهد."""
    if value is None:
        return "—"
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)

"""تست دستورهای تلگرام.

تمرکز روی سه چیزی که می‌توانند بی‌صدا خطرناک شوند:

۱. **فیلتر `chat_id`** — توکن ربات اگر لو برود، بدون این فیلتر هر غریبه‌ای
   می‌تواند سیگنال‌ها و وضعیت حساب را بخواند.
۲. **`/mute` واقعاً باید ارسال را قطع کند** — نه اینکه فقط تأیید بدهد.
۳. **`offset`** — دستور تکراری یعنی یک `/mute` می‌تواند بی‌دلیل دوبار اجرا
   شود، و یک پیام غریبه می‌تواند حلقه را برای همیشه گیر بیندازد.
"""

from __future__ import annotations

import json

import pytest

from notifiers.telegram_commands import (
    CommandContext,
    MuteState,
    TelegramCommandBot,
)
from notifiers.telegram_notifier import TelegramNotifier

CHAT = "12345"
OTHER_CHAT = "99999"


class _FakeApi:
    """جای `getUpdates`/`sendMessage` را می‌گیرد و همه را ثبت می‌کند."""

    def __init__(self, updates: list[dict] | None = None):
        self.updates = updates or []
        self.sent: list[str] = []
        self.calls: list[str] = []

    def __call__(self, method: str, payload: dict):
        self.calls.append(method)
        if method == "getUpdates":
            self.last_offset = payload.get("offset")
            return {"ok": True, "result": self.updates}
        if method == "sendMessage":
            self.sent.append(str(payload.get("text", "")))
            return {"ok": True}
        return {"ok": True}


def _message(text: str, chat_id: str = CHAT, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "text": text},
    }


def _bot(api: _FakeApi, tmp_path=None, **kwargs) -> TelegramCommandBot:
    bot = TelegramCommandBot(
        bot_token="test-token",
        allowed_chat_id=CHAT,
        state_path=(tmp_path / "offset.json") if tmp_path else None,
        **kwargs,
    )
    bot._call = api  # type: ignore[method-assign]
    return bot


# ----------------------------------------------------------------------
# مرز امنیتی — مهم‌ترین تست این فایل
# ----------------------------------------------------------------------
def test_command_from_another_chat_is_ignored():
    """توکن لو رفته نباید به داده‌ی کاربر برسد."""
    api = _FakeApi([_message("/signals", chat_id=OTHER_CHAT)])
    bot = _bot(api)
    assert bot.poll_once() == 0
    assert api.sent == [], "به چت غیرمجاز نباید پاسخی برود"


def test_offset_advances_even_for_a_rejected_message():
    """وگرنه یک پیام غریبه حلقه را برای همیشه گیر می‌اندازد."""
    api = _FakeApi([_message("/signals", chat_id=OTHER_CHAT, update_id=7)])
    bot = _bot(api)
    bot.poll_once()
    assert bot._offset == 8


def test_bot_without_token_is_disabled():
    bot = TelegramCommandBot(bot_token="", allowed_chat_id=CHAT)
    assert bot.enabled is False
    assert bot.poll_once() == 0


def test_bot_without_chat_id_is_disabled():
    assert TelegramCommandBot(bot_token="t", allowed_chat_id="").enabled is False


def test_commands_do_not_import_the_execution_layer():
    """قرارداد سخت پروژه: هیچ مسیری به لایه‌ی اجرا نیست.

    با AST بررسی می‌شود و نه با جستجوی متن، چون خودِ docstring این ماژول
    کلمه‌ی `execution` را دارد — آن توضیحِ همین تضمین است، نه نقضش.
    (گارد سراسری در `test_signal_generator.py` هم این فایل را پوشش
    می‌دهد؛ این تست صریح‌تر است و همراه خودِ ماژول می‌ماند.)
    """
    import ast
    from pathlib import Path

    source = Path("notifiers/telegram_commands.py")
    if not source.exists():  # pragma: no cover - اجرای از ریشه‌ی دیگر
        source = Path(__file__).resolve().parent.parent / source
    tree = ast.parse(source.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "execution" not in imported


# ----------------------------------------------------------------------
# `/mute` — باید واقعاً ارسال را قطع کند
# ----------------------------------------------------------------------
def test_mute_actually_blocks_the_notifier(tmp_path):
    """تأیید گرفتن کافی نیست؛ پیام هم نباید برود."""
    mute = MuteState(path=tmp_path / "mute.json")
    notifier = TelegramNotifier(bot_token="t", chat_id=CHAT, mute=mute)

    posted: list[dict] = []
    notifier._post = lambda method, payload: posted.append(payload) or True  # type: ignore[method-assign,assignment]

    assert notifier.send_text("قبل از mute") is True
    assert len(posted) == 1

    api = _FakeApi([_message("/mute")])
    bot = _bot(api, mute=mute)
    bot.poll_once()

    assert mute.muted is True
    assert notifier.send_text("بعد از mute") is False
    assert len(posted) == 1, "در حالت mute نباید پیامی ارسال شود"


def test_unmute_restores_sending(tmp_path):
    mute = MuteState(path=tmp_path / "mute.json")
    mute.set(True)
    notifier = TelegramNotifier(bot_token="t", chat_id=CHAT, mute=mute)
    notifier._post = lambda method, payload: True  # type: ignore[method-assign,assignment]

    assert notifier.send_text("x") is False

    api = _FakeApi([_message("/unmute")])
    _bot(api, mute=mute).poll_once()

    assert mute.muted is False
    assert notifier.send_text("x") is True


def test_mute_survives_a_restart(tmp_path):
    """بدون تداوم، هر ری‌استارت `/mute` را بی‌صدا لغو می‌کرد."""
    path = tmp_path / "mute.json"
    MuteState(path=path).set(True)
    assert MuteState(path=path).muted is True


def test_mute_without_a_path_still_works_in_memory():
    mute = MuteState()
    mute.set(True)
    assert mute.muted is True


def test_corrupt_mute_file_is_ignored(tmp_path):
    path = tmp_path / "mute.json"
    path.write_text("{ not json", encoding="utf-8")
    assert MuteState(path=path).muted is False


def test_mute_is_separate_from_disabled():
    """`disabled` یعنی «توکن نداریم»؛ `mute` یعنی «موقتاً نمی‌خواهم بشنوم».

    یکی گرفتنشان باعث می‌شد `/unmute` روی کانالی که توکن ندارد هم ادعای
    موفقیت کند.
    """
    notifier = TelegramNotifier(bot_token="", chat_id="", mute=MuteState())
    assert notifier.disabled is True
    assert notifier.muted is False
    assert notifier.send_text("x") is False


def test_notifier_without_mute_state_is_never_muted():
    notifier = TelegramNotifier(bot_token="t", chat_id=CHAT)
    assert notifier.muted is False


# ----------------------------------------------------------------------
# دستورهای خواندنی
# ----------------------------------------------------------------------
def test_signals_command_lists_recent_signals():
    rows = [
        {
            "symbol": "ضخود7136",
            "side": "buy",
            "option_type": "call",
            "strike": 750.0,
            "suggested_price": 61.0,
            "strategy_name": "ma_cross",
        }
    ]
    api = _FakeApi([_message("/signals")])
    bot = _bot(api, context=CommandContext(recent_signals=lambda n: rows[:n]))
    bot.poll_once()

    assert len(api.sent) == 1
    text = api.sent[0]
    assert "ضخود7136" in text
    assert "خرید" in text and "Call" in text
    assert "ma_cross" in text


def test_signals_command_respects_a_count_argument():
    seen: list[int] = []

    def recent(limit: int) -> list[dict]:
        seen.append(limit)
        return []

    api = _FakeApi([_message("/signals 3")])
    _bot(api, context=CommandContext(recent_signals=recent)).poll_once()
    assert seen == [3]


def test_signals_count_is_clamped():
    """عدد بزرگ نباید یک پیام غول تولید کند."""
    seen: list[int] = []
    api = _FakeApi([_message("/signals 9999")])
    _bot(
        api,
        context=CommandContext(recent_signals=lambda n: seen.append(n) or []),
    ).poll_once()
    assert seen == [20]


def test_signals_rejects_a_non_numeric_count():
    api = _FakeApi([_message("/signals abc")])
    _bot(api, context=CommandContext(recent_signals=lambda n: [])).poll_once()
    assert "نامعتبر" in api.sent[0]


def test_signals_says_so_when_there_are_none():
    api = _FakeApi([_message("/signals")])
    _bot(api, context=CommandContext(recent_signals=lambda n: [])).poll_once()
    assert "هنوز سیگنالی ثبت نشده" in api.sent[0]


def test_status_command_reports_market_and_source():
    data = {
        "market_open": False,
        "signal_count": 42,
        "market_data_provider": "tsetmc",
        "option_chain_provider": "tsetmc",
        "today_jalali": "1405/06/16",
        "next_trading_day": "2026-09-07 (1405/06/16)",
    }
    api = _FakeApi([_message("/status")])
    _bot(api, context=CommandContext(status=lambda: data)).poll_once()

    text = api.sent[0]
    assert "بسته" in text
    assert "tsetmc" in text
    assert "1405/06/16" in text


def test_status_shows_mute_state(tmp_path):
    mute = MuteState(path=tmp_path / "m.json")
    mute.set(True)
    api = _FakeApi([_message("/status")])
    _bot(
        api,
        mute=mute,
        context=CommandContext(status=lambda: {"market_open": True}),
    ).poll_once()
    assert "خاموش" in api.sent[0]


def test_status_says_unknown_when_market_state_is_none():
    """`None` یعنی نتوانستیم بفهمیم — نه «بسته»."""
    api = _FakeApi([_message("/status")])
    _bot(
        api, context=CommandContext(status=lambda: {"market_open": None})
    ).poll_once()
    assert "نامعلوم" in api.sent[0]


def test_report_command_shows_metrics():
    metrics = {
        "total": 10,
        "wins": 6,
        "losses": 4,
        "win_rate_pct": 60.0,
        "expectancy_pct": 1.5,
        "profit_factor": 1.4,
        "max_drawdown_pct": 12.0,
    }
    api = _FakeApi([_message("/report")])
    _bot(api, context=CommandContext(performance=lambda: metrics)).poll_once()

    text = api.sent[0]
    assert "انتظار ریاضی" in text
    assert "ضریب سود" in text
    assert "حداکثر افت" in text


def test_report_says_unknown_not_zero_for_missing_metrics():
    """قرارداد `None` در برابر صفر، در تلگرام هم برقرار است."""
    metrics = {"total": 3, "wins": 3, "losses": 0, "profit_factor": None}
    api = _FakeApi([_message("/report")])
    _bot(api, context=CommandContext(performance=lambda: metrics)).poll_once()
    assert "نامعلوم" in api.sent[0]


def test_report_with_nothing_evaluated():
    api = _FakeApi([_message("/report")])
    _bot(api, context=CommandContext(performance=lambda: {"total": 0})).poll_once()
    assert "ارزیابی نشده" in api.sent[0]


def test_commands_without_a_data_source_say_so():
    """بدون context، باید صریح بگوید در دسترس نیست — نه لیست خالی."""
    api = _FakeApi([_message("/signals"), _message("/status", update_id=2)])
    _bot(api).poll_once()
    assert all("در دسترس نیست" in text for text in api.sent)


# ----------------------------------------------------------------------
# تحلیل دستور
# ----------------------------------------------------------------------
def test_help_lists_the_commands():
    api = _FakeApi([_message("/help")])
    _bot(api).poll_once()
    for command in ("/signals", "/status", "/report", "/mute"):
        assert command in api.sent[0]


def test_help_states_that_no_order_is_placed():
    api = _FakeApi([_message("/start")])
    _bot(api).poll_once()
    assert "سفارشی ثبت نمی‌کند" in api.sent[0]


def test_unknown_command_shows_help():
    api = _FakeApi([_message("/nope")])
    _bot(api).poll_once()
    assert "ناشناخته" in api.sent[0]
    assert "/signals" in api.sent[0]


def test_bot_username_suffix_is_stripped():
    """در گروه، تلگرام دستور را `/status@MyBot` می‌فرستد."""
    api = _FakeApi([_message("/status@GarnetBot")])
    _bot(api, context=CommandContext(status=lambda: {"market_open": True})).poll_once()
    assert "بازار" in api.sent[0]


def test_plain_text_is_not_a_command():
    api = _FakeApi([{"update_id": 1, "message": {"chat": {"id": CHAT}, "text": "سلام"}}])
    assert _bot(api).poll_once() == 0
    assert api.sent == []


def test_edited_message_is_handled():
    api = _FakeApi(
        [{"update_id": 1, "edited_message": {"chat": {"id": CHAT}, "text": "/help"}}]
    )
    assert _bot(api).poll_once() == 1


def test_update_without_a_message_is_skipped():
    api = _FakeApi([{"update_id": 1, "channel_post": {"text": "/help"}}])
    assert _bot(api).poll_once() == 0


def test_a_failing_command_does_not_stop_the_loop():
    """یک دستور خراب نباید حلقه‌ی رصد بازار را بخواباند."""

    def boom(_limit: int) -> list[dict]:
        raise RuntimeError("دیتابیس قفل است")

    api = _FakeApi([_message("/signals")])
    bot = _bot(api, context=CommandContext(recent_signals=boom))
    assert bot.poll_once() == 1
    assert "خطا" in api.sent[0]


# ----------------------------------------------------------------------
# offset
# ----------------------------------------------------------------------
def test_offset_prevents_reprocessing(tmp_path):
    api = _FakeApi([_message("/help", update_id=5)])
    bot = _bot(api, tmp_path=tmp_path)
    bot.poll_once()

    # ربات تازه با همان فایل حالت: باید از ۶ شروع کند
    fresh = _bot(_FakeApi([]), tmp_path=tmp_path)
    assert fresh._offset == 6


def test_offset_is_persisted_to_disk(tmp_path):
    path = tmp_path / "offset.json"
    bot = _bot(_FakeApi([_message("/help", update_id=11)]), tmp_path=tmp_path)
    bot.poll_once()
    assert json.loads(path.read_text(encoding="utf-8"))["offset"] == 12


def test_corrupt_offset_file_starts_from_zero(tmp_path):
    path = tmp_path / "offset.json"
    path.write_text("garbage", encoding="utf-8")
    assert _bot(_FakeApi([]), tmp_path=tmp_path)._offset == 0


def test_network_failure_returns_zero_handled():
    """قطعی تلگرام نباید استثنا بدهد؛ فقط این دور را رد می‌کند."""
    bot = TelegramCommandBot(bot_token="t", allowed_chat_id=CHAT)
    bot._call = lambda method, payload: None  # type: ignore[method-assign]
    assert bot.poll_once() == 0


# ----------------------------------------------------------------------
# wiring
# ----------------------------------------------------------------------
def test_command_bot_is_off_by_default():
    import bootstrap
    from config.loader import default_settings

    assert bootstrap.build_command_bot(default_settings()) is None


def test_mute_state_is_shared_across_builders():
    """اگر دو نمونه ساخته شود، `/mute` روی ارسال اثر نمی‌کند."""
    import bootstrap
    from config.loader import default_settings

    settings = default_settings()
    assert bootstrap.build_mute_state(settings) is bootstrap.build_mute_state(settings)


def test_commands_need_both_enabled_flags(monkeypatch):
    """`enabled` برای اعلان است و `commands_enabled` برای ربات دوطرفه."""
    import bootstrap
    from config.loader import default_settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)

    settings = default_settings()
    settings["notifiers"]["telegram"]["enabled"] = True
    assert bootstrap.build_command_bot(settings) is None, "بدون commands_enabled"

    settings["notifiers"]["telegram"]["commands_enabled"] = True
    assert bootstrap.build_command_bot(settings) is not None


def test_credentials_prefer_the_environment(monkeypatch):
    """توکن هرگز نباید از فایل گیت‌شده بیاید."""
    import bootstrap

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env")
    token, _ = bootstrap.telegram_credentials({"bot_token": "from-file"})
    assert token == "from-env"


def test_placeholder_token_is_treated_as_missing():
    """`settings.example.yaml` مقدار `<...>` دارد؛ نباید معتبر شمرده شود."""
    import bootstrap

    token, _ = bootstrap.telegram_credentials({"bot_token": "<توکن اینجا>"})
    assert token == ""


@pytest.mark.parametrize("interval", [1, 7])
def test_wait_polls_commands_while_sleeping(monkeypatch, interval):
    """`/mute` نباید تا پاس بعدی (۵ دقیقه) بی‌جواب بماند."""
    import main

    polls: list[int] = []
    monkeypatch.setattr(main.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(main, "COMMAND_POLL_SECONDS", 1)

    class _Bot:
        def poll_once(self) -> int:
            polls.append(1)
            return 0

    main._wait(interval, _Bot())
    assert len(polls) == interval


def test_wait_without_a_command_bot_just_sleeps(monkeypatch):
    import main

    slept: list[float] = []
    monkeypatch.setattr(main.time, "sleep", lambda seconds: slept.append(seconds))
    main._wait(42, None)
    assert slept == [42]

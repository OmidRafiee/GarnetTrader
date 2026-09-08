"""تست تأیید دریافت سیگنال (دکمه‌های inline تلگرام).

**چرا این قابلیت جدا از `signal_outcomes` است**

آن جدول می‌گوید *بازار* چه گفت؛ این یکی می‌گوید *کاربر* چه کرد. سیگنالی
که کاربر هرگز ندیده و ضرر هم داده، شکستِ استراتژی نیست — شکستِ
اطلاع‌رسانی است. قاطی‌کردنشان هر دو گزارش را بی‌معنا می‌کند.

تمرکز تست‌ها روی چیزهایی است که بی‌صدا خراب می‌شوند:

* دکمه‌ی دوبار خورده (در تلگرام عادی است) نباید دو ردیف بسازد.
* `answerCallbackQuery` باید **همیشه** برود، حتی وقتی تأیید را رد
  می‌کنیم — وگرنه دکمه برای کاربر تا ابد می‌چرخد.
* چت غیرمجاز نباید بتواند داده‌ی ما را بنویسد.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from storage.acknowledgement import (
    ACK_SEEN,
    ACK_SKIPPED,
    ACK_TAKEN,
    ACTION_LABELS,
    AckStore,
    build_keyboard,
    parse_callback,
)
from storage.signal_log import SignalLog


@pytest.fixture
def store(tmp_path) -> AckStore:
    with AckStore(tmp_path / "signals.db") as ack:
        yield ack


def _seed_signals(tmp_path, count: int) -> Path:
    """چند سیگنال **واقعی** در دیتابیس، برای آمار."""
    from signals.signal_model import OptionType, Side, Signal

    db = tmp_path / "signals.db"
    log = SignalLog(db_path=db, jsonl_path=tmp_path / "signals.jsonl")
    try:
        for i in range(count):
            log.save(
                Signal(
                    signal_id=f"sig-{i}",
                    symbol=f"ضخود{i}",
                    option_type=OptionType.CALL,
                    side=Side.BUY,
                    strike=1000.0,
                    expiry=(datetime.now() + timedelta(days=30)).date(),
                    suggested_price=50.0,
                    suggested_qty=1,
                    reason="تست",
                    strategy_name="t",
                    created_at=datetime.now(),
                )
            )
    finally:
        log.close()
    return db


# ======================================================================
# ذخیره‌سازی
# ======================================================================
def test_no_acknowledgement_is_none_not_a_default(store):
    """«ندیدن» یک وضعیت نیست — نبودِ ردیف است.

    اگر پیش‌فرض `unseen` بود، هر سیگنال قدیمی یک ادعای صریح می‌شد که
    کاربر ندیده‌اش، در حالی که فقط نمی‌دانیم.
    """
    assert store.get("never-touched") is None


@pytest.mark.parametrize("action", [ACK_SEEN, ACK_TAKEN, ACK_SKIPPED])
def test_each_action_round_trips(store, action):
    store.record("sig-1", action)
    assert store.get("sig-1").action == action


def test_clicking_twice_keeps_one_row_and_the_last_decision(store):
    """در تلگرام دوبار زدن عادی است؛ نباید دو ردیف بسازد."""
    store.record("sig-1", ACK_SEEN)
    store.record("sig-1", ACK_TAKEN)

    assert len(store.recent()) == 1
    assert store.get("sig-1").action == ACK_TAKEN


def test_only_taken_counts_as_executed(store):
    """`seen` را اجرا حساب کردن، «نرخ اجرا» را بی‌معنا می‌کند."""
    store.record("a", ACK_TAKEN)
    store.record("b", ACK_SEEN)
    store.record("c", ACK_SKIPPED)

    assert store.get("a").is_executed
    assert not store.get("b").is_executed
    assert not store.get("c").is_executed


def test_invalid_action_is_rejected_loudly(store):
    """بی‌صدا پذیرفتنش یعنی گزارش روی مقداری حساب کند که کسی نمی‌شناسد."""
    with pytest.raises(ValueError, match="نامعتبر"):
        store.record("sig-1", "executed")


def test_recent_is_newest_first(store):
    now = datetime.now()
    store.record("old", ACK_SEEN, when=now - timedelta(hours=2))
    store.record("new", ACK_TAKEN, when=now)

    assert [a.signal_id for a in store.recent()] == ["new", "old"]


def test_note_and_source_are_kept(store):
    store.record("sig-1", ACK_SKIPPED, source="dashboard", note="اسپرد زیاد بود")
    ack = store.get("sig-1")
    assert ack.source == "dashboard"
    assert ack.note == "اسپرد زیاد بود"


def test_to_dict_is_json_friendly(store):
    import json

    store.record("sig-1", ACK_TAKEN)
    data = json.loads(json.dumps(store.get("sig-1").to_dict(), ensure_ascii=False))
    assert data["action"] == ACK_TAKEN
    assert data["label"] == ACTION_LABELS[ACK_TAKEN]


def test_store_survives_reopening(tmp_path):
    """تأیید باید بین اجراها بماند، وگرنه با هر ری‌استارت پاک می‌شد."""
    path = tmp_path / "signals.db"
    with AckStore(path) as first:
        first.record("sig-1", ACK_TAKEN)
    with AckStore(path) as second:
        assert second.get("sig-1").action == ACK_TAKEN


# ======================================================================
# آمار — `None` یعنی نامعلوم، نه صفر
# ======================================================================
def test_rates_are_none_when_there_is_nothing_to_divide_by(store):
    stats = store.stats()
    assert stats["delivery_rate_pct"] is None
    assert stats["execution_rate_pct"] is None


def test_delivery_and_execution_rates_are_separate(tmp_path):
    """دو سؤال متفاوت: «چند تا دیده شد» و «چند تا از دیده‌شده‌ها اجرا شد»."""
    db = _seed_signals(tmp_path, 4)
    with AckStore(db) as store:
        store.record("sig-0", ACK_TAKEN)
        store.record("sig-1", ACK_SKIPPED)
        stats = store.stats()

    assert stats["total_signals"] == 4
    assert stats["acknowledged"] == 2
    assert stats["unacknowledged"] == 2
    # ۲ از ۴ دیده شد، ولی از آن دو فقط یکی اجرا شد
    assert stats["delivery_rate_pct"] == 50.0
    assert stats["execution_rate_pct"] == 50.0


def test_unacknowledged_never_goes_negative(tmp_path):
    """تأییدِ سیگنالی که در جدول نیست نباید شمارش را منفی کند."""
    db = _seed_signals(tmp_path, 1)
    with AckStore(db) as store:
        store.record("sig-0", ACK_TAKEN)
        assert store.stats()["unacknowledged"] == 0


# ======================================================================
# صفحه‌کلید و callback
# ======================================================================
def test_keyboard_offers_all_three_actions():
    buttons = [b for row in build_keyboard("s1")["inline_keyboard"] for b in row]
    actions = {parse_callback(b["callback_data"])[0] for b in buttons}
    assert actions == {ACK_TAKEN, ACK_SKIPPED, ACK_SEEN}


def test_callback_data_fits_telegram_limit():
    """سقف ۶۴ بایت است؛ بیشتر شود، تلگرام دکمه را **بی‌صدا** رد می‌کند."""
    import uuid

    keyboard = build_keyboard(str(uuid.uuid4()))
    for row in keyboard["inline_keyboard"]:
        for button in row:
            assert len(button["callback_data"].encode("utf-8")) <= 64


def test_callback_round_trips():
    assert parse_callback(f"ack:{ACK_TAKEN}:sig-9") == (ACK_TAKEN, "sig-9")


@pytest.mark.parametrize(
    "data",
    ["", "garbage", "ack:taken", "other:taken:s1", "ack:bogus:s1", "ack:taken:", None],
)
def test_unknown_callbacks_are_refused(data):
    """callback ناشناخته نباید به‌عنوان تأیید ثبت شود."""
    assert parse_callback(data) is None


def test_signal_id_with_colons_survives():
    """`split(":", 2)` یعنی شناسه می‌تواند خودش دونقطه داشته باشد."""
    assert parse_callback("ack:seen:a:b:c") == (ACK_SEEN, "a:b:c")


# ======================================================================
# مسیر تلگرام
# ======================================================================
class _Bot:
    """ربات دستور با شبکه‌ی جعلی."""

    def __init__(self, tmp_path, ack_store=None, chat="42"):
        from notifiers.telegram_commands import TelegramCommandBot

        self.calls: list[tuple[str, dict]] = []
        self.bot = TelegramCommandBot(
            bot_token="token",
            allowed_chat_id=chat,
            ack_store=ack_store,
            state_path=tmp_path / "offset.json",
        )
        self.bot._call = self._call  # type: ignore[method-assign]

    def _call(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True, "result": []}

    def press(self, data, chat="42", query_id="q1"):
        return self.bot._handle_callback(
            {"id": query_id, "data": data, "message": {"chat": {"id": chat}}}
        )

    @property
    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]


def test_pressing_a_button_records_the_acknowledgement(tmp_path, store):
    bot = _Bot(tmp_path, ack_store=store)
    assert bot.press(f"ack:{ACK_TAKEN}:sig-9") is True
    assert store.get("sig-9").action == ACK_TAKEN


def test_telegram_is_always_answered(tmp_path, store):
    """تا `answerCallbackQuery` نرسد، دکمه برای کاربر می‌چرخد.

    پس حتی مسیرهای رد هم باید جواب بدهند — سکوت یعنی رابط خراب.
    """
    for label, bot, data, chat in [
        ("ok", _Bot(tmp_path, store), f"ack:{ACK_TAKEN}:s1", "42"),
        ("junk", _Bot(tmp_path, store), "garbage", "42"),
        ("foreign", _Bot(tmp_path, store), f"ack:{ACK_TAKEN}:s2", "999"),
        ("no store", _Bot(tmp_path, None), f"ack:{ACK_TAKEN}:s3", "42"),
    ]:
        bot.press(data, chat=chat)
        assert "answerCallbackQuery" in bot.methods, label


def test_a_foreign_chat_cannot_write_our_data(tmp_path, store):
    """همان مرز امنیتیِ دستورها، برای دکمه‌ها هم لازم است."""
    bot = _Bot(tmp_path, ack_store=store)
    assert bot.press(f"ack:{ACK_TAKEN}:sig-x", chat="999") is False
    assert store.get("sig-x") is None


def test_without_a_store_nothing_is_recorded_but_no_crash(tmp_path):
    bot = _Bot(tmp_path, ack_store=None)
    assert bot.press(f"ack:{ACK_TAKEN}:sig-1") is False


def test_a_failing_store_does_not_kill_the_loop(tmp_path):
    """ثبت نشدن تأیید نباید حلقه‌ی رصد را بخواباند."""

    class _Broken:
        def record(self, *args, **kwargs):
            raise RuntimeError("دیسک پر است")

    bot = _Bot(tmp_path, ack_store=_Broken())
    assert bot.press(f"ack:{ACK_TAKEN}:sig-1") is False
    assert "answerCallbackQuery" in bot.methods


def test_callback_updates_advance_the_offset(tmp_path, store):
    """وگرنه همان دکمه در هر پاس دوباره پردازش می‌شود."""
    bot = _Bot(tmp_path, ack_store=store)
    bot.bot._call = lambda method, payload: (  # type: ignore[method-assign]
        bot.calls.append((method, payload))
        or {
            "ok": True,
            "result": (
                [
                    {
                        "update_id": 7,
                        "callback_query": {
                            "id": "q1",
                            "data": f"ack:{ACK_TAKEN}:sig-7",
                            "message": {"chat": {"id": "42"}},
                        },
                    }
                ]
                if method == "getUpdates"
                else []
            ),
        }
    )
    bot.bot.poll_once()
    assert bot.bot._offset == 8
    assert store.get("sig-7").action == ACK_TAKEN


# ======================================================================
# نوتیفایر
# ======================================================================
def test_signal_message_carries_the_buttons():
    from notifiers.telegram_notifier import TelegramNotifier

    sent: dict = {}

    notifier = TelegramNotifier(bot_token="t", chat_id="42", ack_buttons=True)
    notifier._post = lambda method, payload: sent.update(payload) or True  # type: ignore

    from signals.signal_model import OptionType, Side, Signal

    notifier.send(
        Signal(
            signal_id="sig-42",
            symbol="ضخود7136",
            option_type=OptionType.CALL,
            side=Side.BUY,
            strike=1000.0,
            expiry=(datetime.now() + timedelta(days=30)).date(),
            suggested_price=50.0,
            suggested_qty=1,
            reason="تست",
            strategy_name="t",
            created_at=datetime.now(),
        )
    )
    assert "reply_markup" in sent
    assert "sig-42" in sent["reply_markup"]


def test_buttons_can_be_turned_off():
    """در گروه پرعضو، هر کسی می‌تواند دکمه را بزند."""
    from notifiers.telegram_notifier import TelegramNotifier
    from signals.signal_model import OptionType, Side, Signal

    sent: dict = {}
    notifier = TelegramNotifier(bot_token="t", chat_id="42", ack_buttons=False)
    notifier._post = lambda method, payload: sent.update(payload) or True  # type: ignore

    notifier.send(
        Signal(
            signal_id="sig-42",
            symbol="ضخود7136",
            option_type=OptionType.CALL,
            side=Side.BUY,
            strike=1000.0,
            expiry=(datetime.now() + timedelta(days=30)).date(),
            suggested_price=50.0,
            suggested_qty=1,
            reason="تست",
            strategy_name="t",
            created_at=datetime.now(),
        )
    )
    assert "reply_markup" not in sent


def test_plain_text_messages_carry_no_buttons():
    """گزارش و هشدار سلامت سیگنال نیستند؛ دکمه‌ی تأیید نمی‌خواهند."""
    from notifiers.telegram_notifier import TelegramNotifier

    sent: dict = {}
    notifier = TelegramNotifier(bot_token="t", chat_id="42")
    notifier._post = lambda method, payload: sent.update(payload) or True  # type: ignore

    notifier.send_text("گزارش روزانه")
    assert "reply_markup" not in sent

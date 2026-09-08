"""تأیید دریافت سیگنال — «دیدمش» و «اجرا کردم».

**چرا جدا از `signal_outcomes`؟**

آن جدول می‌گوید *بازار* چه گفت (برد/باخت). این یکی می‌گوید *کاربر* چه
کرد. دو سؤال کاملاً متفاوت‌اند و قاطی‌کردنشان گزارش را بی‌معنا می‌کند:

* سیگنالی که کاربر هرگز ندیده و در بازار هم ضرر داده، **شکستِ استراتژی
  نیست** — شکستِ اطلاع‌رسانی است.
* سیگنالی که کاربر دیده ولی عمداً اجرا نکرده، داده‌ی باارزشی است: یعنی
  فیلترها چیزی را رد نکرده‌اند که باید می‌کردند.

با یک جدول جدا، «نرخ برد» و «نرخ اجرا» مستقل قابل اندازه‌گیری می‌مانند.

**سه وضعیت، نه دو**

    `seen`     → دیدمش (ولی هنوز تصمیم نگرفته‌ام)
    `taken`    → اجرا کردم
    `skipped`  → دیدم و عمداً رد کردم

«ندیدنِ» سیگنال وضعیت نیست — نبودِ ردیف است. عمداً: اگر پیش‌فرض را
`unseen` می‌گذاشتیم، هر سیگنالِ قدیمی هم یک ادعای صریح می‌شد که کاربر
ندیده‌اش، در حالی که فقط نمی‌دانیم.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_acks (
    signal_id      TEXT PRIMARY KEY,
    acknowledged_at TEXT NOT NULL,
    action         TEXT NOT NULL,
    source         TEXT NOT NULL,
    note           TEXT,
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);
CREATE INDEX IF NOT EXISTS idx_acks_action ON signal_acks(action);
CREATE INDEX IF NOT EXISTS idx_acks_time   ON signal_acks(acknowledged_at);
"""

#: مقادیر مجاز ستون `action`
ACK_SEEN = "seen"
ACK_TAKEN = "taken"
ACK_SKIPPED = "skipped"

VALID_ACTIONS = frozenset({ACK_SEEN, ACK_TAKEN, ACK_SKIPPED})

#: متن دکمه‌ها به فارسی — همان چیزی که کاربر در تلگرام می‌بیند
ACTION_LABELS = {
    ACK_SEEN: "👀 دیدم",
    ACK_TAKEN: "✅ اجرا کردم",
    ACK_SKIPPED: "⏭ رد کردم",
}


@dataclass(frozen=True)
class Acknowledgement:
    """یک تأیید دریافت."""

    signal_id: str
    acknowledged_at: datetime
    action: str
    source: str = "telegram"
    note: str | None = None

    @property
    def is_executed(self) -> bool:
        """فقط `taken` یعنی واقعاً معامله شده.

        `seen` را اجرا حساب کردن، «نرخ اجرا» را بی‌معنا می‌کند.
        """
        return self.action == ACK_TAKEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "acknowledged_at": self.acknowledged_at.isoformat(),
            "action": self.action,
            "label": ACTION_LABELS.get(self.action, self.action),
            "source": self.source,
            "note": self.note,
        }


class AckStore:
    """ثبت و خواندن تأییدها. روی همان دیتابیس سیگنال‌ها می‌نشیند."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(ACK_SCHEMA)
        self._conn.commit()

    # -- نوشتن ----------------------------------------------------------
    def record(
        self,
        signal_id: str,
        action: str,
        source: str = "telegram",
        note: str | None = None,
        when: datetime | None = None,
    ) -> Acknowledgement:
        """یک تأیید ثبت می‌کند (idempotent روی `signal_id`).

        دکمه‌ی دوبار خورده — که در تلگرام عادی است — نباید دو ردیف بسازد.
        آخرین تصمیم برنده است: کسی که اول «دیدم» زده و بعد «اجرا کردم»،
        منظورش `taken` است.

        Raises:
            ValueError: اگر `action` معتبر نباشد. بی‌صدا پذیرفتنش یعنی
                گزارش بعداً روی مقداری حساب کند که هیچ‌کس نمی‌شناسد.
        """
        if action not in VALID_ACTIONS:
            raise ValueError(
                f"وضعیت نامعتبر «{action}». مجاز: {', '.join(sorted(VALID_ACTIONS))}"
            )

        moment = when or datetime.now()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO signal_acks
                (signal_id, acknowledged_at, action, source, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (signal_id, moment.isoformat(), action, source, note),
        )
        self._conn.commit()
        logger.info("تأیید سیگنال %s ثبت شد: %s", signal_id, action)
        return Acknowledgement(signal_id, moment, action, source, note)

    # -- خواندن ---------------------------------------------------------
    def get(self, signal_id: str) -> Acknowledgement | None:
        """تأیید یک سیگنال، یا `None` اگر کاربر واکنشی نشان نداده."""
        row = self._conn.execute(
            "SELECT * FROM signal_acks WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        return self._to_ack(row) if row else None

    def recent(self, limit: int = 50) -> list[Acknowledgement]:
        rows = self._conn.execute(
            "SELECT * FROM signal_acks ORDER BY acknowledged_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_ack(row) for row in rows]

    def stats(self, days: int | None = None) -> dict[str, Any]:
        """آمار تأیید — «نرخ اجرا» در کنار «نرخ برد».

        `delivery_rate` عمداً `None` است وقتی هیچ سیگنالی وجود ندارد:
        صفر یعنی «هیچ سیگنالی دیده نشد»، که با «سیگنالی نبود» فرق دارد.
        """
        where, params = "", ()
        if days is not None:
            where = "WHERE s.created_at >= datetime('now', ?)"
            params = (f"-{int(days)} days",)

        # جدول `signals` را `SignalLog` می‌سازد، نه ما. اگر هنوز ساخته
        # نشده باشد (تأیید قبل از اولین سیگنال باز شود)، آمار باید
        # «هیچ سیگنالی نیست» بگوید — نه اینکه با OperationalError بیفتد.
        if not self._has_signals_table():
            return self._empty_stats()

        total = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM signals s {where}", params
        ).fetchone()["n"]

        rows = self._conn.execute(
            f"""
            SELECT a.action AS action, COUNT(*) AS n
            FROM signal_acks a JOIN signals s ON s.signal_id = a.signal_id
            {where}
            GROUP BY a.action
            """,
            params,
        ).fetchall()
        counts = {row["action"]: row["n"] for row in rows}

        acknowledged = sum(counts.values())
        taken = counts.get(ACK_TAKEN, 0)
        return {
            "total_signals": total,
            "acknowledged": acknowledged,
            "unacknowledged": max(total - acknowledged, 0),
            "seen": counts.get(ACK_SEEN, 0),
            "taken": taken,
            "skipped": counts.get(ACK_SKIPPED, 0),
            # هر دو `None` می‌شوند اگر پایه‌ی محاسبه صفر باشد
            "delivery_rate_pct": (
                None if total == 0 else round(acknowledged / total * 100, 1)
            ),
            "execution_rate_pct": (
                None if acknowledged == 0 else round(taken / acknowledged * 100, 1)
            ),
        }

    def _has_signals_table(self) -> bool:
        return (
            self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='signals'"
            ).fetchone()
            is not None
        )

    @staticmethod
    def _empty_stats() -> dict[str, Any]:
        """آمار خالی — نرخ‌ها `None` می‌مانند، نه صفر."""
        return {
            "total_signals": 0,
            "acknowledged": 0,
            "unacknowledged": 0,
            "seen": 0,
            "taken": 0,
            "skipped": 0,
            "delivery_rate_pct": None,
            "execution_rate_pct": None,
        }

    @staticmethod
    def _to_ack(row: sqlite3.Row) -> Acknowledgement:
        return Acknowledgement(
            signal_id=row["signal_id"],
            acknowledged_at=datetime.fromisoformat(row["acknowledged_at"]),
            action=row["action"],
            source=row["source"],
            note=row["note"],
        )

    # -- چرخه‌ی عمر -----------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AckStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _button(action: str, signal_id: str) -> dict[str, str]:
    return {
        "text": ACTION_LABELS[action],
        "callback_data": f"ack:{action}:{signal_id}",
    }


def build_keyboard(signal_id: str) -> dict[str, Any]:
    """صفحه‌کلید inline تلگرام برای یک سیگنال.

    `callback_data` سقف **۶۴ بایت** دارد؛ `signal_id` پروژه یک uuid است
    (۳۶ کاراکتر) پس `ack:<action>:<id>` جا می‌شود. اگر روزی شناسه بلندتر
    شد، تلگرام دکمه را بی‌صدا رد می‌کند — به همین دلیل اینجا کوتاه‌ترین
    شکل ممکن استفاده شده.
    """
    return {
        "inline_keyboard": [
            [
                _button(ACK_TAKEN, signal_id),
                _button(ACK_SKIPPED, signal_id),
            ],
            [_button(ACK_SEEN, signal_id)],
        ]
    }


def parse_callback(data: str) -> tuple[str, str] | None:
    """`ack:taken:<id>` → `("taken", "<id>")`، یا `None` اگر مال ما نباشد.

    هر چیزی که شکل مورد انتظار را نداشته باشد رد می‌شود: یک callback
    ناشناخته نباید به‌عنوان تأیید ثبت شود.
    """
    parts = str(data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "ack":
        return None
    action, signal_id = parts[1], parts[2].strip()
    if action not in VALID_ACTIONS or not signal_id:
        return None
    return action, signal_id

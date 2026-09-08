"""ذخیره هر سیگنال صادرشده در SQLite برای Audit.

هدف: بعداً بتوان پرسید «چه سیگنالی، چه زمانی، با چه دلیلی صادر شد؟» —
مبنای بک‌تست و ارزیابی کیفیت استراتژی‌ها. فایل JSONL هم به‌عنوان پشتیبان نوشته می‌شود.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from signals.signal_model import Signal

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id        TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    valid_until      TEXT,
    strategy_name    TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    underlying       TEXT,
    option_type      TEXT NOT NULL,
    side             TEXT NOT NULL,
    strike           REAL NOT NULL,
    expiry           TEXT NOT NULL,
    suggested_price  REAL NOT NULL,
    suggested_qty    INTEGER NOT NULL,
    stop_loss        REAL,
    take_profit      REAL,
    underlying_price REAL,
    confidence       REAL,
    status           TEXT NOT NULL,
    reason           TEXT,
    payload          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
"""

_INSERT = """
INSERT OR REPLACE INTO signals (
    signal_id, created_at, valid_until, strategy_name, symbol, underlying,
    option_type, side, strike, expiry, suggested_price, suggested_qty,
    stop_loss, take_profit, underlying_price, confidence, status, reason, payload
) VALUES (
    :signal_id, :created_at, :valid_until, :strategy_name, :symbol, :underlying,
    :option_type, :side, :strike, :expiry, :suggested_price, :suggested_qty,
    :stop_loss, :take_profit, :underlying_price, :confidence, :status, :reason, :payload
)
"""


class SignalLog:
    """لاگ ماندگار سیگنال‌ها.

    Args:
        db_path: مسیر فایل SQLite (پوشه‌اش در صورت نبود ساخته می‌شود)
        jsonl_path: مسیر اختیاری فایل JSONL برای پشتیبان انسان‌خوان
    """

    def __init__(
        self,
        db_path: str | Path = "var/signals.db",
        jsonl_path: str | Path | None = "var/signals.jsonl",
    ) -> None:
        self.db_path = Path(db_path)
        self.jsonl_path = Path(jsonl_path) if jsonl_path else None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.jsonl_path:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    # ------------------------------------------------------------------
    def save(self, signal: Signal) -> None:
        """ثبت یک سیگنال (idempotent روی signal_id)."""
        row = self._to_row(signal)
        self._connection.execute(_INSERT, row)
        self._connection.commit()
        self._append_jsonl(signal)

    def save_many(self, signals: list[Signal]) -> int:
        for signal in signals:
            self.save(signal)
        return len(signals)

    def all_signals(self, limit: int | None = None) -> list[Signal]:
        """خواندن سیگنال‌ها از جدید به قدیم."""
        query = "SELECT payload FROM signals ORDER BY created_at DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        return [Signal.from_dict(json.loads(r["payload"])) for r in self._connection.execute(query)]

    def iter_signals(self) -> Iterator[Signal]:
        """پیمایش تنبل همه سیگنال‌ها (قدیم به جدید) برای بک‌تست."""
        cursor = self._connection.execute("SELECT payload FROM signals ORDER BY created_at ASC")
        for row in cursor:
            yield Signal.from_dict(json.loads(row["payload"]))

    def count(self) -> int:
        return int(self._connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0])

    def update_status(self, signal_id: str, status: str) -> None:
        """به‌روزرسانی وضعیت (مثلاً notified/expired) — فقط برای Audit.

        هم ستون `status` و هم `payload` به‌روز می‌شوند تا خروجی `all_signals`
        با ستون‌های جدول ناهمخوان نشود.
        """
        row = self._connection.execute(
            "SELECT payload FROM signals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            logger.warning("سیگنال %s برای به‌روزرسانی وضعیت پیدا نشد.", signal_id)
            return
        payload = json.loads(row["payload"])
        payload["status"] = status
        self._connection.execute(
            "UPDATE signals SET status = ?, payload = ? WHERE signal_id = ?",
            (status, json.dumps(payload, ensure_ascii=False), signal_id),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SignalLog:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    @staticmethod
    def _to_row(signal: Signal) -> dict[str, object]:
        data = signal.to_dict()
        return {
            "signal_id": signal.signal_id,
            "created_at": data["created_at"],
            "valid_until": data["valid_until"],
            "strategy_name": signal.strategy_name,
            "symbol": signal.symbol,
            "underlying": signal.underlying,
            "option_type": data["option_type"],
            "side": data["side"],
            "strike": signal.strike,
            "expiry": data["expiry"],
            "suggested_price": signal.suggested_price,
            "suggested_qty": signal.suggested_qty,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "underlying_price": signal.underlying_price,
            "confidence": signal.confidence,
            "status": data["status"],
            "reason": signal.reason,
            "payload": json.dumps(data, ensure_ascii=False),
        }

    def _append_jsonl(self, signal: Signal) -> None:
        if not self.jsonl_path:
            return
        try:
            with self.jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(signal.to_json() + "\n")
        except OSError as exc:  # نوشتن پشتیبان نباید حلقه اصلی را بشکند
            logger.warning("نوشتن فایل JSONL ناموفق بود: %s", exc)

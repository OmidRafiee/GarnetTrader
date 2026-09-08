"""ذخیره‌ی حساب، سفارش، پوزیشن و معامله‌ی معاملات کاغذی در SQLite.

هدف: وضعیت حساب شبیه‌سازی‌شده (`PaperBroker`) بین اجراهای مختلف داشبورد
پایدار بماند. مثل `storage/signal_log.py`، بدون ORM — یک اسکیمای دستی و
یک کلاس نگه‌دارنده‌ی اتصال.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_account (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    cash            REAL NOT NULL,
    initial_balance REAL NOT NULL,
    created_at      TEXT NOT NULL,
    reset_at        TEXT
);

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    avg_fill_price  REAL,
    status          TEXT NOT NULL,
    fee_paid        REAL NOT NULL DEFAULT 0,
    signal_id       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_orders_symbol ON paper_orders(symbol);
CREATE INDEX IF NOT EXISTS idx_paper_orders_status ON paper_orders(status);

CREATE TABLE IF NOT EXISTS paper_positions (
    symbol          TEXT PRIMARY KEY,
    quantity        INTEGER NOT NULL,
    average_price   REAL NOT NULL,
    opened_at       TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    fee_paid        REAL NOT NULL DEFAULT 0,
    pnl_absolute    REAL NOT NULL,
    pnl_pct         REAL NOT NULL,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT NOT NULL,
    signal_id       TEXT,
    close_reason    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_closed_at ON paper_trades(closed_at);
"""


class PaperTradingStore:
    """لایه‌ی ماندگاری معاملات کاغذی، روی یک فایل SQLite مستقل.

    Args:
        db_path: مسیر فایل SQLite (پوشه‌اش در صورت نبود ساخته می‌شود)
    """

    def __init__(self, db_path: str | Path = "var/paper_trading.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    # -- حساب --------------------------------------------------------------
    def get_account(self) -> dict[str, Any] | None:
        """وضعیت فعلی حساب کاغذی؛ `None` اگر هنوز مقداردهی اولیه نشده."""
        row = self._connection.execute(
            "SELECT cash, initial_balance, created_at, reset_at FROM paper_account WHERE id = 1"
        ).fetchone()
        return dict(row) if row else None

    def init_account(self, initial_balance: float) -> dict[str, Any]:
        """مقداردهی اولیه‌ی حساب، فقط اگر هنوز وجود نداشته باشد (idempotent)."""
        existing = self.get_account()
        if existing is not None:
            return existing
        now = datetime.now().isoformat(timespec="seconds")
        self._connection.execute(
            """
            INSERT INTO paper_account (id, cash, initial_balance, created_at, reset_at)
            VALUES (1, ?, ?, ?, NULL)
            """,
            (initial_balance, initial_balance, now),
        )
        self._connection.commit()
        return self.get_account()  # type: ignore[return-value]

    def update_cash(self, cash: float) -> None:
        self._connection.execute("UPDATE paper_account SET cash = ? WHERE id = 1", (cash,))
        self._connection.commit()

    # -- سفارش‌ها ------------------------------------------------------------
    def save_order(self, order: dict[str, Any]) -> None:
        """ثبت یک سفارش جدید (idempotent روی order_id)."""
        self._connection.execute(
            """
            INSERT OR REPLACE INTO paper_orders (
                order_id, symbol, side, quantity, filled_quantity, avg_fill_price,
                status, fee_paid, signal_id, created_at, updated_at, metadata
            ) VALUES (
                :order_id, :symbol, :side, :quantity, :filled_quantity, :avg_fill_price,
                :status, :fee_paid, :signal_id, :created_at, :updated_at, :metadata
            )
            """,
            order,
        )
        self._connection.commit()

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM paper_orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_orders(self, limit: int | None = None) -> list[dict[str, Any]]:
        """سفارش‌ها از جدید به قدیم."""
        query = "SELECT * FROM paper_orders ORDER BY created_at DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._connection.execute(query)]

    # -- پوزیشن‌ها -----------------------------------------------------------
    def get_position(self, symbol: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM paper_positions WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_position(
        self, symbol: str, quantity: int, average_price: float, opened_at: str
    ) -> None:
        """ثبت یا به‌روزرسانی پوزیشن. `quantity <= 0` یعنی صاف‌شده — حذف می‌شود."""
        if quantity <= 0:
            self.delete_position(symbol)
            return
        now = datetime.now().isoformat(timespec="seconds")
        self._connection.execute(
            """
            INSERT INTO paper_positions (symbol, quantity, average_price, opened_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                quantity = excluded.quantity,
                average_price = excluded.average_price,
                updated_at = excluded.updated_at
            """,
            (symbol, quantity, average_price, opened_at, now),
        )
        self._connection.commit()

    def delete_position(self, symbol: str) -> None:
        self._connection.execute("DELETE FROM paper_positions WHERE symbol = ?", (symbol,))
        self._connection.commit()

    def list_positions(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM paper_positions ORDER BY symbol"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- معاملات بسته‌شده -----------------------------------------------------
    def record_trade(self, trade: dict[str, Any]) -> None:
        self._connection.execute(
            """
            INSERT INTO paper_trades (
                trade_id, symbol, quantity, entry_price, exit_price, fee_paid,
                pnl_absolute, pnl_pct, opened_at, closed_at, signal_id, close_reason
            ) VALUES (
                :trade_id, :symbol, :quantity, :entry_price, :exit_price, :fee_paid,
                :pnl_absolute, :pnl_pct, :opened_at, :closed_at, :signal_id, :close_reason
            )
            """,
            trade,
        )
        self._connection.commit()

    def list_trades(self, days: int | None = None) -> list[dict[str, Any]]:
        """معاملات بسته‌شده، قدیم به جدید (ترتیب لازم برای منحنی تجمعی)."""
        query = "SELECT * FROM paper_trades"
        params: tuple[Any, ...] = ()
        if days is not None:
            since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
            query += " WHERE closed_at >= ?"
            params = (since,)
        query += " ORDER BY closed_at ASC"
        return [dict(r) for r in self._connection.execute(query, params)]

    # -- ریست ---------------------------------------------------------------
    def reset(self, initial_balance: float) -> dict[str, Any]:
        """پاک‌کردن کامل سفارش/پوزیشن/معامله و بازگرداندن موجودی به مقدار اولیه."""
        now = datetime.now().isoformat(timespec="seconds")
        self._connection.execute("DELETE FROM paper_orders")
        self._connection.execute("DELETE FROM paper_positions")
        self._connection.execute("DELETE FROM paper_trades")
        self._connection.execute(
            """
            INSERT INTO paper_account (id, cash, initial_balance, created_at, reset_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                cash = excluded.cash,
                initial_balance = excluded.initial_balance,
                reset_at = excluded.reset_at
            """,
            (initial_balance, initial_balance, now, now),
        )
        self._connection.commit()
        return self.get_account()  # type: ignore[return-value]

    # -- چرخه‌ی عمر -----------------------------------------------------------
    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> PaperTradingStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

"""گزارش‌گیری از سیگنال‌های ثبت‌شده + پیگیری نتیجه‌ی واقعی.

جدول `signals` می‌گوید چه سیگنالی داده شده، ولی نمی‌گوید **درست بوده یا
نه**. بدون آن، گزارش فقط شمارش است نه ارزیابی. این ماژول یک جدول
`signal_outcomes` اضافه می‌کند که قیمت واقعی بعدی را نگه می‌دارد.

قیمت‌ها **واقعی** ثبت می‌شوند (از TSETMC)، نه تخمینی؛ گزارشی که روی
عدد ساختگی بنا شود بدتر از نداشتن گزارش است.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OUTCOME_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id      TEXT PRIMARY KEY,
    checked_at     TEXT NOT NULL,
    price_at_check REAL,
    pnl_pct        REAL,
    hit_target     INTEGER,
    hit_stop       INTEGER,
    outcome        TEXT NOT NULL,
    note           TEXT,
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);
CREATE INDEX IF NOT EXISTS idx_outcomes_checked ON signal_outcomes(checked_at);
CREATE INDEX IF NOT EXISTS idx_outcomes_result  ON signal_outcomes(outcome);
"""

#: مقادیر ممکن ستون `outcome`
OUTCOME_PENDING = "pending"
OUTCOME_WIN = "win"
OUTCOME_LOSS = "loss"
OUTCOME_EXPIRED = "expired"
OUTCOME_UNKNOWN = "unknown"


@dataclass(frozen=True)
class StrategyStats:
    """آمار یک استراتژی."""

    strategy: str
    total: int
    wins: int
    losses: int
    pending: int
    avg_pnl_pct: float | None
    best_pnl_pct: float | None
    worst_pnl_pct: float | None

    @property
    def resolved(self) -> int:
        """سیگنال‌هایی که نتیجه‌شان معلوم شده."""
        return self.wins + self.losses

    @property
    def win_rate(self) -> float | None:
        """نرخ برد؛ اگر هیچ سیگنالی هنوز نتیجه ندارد `None`.

        برگرداندن صفر در این حالت گمراه‌کننده است: «۰٪ برد» با «هنوز
        معلوم نیست» یکی نیست.
        """
        return None if self.resolved == 0 else self.wins / self.resolved


class SignalReporter:
    """گزارش‌گیری و پیگیری نتیجه، روی همان دیتابیس سیگنال‌ها."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(OUTCOME_SCHEMA)
        self._connection.commit()

    # ------------------------------------------------------------------
    def record_outcome(
        self,
        signal_id: str,
        price_at_check: float | None,
        pnl_pct: float | None,
        outcome: str,
        hit_target: bool = False,
        hit_stop: bool = False,
        note: str | None = None,
    ) -> None:
        """ثبت نتیجه‌ی یک سیگنال (idempotent روی `signal_id`)."""
        self._connection.execute(
            """
            INSERT OR REPLACE INTO signal_outcomes
                (signal_id, checked_at, price_at_check, pnl_pct,
                 hit_target, hit_stop, outcome, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                datetime.now().isoformat(timespec="seconds"),
                price_at_check,
                pnl_pct,
                int(hit_target),
                int(hit_stop),
                outcome,
                note,
            ),
        )
        self._connection.commit()

    # ------------------------------------------------------------------
    def summary(self, days: int | None = None) -> dict[str, Any]:
        """خلاصه‌ی کلی: تعداد، نرخ برد، میانگین سود."""
        where, params = self._window(days)
        row = self._connection.execute(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN o.outcome = 'win'  THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN o.outcome = 'loss' THEN 1 ELSE 0 END) AS losses,
                   AVG(o.pnl_pct) AS avg_pnl
            FROM signals s
            LEFT JOIN signal_outcomes o ON o.signal_id = s.signal_id
            {where}
            """,
            params,
        ).fetchone()

        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        resolved = wins + losses
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "pending": total - resolved,
            "resolved": resolved,
            # None یعنی «هنوز معلوم نیست»، که با ۰٪ فرق دارد
            "win_rate": (wins / resolved) if resolved else None,
            "avg_pnl_pct": row["avg_pnl"],
            "window_days": days,
        }

    def by_strategy(self, days: int | None = None) -> list[StrategyStats]:
        """آمار به تفکیک استراتژی — کدام واقعاً کار می‌کند."""
        where, params = self._window(days)
        rows = self._connection.execute(
            f"""
            SELECT s.strategy_name AS strategy,
                   COUNT(*) AS total,
                   SUM(CASE WHEN o.outcome = 'win'  THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN o.outcome = 'loss' THEN 1 ELSE 0 END) AS losses,
                   AVG(o.pnl_pct) AS avg_pnl,
                   MAX(o.pnl_pct) AS best_pnl,
                   MIN(o.pnl_pct) AS worst_pnl
            FROM signals s
            LEFT JOIN signal_outcomes o ON o.signal_id = s.signal_id
            {where}
            GROUP BY s.strategy_name
            ORDER BY total DESC
            """,
            params,
        ).fetchall()

        stats = []
        for r in rows:
            total, wins, losses = r["total"] or 0, r["wins"] or 0, r["losses"] or 0
            stats.append(
                StrategyStats(
                    strategy=r["strategy"],
                    total=total,
                    wins=wins,
                    losses=losses,
                    pending=total - wins - losses,
                    avg_pnl_pct=r["avg_pnl"],
                    best_pnl_pct=r["best_pnl"],
                    worst_pnl_pct=r["worst_pnl"],
                )
            )
        return stats

    def by_underlying(self, days: int | None = None) -> list[dict[str, Any]]:
        """آمار به تفکیک نماد پایه."""
        where, params = self._window(days)
        rows = self._connection.execute(
            f"""
            SELECT s.underlying AS underlying,
                   COUNT(*) AS total,
                   SUM(CASE WHEN o.outcome = 'win'  THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN o.outcome = 'loss' THEN 1 ELSE 0 END) AS losses,
                   AVG(o.pnl_pct) AS avg_pnl
            FROM signals s
            LEFT JOIN signal_outcomes o ON o.signal_id = s.signal_id
            {where}
            GROUP BY s.underlying
            ORDER BY total DESC
            """,
            params,
        ).fetchall()
        return [
            {
                "underlying": r["underlying"],
                "total": r["total"] or 0,
                "wins": r["wins"] or 0,
                "losses": r["losses"] or 0,
                "avg_pnl_pct": r["avg_pnl"],
            }
            for r in rows
        ]

    def daily_counts(self, days: int = 30) -> list[dict[str, Any]]:
        """تعداد سیگنال روزانه — برای نمودار."""
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = self._connection.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count
            FROM signals
            WHERE created_at >= ?
            GROUP BY day
            ORDER BY day
            """,
            (since,),
        ).fetchall()
        return [{"day": r["day"], "count": r["count"]} for r in rows]

    def recent(self, limit: int = 50, days: int | None = None) -> list[dict[str, Any]]:
        """سیگنال‌های اخیر همراه با نتیجه‌شان."""
        where, params = self._window(days)
        rows = self._connection.execute(
            f"""
            SELECT s.*, o.outcome, o.pnl_pct, o.price_at_check, o.checked_at
            FROM signals s
            LEFT JOIN signal_outcomes o ON o.signal_id = s.signal_id
            {where}
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()

        result = []
        for r in rows:
            item = dict(r)
            item.pop("payload", None)  # حجیم است و UI لازمش ندارد
            item["outcome"] = item["outcome"] or OUTCOME_PENDING
            result.append(item)
        return result

    def pending_signals(self) -> list[dict[str, Any]]:
        """سیگنال‌هایی که هنوز نتیجه‌شان ثبت نشده."""
        rows = self._connection.execute(
            """
            SELECT s.signal_id, s.symbol, s.underlying, s.created_at,
                   s.suggested_price, s.stop_loss, s.take_profit,
                   s.option_type, s.side, s.expiry, s.payload
            FROM signals s
            LEFT JOIN signal_outcomes o ON o.signal_id = s.signal_id
            WHERE o.signal_id IS NULL
            ORDER BY s.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    @staticmethod
    def _window(days: int | None) -> tuple[str, tuple[Any, ...]]:
        """بند WHERE برای بازه‌ی زمانی."""
        if days is None:
            return "", ()
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        return "WHERE s.created_at >= ?", (since,)

    def export_csv(self, path: str | Path, days: int | None = None) -> int:
        """خروجی CSV از سیگنال‌ها و نتایج — برای اکسل."""
        import csv

        rows = self.recent(limit=100_000, days=days)
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            out.write_text("", encoding="utf-8-sig")
            return 0

        with out.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return len(rows)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SignalReporter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def evaluate_signal(
    signal_payload: dict[str, Any], current_price: float
) -> tuple[str, float, bool, bool]:
    """نتیجه‌ی یک سیگنال را با قیمت فعلی می‌سنجد.

    Returns:
        (outcome, pnl_pct, hit_target, hit_stop)
    """
    entry = float(signal_payload.get("suggested_price") or 0)
    if entry <= 0:
        return OUTCOME_UNKNOWN, 0.0, False, False

    # این پروژه فقط سیگنال **خرید** می‌دهد؛ سود یعنی بالا رفتن پرمیوم.
    # اگر روزی فروش اضافه شد، اینجا باید جهت را برعکس کند.
    pnl_pct = (current_price - entry) / entry * 100

    target = signal_payload.get("take_profit")
    stop = signal_payload.get("stop_loss")
    hit_target = bool(target) and current_price >= float(target)
    hit_stop = bool(stop) and current_price <= float(stop)

    if hit_target:
        return OUTCOME_WIN, pnl_pct, True, False
    if hit_stop:
        return OUTCOME_LOSS, pnl_pct, False, True

    # نه حد سود خورده نه حد ضرر → معامله **باز** است، نه باخته.
    # شمردن سود لحظه‌ای منفی به‌عنوان «باخت» نرخ برد را بی‌معنا می‌کند:
    # سیگنالی که یک ساعت پیش صادر شده و ۰.۲٪ پایین است، شکست نخورده.
    # سررسید گذشته اما یعنی دیگر فرصتی نمانده.
    expiry = signal_payload.get("expiry")
    if expiry:
        try:
            if date.fromisoformat(str(expiry)) < date.today():
                return (
                    OUTCOME_WIN if pnl_pct > 0 else OUTCOME_LOSS
                ), pnl_pct, False, False
        except ValueError:
            pass

    return OUTCOME_PENDING, pnl_pct, False, False

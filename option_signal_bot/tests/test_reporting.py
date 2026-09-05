"""تست‌های لایه گزارش‌گیری.

تمرکز روی جایی که گزارش می‌تواند **دروغ بگوید**: نرخ برد صفر در برابر
نامعلوم، و شمردن معامله‌ی باز به‌عنوان باخت.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import pytest

from storage.reporting import (
    OUTCOME_LOSS,
    OUTCOME_PENDING,
    OUTCOME_WIN,
    SignalReporter,
    evaluate_signal,
)

SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, valid_until TEXT,
    strategy_name TEXT NOT NULL, symbol TEXT NOT NULL, underlying TEXT,
    option_type TEXT NOT NULL, side TEXT NOT NULL, strike REAL NOT NULL,
    expiry TEXT NOT NULL, suggested_price REAL NOT NULL, suggested_qty INTEGER NOT NULL,
    stop_loss REAL, take_profit REAL, underlying_price REAL, confidence REAL,
    status TEXT NOT NULL, reason TEXT, payload TEXT NOT NULL
);
"""


def _seed(db_path, rows):
    conn = sqlite3.connect(db_path)
    conn.executescript(SIGNALS_SCHEMA)
    for r in rows:
        payload = json.dumps(r, ensure_ascii=False)
        conn.execute(
            """INSERT INTO signals (signal_id, created_at, strategy_name, symbol,
               underlying, option_type, side, strike, expiry, suggested_price,
               suggested_qty, stop_loss, take_profit, status, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["signal_id"], r["created_at"], r["strategy_name"], r["symbol"],
             r["underlying"], "call", "buy", 750.0, r["expiry"],
             r["suggested_price"], 10, r.get("stop_loss"), r.get("take_profit"),
             "new", payload),
        )
    conn.commit()
    conn.close()


def _signal(sid, strategy="s1", underlying="خودرو", price=100.0, **kw):
    return {
        "signal_id": sid,
        "created_at": "2026-09-05T10:00:00",
        "strategy_name": strategy,
        "symbol": f"ضخود{sid}",
        "underlying": underlying,
        "expiry": (date.today() + timedelta(days=30)).isoformat(),
        "suggested_price": price,
        "stop_loss": kw.get("stop_loss", 65.0),
        "take_profit": kw.get("take_profit", 170.0),
    }


@pytest.fixture
def reporter(tmp_path):
    db = tmp_path / "signals.db"
    _seed(db, [_signal("a"), _signal("b"), _signal("c", strategy="s2")])
    with SignalReporter(db) as r:
        yield r


# ----------------------------------------------------------------------
# مرز مهم: صفر در برابر نامعلوم
# ----------------------------------------------------------------------
def test_win_rate_is_none_not_zero_when_nothing_resolved(reporter):
    """«۰٪ برد» با «هنوز معلوم نیست» یکی نیست.

    برگرداندن صفر یعنی کاربر فکر می‌کند استراتژی شکست خورده، در حالی که
    فقط هنوز هیچ معامله‌ای بسته نشده.
    """
    s = reporter.summary()
    assert s["total"] == 3
    assert s["pending"] == 3
    assert s["win_rate"] is None


def test_win_rate_counts_only_resolved(reporter):
    reporter.record_outcome("a", 170.0, 70.0, OUTCOME_WIN, hit_target=True)
    reporter.record_outcome("b", 65.0, -35.0, OUTCOME_LOSS, hit_stop=True)

    s = reporter.summary()
    assert (s["wins"], s["losses"], s["pending"]) == (1, 1, 1)
    assert s["win_rate"] == 0.5  # ۱ از ۲ نتیجه‌گرفته، نه ۱ از ۳


# ----------------------------------------------------------------------
# طبقه‌بندی نتیجه
# ----------------------------------------------------------------------
def test_open_trade_is_pending_not_loss():
    """معامله‌ای که نه حد سود خورده نه حد ضرر، باز است نه باخته.

    باگ واقعی: سیگنالی که ۰.۲٪ پایین بود «باخت» شمرده می‌شد و نرخ برد
    را بی‌معنا می‌کرد.
    """
    payload = _signal("x", price=100.0)
    outcome, pnl, hit_t, hit_s = evaluate_signal(payload, 99.8)

    assert outcome == OUTCOME_PENDING
    assert pnl == pytest.approx(-0.2)
    assert not hit_t and not hit_s


def test_unchanged_price_is_pending():
    payload = _signal("x", price=100.0)
    outcome, pnl, _, _ = evaluate_signal(payload, 100.0)
    assert outcome == OUTCOME_PENDING
    assert pnl == 0.0


def test_hitting_target_is_a_win():
    payload = _signal("x", price=100.0, take_profit=170.0)
    outcome, pnl, hit_t, hit_s = evaluate_signal(payload, 175.0)
    assert outcome == OUTCOME_WIN
    assert hit_t and not hit_s
    assert pnl == pytest.approx(75.0)


def test_hitting_stop_is_a_loss():
    payload = _signal("x", price=100.0, stop_loss=65.0)
    outcome, _, hit_t, hit_s = evaluate_signal(payload, 60.0)
    assert outcome == OUTCOME_LOSS
    assert hit_s and not hit_t


def test_expired_signal_is_resolved_by_sign():
    """بعد از سررسید دیگر فرصتی نمانده، پس علامت سود تصمیم می‌گیرد."""
    payload = _signal("x", price=100.0)
    payload["expiry"] = (date.today() - timedelta(days=1)).isoformat()

    assert evaluate_signal(payload, 110.0)[0] == OUTCOME_WIN
    assert evaluate_signal(payload, 90.0)[0] == OUTCOME_LOSS


def test_zero_entry_price_is_not_divided_by():
    payload = _signal("x", price=0.0)
    outcome, pnl, _, _ = evaluate_signal(payload, 50.0)
    assert pnl == 0.0
    assert outcome != OUTCOME_WIN


# ----------------------------------------------------------------------
# تفکیک‌ها
# ----------------------------------------------------------------------
def test_by_strategy_separates_strategies(reporter):
    stats = {s.strategy: s for s in reporter.by_strategy()}
    assert set(stats) == {"s1", "s2"}
    assert stats["s1"].total == 2
    assert stats["s2"].total == 1
    assert stats["s1"].win_rate is None  # هنوز نتیجه‌ای نیست


def test_by_underlying_groups_by_symbol(reporter):
    rows = {r["underlying"]: r for r in reporter.by_underlying()}
    assert rows["خودرو"]["total"] == 3


def test_recent_marks_unevaluated_as_pending(reporter):
    rows = reporter.recent()
    assert len(rows) == 3
    assert all(r["outcome"] == OUTCOME_PENDING for r in rows)
    assert all("payload" not in r for r in rows), "payload حجیم نباید به UI برود"


def test_outcome_record_is_idempotent(reporter):
    reporter.record_outcome("a", 150.0, 50.0, OUTCOME_WIN)
    reporter.record_outcome("a", 160.0, 60.0, OUTCOME_WIN)
    assert reporter.summary()["wins"] == 1


def test_export_csv_writes_rows(reporter, tmp_path):
    out = tmp_path / "report.csv"
    count = reporter.export_csv(out)
    assert count == 3
    assert out.exists()
    assert "ضخود" in out.read_text(encoding="utf-8-sig")


def test_pending_signals_excludes_evaluated(reporter):
    assert len(reporter.pending_signals()) == 3
    reporter.record_outcome("a", 150.0, 50.0, OUTCOME_WIN)
    assert len(reporter.pending_signals()) == 2

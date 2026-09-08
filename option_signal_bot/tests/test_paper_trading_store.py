"""تست‌های لایه ماندگاری معاملات کاغذی."""

from __future__ import annotations

from datetime import datetime

import pytest

from storage.paper_trading_store import PaperTradingStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "paper_trading.db"
    with PaperTradingStore(db) as s:
        yield s


def _order(order_id="o1", symbol="ضخود7001", side="buy", qty=1, **overrides):
    row = {
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "filled_quantity": qty,
        "avg_fill_price": 1000.0,
        "status": "filled",
        "fee_paid": 0.0,
        "signal_id": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "metadata": None,
    }
    row.update(overrides)
    return row


def test_account_does_not_exist_before_init(store):
    assert store.get_account() is None


def test_init_account_creates_it_once(store):
    first = store.init_account(1_000.0)
    assert first["cash"] == 1_000.0
    assert first["initial_balance"] == 1_000.0

    # فراخوانی دوم نباید موجودی را دوباره صفر کند
    second = store.init_account(9_999.0)
    assert second["cash"] == 1_000.0


def test_update_cash_persists(store):
    store.init_account(1_000.0)
    store.update_cash(750.0)
    assert store.get_account()["cash"] == 750.0


def test_save_and_get_order_roundtrip(store):
    store.save_order(_order())
    row = store.get_order("o1")
    assert row is not None
    assert row["symbol"] == "ضخود7001"
    assert row["status"] == "filled"


def test_save_order_is_idempotent_on_order_id(store):
    store.save_order(_order(status="pending"))
    store.save_order(_order(status="filled"))
    assert store.get_order("o1")["status"] == "filled"
    assert len(store.list_orders()) == 1


def test_list_orders_is_newest_first(store):
    store.save_order(_order("o1", created_at="2026-01-01T00:00:00"))
    store.save_order(_order("o2", created_at="2026-01-02T00:00:00"))
    ids = [row["order_id"] for row in store.list_orders()]
    assert ids == ["o2", "o1"]


def test_list_orders_respects_limit(store):
    for i in range(3):
        store.save_order(_order(f"o{i}", created_at=f"2026-01-0{i + 1}T00:00:00"))
    assert len(store.list_orders(limit=2)) == 2


def test_get_position_missing_returns_none(store):
    assert store.get_position("ضخود7001") is None


def test_upsert_position_creates_and_updates(store):
    store.upsert_position("ضخود7001", 2, 1000.0, "2026-01-01T00:00:00")
    row = store.get_position("ضخود7001")
    assert row["quantity"] == 2
    assert row["average_price"] == 1000.0

    store.upsert_position("ضخود7001", 5, 1200.0, "2026-01-01T00:00:00")
    row = store.get_position("ضخود7001")
    assert row["quantity"] == 5
    assert row["average_price"] == 1200.0


def test_upsert_position_with_non_positive_quantity_deletes_it(store):
    store.upsert_position("ضخود7001", 2, 1000.0, "2026-01-01T00:00:00")
    store.upsert_position("ضخود7001", 0, 0.0, "2026-01-01T00:00:00")
    assert store.get_position("ضخود7001") is None


def test_list_positions_is_sorted_by_symbol(store):
    store.upsert_position("ب", 1, 100.0, "2026-01-01T00:00:00")
    store.upsert_position("آ", 1, 100.0, "2026-01-01T00:00:00")
    symbols = [row["symbol"] for row in store.list_positions()]
    assert symbols == ["آ", "ب"]


def test_record_and_list_trades(store):
    store.record_trade(
        {
            "trade_id": "t1",
            "symbol": "ضخود7001",
            "quantity": 1,
            "entry_price": 1000.0,
            "exit_price": 1100.0,
            "fee_paid": 0.0,
            "pnl_absolute": 100.0,
            "pnl_pct": 10.0,
            "opened_at": "2026-01-01T00:00:00",
            "closed_at": "2026-01-02T00:00:00",
            "signal_id": None,
            "close_reason": "manual",
        }
    )
    trades = store.list_trades()
    assert len(trades) == 1
    assert trades[0]["pnl_pct"] == 10.0


def test_reset_clears_everything_and_restores_balance(store):
    store.init_account(1_000.0)
    store.save_order(_order())
    store.upsert_position("ضخود7001", 1, 1000.0, "2026-01-01T00:00:00")
    store.record_trade(
        {
            "trade_id": "t1",
            "symbol": "ضخود7001",
            "quantity": 1,
            "entry_price": 1000.0,
            "exit_price": 900.0,
            "fee_paid": 0.0,
            "pnl_absolute": -100.0,
            "pnl_pct": -10.0,
            "opened_at": "2026-01-01T00:00:00",
            "closed_at": "2026-01-02T00:00:00",
            "signal_id": None,
            "close_reason": "manual",
        }
    )

    account = store.reset(5_000.0)

    assert account["cash"] == 5_000.0
    assert account["initial_balance"] == 5_000.0
    assert account["reset_at"] is not None
    assert store.list_orders() == []
    assert store.list_positions() == []
    assert store.list_trades() == []

"""تست‌های MockBroker — شبیه‌ساز درون‌حافظه لایه اجرا (بدون اتصال واقعی)."""

from __future__ import annotations

import inspect

import pytest

from execution.order_executor_interface import (
    MockBroker,
    Order,
    OrderExecutorInterface,
    OrderStatus,
)

SYMBOL = "ضخود-1"
PRICE = 100.0


@pytest.fixture
def broker() -> MockBroker:
    return MockBroker(initial_cash=10_000_000.0, contract_size=1_000)


# ----------------------------------------------------------------------
# قرارداد اینترفیس
# ----------------------------------------------------------------------
def test_interface_cannot_be_instantiated():
    with pytest.raises(TypeError):
        OrderExecutorInterface()  # type: ignore[abstract]


def test_interface_declares_required_methods():
    expected = {
        "place_order",
        "cancel_order",
        "modify_order",
        "get_order_status",
        "get_positions",
        "get_account_balance",
    }
    assert expected <= set(OrderExecutorInterface.__abstractmethods__)


def test_mock_broker_implements_interface():
    assert issubclass(MockBroker, OrderExecutorInterface)
    assert not MockBroker.__abstractmethods__


def test_execution_layer_has_no_real_broker_calls():
    """مایل‌استون ۱: این لایه نباید هیچ I/O شبکه‌ای داشته باشد."""
    source = inspect.getsource(MockBroker)
    for forbidden in ("requests", "urllib", "http", "socket", "api_key"):
        assert forbidden not in source


# ----------------------------------------------------------------------
# ثبت سفارش
# ----------------------------------------------------------------------
def test_place_order_opens_order(broker):
    order = broker.place_order(SYMBOL, "buy", quantity=5, price=PRICE)
    assert isinstance(order, Order)
    assert order.status is OrderStatus.OPEN
    assert order.remaining_quantity == 5
    assert broker.get_order_status(order.order_id) is OrderStatus.OPEN


@pytest.mark.parametrize("quantity,price", [(0, PRICE), (-1, PRICE), (5, 0.0)])
def test_place_order_rejects_invalid_input(broker, quantity, price):
    order = broker.place_order(SYMBOL, "buy", quantity=quantity, price=price)
    assert order.status is OrderStatus.REJECTED


def test_place_order_rejects_insufficient_cash(broker):
    # ۱۰۰۰ قرارداد × ۱۰۰ ریال × اندازه ۱۰۰۰ = ۱۰۰ میلیون، بیش از موجودی
    order = broker.place_order(SYMBOL, "buy", quantity=1_000, price=PRICE)
    assert order.status is OrderStatus.REJECTED
    assert "reject_reason" in order.metadata


def test_place_order_rejects_invalid_side(broker):
    with pytest.raises(ValueError):
        broker.place_order(SYMBOL, "hold", quantity=1, price=PRICE)


# ----------------------------------------------------------------------
# چرخه عمر سفارش
# ----------------------------------------------------------------------
def test_cancel_order(broker):
    order = broker.place_order(SYMBOL, "buy", 2, PRICE)
    assert broker.cancel_order(order.order_id) is True
    assert broker.get_order_status(order.order_id) is OrderStatus.CANCELLED
    assert broker.cancel_order(order.order_id) is False  # دوباره لغو نمی‌شود


def test_modify_order(broker):
    order = broker.place_order(SYMBOL, "buy", 2, PRICE)
    modified = broker.modify_order(order.order_id, price=120.0, quantity=3)
    assert modified.price == 120.0
    assert modified.quantity == 3


def test_modify_cancelled_order_fails(broker):
    order = broker.place_order(SYMBOL, "buy", 2, PRICE)
    broker.cancel_order(order.order_id)
    with pytest.raises(ValueError):
        broker.modify_order(order.order_id, price=120.0)


def test_unknown_order_raises(broker):
    with pytest.raises(KeyError):
        broker.get_order_status("MOCK-99999")


# ----------------------------------------------------------------------
# پر شدن سفارش، پوزیشن و موجودی
# ----------------------------------------------------------------------
def test_partial_then_full_fill(broker):
    order = broker.place_order(SYMBOL, "buy", 4, PRICE)
    broker.fill_order(order.order_id, quantity=1)
    assert broker.get_order_status(order.order_id) is OrderStatus.PARTIALLY_FILLED

    broker.fill_order(order.order_id)
    assert broker.get_order_status(order.order_id) is OrderStatus.FILLED
    assert broker.get_order(order.order_id).filled_quantity == 4


def test_fill_updates_cash_and_position(broker):
    initial_cash = broker.get_account_balance()["cash"]
    order = broker.place_order(SYMBOL, "buy", 3, PRICE)
    broker.fill_order(order.order_id)

    expected_cost = 3 * PRICE * broker.contract_size
    assert broker.get_account_balance()["cash"] == pytest.approx(initial_cash - expected_cost)

    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == SYMBOL
    assert positions[0].quantity == 3
    assert positions[0].average_price == pytest.approx(PRICE)


def test_average_price_is_weighted(broker):
    broker.fill_order(broker.place_order(SYMBOL, "buy", 1, 100.0).order_id)
    broker.fill_order(broker.place_order(SYMBOL, "buy", 3, 200.0).order_id)

    position = broker.get_positions()[0]
    assert position.quantity == 4
    assert position.average_price == pytest.approx((100.0 + 3 * 200.0) / 4)


def test_closing_position_flattens_it(broker):
    broker.fill_order(broker.place_order(SYMBOL, "buy", 2, PRICE).order_id)
    broker.fill_order(broker.place_order(SYMBOL, "sell", 2, PRICE).order_id)

    assert broker.get_positions() == []  # پوزیشن صفر در لیست باز نمی‌آید
    assert broker.get_account_balance()["cash"] == pytest.approx(broker.initial_cash)


def test_fill_all_and_reset(broker):
    broker.place_order(SYMBOL, "buy", 1, PRICE)
    broker.place_order(SYMBOL, "buy", 2, PRICE)
    filled = broker.fill_all()
    assert len(filled) == 2
    assert all(o.status is OrderStatus.FILLED for o in filled)

    broker.reset()
    assert broker.list_orders() == []
    assert broker.get_positions() == []
    assert broker.get_account_balance()["cash"] == broker.initial_cash


def test_fill_closed_order_raises(broker):
    order = broker.place_order(SYMBOL, "buy", 1, PRICE)
    broker.cancel_order(order.order_id)
    with pytest.raises(ValueError):
        broker.fill_order(order.order_id)

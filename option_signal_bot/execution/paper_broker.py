"""کارگزار شبیه‌سازی‌شده (معاملات کاغذی) — پیاده‌سازی واقعیِ `OrderExecutorInterface`.

هیچ تماس شبکه‌ای به کارگزاری واقعی برقرار نمی‌شود. پرشدن هر سفارش از
**عمق واقعی دفتر سفارش** (`data/order_book.py`) حساب می‌شود، هرگز از
آخرین قیمت معامله یا عددی حدسی. سفارش‌ها فقط **فوری** هستند: در برابر
عمق لحظه‌ی ثبت پر می‌شوند یا رد می‌شوند — سفارش باز/معلق وجود ندارد.

فقط پوزیشن long پشتیبانی می‌شود (خرید برای باز کردن، فروش برای بستن).
فروش استقراضی مدل نشده، چون مکانیزم وجه تضمین آن در این پروژه پیاده‌سازی
نشده است.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import date, datetime

from data.option_chain_client import OptionContract
from data.order_book import OrderBook
from execution.order_executor_interface import (
    Order,
    OrderExecutorInterface,
    OrderStatus,
    Position,
)
from risk.fees import FeeSchedule
from storage.paper_trading_store import PaperTradingStore

logger = logging.getLogger(__name__)

#: علت بسته‌شدن یک معامله
CLOSE_REASON_MANUAL = "manual"
CLOSE_REASON_EXPIRY = "expiry_settlement"


class PaperBroker(OrderExecutorInterface):
    """کارگزار کاغذی: پر شدن فوری روی عمق واقعی، پوزیشن‌های long، کارمزد.

    Args:
        store: لایه ماندگاری (`PaperTradingStore`)
        order_book_client: هر شیء با متد
            `try_get_order_book(ins_code, symbol) -> OrderBook | None`
            (مثل `data.order_book.OrderBookClient`؛ خطای شبکه را می‌بلعد و
            `None` برمی‌گرداند تا رد سفارش، نه ۵۰۰، نتیجه‌ی آن باشد)
        resolve_contract: نگاشت نماد آپشن به `OptionContract` (برای `ins_code`
            و `contract_size`)؛ معمولاً `OptionChainClient.get_contract`
        initial_balance: موجودی اولیه حساب کاغذی (ریال)
        fees: نرخ کارمزد و مالیات؛ پیش‌فرض صفر
    """

    def __init__(
        self,
        store: PaperTradingStore,
        order_book_client: object,
        resolve_contract: Callable[[str], OptionContract | None],
        initial_balance: float,
        fees: FeeSchedule | None = None,
    ) -> None:
        self.store = store
        self.order_book_client = order_book_client
        self.resolve_contract = resolve_contract
        self.fees = fees or FeeSchedule()
        self.store.init_account(initial_balance)

    # -- OrderExecutorInterface --------------------------------------------
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float | None = None,
        **kwargs: object,
    ) -> Order:
        """ثبت سفارش کاغذی — فوری، در برابر عمق واقعی دفتر سفارش.

        `price` صرفاً مرجع/نمایشی است؛ پرشدن همیشه از عمق واقعی محاسبه
        می‌شود، نه از این عدد (این کارگزار سفارش limit ندارد).
        """
        del price  # مرجع/نمایشی؛ پرشدن همیشه از عمق واقعی است
        now = datetime.now().isoformat(timespec="seconds")
        order_id = str(uuid.uuid4())

        if quantity <= 0:
            return self._rejected(order_id, symbol, side, quantity, now, "تعداد نامعتبر")

        side_normalized = side.lower()
        if side_normalized not in ("buy", "sell"):
            return self._rejected(order_id, symbol, side, quantity, now, f"سمت نامعتبر: {side}")

        if side_normalized == "sell":
            position = self.store.get_position(symbol)
            held = position["quantity"] if position else 0
            if held < quantity:
                return self._rejected(
                    order_id,
                    symbol,
                    side,
                    quantity,
                    now,
                    f"پوزیشنی برای فروش کافی نیست (موجود: {held}, درخواست: {quantity})؛ "
                    "این کارگزاری فروش استقراضی را پشتیبانی نمی‌کند",
                )

        contract = self.resolve_contract(symbol)
        if contract is None or not contract.ins_code:
            reason = "نماد یا ins_code یافت نشد"
            return self._rejected(order_id, symbol, side, quantity, now, reason)

        book = self.order_book_client.try_get_order_book(contract.ins_code, symbol)
        if book is None:
            reason = "دفتر سفارش در دسترس نیست (خطای شبکه یا داده)"
            return self._rejected(order_id, symbol, side, quantity, now, reason)

        avg_price, filled_qty = book.fill_price(side_normalized, quantity)
        if avg_price is None or filled_qty <= 0:
            reason = "عمق کافی در دفتر سفارش نیست"
            return self._rejected(order_id, symbol, side, quantity, now, reason)

        notional = avg_price * filled_qty * contract.contract_size
        is_buy = side_normalized == "buy"
        fee = (
            self.fees.entry_cost(notional, is_buy)
            if is_buy
            else self.fees.exit_cost(notional, was_buy=True)
        )

        status = OrderStatus.FILLED if filled_qty == quantity else OrderStatus.PARTIALLY_FILLED
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side_normalized,
            quantity=quantity,
            price=avg_price,
            status=status,
            filled_quantity=filled_qty,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
            metadata={"ins_code": contract.ins_code, "fee_paid": fee},
        )

        signal_id = kwargs.get("signal_id")
        if is_buy:
            self._apply_buy(symbol, filled_qty, avg_price, fee, now)
        else:
            self._apply_sell(symbol, filled_qty, avg_price, fee, now, contract, signal_id)

        self._save_order(order, fee, signal_id)
        return order

    def cancel_order(self, order_id: str) -> bool:
        """هر سفارش کاغذی همان لحظه‌ی ثبت، پر یا رد می‌شود؛ چیزی برای لغو نمی‌ماند."""
        order = self.store.get_order(order_id)
        return order is not None and order["status"] == OrderStatus.OPEN.value

    def modify_order(
        self,
        order_id: str,  # noqa: ARG002 — امضا از OrderExecutorInterface می‌آید
        price: float | None = None,  # noqa: ARG002
        quantity: int | None = None,  # noqa: ARG002
    ) -> Order:
        """این کارگزاری سفارش باز/قابل‌ویرایش ندارد — هر سفارش فوری حل می‌شود."""
        raise ValueError("این کارگزاری سفارش باز/قابل‌ویرایش ندارد")

    def get_order_status(self, order_id: str) -> OrderStatus:
        order = self.store.get_order(order_id)
        if order is None:
            raise ValueError(f"سفارش {order_id} یافت نشد")
        return OrderStatus(order["status"])

    def get_positions(self) -> list[Position]:
        return [
            Position(
                symbol=row["symbol"],
                quantity=row["quantity"],
                average_price=row["average_price"],
            )
            for row in self.store.list_positions()
        ]

    def get_account_balance(self) -> dict[str, float]:
        """موجودی حساب. `buying_power` بدون شبیه‌سازی مارجین، برابر نقد است."""
        account = self.store.get_account()
        cash = account["cash"] if account else 0.0
        return {"cash": cash, "buying_power": cash}

    # -- کمکی‌های داخلی -------------------------------------------------------
    def _apply_buy(
        self, symbol: str, filled_qty: int, avg_price: float, fee: float, now: str
    ) -> None:
        account = self.store.get_account()
        notional = avg_price * filled_qty * self._contract_size(symbol)
        self.store.update_cash(account["cash"] - notional - fee)

        existing = self.store.get_position(symbol)
        if existing is None:
            self.store.upsert_position(symbol, filled_qty, avg_price, now)
            return
        new_qty = existing["quantity"] + filled_qty
        old_cost = existing["quantity"] * existing["average_price"]
        new_avg = (old_cost + filled_qty * avg_price) / new_qty
        self.store.upsert_position(symbol, new_qty, new_avg, existing["opened_at"])

    def _apply_sell(
        self,
        symbol: str,
        filled_qty: int,
        avg_price: float,
        fee: float,
        now: str,
        contract: OptionContract,
        signal_id: object,
    ) -> None:
        position = self.store.get_position(symbol)
        entry_price = position["average_price"]
        opened_at = position["opened_at"]

        notional = avg_price * filled_qty * contract.contract_size
        account = self.store.get_account()
        self.store.update_cash(account["cash"] + notional - fee)

        remaining = position["quantity"] - filled_qty
        self.store.upsert_position(symbol, remaining, entry_price, opened_at)

        pnl_pct = (avg_price - entry_price) / entry_price * 100 if entry_price else 0.0
        pnl_absolute = (avg_price - entry_price) * filled_qty * contract.contract_size - fee
        self.store.record_trade(
            {
                "trade_id": str(uuid.uuid4()),
                "symbol": symbol,
                "quantity": filled_qty,
                "entry_price": entry_price,
                "exit_price": avg_price,
                "fee_paid": fee,
                "pnl_absolute": pnl_absolute,
                "pnl_pct": pnl_pct,
                "opened_at": opened_at,
                "closed_at": now,
                "signal_id": signal_id,
                "close_reason": CLOSE_REASON_MANUAL,
            }
        )

    def _contract_size(self, symbol: str) -> int:
        contract = self.resolve_contract(symbol)
        return contract.contract_size if contract else 1

    def _rejected(
        self, order_id: str, symbol: str, side: str, quantity: int, now: str, reason: str
    ) -> Order:
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=0.0,
            status=OrderStatus.REJECTED,
            filled_quantity=0,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
            metadata={"reason": reason},
        )
        self._save_order(order, fee=0.0, signal_id=None)
        logger.info("سفارش کاغذی رد شد (%s): %s", symbol, reason)
        return order

    def _save_order(self, order: Order, fee: float, signal_id: object) -> None:
        self.store.save_order(
            {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": order.quantity,
                "filled_quantity": order.filled_quantity,
                "avg_fill_price": order.price,
                "status": order.status.value,
                "fee_paid": fee,
                "signal_id": signal_id,
                "created_at": order.created_at.isoformat(timespec="seconds"),
                "updated_at": order.updated_at.isoformat(timespec="seconds"),
                "metadata": json.dumps(order.metadata, ensure_ascii=False),
            }
        )

    # -- امکانات فراتر از اینترفیس (لازم برای داشبورد) -------------------------
    def unrealized_pnl(self) -> dict[str, dict[str, float]]:
        """P&L شناور هر پوزیشن باز، با قیمت بستن (سمت فروش) از عمق واقعی."""
        result: dict[str, dict[str, float]] = {}
        for row in self.store.list_positions():
            symbol = row["symbol"]
            contract = self.resolve_contract(symbol)
            if contract is None or not contract.ins_code:
                continue
            book: OrderBook | None = self.order_book_client.try_get_order_book(
                contract.ins_code, symbol
            )
            if book is None:
                continue
            mark_price, _ = book.fill_price("sell", row["quantity"])
            if mark_price is None:
                continue
            price_diff = mark_price - row["average_price"]
            pnl_absolute = price_diff * row["quantity"] * contract.contract_size
            pnl_pct = price_diff / row["average_price"] * 100
            result[symbol] = {
                "mark_price": mark_price,
                "pnl_absolute": pnl_absolute,
                "pnl_pct": pnl_pct,
            }
        return result

    def settle_expired_positions(self, today: date | None = None) -> list[dict[str, object]]:
        """بستن خودکار پوزیشن‌هایی که سررسیدشان گذشته، با آخرین قیمت واقعی معامله.

        هرگز قیمت تخمینی نمی‌سازد: اگر قرارداد یا آخرین قیمت آن یافت نشود،
        پوزیشن دست‌نخورده باقی می‌ماند تا کاربر بعداً دستی رسیدگی کند.
        """
        settled = []
        for row in self.store.list_positions():
            symbol = row["symbol"]
            contract = self.resolve_contract(symbol)
            if contract is None:
                continue
            if contract.days_to_expiry(today) > 0:
                continue
            settlement_price = contract.last_price
            if settlement_price is None:
                logger.warning("قیمت تسویه برای %s در دسترس نیست؛ پوزیشن باز می‌ماند.", symbol)
                continue

            now = datetime.now().isoformat(timespec="seconds")
            notional = settlement_price * row["quantity"] * contract.contract_size
            fee = self.fees.exit_cost(notional, was_buy=True)
            account = self.store.get_account()
            self.store.update_cash(account["cash"] + notional - fee)
            self.store.delete_position(symbol)

            price_diff = settlement_price - row["average_price"]
            pnl_pct = price_diff / row["average_price"] * 100
            pnl_absolute = price_diff * row["quantity"] * contract.contract_size - fee
            trade = {
                "trade_id": str(uuid.uuid4()),
                "symbol": symbol,
                "quantity": row["quantity"],
                "entry_price": row["average_price"],
                "exit_price": settlement_price,
                "fee_paid": fee,
                "pnl_absolute": pnl_absolute,
                "pnl_pct": pnl_pct,
                "opened_at": row["opened_at"],
                "closed_at": now,
                "signal_id": None,
                "close_reason": CLOSE_REASON_EXPIRY,
            }
            self.store.record_trade(trade)
            settled.append(trade)
        return settled

    def performance_summary(self, days: int | None = None) -> dict[str, object]:
        """معیارهای حرفه‌ای روی معاملات کاغذی بسته‌شده — همان تابع بک‌تست/گزارش زنده."""
        from backtest import metrics

        pnl_pcts = [t["pnl_pct"] for t in self.store.list_trades(days)]
        return metrics.summarize(pnl_pcts)

    def reset(self) -> dict[str, object]:
        """پاک‌کردن کامل حساب کاغذی و بازگرداندن موجودی به مقدار اولیه."""
        account = self.store.get_account()
        initial_balance = account["initial_balance"] if account else 0.0
        return self.store.reset(initial_balance)

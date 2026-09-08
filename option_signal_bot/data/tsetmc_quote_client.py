"""قیمت لحظه‌ای و عمق بازار یک نماد از TSETMC.

فرق این ماژول با `tsetmc_option_chain_client`:

* آن یکی **کل بازار** را در یک درخواست می‌گیرد (برای ساخت زنجیره)، ولی
  فقط بهترین مظنه را دارد.
* این یکی **یک نماد** را می‌گیرد ولی **۵ سطح عمق** می‌دهد — برای وقتی
  که می‌خواهید ببینید پشت بهترین مظنه چقدر حجم هست.

⚠️ **دامنه مجاز نوسان اینجا نیست.** `priceMin`/`priceMax` در پاسخ
TSETMC **کمترین و بیشترین معامله‌ی امروز** است، نه سقف و کف مجاز.
اشتباه‌گرفتن این دو باعث می‌شود سفارش‌های معتبر رد شوند. تنها منبع
دامنه‌ی مجاز، `lowAllowedPrice`/`highAllowedPrice` در API کارگزاری است.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from data.tsetmc_http import DEFAULT_USER_AGENT, fetch_json

logger = logging.getLogger(__name__)

BEST_LIMITS_URL = "https://cdn.tsetmc.com/api/BestLimits/{ins_code}"
CLOSING_INFO_URL = "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceInfo/{ins_code}"


@dataclass(frozen=True)
class DepthLevel:
    """یک سطح از عمق بازار."""

    level: int
    bid_price: float
    bid_quantity: int
    bid_orders: int
    ask_price: float
    ask_quantity: int
    ask_orders: int

    @property
    def spread(self) -> float | None:
        """اسپرد این سطح؛ اگر یک طرف خالی باشد `None`."""
        if self.bid_price <= 0 or self.ask_price <= 0:
            return None
        return self.ask_price - self.bid_price


def parse_best_limits(payload: dict[str, Any], label: str = "") -> tuple[DepthLevel, ...]:
    """پاسخ خام `BestLimits` را به سطوح عمق تبدیل می‌کند.

    **تنها جای نگاشتِ این پاسخ در پروژه.** `data/order_book.py` هم همین
    را صدا می‌زند: دو نگاشت موازی از یک پاسخ، دیر یا زود با هم واگرا
    می‌شوند و بعد دو عدد مختلف برای «بهترین مظنه» می‌دهند.

    نگاشت: `pMeDem`/`qTitMeDem`/`zOrdMeDem` سمت **خرید** (تقاضا) و
    `pMeOf`/`qTitMeOf`/`zOrdMeOf` سمت **فروش** (عرضه).
    """
    rows = payload.get("bestLimits")
    if not isinstance(rows, list):
        logger.warning("عمق بازار %s در دسترس نبود.", label)
        return ()

    levels = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            levels.append(
                DepthLevel(
                    level=int(row.get("number") or 0),
                    bid_price=float(row.get("pMeDem") or 0),
                    bid_quantity=int(row.get("qTitMeDem") or 0),
                    bid_orders=int(row.get("zOrdMeDem") or 0),
                    ask_price=float(row.get("pMeOf") or 0),
                    ask_quantity=int(row.get("qTitMeOf") or 0),
                    ask_orders=int(row.get("zOrdMeOf") or 0),
                )
            )
        except (TypeError, ValueError):
            # یک سطح خراب نباید کل عمق را از بین ببرد
            logger.debug("سطح عمق ناخوانا برای %s رد شد.", label)

    levels.sort(key=lambda d: d.level)
    return tuple(levels)


@dataclass(frozen=True)
class LiveQuote:
    """وضعیت لحظه‌ای یک نماد، با عمق بازار."""

    ins_code: str
    last_price: float
    closing_price: float
    previous_close: float
    first_price: float
    day_high: float
    day_low: float
    total_volume: float
    total_value: float
    trade_count: int
    as_of: datetime
    depth: tuple[DepthLevel, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def best_bid(self) -> float | None:
        return self.depth[0].bid_price if self.depth and self.depth[0].bid_price > 0 else None

    @property
    def best_ask(self) -> float | None:
        return self.depth[0].ask_price if self.depth and self.depth[0].ask_price > 0 else None

    @property
    def spread(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        return None if bid is None or ask is None else ask - bid

    @property
    def relative_spread(self) -> float | None:
        """اسپرد نسبت به میانگین مظنه — معیار نقدشوندگی.

        بالای ~۵۰٪ یعنی عملاً غیرقابل معامله.
        """
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        mid = (bid + ask) / 2
        return None if mid <= 0 else (ask - bid) / mid

    @property
    def price_change_pct(self) -> float | None:
        if self.previous_close <= 0:
            return None
        return (self.last_price - self.previous_close) / self.previous_close * 100

    def bid_depth(self, levels: int = 5) -> int:
        """مجموع حجم خرید تا این تعداد سطح."""
        return sum(d.bid_quantity for d in self.depth[:levels] if d.bid_price > 0)

    def ask_depth(self, levels: int = 5) -> int:
        return sum(d.ask_quantity for d in self.depth[:levels] if d.ask_price > 0)


class TsetmcQuoteClient:
    """قیمت لحظه‌ای و عمق بازار یک نماد.

    هر فراخوان **دو درخواست** می‌زند (قیمت + عمق)، پس برای پیمایش کل
    بازار مناسب نیست؛ برای آن از `TsetmcOptionChainClient` استفاده کنید
    که کل بازار را با یک درخواست می‌دهد.
    """

    source_name = "tsetmc-live"

    def __init__(
        self,
        timeout: int = 15,
        retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent

    def get_quote(self, ins_code: str, with_depth: bool = True) -> LiveQuote:
        """وضعیت لحظه‌ای یک نماد با کد یکتای TSETMC."""
        if not ins_code:
            raise ValueError("ins_code خالی است.")

        payload = fetch_json(
            CLOSING_INFO_URL.format(ins_code=ins_code),
            timeout=self.timeout,
            retries=self.retries,
            user_agent=self.user_agent,
            label=f"قیمت لحظه‌ای {ins_code}",
        )
        info = payload.get("closingPriceInfo")
        if not isinstance(info, dict):
            raise ValueError(
                f"پاسخ قیمت لحظه‌ای برای {ins_code} شکل مورد انتظار را ندارد."
            )

        depth: tuple[DepthLevel, ...] = ()
        if with_depth:
            depth = self.get_depth(ins_code)

        return LiveQuote(
            ins_code=str(ins_code),
            last_price=float(info.get("pDrCotVal") or 0),
            closing_price=float(info.get("pClosing") or 0),
            previous_close=float(info.get("priceYesterday") or 0),
            first_price=float(info.get("priceFirst") or 0),
            # ⚠️ این دو «بیشترین/کمترین معامله‌ی امروز» است، نه دامنه‌ی مجاز
            day_high=float(info.get("priceMax") or 0),
            day_low=float(info.get("priceMin") or 0),
            total_volume=float(info.get("qTotTran5J") or 0),
            total_value=float(info.get("qTotCap") or 0),
            trade_count=int(info.get("zTotTran") or 0),
            as_of=datetime.now(),
            depth=depth,
            raw=info,
        )

    def get_depth(self, ins_code: str) -> tuple[DepthLevel, ...]:
        """۵ سطح عمق بازار."""
        payload = fetch_json(
            BEST_LIMITS_URL.format(ins_code=ins_code),
            timeout=self.timeout,
            retries=self.retries,
            user_agent=self.user_agent,
            label=f"عمق بازار {ins_code}",
        )
        return parse_best_limits(payload, ins_code)

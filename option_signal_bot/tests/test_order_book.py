"""تست عمق مظنه — روی پاسخ **واقعیِ** ضبط‌شده‌ی `BestLimits`.

فیکسچر عمداً دو نماد متضاد دارد:

* `liquid_call` — بهترین فروش فقط **۱ قرارداد** دارد. دقیقاً همان تله‌ای
  که با مظنه‌ی تک‌سطحی دیده نمی‌شود.
* `thin_put` — دفتری با سطوح خالی (قیمت و حجم صفر) در انتها.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data.order_book import BookLevel, OrderBook, OrderBookClient

FIXTURE = Path(__file__).parent / "fixtures" / "tsetmc_best_limits.json"


@pytest.fixture(scope="module")
def payloads() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def liquid(payloads) -> OrderBook:
    return OrderBook.from_tsetmc("ضهرم6040", payloads["liquid_call"]["payload"])


@pytest.fixture
def thin(payloads) -> OrderBook:
    return OrderBook.from_tsetmc("طهرم6040", payloads["thin_put"]["payload"])


# ----------------------------------------------------------------------
# نگاشت پاسخ خام
# ----------------------------------------------------------------------
def test_parses_real_levels(liquid):
    # سطح پنجمِ خرید در پاسخ واقعی صفر است و نباید سطح شمرده شود
    assert len(liquid.bids) == 4
    assert len(liquid.asks) == 5


def test_empty_levels_are_dropped(thin):
    assert len(thin.bids) == 3  # دو سطح آخرِ خرید صفرند
    assert len(thin.asks) == 5
    assert all(level.is_real for level in thin.bids + thin.asks)


def test_maps_price_quantity_and_order_count(liquid):
    best_ask = liquid.asks[0]
    assert best_ask.price == 33800.0
    assert best_ask.quantity == 1
    assert best_ask.orders == 1


def test_sides_are_sorted_best_first(liquid, thin):
    for book in (liquid, thin):
        bids = [lv.price for lv in book.bids]
        asks = [lv.price for lv in book.asks]
        assert bids == sorted(bids, reverse=True), "خرید باید نزولی باشد"
        assert asks == sorted(asks), "فروش باید صعودی باشد"


def test_sorting_does_not_trust_source_order():
    """اگر منبع سطوح را به‌هم‌ریخته بدهد، باز هم بهترین مظنه درست باشد."""
    payload = {
        "bestLimits": [
            {"pMeDem": 10, "qTitMeDem": 5, "pMeOf": 30, "qTitMeOf": 5},
            {"pMeDem": 20, "qTitMeDem": 5, "pMeOf": 25, "qTitMeOf": 5},
        ]
    }
    book = OrderBook.from_tsetmc("X", payload)
    assert book.best_bid == 20
    assert book.best_ask == 25


def test_missing_and_null_fields_do_not_crash():
    payload = {"bestLimits": [{"pMeDem": None, "qTitMeDem": "x"}, "not-a-dict", {}]}
    book = OrderBook.from_tsetmc("X", payload)
    assert book.bids == () and book.asks == ()


def test_empty_payload():
    book = OrderBook.from_tsetmc("X", {})
    assert book.best_bid is None and book.best_ask is None
    assert book.spread is None and book.relative_spread is None


# ----------------------------------------------------------------------
# بهترین مظنه و اسپرد
# ----------------------------------------------------------------------
def test_best_quotes_and_spread(thin):
    assert thin.best_bid == 3.0
    assert thin.best_ask == 10.0
    assert thin.spread == 7.0
    assert thin.relative_spread == pytest.approx(7.0 / 6.5)


# ----------------------------------------------------------------------
# عمق و قیمت پر شدن
# ----------------------------------------------------------------------
def test_depth_sums_each_side(liquid):
    # خرید ما روی فروش‌های دفتر پر می‌شود
    assert liquid.depth("buy") == 1 + 101 + 5 + 1 + 3
    assert liquid.depth("sell") == 100 + 300 + 1000 + 1127


def test_buy_walks_the_ask_side_not_the_bid(liquid):
    """وارونگی سمت‌ها منبع رایج اشتباه است — اینجا صریح تست می‌شود."""
    avg, filled = liquid.fill_price("buy", 1)
    assert filled == 1
    assert avg == liquid.best_ask  # نه best_bid


def test_sell_walks_the_bid_side(liquid):
    avg, filled = liquid.fill_price("sell", 100)
    assert filled == 100
    assert avg == liquid.best_bid


def test_single_contract_fills_at_best_price(liquid):
    avg, filled = liquid.fill_price("buy", 1)
    assert (avg, filled) == (33800.0, 1)


def test_larger_order_is_worse_than_the_top_level(liquid):
    """قلب ماجرا: سطح اول فقط ۱ قرارداد دارد، بقیه گران‌تر پر می‌شود."""
    top, _ = liquid.fill_price("buy", 1)
    ten, filled = liquid.fill_price("buy", 10)
    assert filled == 10
    assert ten > top
    # (1×33800 + 9×34000) / 10
    assert ten == pytest.approx((33800 + 9 * 34000) / 10)


def test_fill_price_is_volume_weighted_across_levels(liquid):
    avg, filled = liquid.fill_price("buy", 107)
    assert filled == 107
    expected = (1 * 33800 + 101 * 34000 + 5 * 34950) / 107
    assert avg == pytest.approx(expected)


def test_partial_fill_reports_the_shortfall(liquid):
    """وقتی عمق کم است، باید بدانیم سفارش پر نمی‌شود — نه اینکه خوش‌بین باشیم."""
    total = liquid.depth("buy")
    avg, filled = liquid.fill_price("buy", total + 500)
    assert filled == total
    assert avg is not None


def test_zero_or_negative_quantity():
    book = OrderBook("X", asks=(BookLevel(10, 5),))
    assert book.fill_price("buy", 0) == (None, 0)
    assert book.fill_price("buy", -3) == (None, 0)


def test_fill_on_empty_side():
    book = OrderBook("X", asks=(BookLevel(10, 5),))
    assert book.fill_price("sell", 1) == (None, 0)


def test_invalid_side_raises():
    with pytest.raises(ValueError, match="سمت نامعتبر"):
        OrderBook("X").depth("sideways")


def test_persian_side_names_work(liquid):
    assert liquid.depth("خرید") == liquid.depth("buy")
    assert liquid.depth("فروش") == liquid.depth("sell")


# ----------------------------------------------------------------------
# لغزش و امکان اجرا
# ----------------------------------------------------------------------
def test_slippage_is_zero_at_the_top_level(liquid):
    assert liquid.slippage("buy", 1) == pytest.approx(0.0)


def test_slippage_grows_with_size(liquid):
    small = liquid.slippage("buy", 5)
    large = liquid.slippage("buy", 100)
    assert 0 < small < large


def test_slippage_is_none_when_order_cannot_fill(liquid):
    """لغزشِ سفارشی که پر نمی‌شود بی‌معناست؛ عدد دادن اینجا گمراه‌کننده است."""
    assert liquid.slippage("buy", liquid.depth("buy") + 1) is None


def test_can_fill(liquid):
    total = liquid.depth("buy")
    assert liquid.can_fill("buy", total)
    assert not liquid.can_fill("buy", total + 1)


# ----------------------------------------------------------------------
# سریالایز
# ----------------------------------------------------------------------
def test_to_dict_round_trips_through_json(liquid):
    data = liquid.to_dict()
    assert json.loads(json.dumps(data, ensure_ascii=False))["best_ask"] == 33800.0
    assert len(data["asks"]) == 5
    assert data["ask_depth"] == liquid.depth("buy")


# ----------------------------------------------------------------------
# کلاینت
# ----------------------------------------------------------------------
class _FakeFetch:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error
        self.calls = 0

    def __call__(self, url, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


def test_client_caches_within_ttl(monkeypatch, payloads):
    fake = _FakeFetch(payloads["liquid_call"]["payload"])
    monkeypatch.setattr("data.order_book.fetch_json", fake)

    client = OrderBookClient(ttl_seconds=60.0)
    first = client.get_order_book("123", "ضهرم6040")
    second = client.get_order_book("123", "ضهرم6040")

    assert fake.calls == 1, "درخواست دوم باید از کش بیاید"
    assert first.best_ask == second.best_ask


def test_client_refetches_after_ttl(monkeypatch, payloads):
    """کش عمداً کوتاه است: عمقِ کهنه اعتماد کاذب می‌سازد."""
    fake = _FakeFetch(payloads["liquid_call"]["payload"])
    monkeypatch.setattr("data.order_book.fetch_json", fake)

    client = OrderBookClient(ttl_seconds=0.0)
    client.get_order_book("123")
    client.get_order_book("123")
    assert fake.calls == 2


def test_client_raises_on_failure(monkeypatch):
    monkeypatch.setattr(
        "data.order_book.fetch_json", _FakeFetch(None, error=RuntimeError("شبکه"))
    )
    with pytest.raises(RuntimeError):
        OrderBookClient().get_order_book("123")


def test_try_get_swallows_failure(monkeypatch):
    """عمق یک افزونه است؛ نبودش نباید پاس رصد را بخواباند."""
    monkeypatch.setattr(
        "data.order_book.fetch_json", _FakeFetch(None, error=RuntimeError("شبکه"))
    )
    assert OrderBookClient().try_get_order_book("123", "ضهرم6040") is None


# ----------------------------------------------------------------------
# اتصال عمق به نقشه‌ی سفارش داشبورد
# ----------------------------------------------------------------------
pytest.importorskip("fastapi")


def _structures(action="BUY", quantity=10, ins_code="123"):
    return {
        "long_straddle": [
            {"legs": [{"action": action, "quantity": quantity,
                       "ins_code": ins_code, "symbol": "ضهرم6040"}]}
        ]
    }


def test_attach_depth_reports_fill_and_slippage(monkeypatch, payloads):
    from web.api import _attach_depth

    monkeypatch.setattr(
        "data.order_book.fetch_json", _FakeFetch(payloads["liquid_call"]["payload"])
    )
    data = _structures(quantity=10)
    _attach_depth(data)

    depth = data["long_straddle"][0]["legs"][0]["depth"]
    assert depth["fully_fillable"] is True
    assert depth["filled_quantity"] == 10
    # بهترین فروش فقط ۱ قرارداد دارد، پس میانگین باید بدتر از آن باشد
    assert depth["fill_price"] > 33800.0
    assert depth["slippage"] > 0


def test_attach_depth_flags_an_unfillable_leg(monkeypatch, payloads):
    """ساختاری که پایه‌اش پر نمی‌شود باید صریح علامت بخورد، نه ساکت بماند."""
    from web.api import _attach_depth

    monkeypatch.setattr(
        "data.order_book.fetch_json", _FakeFetch(payloads["liquid_call"]["payload"])
    )
    data = _structures(quantity=100_000)
    _attach_depth(data)

    depth = data["long_straddle"][0]["legs"][0]["depth"]
    assert depth["fully_fillable"] is False
    assert depth["filled_quantity"] < 100_000
    assert depth["slippage"] is None


def test_attach_depth_uses_the_correct_side(monkeypatch, payloads):
    """فروش باید روی سمت خرید دفتر حساب شود، نه سمت فروش."""
    from web.api import _attach_depth

    monkeypatch.setattr(
        "data.order_book.fetch_json", _FakeFetch(payloads["liquid_call"]["payload"])
    )
    sell = _structures(action="SELL", quantity=1)
    _attach_depth(sell)
    assert sell["long_straddle"][0]["legs"][0]["depth"]["fill_price"] == 157.0


def test_attach_depth_skips_legs_without_ins_code(monkeypatch, payloads):
    """سهم پایه در کالر کد آپشن ندارد؛ نباید درخواستی برایش برود."""
    from web.api import _attach_depth

    fake = _FakeFetch(payloads["liquid_call"]["payload"])
    monkeypatch.setattr("data.order_book.fetch_json", fake)

    data = _structures(ins_code="")
    _attach_depth(data)
    assert data["long_straddle"][0]["legs"][0]["depth"] is None
    assert fake.calls == 0


def test_attach_depth_survives_network_failure(monkeypatch):
    """عمق یک افزونه است؛ شکستش نباید کل اسکن را بی‌نتیجه کند."""
    from web.api import _attach_depth

    monkeypatch.setattr(
        "data.order_book.fetch_json", _FakeFetch(None, error=RuntimeError("شبکه"))
    )
    data = _structures()
    _attach_depth(data)
    assert data["long_straddle"][0]["legs"][0]["depth"] is None


# ----------------------------------------------------------------------
# قرارداد منبع مظنه
# ----------------------------------------------------------------------
def test_client_implements_the_quote_source_contract():
    """هسته باید فقط قرارداد را بشناسد، نه TSETMC را.

    بدون این، افزودن مسیر push (Lightstreamer ایزی‌تریدر) یعنی تغییر در
    همه‌ی مصرف‌کننده‌ها.
    """
    from data.quote_source import RealtimeQuoteSource

    assert issubclass(OrderBookClient, RealtimeQuoteSource)
    assert OrderBookClient.name == "tsetmc"


def test_tsetmc_source_declares_itself_as_polling():
    """مصرف‌کننده با همین تصمیم می‌گیرد هر چند وقت بپرسد."""
    assert OrderBookClient.is_push is False


def test_try_get_comes_from_the_base_contract():
    """رفتار «خطا را بخور» یک جا تعریف شده، نه در هر پیاده‌سازی."""
    from data.quote_source import RealtimeQuoteSource

    assert "try_get_order_book" not in vars(OrderBookClient)
    assert "try_get_order_book" in vars(RealtimeQuoteSource)


def test_a_custom_source_gets_error_swallowing_for_free():
    """پیاده‌سازی تازه فقط `get_order_book` را لازم دارد."""
    from data.quote_source import RealtimeQuoteSource

    class _Broken(RealtimeQuoteSource):
        name = "broken"

        def get_order_book(self, ins_code, symbol=""):
            raise RuntimeError("شبکه")

    assert _Broken().try_get_order_book("1") is None


def test_the_contract_cannot_be_instantiated():
    from data.quote_source import RealtimeQuoteSource

    with pytest.raises(TypeError):
        RealtimeQuoteSource()


def test_quote_source_does_not_reach_the_execution_layer():
    """قرارداد سخت پروژه، با AST و نه جستجوی متن."""
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "data" / "quote_source.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "execution" not in imported


# ----------------------------------------------------------------------
# یکپارچگیِ نگاشت — یک پارسر، دو مصرف‌کننده
# ----------------------------------------------------------------------
def test_best_limits_is_parsed_in_exactly_one_place(payloads):
    """دو نگاشت موازی از یک پاسخ، دیر یا زود واگرا می‌شوند و بعد دو عدد
    مختلف برای «بهترین مظنه» می‌دهند."""
    from data.tsetmc_quote_client import parse_best_limits

    payload = payloads["liquid_call"]["payload"]
    levels = parse_best_limits(payload, "x")
    direct = OrderBook.from_tsetmc("x", payload)
    via_depth = OrderBook.from_depth("x", levels)

    assert direct.bids == via_depth.bids
    assert direct.asks == via_depth.asks


def test_shared_parser_keeps_order_book_semantics(payloads):
    """سطوح خالی حذف و ترتیب تضمین می‌شود، حتی از مسیر پارسر مشترک."""
    from data.tsetmc_quote_client import parse_best_limits

    book = OrderBook.from_depth(
        "x", parse_best_limits(payloads["thin_put"]["payload"], "x")
    )
    assert all(lv.is_real for lv in book.bids + book.asks)
    assert [lv.price for lv in book.asks] == sorted(lv.price for lv in book.asks)


def test_quote_client_depth_feeds_the_order_book(payloads, monkeypatch):
    """مسیر واقعی: کلاینت مظنه عمق می‌دهد، دفتر سفارش منطق را اضافه می‌کند."""
    from data.tsetmc_quote_client import TsetmcQuoteClient

    monkeypatch.setattr(
        "data.tsetmc_quote_client.fetch_json",
        lambda *a, **k: payloads["liquid_call"]["payload"],
    )
    levels = TsetmcQuoteClient().get_depth("123")
    assert levels

    book = OrderBook.from_depth("ضهرم6040", levels)
    assert book.best_ask == 33800.0
    # همان منطقی که قبلاً تست شد، از مسیر تازه
    avg, filled = book.fill_price("buy", 10)
    assert filled == 10 and avg > book.best_ask


def test_malformed_rows_are_dropped_by_the_shared_parser():
    from data.tsetmc_quote_client import parse_best_limits

    levels = parse_best_limits({"bestLimits": ["junk", None, {}]}, "x")
    book = OrderBook.from_depth("x", levels)
    assert book.bids == () and book.asks == ()


def test_missing_best_limits_key_returns_no_levels():
    from data.tsetmc_quote_client import parse_best_limits

    assert parse_best_limits({}, "x") == ()

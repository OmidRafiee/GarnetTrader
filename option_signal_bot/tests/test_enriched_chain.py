"""تست‌های غنی‌سازی زنجیره با داده‌ی کارگزاری.

نکته‌ی محوری: ایزی‌تریدر مشخصات قرارداد را **تک‌به‌تک** می‌دهد، پس
غنی‌سازی باید محدود بماند وگرنه هر پاس رصد دقایق طول می‌کشد.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brokers.base import AccountDataSource, OptionContractSpec
from data.enriched_option_chain import BrokerEnrichedOptionChain
from data.tsetmc_option_chain_client import FilePayloadSource, TsetmcOptionChainClient

FIXTURE = Path(__file__).parent / "fixtures" / "tsetmc_option_market_watch.json"
UNDERLYING = "خودرو"


class FakeAccount(AccountDataSource):
    """کارگزاری جعلی که هر درخواست را می‌شمارد."""

    name = "fake"

    def __init__(self, contract_size: int = 500, fail_on: set[str] | None = None):
        self.calls: list[str] = []
        self.contract_size = contract_size
        self.fail_on = fail_on or set()

    def is_authenticated(self) -> bool:
        return True

    def get_positions(self):
        return []

    def get_underlying_limit(self, base_isin, end_date):
        raise NotImplementedError

    def get_contract_spec(self, symbol_isin: str) -> OptionContractSpec:
        self.calls.append(symbol_isin)
        if symbol_isin in self.fail_on:
            raise RuntimeError("قرارداد پیدا نشد")
        return OptionContractSpec(
            symbol_isin=symbol_isin,
            strike_price=0.0,
            contract_size=self.contract_size,
            base_isin="",
            initial_margin=1_000_000,
            required_margin=900_000,
            maintenance_margin=800_000,
            max_orders=50,
        )


@pytest.fixture
def base_chain():
    if not FIXTURE.exists():
        pytest.skip("نمونه‌ی ضبط‌شده نیست؛ scripts/record_fixtures.py را اجرا کنید.")
    return TsetmcOptionChainClient(
        FilePayloadSource(FIXTURE), cache_ttl_seconds=float("inf")
    )


# ----------------------------------------------------------------------
# محدود ماندن تعداد درخواست — دلیل اصلی وجود این لایه
# ----------------------------------------------------------------------
def test_enrichment_is_capped_not_whole_chain(base_chain):
    """نباید برای هر قرارداد یک درخواست بزند.

    ایزی‌تریدر مشخصات را تک‌به‌تک می‌دهد؛ غنی‌سازی کل بازار یعنی ~۱۳۸۶
    درخواست و چند دقیقه در هر پاس.
    """
    account = FakeAccount()
    chain = BrokerEnrichedOptionChain(base_chain, account, enrich_limit=5)
    result = chain.get_chain(UNDERLYING)

    assert len(account.calls) == 5
    assert len(result.contracts) > 5, "بقیه‌ی زنجیره باید سالم بماند"


def test_zero_limit_skips_broker_entirely(base_chain):
    account = FakeAccount()
    chain = BrokerEnrichedOptionChain(base_chain, account, enrich_limit=0)
    chain.get_chain(UNDERLYING)
    assert account.calls == []


def test_enriched_contracts_are_nearest_to_spot(base_chain):
    """قراردادهای نزدیک به قیمت پایه غنی می‌شوند، نه تصادفی.

    استراتژی معمولاً همان‌ها را انتخاب می‌کند؛ غنی‌سازی دورترین‌ها هدر
    دادن درخواست است.
    """
    account = FakeAccount()
    chain = BrokerEnrichedOptionChain(base_chain, account, enrich_limit=4)
    result = chain.get_chain(UNDERLYING)

    enriched = [c for c in result.contracts if c.contract_size == 500]
    others = [c for c in result.contracts if c.contract_size != 500]
    assert enriched, "چیزی غنی نشد"

    spot = result.spot_price
    if others:
        assert max(abs(c.strike - spot) for c in enriched) <= max(
            abs(c.strike - spot) for c in others
        )


# ----------------------------------------------------------------------
# صداقت درباره‌ی منبع
# ----------------------------------------------------------------------
def test_source_name_does_not_claim_broker_when_absent(base_chain):
    """بدون کارگزاری نباید وانمود کند داده‌ی کارگزاری دارد.

    برچسب منبع روی هر سیگنال می‌نشیند؛ ادعای غلط یعنی کاربر فکر کند
    وجه تضمین از کارگزاری آمده در حالی که نیامده.
    """
    chain = BrokerEnrichedOptionChain(base_chain, None)
    assert "emofid" not in chain.source_name


def test_source_name_includes_broker_when_present(base_chain):
    chain = BrokerEnrichedOptionChain(base_chain, FakeAccount())
    assert "emofid" in chain.source_name


# ----------------------------------------------------------------------
# مقاومت
# ----------------------------------------------------------------------
def test_broker_failure_leaves_chain_usable(base_chain):
    """شکست کارگزاری نباید زنجیره را از بین ببرد.

    نبود وجه تضمین بدتر از نبود کل زنجیره نیست.
    """
    plain = base_chain.get_chain(UNDERLYING)

    broken = FakeAccount()
    broken.get_contract_spec = lambda isin: (_ for _ in ()).throw(RuntimeError("قطع"))
    chain = BrokerEnrichedOptionChain(base_chain, broken, enrich_limit=5)

    result = chain.get_chain(UNDERLYING)
    assert len(result.contracts) == len(plain.contracts)


def test_failed_symbols_are_not_retried_every_pass(base_chain):
    """نماد شکست‌خورده نباید هر پاس دوباره درخواست بزند.

    نمادهای منقضی همیشه شکست می‌خورند؛ تلاش مکرر فقط پاس را کند می‌کند.
    """
    account = FakeAccount()
    account.fail_on = {c.symbol for c in base_chain.get_chain(UNDERLYING).contracts}

    chain = BrokerEnrichedOptionChain(base_chain, account, enrich_limit=3)
    chain.get_chain(UNDERLYING)
    first = len(account.calls)

    chain.get_chain(UNDERLYING)
    assert len(account.calls) == first, "نباید دوباره تلاش می‌کرد"


def test_margin_lookup_is_cached(base_chain):
    account = FakeAccount()
    chain = BrokerEnrichedOptionChain(base_chain, account, enrich_limit=3)

    chain.get_chain(UNDERLYING)
    first = len(account.calls)
    chain.get_chain(UNDERLYING)

    assert len(account.calls) == first, "نتیجه باید کش شود"


def test_margin_for_symbol_returns_none_without_broker(base_chain):
    chain = BrokerEnrichedOptionChain(base_chain, None)
    assert chain.margin_for_symbol("IRO9IKCO6K41") is None


def test_margin_for_symbol_exposes_broker_numbers(base_chain):
    chain = BrokerEnrichedOptionChain(base_chain, FakeAccount())
    margin = chain.margin_for_symbol("IRO9IKCO6K41")

    assert margin is not None
    assert margin.initial_margin == 1_000_000
    assert margin.required_margin == 900_000


def test_delegates_quality_report_and_underlyings(base_chain):
    """پوشش نباید متدهای مفید منبع پایه را قطع کند."""
    chain = BrokerEnrichedOptionChain(base_chain, FakeAccount())
    chain.get_chain(UNDERLYING)

    assert chain.last_quality_report is not None
    assert UNDERLYING in chain.available_underlyings()

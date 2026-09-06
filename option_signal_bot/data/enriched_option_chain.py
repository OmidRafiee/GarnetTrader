"""زنجیره‌ی آپشن با غنی‌سازی از کارگزاری.

**چرا این لایه لازم است**

هیچ‌کدام از دو منبع به‌تنهایی کافی نیستند:

| نیاز | TSETMC | ایزی‌تریدر |
|---|---|---|
| زنجیره‌ی کامل (strike، سررسید، اندازه) | ✅ یک درخواست | ⚠️ هر ISIN یک درخواست |
| مظنه و موقعیت باز | ✅ | ✅ |
| وجه تضمین اولیه/لازم | ❌ | ✅ |
| سقف موقعیت مشتری/بازار | ❌ | ✅ |

ایزی‌تریدر مشخصات قرارداد را **فقط تک‌به‌تک** می‌دهد
(`/option/api/Contracts/{isin}/symbol`). برای ~۱۳۸۶ قرارداد بازار یعنی
۱۳۸۶ درخواست و حدود ۷ دقیقه — که برای هر پاس رصد غیرعملی است.

پس این کلاس زنجیره را از منبع سریع می‌گیرد و **فقط برای تعداد محدودی
قرارداد** که واقعاً مهم‌اند، داده‌ی کارگزاری را اضافه می‌کند.

اگر کارگزاری در دسترس نباشد، زنجیره‌ی پایه بدون غنی‌سازی برمی‌گردد —
با لاگ هشدار. نبودِ وجه تضمین بهتر از نبودِ کل زنجیره است، ولی
هیچ‌وقت مقدار حدسی جای آن نمی‌نشیند.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from data.option_chain_client import OptionChain, OptionChainClient, OptionContract

if TYPE_CHECKING:
    from brokers.base import AccountDataSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContractMargin:
    """اطلاعات وجه تضمین یک قرارداد، از کارگزاری."""

    symbol_isin: str
    initial_margin: float
    required_margin: float
    maintenance_margin: float
    max_customer_open_position: int
    max_market_open_position: int
    contract_size: int


class BrokerEnrichedOptionChain(OptionChainClient):
    """زنجیره از یک منبع، غنی‌سازی از کارگزاری.

    Args:
        base: منبع زنجیره (معمولاً `TsetmcOptionChainClient`)
        account: آداپتر کارگزاری برای مشخصات قرارداد
        enrich_limit: حداکثر تعداد قراردادی که در هر پاس غنی می‌شود.
            هر کدام یک درخواست شبکه است، پس عدد بزرگ پاس را کند می‌کند.
    """

    def __init__(
        self,
        base: OptionChainClient,
        account: AccountDataSource | None = None,
        enrich_limit: int = 20,
    ) -> None:
        self.base = base
        self.account = account
        self.enrich_limit = max(enrich_limit, 0)
        self._margin_cache: dict[str, ContractMargin] = {}
        self._failed: set[str] = set()

    @property
    def source_name(self) -> str:
        """منبع را صادقانه اعلام می‌کند.

        اگر غنی‌سازی فعال نباشد، نباید وانمود کند که داده‌ی کارگزاری دارد.
        """
        base_name = getattr(self.base, "source_name", "unknown")
        if self.account is None:
            return base_name
        return f"{base_name}+emofid"

    @property
    def last_quality_report(self):
        """گزارش کیفیت را از منبع پایه عبور می‌دهد."""
        return getattr(self.base, "last_quality_report", None)

    def available_underlyings(self) -> list[str]:
        getter = getattr(self.base, "available_underlyings", None)
        return getter() if getter else []

    # ------------------------------------------------------------------
    def get_chain(self, underlying: str) -> OptionChain:
        chain = self.base.get_chain(underlying)
        if self.account is None or not self.enrich_limit:
            return chain
        return self._enrich(chain)

    def get_contract(self, option_symbol: str) -> OptionContract | None:
        return self.base.get_contract(option_symbol)

    # ------------------------------------------------------------------
    def _enrich(self, chain: OptionChain) -> OptionChain:
        """اندازه‌ی قرارداد را از کارگزاری تصحیح می‌کند.

        فقط نزدیک‌ترین قراردادها به قیمت پایه غنی می‌شوند: آن‌ها همان‌هایی
        هستند که استراتژی احتمالاً انتخاب می‌کند، و بقیه هزینه‌ی شبکه‌ی
        بی‌فایده‌اند.
        """
        spot = chain.spot_price
        ranked = sorted(chain.contracts, key=lambda c: abs(c.strike - spot))
        targets = ranked[: self.enrich_limit]

        enriched: dict[str, OptionContract] = {}
        for contract in targets:
            margin = self._margin_for(contract)
            if margin is None:
                continue
            # فقط چیزی را جایگزین می‌کنیم که کارگزاری **معتبرتر** می‌داند
            if margin.contract_size > 0 and margin.contract_size != contract.contract_size:
                logger.info(
                    "اندازه قرارداد %s از %s به %s اصلاح شد (منبع: کارگزاری).",
                    contract.symbol,
                    contract.contract_size,
                    margin.contract_size,
                )
                enriched[contract.symbol] = replace(
                    contract, contract_size=margin.contract_size
                )

        if not enriched:
            return chain

        contracts = tuple(enriched.get(c.symbol, c) for c in chain.contracts)
        return replace(chain, contracts=contracts)

    def _margin_for(self, contract: OptionContract) -> ContractMargin | None:
        """مشخصات یک قرارداد از زنجیره."""
        return self.margin_for_symbol(
            getattr(contract, "isin", None) or contract.symbol
        )

    def margin_for_symbol(self, symbol_isin: str) -> ContractMargin | None:
        """وجه تضمین و سقف موقعیت یک قرارداد، از کارگزاری.

        با کش؛ و شکست یک قرارداد در `_failed` ثبت می‌شود تا هر پاس دوباره
        تلاش نشود (نمادهای منقضی همیشه شکست می‌خورند).
        """
        if self.account is None or not symbol_isin:
            return None
        if symbol_isin in self._margin_cache:
            return self._margin_cache[symbol_isin]
        if symbol_isin in self._failed:
            return None

        try:
            spec = self.account.get_contract_spec(symbol_isin)
        except Exception as exc:
            logger.debug("مشخصات %s از کارگزاری نیامد: %s", symbol_isin, exc)
            self._failed.add(symbol_isin)
            return None

        margin = ContractMargin(
            symbol_isin=symbol_isin,
            initial_margin=spec.initial_margin,
            required_margin=spec.required_margin,
            maintenance_margin=spec.maintenance_margin,
            max_customer_open_position=spec.max_customer_open_position,
            max_market_open_position=spec.max_market_open_position,
            contract_size=spec.contract_size,
        )
        self._margin_cache[symbol_isin] = margin
        return margin

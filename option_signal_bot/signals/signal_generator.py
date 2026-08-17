"""ارکستریتور تولید سیگنال: دیتا → استراتژی‌ها → ریسک → لیست سیگنال.

⚠️ قرارداد معماری (مهم‌ترین نکته این مایل‌استون):
این ماژول **هیچ‌گاه** لایه `execution` را import یا صدا نمی‌زند. خروجی آن فقط
لیستی از `Signal` است و تصمیم اجرا کاملاً بیرون از این‌جا (و دستی) گرفته می‌شود.
تست `tests/test_signal_generator.py` این جداسازی را بررسی می‌کند.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from data.market_data_client import MarketDataClient
from data.option_chain_client import OptionChainClient
from risk.risk_calculator import RiskCalculator
from signals.signal_model import Signal
from strategies.base_strategy import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class GeneratorConfig:
    """تنظیمات حلقه تولید سیگنال."""

    symbols: list[str] = field(default_factory=lambda: ["خودرو", "فولاد"])
    risk_free_rate: float = 0.25
    history_days: int = 90
    #: تا این مدت، سیگنال تکراری برای همان نماد/سمت دوباره صادر نمی‌شود
    dedupe_window_minutes: int = 60
    #: حداقل اعتماد لازم برای انتشار سیگنال (None = بدون فیلتر)
    min_confidence: float | None = None
    signal_validity_minutes: int = 30


class SignalGenerator:
    """استراتژی‌ها را روی نمادهای هدف اجرا و سیگنال‌های نهایی را برمی‌گرداند."""

    def __init__(
        self,
        market_data: MarketDataClient,
        option_chain: OptionChainClient,
        strategies: list[BaseStrategy],
        risk_calculator: RiskCalculator | None = None,
        config: GeneratorConfig | None = None,
    ) -> None:
        self.market_data = market_data
        self.option_chain = option_chain
        self.strategies = strategies
        self.risk_calculator = risk_calculator or RiskCalculator()
        self.config = config or GeneratorConfig()
        self._last_emitted: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    def run_once(self, symbols: list[str] | None = None) -> list[Signal]:
        """یک پاس کامل روی همه نمادها؛ خطای یک نماد بقیه را متوقف نمی‌کند."""
        signals: list[Signal] = []
        for symbol in symbols or self.config.symbols:
            try:
                signals.extend(self.generate_for_symbol(symbol))
            except Exception:  # noqa: BLE001 - یک نماد خراب، حلقه اصلی را نکشد
                logger.exception("تولید سیگنال برای نماد %s شکست خورد.", symbol)
        return signals

    def generate_for_symbol(self, symbol: str) -> list[Signal]:
        """ساخت context و اجرای همه استراتژی‌ها روی یک نماد پایه."""
        context = self.build_context(symbol)
        collected: list[Signal] = []

        for strategy in self.strategies:
            try:
                raw_signals = strategy.generate(context)
            except Exception:  # noqa: BLE001 - یک استراتژی خراب، بقیه را نکشد
                logger.exception("استراتژی %s روی %s خطا داد.", strategy.name, symbol)
                continue

            for signal in raw_signals:
                final = self._post_process(signal)
                if final is not None:
                    collected.append(final)
                    logger.info("سیگنال صادر شد: %s", final.summary())
        return collected

    def build_context(self, symbol: str) -> StrategyContext:
        """جمع‌آوری داده پایه و زنجیره آپشن در یک شیء فقط-خواندنی."""
        quote = self.market_data.get_quote(symbol)
        history = self.market_data.get_history(symbol, self.config.history_days)
        chain = self.option_chain.get_chain(symbol)
        return StrategyContext(
            underlying=symbol,
            quote=quote,
            history=history,
            chain=chain,
            risk_free_rate=self.config.risk_free_rate,
            now=datetime.now(),
        )

    # ------------------------------------------------------------------
    def _post_process(self, signal: Signal) -> Signal | None:
        """اعمال فیلتر اعتماد، محاسبه ریسک و حذف تکراری‌ها."""
        min_conf = self.config.min_confidence
        if min_conf is not None and (signal.confidence or 0.0) < min_conf:
            logger.debug("سیگنال %s به‌دلیل اعتماد کم رد شد.", signal.symbol)
            return None

        contract_size = int(signal.metadata.get("contract_size", 1_000))
        sized = self.risk_calculator.apply(signal, contract_size)
        if sized is None:
            logger.info("سیگنال %s از حدود ریسک عبور کرد و صرف‌نظر شد.", signal.symbol)
            return None

        sized.valid_until = sized.created_at + timedelta(
            minutes=self.config.signal_validity_minutes
        )

        if self._is_duplicate(sized):
            logger.debug("سیگنال تکراری %s نادیده گرفته شد.", sized.symbol)
            return None
        self._last_emitted[self._dedupe_key(sized)] = sized.created_at
        return sized

    @staticmethod
    def _dedupe_key(signal: Signal) -> str:
        return "|".join(
            [signal.strategy_name, signal.symbol, signal.side.value, str(signal.strike)]
        )

    def _is_duplicate(self, signal: Signal) -> bool:
        window = timedelta(minutes=self.config.dedupe_window_minutes)
        previous = self._last_emitted.get(self._dedupe_key(signal))
        return previous is not None and signal.created_at - previous < window

    def reset_dedupe_cache(self) -> None:
        """پاک کردن حافظه ضدتکرار (برای تست و بک‌تست)."""
        self._last_emitted.clear()

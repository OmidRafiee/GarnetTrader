"""بک‌تست **کیفیت سیگنال‌ها** روی داده تاریخی — بدون اجرای هیچ سفارشی.

روش کار: استراتژی‌ها روی پنجره‌های گذشته اجرا می‌شوند، سیگنال‌ها جمع می‌شوند و
سپس با قیمت پایه در افق ارزیابی مقایسه می‌گردند. اندازه‌گیری روی **حرکت نماد پایه**
است، نه سود واقعی آپشن؛ برای مایل‌استون ۱ همین کافی است تا بفهمیم جهت‌دهی
استراتژی معنادار بوده یا نه.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from backtest import metrics
from data.market_data_client import Candle, MarketDataClient, Quote
from data.option_chain_client import OptionChain, OptionChainClient
from signals.signal_model import OptionType, Side, Signal
from strategies.base_strategy import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class SignalOutcome:
    """نتیجه ارزیابی یک سیگنال روی داده تاریخی."""

    signal: Signal
    entry_price: float
    exit_price: float
    horizon_days: int

    @property
    def underlying_return_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price * 100.0

    @property
    def directional_return_pct(self) -> float:
        """بازده هم‌راستا با جهت سیگنال (سیگنال نزولی از افت قیمت سود می‌برد)."""
        return self.underlying_return_pct * self._direction_sign()

    @property
    def is_win(self) -> bool:
        return self.directional_return_pct > 0

    def _direction_sign(self) -> int:
        bullish = (self.signal.option_type is OptionType.CALL) == (
            self.signal.side is Side.BUY
        )
        return 1 if bullish else -1


@dataclass
class BacktestReport:
    """خلاصه آماری یک بک‌تست."""

    outcomes: list[SignalOutcome] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def wins(self) -> int:
        return sum(1 for o in self.outcomes if o.is_win)

    @property
    def win_rate_pct(self) -> float:
        return self.wins / self.total * 100.0 if self.total else 0.0

    @property
    def avg_return_pct(self) -> float:
        if not self.total:
            return 0.0
        return sum(o.directional_return_pct for o in self.outcomes) / self.total

    @property
    def best_return_pct(self) -> float:
        return max((o.directional_return_pct for o in self.outcomes), default=0.0)

    @property
    def worst_return_pct(self) -> float:
        return min((o.directional_return_pct for o in self.outcomes), default=0.0)

    # ------------------------------------------------------------------
    # معیارهای حرفه‌ای — منطق در `backtest/metrics.py` است
    #
    # ⚠️ همه روی **بازده جهت‌دار نماد پایه** حساب می‌شوند، نه سود واقعی
    # آپشن. اهرم لحاظ نشده، پس این اعداد کیفیت **جهت‌دهی** سیگنال را
    # می‌سنجند، نه بازده پرتفو.
    # ------------------------------------------------------------------
    @property
    def returns(self) -> list[float]:
        """بازده جهت‌دار هر سیگنال (درصد)، به ترتیب **زمانی**.

        ترتیب زمانی برای منحنی تجمعی و حداکثر افت حیاتی است؛ ترتیب
        ورودی تضمینی ندارد.
        """
        ordered = sorted(self.outcomes, key=lambda o: o.signal.created_at)
        return [o.directional_return_pct for o in ordered]

    @property
    def median_return_pct(self) -> float | None:
        return metrics.median(self.returns)

    @property
    def stdev_return_pct(self) -> float | None:
        return metrics.stdev(self.returns)

    @property
    def avg_win_pct(self) -> float | None:
        return metrics.average_win(self.returns)

    @property
    def avg_loss_pct(self) -> float | None:
        return metrics.average_loss(self.returns)

    @property
    def expectancy_pct(self) -> float | None:
        """انتظار ریاضی هر سیگنال — مهم‌ترین عدد این گزارش.

        نرخ برد بالا با زیان‌های بزرگ می‌تواند انتظار **منفی** بدهد.
        """
        return metrics.expectancy(self.returns)

    @property
    def profit_factor(self) -> float | None:
        return metrics.profit_factor(self.returns)

    @property
    def sharpe(self) -> float | None:
        """شارپِ هر سیگنال — عمداً سالانه‌سازی نشده."""
        return metrics.sharpe(self.returns)

    @property
    def sortino(self) -> float | None:
        return metrics.sortino(self.returns)

    @property
    def equity_curve(self) -> list[float]:
        return metrics.equity_curve(self.returns)

    @property
    def max_drawdown_pct(self) -> float | None:
        return metrics.max_drawdown(self.returns)

    @property
    def longest_losing_streak(self) -> int:
        return metrics.longest_losing_streak(self.returns)

    def metrics(self) -> dict[str, float | int | None]:
        """همه‌ی معیارها در یک دیکشنری — برای API، CSV و گزارش."""
        return metrics.summarize(self.returns)

    def by_strategy(self) -> dict[str, BacktestReport]:
        grouped: dict[str, BacktestReport] = {}
        for outcome in self.outcomes:
            name = outcome.signal.strategy_name
            grouped.setdefault(name, BacktestReport()).outcomes.append(outcome)
        return grouped

    def summary(self) -> str:
        if not self.total:
            return "هیچ سیگنالی در بازه بک‌تست تولید نشد."
        def num(value: float | None, digits: int = 2, suffix: str = "") -> str:
            """`None` یعنی «نمونه کافی نبود»، نه صفر."""
            return "نامعلوم" if value is None else f"{value:+.{digits}f}{suffix}"

        def mag(value: float | None, digits: int = 2, suffix: str = "") -> str:
            """برای مقادیری که همیشه مثبت‌اند (افت، ضریب) — علامت `+` گمراه‌کننده است."""
            return "نامعلوم" if value is None else f"{value:.{digits}f}{suffix}"

        lines = [
            f"تعداد سیگنال: {self.total}",
            f"نرخ برد: {self.win_rate_pct:.1f}٪ ({self.wins}/{self.total})",
            f"انتظار ریاضی هر سیگنال: {num(self.expectancy_pct, 2, '٪')}",
            f"میانگین بازده جهت‌دار پایه: {self.avg_return_pct:+.2f}٪"
            f" | میانه: {num(self.median_return_pct, 2, '٪')}",
            f"میانگین برد: {num(self.avg_win_pct, 2, '٪')}"
            f" | میانگین زیان: {num(self.avg_loss_pct, 2, '٪')}",
            f"ضریب سود: {mag(self.profit_factor, 2)}",
            f"شارپ (هر سیگنال): {num(self.sharpe, 2)}"
            f" | سورتینو: {num(self.sortino, 2)}",
            f"حداکثر افت تجمعی: {mag(self.max_drawdown_pct, 2, '٪')}"
            f" | بلندترین زنجیره باخت: {self.longest_losing_streak}",
            f"بهترین: {self.best_return_pct:+.2f}٪ | بدترین: {self.worst_return_pct:+.2f}٪",
            "",
            "⚠️ این اعداد روی بازده جهت‌دار **نماد پایه** حساب شده‌اند، نه سود",
            "   واقعی آپشن؛ اهرم لحاظ نشده و شارپ سالانه‌سازی نشده است.",
        ]
        for name, report in self.by_strategy().items():
            lines.append(
                f"  └ {name}: {report.total} سیگنال، نرخ برد {report.win_rate_pct:.1f}٪، "
                f"انتظار {num(report.expectancy_pct, 2, '٪')}، "
                f"افت {mag(report.max_drawdown_pct, 1, '٪')}"
            )
        return "\n".join(lines)


class SignalBacktester:
    """اجرای استراتژی‌ها روی پنجره‌های گذشته و سنجش کیفیت سیگنال‌ها.

    Args:
        horizon_days: چند روز بعد از صدور سیگنال، نتیجه سنجیده شود
        warmup_days: حداقل تعداد کندل لازم قبل از شروع ارزیابی
        step_days: گام حرکت پنجره (۱ = هر روز معاملاتی)
    """

    def __init__(
        self,
        market_data: MarketDataClient,
        option_chain: OptionChainClient,
        strategies: list[BaseStrategy],
        horizon_days: int = 10,
        warmup_days: int = 30,
        step_days: int = 1,
        risk_free_rate: float = 0.25,
        adjust_corporate_actions: bool = True,
    ) -> None:
        self.market_data = market_data
        self.option_chain = option_chain
        self.strategies = strategies
        self.horizon_days = horizon_days
        self.warmup_days = warmup_days
        self.step_days = step_days
        self.risk_free_rate = risk_free_rate
        self.adjust_corporate_actions = adjust_corporate_actions

    def run(self, symbols: list[str], days: int = 180) -> BacktestReport:
        report = BacktestReport()
        for symbol in symbols:
            history = self._adjust(symbol, self.market_data.get_history(symbol, days))
            report.outcomes.extend(self._run_symbol(symbol, history))
        return report

    def _adjust(self, symbol: str, history: list[Candle]) -> list[Candle]:
        """قیمت تاریخی را برای افزایش سرمایه تعدیل می‌کند.

        بدون این، روز افزایش سرمایه یک ریزش **ساختگی** است — روی خودرو در
        ۲۰۲۵-۰۴-۲۲ ظاهراً ۸۴٪ افت دیده می‌شود که هرگز رخ نداده. هر استراتژی
        تکنیکالی آن را سیگنال نزولی قوی می‌فهمد، و نتیجه‌ی کل بک‌تست بی‌معنا
        می‌شود.

        شکست اینجا کشنده نیست: تاریخچه‌ی تعدیل‌نشده همان چیزی است که قبلاً
        داشتیم، نه چیزی بدتر.
        """
        if not self.adjust_corporate_actions or not history:
            return history

        resolve = getattr(self.market_data, "resolve_ins_code", None)
        if resolve is None:
            return history

        try:
            from market.corporate_actions import try_fetch_corporate_actions

            log = try_fetch_corporate_actions(resolve(symbol), symbol)
        except Exception as exc:  # نبود کد نماد نباید بک‌تست را بخواباند
            logger.warning("تعدیل رویداد شرکتی %s انجام نشد: %s", symbol, exc)
            return history

        return log.adjust_history(history)

    # ------------------------------------------------------------------
    def _run_symbol(self, symbol: str, history: list[Candle]) -> list[SignalOutcome]:
        outcomes: list[SignalOutcome] = []
        # زنجیره آپشن تاریخی در دسترس نیست؛ از زنجیره فعلی به‌عنوان تقریب استفاده می‌کنیم.
        chain = self.option_chain.get_chain(symbol)
        last_index = len(history) - self.horizon_days

        for index in range(self.warmup_days, last_index, self.step_days):
            window = history[: index + 1]
            context = self._context_at(
                symbol, window, self._chain_at(chain, window[-1].date, window[-1].close)
            )
            future_close = history[index + self.horizon_days].close

            for strategy in self.strategies:
                for signal in strategy.generate(context):
                    outcomes.append(
                        SignalOutcome(
                            signal=signal,
                            entry_price=context.spot,
                            exit_price=future_close,
                            horizon_days=self.horizon_days,
                        )
                    )
        return outcomes

    @staticmethod
    def _chain_at(chain: OptionChain, as_of: date, spot: float) -> OptionChain:
        """زنجیره را به تاریخ و قیمت پنجره منتقل می‌کند.

        دو تنظیم لازم است:
          - **سررسیدها** جابه‌جا شوند، وگرنه «روز باقی‌مانده» در پنجره‌های قدیمی
            از حد مجاز استراتژی بزرگ‌تر می‌شد و هیچ نمادی انتخاب نمی‌شد.
          - **قیمت پایه‌ی زنجیره** برابر قیمت همان پنجره شود، چون
            `StrategyContext.spot` اول از زنجیره می‌خواند؛ اگر این‌جا قیمت امروز
            بماند، استراتژی با مومنتوم گذشته ولی قیمت امروز تصمیم می‌گیرد.

        محدودیت شناخته‌شده: پرمیوم‌ها همان مقادیر امروز می‌مانند، پس بک‌تست فقط
        کیفیت **جهت‌دهی** سیگنال را می‌سنجد، نه سود واقعی آپشن.
        """
        offset = as_of - chain.as_of.date()
        contracts = tuple(replace(c, expiry=c.expiry + offset) for c in chain.contracts)
        return replace(
            chain,
            contracts=contracts,
            spot_price=spot,
            as_of=datetime.combine(as_of, datetime.min.time()),
        )

    def _context_at(
        self, symbol: str, window: list[Candle], chain: OptionChain
    ) -> StrategyContext:
        last = window[-1]
        as_of = datetime.combine(last.date, datetime.min.time())
        quote = Quote(
            symbol=symbol,
            last_price=last.close,
            close_price=last.close,
            timestamp=as_of,
            volume=last.volume,
        )
        return StrategyContext(
            underlying=symbol,
            quote=quote,
            history=window,
            chain=chain,
            risk_free_rate=self.risk_free_rate,
            now=as_of,
        )

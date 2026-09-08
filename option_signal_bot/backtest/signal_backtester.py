"""بک‌تست **کیفیت سیگنال‌ها** روی داده تاریخی — بدون اجرای هیچ سفارشی.

روش کار: استراتژی‌ها روی پنجره‌های گذشته اجرا می‌شوند، سیگنال‌ها جمع می‌شوند و
سپس در افق ارزیابی سنجیده می‌گردند.

**دو معیار، به ترتیب اولویت:**

۱. **پرمیوم واقعی آپشن** (`use_real_premiums`، پیش‌فرض روشن) — تاریخچه‌ی
   خودِ قرارداد از TSETMC خوانده می‌شود. این سود و زیان *واقعی* است:
   اهرم و تتا هر دو در آن هستند.
۲. **جهت‌دهی نماد پایه** — وقتی تاریخچه‌ی آن قرارداد در دسترس نباشد.
   فقط می‌گوید جهت درست بود یا نه، نه اینکه چقدر سود داد.

تفاوت این دو کم نیست: روی داده‌ی واقعی، اهرم بین ۲ تا ۵ برابر نوسان
می‌کند. پس بک‌تستِ جهت‌دهی «نرخ برد» را تقریباً درست می‌گفت ولی «انتظار
ریاضی» را نه.

⚠️ **سوگیری بقا هنوز هست:** تاریخچه فقط برای قراردادهایی خوانده می‌شود
که **امروز** در دیده‌بان بازار هستند. قراردادی که سررسید شده و رفته، در
بک‌تست دیده نمی‌شود.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

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

    #: پرمیوم **واقعی** قرارداد در ورود و خروج. `None` یعنی تاریخچه‌ی آن
    #: قرارداد در دسترس نبود (یا آن روز معامله‌ای نشده) — که با «صفر»
    #: فرق دارد و نباید به آن تبدیل شود.
    entry_premium: float | None = None
    exit_premium: float | None = None

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
    def has_real_premium(self) -> bool:
        """آیا پرمیوم واقعی برای هر دو سرِ معامله داریم؟"""
        return bool(self.entry_premium and self.exit_premium)

    @property
    def premium_return_pct(self) -> float | None:
        """بازده **واقعی** روی پرمیوم آپشن، نه روی قیمت پایه.

        `None` یعنی تاریخچه نداشتیم. عمداً به بازده‌ی جهت‌دهی برنمی‌گردد:
        اگر بی‌صدا جایگزین می‌شد، گزارش دو معیارِ کاملاً متفاوت را در یک
        ستون قاطی می‌کرد و کسی نمی‌فهمید کدام عدد کدام است.

        فروشنده‌ی آپشن از **افت** پرمیوم سود می‌برد، پس علامت برعکس است.
        """
        if not self.has_real_premium:
            return None
        raw = (self.exit_premium - self.entry_premium) / self.entry_premium * 100.0
        return raw if self.signal.side is Side.BUY else -raw

    @property
    def effective_return_pct(self) -> float:
        """بهترین عددی که داریم: پرمیوم واقعی، وگرنه جهت‌دهی پایه."""
        premium = self.premium_return_pct
        return premium if premium is not None else self.directional_return_pct

    @property
    def is_win(self) -> bool:
        """برد بر اساس **بهترین** داده‌ی موجود.

        با پرمیوم واقعی، «برد» یعنی معامله واقعاً سود داد — نه صرفاً
        اینکه جهت درست بود. سیگنالی که جهتش درست ولی تتا خورده باشد،
        قبلاً برنده شمرده می‌شد.
        """
        return self.effective_return_pct > 0

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
        return sum(o.effective_return_pct for o in self.outcomes) / self.total

    @property
    def best_return_pct(self) -> float:
        return max((o.effective_return_pct for o in self.outcomes), default=0.0)

    @property
    def worst_return_pct(self) -> float:
        return min((o.effective_return_pct for o in self.outcomes), default=0.0)

    # ------------------------------------------------------------------
    # معیارهای حرفه‌ای — منطق در `backtest/metrics.py` است
    #
    # روی **بهترین داده‌ی موجود** حساب می‌شوند: پرمیوم واقعی آپشن اگر
    # داشته باشیم، وگرنه جهت‌دهی نماد پایه. `premium_coverage_pct`
    # می‌گوید چه سهمی از سیگنال‌ها پرمیوم واقعی داشته‌اند — بدون آن
    # معلوم نیست این اعداد چقدر واقعی‌اند.
    # ------------------------------------------------------------------
    @property
    def returns(self) -> list[float]:
        """بازده هر سیگنال (درصد)، به ترتیب **زمانی**.

        ترتیب زمانی برای منحنی تجمعی و حداکثر افت حیاتی است؛ ترتیب
        ورودی تضمینی ندارد.
        """
        ordered = sorted(self.outcomes, key=lambda o: o.signal.created_at)
        return [o.effective_return_pct for o in ordered]

    @property
    def with_real_premium(self) -> int:
        return sum(1 for o in self.outcomes if o.has_real_premium)

    @property
    def premium_coverage_pct(self) -> float | None:
        """چه درصدی از سیگنال‌ها با پرمیوم **واقعی** سنجیده شدند.

        `None` وقتی سیگنالی نیست. این عدد اعتبارِ بقیه‌ی گزارش را تعیین
        می‌کند: پوشش ۲۰٪ یعنی ۸۰٪ اعداد هنوز فقط جهت‌دهی‌اند.
        """
        if not self.total:
            return None
        return round(self.with_real_premium / self.total * 100.0, 1)

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
        option_history: Any | None = None,
    ) -> None:
        self.market_data = market_data
        self.option_chain = option_chain
        self.strategies = strategies
        self.horizon_days = horizon_days
        self.warmup_days = warmup_days
        self.step_days = step_days
        self.risk_free_rate = risk_free_rate
        #: منبع تاریخچه‌ی پرمیوم. `None` یعنی بک‌تست به جهت‌دهی پایه
        #: برمی‌گردد — همان رفتار قبلی، نه چیزی بدتر.
        self.option_history = option_history
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
        # زنجیره‌ی **تاریخی** (اینکه آن روز چه استرایک‌هایی بودند) در دسترس
        # نیست، پس زنجیره‌ی امروز با سررسید جابه‌جاشده تقریب زده می‌شود.
        # ولی **پرمیوم** هر قرارداد واقعی است و از تاریخچه‌ی خودش می‌آید.
        chain = self.option_chain.get_chain(symbol)
        last_index = len(history) - self.horizon_days

        for index in range(self.warmup_days, last_index, self.step_days):
            window = history[: index + 1]
            entry_day = window[-1].date
            context = self._context_at(
                symbol, window, self._chain_at(chain, entry_day, window[-1].close)
            )
            exit_bar = history[index + self.horizon_days]

            for strategy in self.strategies:
                for signal in strategy.generate(context):
                    entry_premium, exit_premium = self._premiums(
                        signal, entry_day, exit_bar.date
                    )
                    outcomes.append(
                        SignalOutcome(
                            signal=signal,
                            entry_price=context.spot,
                            exit_price=exit_bar.close,
                            horizon_days=self.horizon_days,
                            entry_premium=entry_premium,
                            exit_premium=exit_premium,
                        )
                    )
        return outcomes

    def _premiums(
        self, signal: Signal, entry_day: date, exit_day: date
    ) -> tuple[float | None, float | None]:
        """پرمیوم واقعی قرارداد در ورود و خروج.

        `(None, None)` یعنی نداریم — و آن‌وقت `SignalOutcome` خودش به
        جهت‌دهی برمی‌گردد. هیچ‌جا قیمتِ روزِ دیگری جایگزین نمی‌شود:
        اگر آن روز معامله‌ای نبوده، معامله ممکن نبوده.
        """
        if self.option_history is None:
            return None, None

        ins_code = str(signal.metadata.get("ins_code") or "")
        if not ins_code:
            return None, None

        bars = {
            bar.date: bar
            for bar in self.option_history.try_get_history(ins_code, signal.symbol)
            if bar.traded
        }
        entry, exit_ = bars.get(entry_day), bars.get(exit_day)
        return (
            entry.reference_price if entry else None,
            exit_.reference_price if exit_ else None,
        )

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

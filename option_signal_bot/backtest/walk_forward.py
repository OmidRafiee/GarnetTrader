"""بهینه‌سازی پارامتر با اعتبارسنجی walk-forward.

**مسئله‌ای که حل می‌کند: overfit**

بهینه‌سازی روی کلِ تاریخچه همیشه پارامتری پیدا می‌کند که «عالی» است —
حتی روی داده‌ی تصادفی. اگر ۵۰ ترکیب را روی یک بازه امتحان کنید، بهترینش
تا حد زیادی **نویزِ همان بازه** را یاد گرفته، نه الگوی بازار را. آن عدد
درخشان هیچ‌وقت در آینده تکرار نمی‌شود.

Walk-forward این تله را می‌بندد: پارامتر روی یک بازه انتخاب می‌شود و
روی بازه‌ی **بعدیِ دیده‌نشده** سنجیده می‌شود. بعد پنجره جلو می‌رود.

    ┌─── آموزش ───┐┌─ آزمون ─┐
                  ┌─── آموزش ───┐┌─ آزمون ─┐
                                ┌─── آموزش ───┐┌─ آزمون ─┐

**عددی که باید نگاه کرد، نتیجه‌ی آزمون است، نه آموزش.** فاصله‌ی این دو
(«افت out-of-sample») خودش معیار است: اگر آموزش +۵٪ بدهد و آزمون −۲٪،
پارامتر روی نویز سوار شده. این ماژول هر دو را برمی‌گرداند تا آن فاصله
قابل دیدن باشد.

**چرا نتیجه ممکن است «هیچ» باشد**

اگر هیچ ترکیبی روی آزمون انتظار مثبت ندهد، خروجی خالی است. یک پارامترِ
«بهترین از میان بدها» پیشنهاد نمی‌شود: پیشنهادِ بی‌پشتوانه از نبودِ
پیشنهاد بدتر است.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import Any

from backtest import metrics as metric_utils
from backtest.signal_backtester import BacktestReport, SignalBacktester
from strategies.registry import create_strategy

logger = logging.getLogger(__name__)

#: کمینه‌ی سیگنال در یک بازه تا نتیجه‌اش قابل اتکا باشد. با ۳ سیگنال،
#: «انتظار ریاضی +۱۰٪» یعنی یک معامله‌ی خوش‌شانس.
MIN_SIGNALS_PER_FOLD = 10


@dataclass(frozen=True)
class Fold:
    """یک برش آموزش/آزمون."""

    index: int
    train_start: int
    train_end: int
    test_end: int

    @property
    def train_days(self) -> int:
        return self.train_end - self.train_start

    @property
    def test_days(self) -> int:
        return self.test_end - self.train_end


@dataclass
class FoldResult:
    """نتیجه‌ی یک برش برای یک ترکیب پارامتر."""

    fold: Fold
    params: dict[str, Any]
    train: dict[str, Any] = field(default_factory=dict)
    test: dict[str, Any] = field(default_factory=dict)

    @property
    def train_expectancy(self) -> float | None:
        return self.train.get("expectancy_pct")

    @property
    def test_expectancy(self) -> float | None:
        return self.test.get("expectancy_pct")

    @property
    def degradation(self) -> float | None:
        """افت آموزش → آزمون. عدد بزرگ = نشانه‌ی overfit.

        `None` اگر یکی از دو طرف نمونه‌ی کافی نداشته باشد؛ صفر گفتن
        آن‌جا یعنی «افتی نبود»، که با «نمی‌دانیم» فرق دارد.
        """
        if self.train_expectancy is None or self.test_expectancy is None:
            return None
        return self.train_expectancy - self.test_expectancy

    @property
    def is_usable(self) -> bool:
        """آیا این برش نمونه‌ی کافی داشت؟"""
        return (
            self.train.get("total", 0) >= MIN_SIGNALS_PER_FOLD
            and self.test.get("total", 0) >= MIN_SIGNALS_PER_FOLD
        )


@dataclass
class WalkForwardResult:
    """جمع‌بندی همه‌ی برش‌ها برای یک ترکیب پارامتر."""

    params: dict[str, Any]
    folds: list[FoldResult] = field(default_factory=list)

    @property
    def usable_folds(self) -> list[FoldResult]:
        return [f for f in self.folds if f.is_usable]

    @property
    def test_expectancies(self) -> list[float]:
        return [
            f.test_expectancy
            for f in self.usable_folds
            if f.test_expectancy is not None
        ]

    @property
    def mean_test_expectancy(self) -> float | None:
        """معیار اصلی انتخاب — **نتیجه‌ی آزمون**، نه آموزش."""
        return metric_utils.average(self.test_expectancies)

    @property
    def worst_test_expectancy(self) -> float | None:
        values = self.test_expectancies
        return min(values) if values else None

    @property
    def positive_fold_ratio(self) -> float | None:
        """چه نسبتی از برش‌ها انتظار مثبت داشتند.

        میانگین بالا با یک برشِ استثنایی هم به دست می‌آید. این نسبت
        می‌گوید نتیجه **پایدار** بوده یا شانسی.
        """
        values = self.test_expectancies
        if not values:
            return None
        return sum(1 for v in values if v > 0) / len(values)

    @property
    def mean_degradation(self) -> float | None:
        values = [
            f.degradation for f in self.usable_folds if f.degradation is not None
        ]
        return metric_utils.average(values)

    @property
    def is_robust(self) -> bool:
        """آیا این ترکیب واقعاً قابل اتکاست؟

        سه شرط، و **هر سه** لازم است:

        1. حداقل دو برش قابل استفاده — با یک برش، walk-forward نیست.
        2. میانگین انتظارِ آزمون مثبت.
        3. اکثریت برش‌ها مثبت — وگرنه میانگین از یک برشِ خوش‌شانس آمده.
        """
        folds = self.usable_folds
        if len(folds) < 2:
            return False
        mean = self.mean_test_expectancy
        ratio = self.positive_fold_ratio
        return bool(mean and mean > 0 and ratio and ratio > 0.5)

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "folds": len(self.folds),
            "usable_folds": len(self.usable_folds),
            "mean_test_expectancy_pct": self.mean_test_expectancy,
            "worst_test_expectancy_pct": self.worst_test_expectancy,
            "positive_fold_ratio": self.positive_fold_ratio,
            "mean_degradation_pct": self.mean_degradation,
            "is_robust": self.is_robust,
        }


# ----------------------------------------------------------------------
def build_folds(
    total_days: int,
    train_days: int,
    test_days: int,
    step_days: int | None = None,
) -> list[Fold]:
    """برش‌های آموزش/آزمون را می‌سازد.

    پنجره‌ها **پشت‌سرهم** جلو می‌روند و آزمونِ هر برش همیشه **بعد از**
    آموزشش است — این ترتیب کل نکته‌ی walk-forward است. اگر برعکس شود،
    استراتژی آینده را دیده و نتیجه بی‌معنا است.
    """
    if train_days <= 0 or test_days <= 0:
        raise ValueError("طول آموزش و آزمون باید مثبت باشد.")

    step = step_days or test_days
    folds: list[Fold] = []
    start = 0
    index = 0
    while start + train_days + test_days <= total_days:
        folds.append(
            Fold(
                index=index,
                train_start=start,
                train_end=start + train_days,
                test_end=start + train_days + test_days,
            )
        )
        start += step
        index += 1
    return folds


def parameter_grid(space: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """همه‌ی ترکیب‌های ممکن پارامترها.

    ⚠️ تعداد ترکیب‌ها **ضربی** رشد می‌کند: سه پارامتر با ۴ مقدار = ۶۴
    اجرا. هر اجرا هم کل تاریخچه را می‌پیماید. عمداً هیچ سقفی گذاشته
    نشده، ولی هشدار لاگ می‌شود.
    """
    if not space:
        return [{}]
    keys = sorted(space)
    combos = [
        dict(zip(keys, values, strict=True))
        for values in itertools.product(*(space[key] for key in keys))
    ]
    if len(combos) > 100:
        logger.warning(
            "شبکه‌ی پارامتر %d ترکیب دارد؛ هر ترکیب یک بک‌تست کامل است.",
            len(combos),
        )
    return combos


class WalkForwardOptimizer:
    """پارامتر را روی آموزش انتخاب و روی آزمونِ دیده‌نشده می‌سنجد.

    Args:
        backtester: بک‌تسترِ ساخته‌شده. `strategies` آن نادیده گرفته
            می‌شود چون هر ترکیب استراتژیِ خودش را می‌سازد.
        strategy_name: نام استراتژی در رجیستری.
        train_days / test_days: طول هر بازه (روز معاملاتی).
    """

    def __init__(
        self,
        backtester: SignalBacktester,
        strategy_name: str,
        train_days: int = 120,
        test_days: int = 40,
        step_days: int | None = None,
    ) -> None:
        self.backtester = backtester
        self.strategy_name = strategy_name
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days

    # ------------------------------------------------------------------
    def run(
        self,
        symbols: list[str],
        space: dict[str, list[Any]],
        history_days: int = 400,
    ) -> list[WalkForwardResult]:
        """همه‌ی ترکیب‌ها را روی همه‌ی برش‌ها اجرا می‌کند.

        Returns:
            نتایج، **مرتب بر اساس انتظارِ آزمون** (بهترین اول).
        """
        histories = self._load_histories(symbols, history_days)
        if not histories:
            logger.warning("تاریخچه‌ای برای بهینه‌سازی در دست نیست.")
            return []

        shortest = min(len(h) for h in histories.values())
        folds = build_folds(
            shortest, self.train_days, self.test_days, self.step_days
        )
        if not folds:
            logger.warning(
                "تاریخچه (%d روز) برای آموزش %d + آزمون %d کافی نیست.",
                shortest,
                self.train_days,
                self.test_days,
            )
            return []

        combos = parameter_grid(space)
        logger.info(
            "walk-forward: %d ترکیب × %d برش روی %d نماد.",
            len(combos),
            len(folds),
            len(histories),
        )

        results = [
            self._evaluate(params, folds, histories) for params in combos
        ]
        # `None` (نمونه‌ی ناکافی) آخر می‌آید، نه اینکه صفر فرض شود
        results.sort(
            key=lambda r: (
                r.mean_test_expectancy is not None,
                r.mean_test_expectancy or 0.0,
            ),
            reverse=True,
        )
        return results

    def best(
        self,
        symbols: list[str],
        space: dict[str, list[Any]],
        history_days: int = 400,
    ) -> WalkForwardResult | None:
        """بهترین ترکیبِ **قابل اتکا**، یا `None`.

        `None` یعنی هیچ ترکیبی سه شرط `is_robust` را نگذراند. عمداً
        «بهترین از میان بدها» برنمی‌گردد: پیشنهادِ بی‌پشتوانه از نبودِ
        پیشنهاد بدتر است.
        """
        for result in self.run(symbols, space, history_days):
            if result.is_robust:
                return result
        logger.info("هیچ ترکیبی شرط پایداری walk-forward را نگذراند.")
        return None

    # ------------------------------------------------------------------
    def _load_histories(
        self, symbols: list[str], history_days: int
    ) -> dict[str, list]:
        histories: dict[str, list] = {}
        for symbol in symbols:
            try:
                candles = self.backtester.market_data.get_history(
                    symbol, history_days
                )
                candles = self.backtester._adjust(symbol, candles)
            except Exception as exc:  # یک نماد خراب، بهینه‌سازی را نکشد
                logger.warning("تاریخچه‌ی %s خوانده نشد: %s", symbol, exc)
                continue
            if candles:
                histories[symbol] = candles
        return histories

    def _evaluate(
        self,
        params: dict[str, Any],
        folds: list[Fold],
        histories: dict[str, list],
    ) -> WalkForwardResult:
        result = WalkForwardResult(params=dict(params))
        for fold in folds:
            result.folds.append(
                FoldResult(
                    fold=fold,
                    params=dict(params),
                    train=self._slice_metrics(
                        params, histories, fold.train_start, fold.train_end
                    ),
                    test=self._slice_metrics(
                        params, histories, fold.train_end, fold.test_end
                    ),
                )
            )
        return result

    def _slice_metrics(
        self,
        params: dict[str, Any],
        histories: dict[str, list],
        start: int,
        end: int,
    ) -> dict[str, Any]:
        """بک‌تست روی یک برش از تاریخچه، با پارامترِ داده‌شده."""
        # نام ناشناخته اینجا **بلند** خطا می‌دهد (خودِ `create_strategy`)،
        # نه اینکه شبکه بی‌صدا خالی برگردد.
        strategy = create_strategy(self.strategy_name, params)

        report = BacktestReport()
        for symbol, candles in histories.items():
            window = candles[start:end]
            if len(window) <= self.backtester.warmup_days:
                continue
            # `strategies` بک‌تستر موقتاً با همین ترکیب عوض می‌شود
            original = self.backtester.strategies
            self.backtester.strategies = [strategy]
            try:
                report.outcomes.extend(
                    self.backtester._run_symbol(symbol, window)
                )
            except Exception as exc:  # یک برش خراب، کل شبکه را نکشد
                logger.debug("برش %s [%d:%d] خطا داد: %s", symbol, start, end, exc)
            finally:
                self.backtester.strategies = original
        return report.metrics()


def format_report(results: list[WalkForwardResult], limit: int = 10) -> str:
    """گزارش متنی — با تأکید بر **نتیجه‌ی آزمون**، نه آموزش."""
    if not results:
        return "walk-forward نتیجه‌ای نداد (تاریخچه یا نمونه کافی نبود)."

    def num(value: float | None, digits: int = 2, suffix: str = "") -> str:
        return "نامعلوم" if value is None else f"{value:+.{digits}f}{suffix}"

    lines = [
        "=== اعتبارسنجی walk-forward ===",
        "",
        "⚠️ عددی که اهمیت دارد «انتظار آزمون» است، نه آموزش. فاصله‌ی این",
        "   دو (افت) نشانه‌ی overfit است.",
        "",
    ]
    robust = [r for r in results if r.is_robust]
    lines.append(
        f"ترکیب بررسی‌شده: {len(results)} | قابل اتکا: {len(robust)}"
    )
    if not robust:
        lines.append(
            "هیچ ترکیبی پایدار نبود — پیشنهادِ بی‌پشتوانه داده نمی‌شود."
        )

    for result in results[:limit]:
        mark = "✅" if result.is_robust else "  "
        lines.append(
            f"{mark} {result.params}: "
            f"آزمون {num(result.mean_test_expectancy, 2, '٪')}، "
            f"بدترین برش {num(result.worst_test_expectancy, 2, '٪')}، "
            f"برش مثبت "
            + (
                "نامعلوم"
                if result.positive_fold_ratio is None
                else f"{result.positive_fold_ratio * 100:.0f}٪"
            )
            + f"، افت {num(result.mean_degradation, 2, '٪')}"
        )
    return "\n".join(lines)

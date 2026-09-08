"""سطح نوسان ضمنی (IV surface) و تشخیص گران/ارزان نسبت به تاریخِ خودِ نماد.

**مسئله‌ای که حل می‌کند**

استراتژی‌های فعلی IV را با **نوسان تاریخی** مقایسه می‌کنند. تقریب بدی
نیست، ولی دو چیز را نمی‌بیند:

۱. **پرمیوم ریسک هر نماد فرق می‌کند.** روی یک نماد ممکن است IV همیشه
   ۱٫۵ برابر نوسان تاریخی باشد و این *وضعیت عادی* آن نماد باشد. با معیار
   `iv/realized > 1.3`، آن نماد **همیشه** «گران» است — یعنی فیلتر عملاً
   نماد را انتخاب می‌کند، نه لحظه را.

۲. **اسکیو (skew).** IV در استرایک‌های مختلف یکی نیست: پوت‌های خارج از
   سود معمولاً گران‌ترند. مقایسه‌ی IV یک قرارداد خاص با یک عددِ سراسری،
   اسکیو را با گرانی اشتباه می‌گیرد.

راه‌حل دو لایه است:

* `IVSurface` — عکسِ لحظه‌ای IV در سراسر استرایک/سررسید، با ATM و اسکیو.
* `IVHistory` — تاریخچه‌ی IV **همین نماد**، برای گفتن «الان نسبت به
  گذشته‌ی خودش کجاست» (صدک).

**قرارداد `None`:** بدون نمونه‌ی کافی، صدک `None` است نه ۵۰. «نمی‌دانم»
با «متوسط» فرق دارد، و اشتباه گرفتنشان یک استراتژی را با اعتماد کاذب
روی داده‌ی ناکافی به معامله می‌اندازد.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: کمینه‌ی نمونه برای اینکه صدک معنا داشته باشد. با ۵ نقطه، «صدک ۹۰»
#: یعنی «بالاترین از ۵ تا» — ادعای بزرگی از داده‌ی کوچک.
MIN_HISTORY_POINTS = 20

#: تا این فاصله از قیمت پایه، قرارداد ATM شمرده می‌شود
ATM_TOLERANCE = 0.03


@dataclass(frozen=True)
class IVPoint:
    """یک نقطه روی سطح: IV یک قرارداد مشخص."""

    strike: float
    expiry: date
    option_type: str
    iv: float
    #: فاصله‌ی نسبی استرایک از قیمت پایه؛ صفر = ATM
    moneyness: float
    days_to_expiry: int
    symbol: str = ""

    def is_atm(self, tolerance: float = ATM_TOLERANCE) -> bool:
        return abs(self.moneyness) <= tolerance


@dataclass(frozen=True)
class IVSurface:
    """عکسِ لحظه‌ای IV در سراسر زنجیره."""

    underlying: str
    spot: float
    as_of: datetime
    points: tuple[IVPoint, ...] = field(default_factory=tuple)

    # -- ساخت ---------------------------------------------------------
    @classmethod
    def from_chain(
        cls,
        chain: Any,
        implied_vol: Any,
        today: date | None = None,
        min_open_interest: int = 1,
    ) -> IVSurface:
        """سطح را از زنجیره‌ی **واقعی** می‌سازد.

        Args:
            chain: `OptionChain`
            implied_vol: تابعی که از یک قرارداد، IV می‌دهد (یا `None`).
                معمولاً `StrategyContext.implied_vol`.
            min_open_interest: قرارداد بدون موقعیت باز، IV بی‌معنا دارد —
                پرمیومش از یک معامله‌ی قدیمی مانده.
        """
        today = today or date.today()
        spot = float(getattr(chain, "spot_price", 0.0) or 0.0)
        points: list[IVPoint] = []

        for contract in getattr(chain, "contracts", ()):
            if contract.open_interest < min_open_interest:
                continue
            if spot <= 0 or contract.strike <= 0:
                continue
            try:
                iv = implied_vol(contract)
            except Exception as exc:  # IV یک قرارداد نباید کل سطح را بخواباند
                logger.debug("IV نماد %s حساب نشد: %s", contract.symbol, exc)
                continue
            if not iv or iv <= 0:
                continue

            points.append(
                IVPoint(
                    strike=float(contract.strike),
                    expiry=contract.expiry,
                    option_type=contract.option_type,
                    iv=float(iv),
                    moneyness=(contract.strike - spot) / spot,
                    days_to_expiry=contract.days_to_expiry(today),
                    symbol=contract.symbol,
                )
            )

        return cls(
            underlying=getattr(chain, "underlying", ""),
            spot=spot,
            as_of=datetime.now(),
            points=tuple(points),
        )

    # -- پرسش‌ها ------------------------------------------------------
    def __len__(self) -> int:
        return len(self.points)

    @property
    def is_empty(self) -> bool:
        return not self.points

    def filter(
        self,
        option_type: str | None = None,
        expiry: date | None = None,
        max_days: int | None = None,
    ) -> tuple[IVPoint, ...]:
        return tuple(
            p
            for p in self.points
            if (option_type is None or p.option_type == option_type)
            and (expiry is None or p.expiry == expiry)
            and (max_days is None or p.days_to_expiry <= max_days)
        )

    @property
    def expiries(self) -> tuple[date, ...]:
        return tuple(sorted({p.expiry for p in self.points}))

    def atm_iv(self, expiry: date | None = None) -> float | None:
        """IV نزدیک‌ترین قرارداد به قیمت پایه — «سطح» IV.

        میانگین کال و پوتِ ATM گرفته می‌شود، چون اسکیو باعث می‌شود این دو
        کمی فرق کنند و انتخاب یکی‌شان دلبخواه باشد.
        """
        candidates = self.filter(expiry=expiry)
        if not candidates:
            return None

        nearest = min(abs(p.moneyness) for p in candidates)
        atm = [p for p in candidates if abs(p.moneyness) <= nearest + 1e-9]
        return sum(p.iv for p in atm) / len(atm)

    def mean_iv(self, expiry: date | None = None) -> float | None:
        points = self.filter(expiry=expiry)
        return sum(p.iv for p in points) / len(points) if points else None

    def skew(self, expiry: date | None = None) -> float | None:
        """اختلاف IV پوت‌های پایین‌تر و کال‌های بالاتر از قیمت پایه.

        عدد **مثبت** یعنی پوت‌ها گران‌ترند — حالت عادی بازار سهام (تقاضای
        بیمه). عدد منفی غیرعادی است و معمولاً یعنی انتظار جهش صعودی.

        `None` اگر یک سمت نمونه نداشته باشد؛ صفر گفتن آن‌جا یعنی «اسکیو
        نیست»، که با «نمی‌دانیم» فرق دارد.
        """
        points = self.filter(expiry=expiry)
        puts = [p.iv for p in points if p.option_type == "put" and p.moneyness < -0.02]
        calls = [p.iv for p in points if p.option_type == "call" and p.moneyness > 0.02]
        if not puts or not calls:
            return None
        return sum(puts) / len(puts) - sum(calls) / len(calls)

    def term_structure(self) -> dict[date, float]:
        """IV ATM به‌ازای هر سررسید.

        شیب صعودی (سررسید دورتر گران‌تر) حالت عادی است. شیب **نزولی**
        یعنی بازار نگرانیِ کوتاه‌مدت دارد — و همان چیزی است که اسپرد
        تقویمی را جذاب می‌کند.
        """
        result: dict[date, float] = {}
        for expiry in self.expiries:
            value = self.atm_iv(expiry)
            if value is not None:
                result[expiry] = value
        return result

    def to_dict(self) -> dict[str, Any]:
        """برای API و داشبورد."""
        return {
            "underlying": self.underlying,
            "spot": self.spot,
            "as_of": self.as_of.isoformat(timespec="seconds"),
            "points": len(self.points),
            "atm_iv": self.atm_iv(),
            "mean_iv": self.mean_iv(),
            "skew": self.skew(),
            "term_structure": {
                d.isoformat(): round(v, 4) for d, v in self.term_structure().items()
            },
        }


# ----------------------------------------------------------------------
# تاریخچه‌ی IV — «نسبت به گذشته‌ی خودش»
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class IVRank:
    """جایگاه IV امروز در تاریخچه‌ی خودِ نماد."""

    current: float
    #: صدک (۰..۱۰۰)؛ `None` یعنی نمونه کافی نبود
    percentile: float | None
    #: جایگاه بین کمینه و بیشینه‌ی بازه (۰..۱۰۰)؛ همان «IV Rank» رایج
    rank: float | None
    low: float | None
    high: float | None
    samples: int

    @property
    def is_known(self) -> bool:
        return self.percentile is not None

    def is_rich(self, threshold: float = 80.0) -> bool | None:
        """آیا IV نسبت به تاریخِ خودش گران است؟ `None` = نمی‌دانیم."""
        if self.percentile is None:
            return None
        return self.percentile >= threshold

    def is_cheap(self, threshold: float = 20.0) -> bool | None:
        if self.percentile is None:
            return None
        return self.percentile <= threshold

    def describe(self) -> str:
        if self.percentile is None:
            return f"IV {self.current * 100:.1f}٪ (تاریخچه‌ی کافی نیست: {self.samples} نمونه)"
        return (
            f"IV {self.current * 100:.1f}٪ — صدک {self.percentile:.0f} "
            f"از {self.samples} روز (بازه {self.low * 100:.0f}–{self.high * 100:.0f}٪)"
        )


class IVHistory:
    """تاریخچه‌ی IV ATM هر نماد، با تداوم روی دیسک.

    **چرا روی دیسک:** IV تاریخی از هیچ endpoint عمومی در دسترس نیست —
    باید خودمان هر روز ثبتش کنیم. بدون تداوم، هر ری‌استارت تاریخچه را صفر
    می‌کرد و صدک هیچ‌وقت معنا پیدا نمی‌کرد.

    هر نماد **یک نقطه در روز** نگه می‌دارد (آخرین مقدار همان روز جایگزین
    می‌شود): چند نقطه در یک روز، صدک را به‌سمت روزهای پرمعامله وزن می‌داد.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        max_days: int = 365,
        min_samples: int = MIN_HISTORY_POINTS,
    ) -> None:
        self.path = Path(path) if path else None
        self.max_days = max_days
        self.min_samples = min_samples
        #: نماد → {تاریخ ISO: IV}
        self._data: dict[str, dict[str, float]] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("تاریخچه‌ی IV خوانده نشد: %s", exc)
            return
        if not isinstance(raw, dict):
            return
        for symbol, series in raw.items():
            if isinstance(series, dict):
                self._data[symbol] = {
                    str(day): float(value)
                    for day, value in series.items()
                    if isinstance(value, (int, float))
                }

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("تاریخچه‌ی IV ذخیره نشد: %s", exc)

    # -- ثبت ----------------------------------------------------------
    def record(
        self, symbol: str, iv: float, when: date | None = None
    ) -> None:
        """IV امروز یک نماد را ثبت می‌کند (یک نقطه در روز)."""
        if not symbol or iv is None or iv <= 0:
            return
        day = (when or date.today()).isoformat()
        self._data.setdefault(symbol, {})[day] = float(iv)
        self._prune(symbol, when)

    def _prune(self, symbol: str, when: date | None = None) -> None:
        """نقاط قدیمی‌تر از `max_days` حذف می‌شوند.

        صدک باید بازه‌ی **مرتبط** را بسنجد؛ IV سه سال پیش وضعیت امروز را
        توضیح نمی‌دهد.
        """
        series = self._data.get(symbol)
        if not series:
            return
        cutoff = (when or date.today()) - timedelta(days=self.max_days)
        for day in [d for d in series if _parse_day(d) and _parse_day(d) < cutoff]:
            series.pop(day, None)

    def series(self, symbol: str) -> list[float]:
        """مقادیر ثبت‌شده، به ترتیب تاریخ."""
        series = self._data.get(symbol) or {}
        return [series[day] for day in sorted(series)]

    def sample_count(self, symbol: str) -> int:
        return len(self._data.get(symbol) or {})

    # -- رتبه‌بندی ----------------------------------------------------
    def rank(self, symbol: str, current_iv: float) -> IVRank:
        """جایگاه IV فعلی در تاریخچه‌ی همین نماد.

        دو معیار برمی‌گردد چون هر کدام چیز دیگری می‌گویند:

        * **صدک** — چند درصد روزها IV پایین‌تر از امروز بوده. به توزیع
          حساس است.
        * **رتبه (IV Rank)** — جایگاه بین کمینه و بیشینه. به دو نقطه‌ی
          انتهایی حساس است، ولی همان چیزی است که معامله‌گرها می‌شناسند.
        """
        values = self.series(symbol)
        if len(values) < self.min_samples:
            return IVRank(
                current=current_iv,
                percentile=None,
                rank=None,
                low=min(values) if values else None,
                high=max(values) if values else None,
                samples=len(values),
            )

        low, high = min(values), max(values)
        below = sum(1 for v in values if v < current_iv)
        percentile = below / len(values) * 100.0
        span = high - low
        rank = ((current_iv - low) / span * 100.0) if span > 0 else 50.0

        return IVRank(
            current=current_iv,
            percentile=percentile,
            rank=max(0.0, min(rank, 100.0)),
            low=low,
            high=high,
            samples=len(values),
        )

    def record_surface(self, surface: IVSurface, when: date | None = None) -> None:
        """IV ATM یک سطح را در تاریخچه ثبت می‌کند."""
        atm = surface.atm_iv()
        if atm is not None:
            self.record(surface.underlying, atm, when)


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None

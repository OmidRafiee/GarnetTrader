"""تعدیل استرایک و اندازه‌ی قرارداد پس از افزایش سرمایه و سود نقدی.

**خطایی که این ماژول جلویش را می‌گیرد**

وقتی نماد پایه افزایش سرمایه می‌دهد، قیمت سهم به تناسب **کاهش** می‌یابد
ولی ارزش کل موقعیت نباید تغییر کند. بورس تهران در همان روز استرایک و
اندازه‌ی قرارداد آپشن را تعدیل می‌کند.

اگر ما تعدیل نکنیم، در روز افزایش سرمایه:

* قیمت پایه یک‌شبه مثلاً نصف می‌شود،
* استرایکِ **قدیمی** را با قیمتِ **جدید** مقایسه می‌کنیم،
* هر کالِ در-سود ناگهان به نظر عمیقاً بی‌ارزش می‌آید.

نتیجه‌اش یک موج سیگنالِ کاملاً غلط است، دقیقاً در روزی که بازار پرنوسان
است. این خطا بی‌صدا هم هست: هیچ‌چیز خراب نمی‌شود، فقط اعداد غلط می‌شوند.

**فرمول**

نسبت تعدیل = تعداد سهام قدیم ÷ تعداد سهام جدید

    استرایک جدید       = استرایک قدیم × نسبت
    اندازه قرارداد جدید = اندازه قدیم ÷ نسبت

ارزش کل (استرایک × اندازه) ثابت می‌ماند — همان قیدی که کل تعدیل بر پایه‌ی
آن تعریف شده، و در تست هم صریح بررسی می‌شود.

برای **سود نقدی** فرمول فرق می‌کند: قیمت به اندازه‌ی مبلغ سود افت می‌کند،
پس استرایک هم به همان اندازه کم می‌شود و اندازه‌ی قرارداد دست‌نخورده
می‌ماند.

**منبع داده**

    GET https://cdn.tsetmc.com/api/Instrument/GetInstrumentShareChange/{insCode}

پاسخ: `instrumentShareChange[]` با `dEven` (تاریخ)، `numberOfShareOld` و
`numberOfShareNew`. بدون احراز هویت، مثل بقیه‌ی منابع این پروژه.

TSETMC endpoint عمومیِ قابل‌اتکایی برای **سود نقدی** نمی‌دهد، پس این نوع
رویداد فقط دستی (از تنظیمات) قابل ثبت است. عمداً حدس نمی‌زنیم.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from data.option_chain_client import OptionContract
from data.tsetmc_http import DEFAULT_USER_AGENT, fetch_json

logger = logging.getLogger(__name__)

SHARE_CHANGE_URL = (
    "https://cdn.tsetmc.com/api/Instrument/GetInstrumentShareChange/{ins_code}"
)
SHARE_CHANGE_KEY = "instrumentShareChange"


@dataclass(frozen=True)
class CorporateAction:
    """یک رویداد شرکتی که استرایک/اندازه‌ی قرارداد را تعدیل می‌کند.

    دو نوع پشتیبانی می‌شود:

    * `capital_increase` — با `ratio` (سهام قدیم ÷ سهام جدید)
    * `cash_dividend` — با `dividend` (مبلغ سود هر سهم)
    """

    underlying: str
    effective_date: date
    kind: str
    ratio: float | None = None
    dividend: float | None = None
    shares_old: float | None = None
    shares_new: float | None = None

    def __post_init__(self) -> None:
        if self.kind == "capital_increase":
            if not self.ratio or self.ratio <= 0:
                raise ValueError("افزایش سرمایه به نسبت مثبت نیاز دارد.")
        elif self.kind == "cash_dividend":
            if self.dividend is None or self.dividend < 0:
                raise ValueError("سود نقدی نمی‌تواند منفی باشد.")
        else:
            raise ValueError(f"نوع رویداد ناشناخته: «{self.kind}»")

    @property
    def is_capital_increase(self) -> bool:
        return self.kind == "capital_increase"

    @classmethod
    def from_share_change(cls, underlying: str, row: dict) -> CorporateAction | None:
        """از یک ردیف `instrumentShareChange`.

        ردیفی که تاریخ یا تعداد سهامش معتبر نیست، `None` برمی‌گرداند —
        رویدادی که نتوانیم نسبتش را حساب کنیم، بهتر است اصلاً اعمال نشود
        تا اینکه با نسبت حدسی اعمال شود.
        """
        from data.tsetmc_option_chain_client import parse_tsetmc_date

        try:
            old = float(row["numberOfShareOld"])
            new = float(row["numberOfShareNew"])
            when = parse_tsetmc_date(row["dEven"])
        except (KeyError, TypeError, ValueError):
            return None

        if when is None or old <= 0 or new <= 0 or old == new:
            return None

        return cls(
            underlying=underlying,
            effective_date=when,
            kind="capital_increase",
            ratio=old / new,
            shares_old=old,
            shares_new=new,
        )

    def apply(self, contract: OptionContract) -> OptionContract:
        """قرارداد تعدیل‌شده. خودِ ورودی تغییر نمی‌کند (dataclass فریز است)."""
        import dataclasses

        if self.is_capital_increase:
            ratio = float(self.ratio or 1.0)
            new_strike = contract.strike * ratio
            # اندازه‌ی قرارداد باید صحیح بماند؛ گرد می‌کنیم ولی هرگز به صفر
            new_size = max(round(contract.contract_size / ratio), 1)
            return dataclasses.replace(
                contract, strike=new_strike, contract_size=new_size
            )

        # سود نقدی: فقط استرایک، و هرگز منفی
        new_strike = max(contract.strike - float(self.dividend or 0.0), 0.0)
        return dataclasses.replace(contract, strike=new_strike)

    def describe(self) -> str:
        """توضیح انسان‌خوان برای لاگ و داشبورد."""
        if self.is_capital_increase:
            pct = (1.0 / float(self.ratio or 1.0) - 1.0) * 100.0
            return (
                f"افزایش سرمایه {self.underlying} در {self.effective_date}: "
                f"{pct:.0f}٪ (نسبت تعدیل {self.ratio:.4f})"
            )
        return (
            f"سود نقدی {self.underlying} در {self.effective_date}: "
            f"{self.dividend:,.0f} ریال به ازای هر سهم"
        )


class CorporateActionLog:
    """رویدادهای شرکتی یک نماد، و اعمالشان روی قرارداد.

    نکته‌ی اصلی طراحی: تعدیل فقط برای قراردادهایی معنا دارد که **قبل از**
    رویداد نوشته شده‌اند. یک قرارداد جدید که بعد از افزایش سرمایه منتشر
    شده، استرایکش از اول تعدیل‌شده است؛ تعدیل دوباره یعنی خراب‌کردنش.

    به همین دلیل `adjust` تاریخِ مرجع می‌گیرد و فقط رویدادهای بعد از آن را
    اعمال می‌کند.
    """

    def __init__(self, actions: list[CorporateAction] | None = None) -> None:
        self._actions = sorted(actions or [], key=lambda a: a.effective_date)

    @property
    def actions(self) -> tuple[CorporateAction, ...]:
        return tuple(self._actions)

    def add(self, action: CorporateAction) -> None:
        self._actions.append(action)
        self._actions.sort(key=lambda a: a.effective_date)

    def since(self, reference: date, until: date | None = None) -> list[CorporateAction]:
        """رویدادهای مؤثر **بعد از** `reference` (و تا `until` اگر داده شود)."""
        end = until or date.max
        return [a for a in self._actions if reference < a.effective_date <= end]

    def adjust(
        self,
        contract: OptionContract,
        issued_on: date,
        today: date | None = None,
    ) -> OptionContract:
        """قرارداد را با همه‌ی رویدادهای پس از انتشارش تعدیل می‌کند.

        Args:
            contract: قرارداد با استرایک و اندازه‌ی **زمان انتشار**.
            issued_on: تاریخ انتشار قرارداد — مرز تصمیم.
            today: تا این تاریخ اعمال می‌شود (پیش‌فرض امروز).
        """
        pending = self.since(issued_on, today or date.today())
        adjusted = contract
        for action in pending:
            adjusted = action.apply(adjusted)
        if pending:
            logger.info(
                "قرارداد %s با %d رویداد شرکتی تعدیل شد: %s → %s",
                contract.symbol,
                len(pending),
                f"{contract.strike:,.0f}×{contract.contract_size}",
                f"{adjusted.strike:,.0f}×{adjusted.contract_size}",
            )
        return adjusted

    def adjust_history(self, candles: list) -> list:
        """قیمت‌های تاریخی را به مقیاس **امروز** می‌آورد.

        بدون این، بک‌تست در روز افزایش سرمایه یک ریزش ساختگی می‌بیند —
        مثلاً خودرو در ۲۰۲۵-۰۴-۲۲ با نسبت ۰٫۱۱۷۷، یعنی افتِ ظاهریِ ۸۸٪ که
        هیچ‌وقت اتفاق نیفتاده. هر استراتژی تکنیکالی این را سیگنالِ نزولیِ
        قوی می‌فهمد و کل نتیجه‌ی بک‌تست بی‌معنا می‌شود.

        هر کندل در حاصل‌ضرب نسبت رویدادهای **بعد از** خودش ضرب می‌شود، تا
        همه‌ی قیمت‌ها در یک مقیاس قرار بگیرند. حجم عمداً دست‌نخورده می‌ماند:
        این ماژول درباره‌ی قیمت است، و تعدیل حجم قید جداگانه‌ای دارد.
        """
        import dataclasses

        if not self._actions or not candles:
            return list(candles)

        adjusted = []
        for candle in candles:
            ratio = self.net_ratio(candle.date)
            if ratio == 1.0:
                adjusted.append(candle)
                continue
            adjusted.append(
                dataclasses.replace(
                    candle,
                    open=candle.open * ratio,
                    high=candle.high * ratio,
                    low=candle.low * ratio,
                    close=candle.close * ratio,
                )
            )
        return adjusted

    def net_ratio(self, reference: date, until: date | None = None) -> float:
        """حاصل‌ضرب نسبت همه‌ی افزایش سرمایه‌های بازه.

        برای تعدیل **قیمت تاریخی** لازم است: قیمت قبل از افزایش سرمایه با
        قیمت بعدش مقایسه‌پذیر نیست، و بک‌تستِ بدون این ضریب یک ریزش
        ساختگی می‌بیند و آن را سیگنال نزولی می‌فهمد.
        """
        ratio = 1.0
        for action in self.since(reference, until):
            if action.is_capital_increase:
                ratio *= float(action.ratio or 1.0)
        return ratio


def fetch_corporate_actions(
    ins_code: str,
    underlying: str = "",
    timeout: int = 15,
    retries: int = 2,
    user_agent: str = DEFAULT_USER_AGENT,
) -> CorporateActionLog:
    """رویدادهای افزایش سرمایه از TSETMC. خطای شبکه را **بالا می‌برد**."""
    payload = fetch_json(
        SHARE_CHANGE_URL.format(ins_code=ins_code),
        timeout=timeout,
        retries=retries,
        user_agent=user_agent,
    )
    rows = payload.get(SHARE_CHANGE_KEY) or []

    actions: list[CorporateAction] = []
    # TSETMC گاهی یک افزایش سرمایه را دو **روز پشت‌سرهم** تکرار می‌کند —
    # روی وبملت واقعاً دیده شد: همان گذارِ ۱٬۲۱۰ به ۲٬۳۷۸ میلیارد سهم، هم
    # در ۲۰۲۵-۰۸-۰۳ و هم ۲۰۲۵-۰۸-۰۴. کلید یکتایی باید خودِ گذارِ سهام
    # باشد نه تاریخ، وگرنه نسبت دوبار اعمال می‌شود و استرایک نصفِ نصف
    # می‌شود. یک تعدیل مضاعف، هر سیگنالِ آن نماد را بی‌صدا خراب می‌کند.
    seen: set[tuple[float | None, float | None]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        action = CorporateAction.from_share_change(underlying or ins_code, row)
        if action is None:
            continue
        key = (action.shares_old, action.shares_new)
        if key in seen:
            logger.debug(
                "رویداد تکراری %s در %s نادیده گرفته شد.",
                underlying or ins_code,
                action.effective_date,
            )
            continue
        seen.add(key)
        actions.append(action)

    return CorporateActionLog(actions)


def try_fetch_corporate_actions(
    ins_code: str, underlying: str = "", **kwargs
) -> CorporateActionLog:
    """مثل بالا، ولی خطا را می‌بلعد و لاگِ خالی برمی‌گرداند.

    لاگِ خالی یعنی «تعدیلی اعمال نشد» — همان رفتار قبلی پروژه، نه چیزی
    بدتر. پس قطعی این endpoint نباید پاس رصد را بخواباند.
    """
    try:
        return fetch_corporate_actions(ins_code, underlying, **kwargs)
    except Exception as exc:  # رویداد شرکتی نباید پاس رصد را بخواباند
        logger.warning(
            "رویدادهای شرکتی %s دریافت نشد: %s", underlying or ins_code, exc
        )
        return CorporateActionLog()

"""قرارداد آداپتر کارگزاری — تنها چیزی که هسته می‌شناسد.

طراحی حول یک قید ساخته شده: **کارگزاری باید قابل تعویض باشد.** پس هسته
هرگز اسم هیچ کارگزاری‌ای را نمی‌داند و فقط با این قرارداد کار می‌کند.
اضافه کردن کارگزاری جدید یعنی یک پیاده‌سازی تازه از `AccountDataSource`
که سوئیت تست مشترک را پاس کند.

عمداً فقط خواندن: نه ثبت سفارش، نه تغییر، نه لغو. وقتی endpoint ثبت
سفارش کشف و اعتبارسنجی شد، آن قابلیت در یک قرارداد **جداگانه** اضافه
می‌شود تا «خواندن حساب» هیچ‌وقت ناخواسته اجازه‌ی نوشتن نگیرد.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


# ----------------------------------------------------------------------
# خطاها — تفکیک‌شان مهم است چون واکنش به هرکدام فرق می‌کند
# ----------------------------------------------------------------------
class BrokerError(RuntimeError):
    """خطای پایه‌ی همه‌ی آداپترها."""


class BrokerAuthError(BrokerError):
    """توکن نامعتبر یا منقضی — کاربر باید دوباره لاگین کند.

    از `BrokerUnavailableError` جداست چون تلاش مجدد اینجا بی‌فایده است.
    """


class BrokerUnavailableError(BrokerError):
    """شبکه قطع، تایم‌اوت، یا خطای سمت سرور — تلاش مجدد منطقی است."""


# ----------------------------------------------------------------------
# مدل‌ها
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class OptionPosition:
    """یک موقعیت باز آپشن.

    نام فیلدها عمداً *مستقل از کارگزاری* است؛ نگاشت از JSON خام در خود
    آداپتر انجام می‌شود تا تغییر شکل پاسخ کارگزاری به هسته نشت نکند.
    """

    symbol_isin: str
    symbol_name: str
    quantity: int
    is_long: bool
    strike_price: float
    base_isin: str
    total_margin: float = 0.0
    required_margin_per_contract: float = 0.0
    open_buy_quantity: int = 0
    open_sell_quantity: int = 0
    buy_average_price: float = 0.0
    sell_average_price: float = 0.0
    closed_pnl: float = 0.0
    cash_settlement_date: date | None = None
    physical_settlement_date: date | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_open(self) -> bool:
        return self.quantity > 0


@dataclass(frozen=True)
class OptionContractSpec:
    """مشخصات قرارداد، از منبع معتبر کارگزاری.

    این‌ها را هرگز حدس نزنید: `contract_size` معمولاً ۱۰۰۰ است ولی
    استثنا دارد، و اشتباه در آن یعنی خطای ۱۰۰۰ برابری در ارزش موقعیت.
    """

    symbol_isin: str
    strike_price: float
    contract_size: int
    base_isin: str
    start_date: date | None = None
    end_date: date | None = None
    initial_margin: float = 0.0
    required_margin: float = 0.0
    maintenance_margin: float = 0.0
    max_orders: int = 0
    max_customer_open_position: int = 0
    max_market_open_position: int = 0
    open_positions: int = 0
    cash_settlement_date: date | None = None
    physical_settlement_date: date | None = None
    early_exercise: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class UnderlyingLimit:
    """سقف موقعیت روی یک دارایی پایه در یک سررسید."""

    base_isin: str
    max_open_position: int
    sum_open_positions: int
    low_limit_open_position: int = 0
    is_request_allowed: bool = True
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def remaining_capacity(self) -> int:
        """ظرفیت باقی‌مانده؛ هرگز منفی برنمی‌گردد."""
        return max(self.max_open_position - self.sum_open_positions, 0)


@dataclass(frozen=True)
class SharePosition:
    """دارایی سهم (نه آپشن) — برای شرطِ مالکیتِ Covered Call.

    Covered Call یعنی فروش کال روی سهمی که **داری**. بدون سهم، همان معامله
    یک کالِ لخت است: سود محدود به پرمیوم، زیان نامحدود. تفاوت‌شان یک
    پارامتر نیست، دو پروفایل ریسکِ کاملاً متفاوت است.
    """

    symbol_isin: str
    symbol_name: str
    quantity: int
    average_price: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class AccountBalance:
    """موجودی و قدرت خرید حساب.

    بورس تهران تسویه‌ی T+۰/T+۱/T+۲ دارد، پس «موجودی» یک عدد نیست بلکه سه
    عدد است. برای اندازه‌گیری ریسک، `equity` عمداً روی **T+۲** بسته می‌شود:
    محافظه‌کارانه‌ترین تعریف، چون پولی که هنوز تسویه نشده هم در آن هست ولی
    وجه بلوکه‌شده کنار گذاشته می‌شود.

    نام فیلدها مستقل از کارگزاری است؛ نگاشت در خودِ آداپتر انجام می‌شود.
    """

    cash_t0: float = 0.0
    cash_t1: float = 0.0
    cash_t2: float = 0.0
    buy_power_t0: float = 0.0
    buy_power_t1: float = 0.0
    buy_power_t2: float = 0.0
    #: وجه بلوکه‌شده (از جمله وجه تضمین موقعیت‌های باز آپشن)
    blocked: float = 0.0
    margin_blocked: float = 0.0
    credit: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def equity(self) -> float:
        """دارایی قابل استفاده برای اندازه‌گیری ریسک.

        قدرت خرید T+۲ اگر موجود باشد، وگرنه نقدِ T+۲. هرگز منفی برنمی‌گردد:
        `RiskCalculator` با دارایی منفی تعداد قرارداد بی‌معنا می‌دهد.
        """
        value = self.buy_power_t2 or self.cash_t2 or self.cash_t0
        return max(float(value), 0.0)


# ----------------------------------------------------------------------
# قرارداد
# ----------------------------------------------------------------------
class AccountDataSource(ABC):
    """خواندن وضعیت حساب از کارگزاری. **هیچ عملیات نوشتنی ندارد.**"""

    #: نامی که در لاگ و UI دیده می‌شود
    name: str = "unknown"

    @abstractmethod
    def is_authenticated(self) -> bool:
        """آیا سشن معتبری در دست است؟ نباید استثنا بدهد."""

    @abstractmethod
    def get_positions(self) -> list[OptionPosition]:
        """موقعیت‌های باز آپشن."""

    @abstractmethod
    def get_balance(self) -> AccountBalance:
        """موجودی و قدرت خرید حساب."""

    #: آیا این آداپتر دارایی سهم را می‌دهد؟ مصرف‌کننده باید **قبل از**
    #: تصمیم‌گیری این را ببیند: «لیست خالی» از یک آداپترِ ناتوان یعنی
    #: «نمی‌دانم»، ولی از یک آداپترِ توانا یعنی «هیچ سهمی نداری». این دو
    #: نباید یک‌جور فهمیده شوند — یکی باید سیگنال را نگه دارد و دیگری رد کند.
    supports_share_positions: bool = False

    def get_share_positions(self) -> list[SharePosition]:
        """دارایی سهم (نه آپشن).

        عمداً **abstract نیست**: هر کارگزاری‌ای این را نمی‌دهد و نبودش
        نباید مانع پیاده‌سازی بقیه‌ی قرارداد شود. آداپتری که این را
        پیاده می‌کند باید `supports_share_positions = True` بگذارد.
        """
        return []

    @abstractmethod
    def get_contract_spec(self, symbol_isin: str) -> OptionContractSpec:
        """مشخصات یک قرارداد."""

    @abstractmethod
    def get_underlying_limit(self, base_isin: str, end_date: date) -> UnderlyingLimit:
        """سقف موقعیت روی دارایی پایه برای یک سررسید."""

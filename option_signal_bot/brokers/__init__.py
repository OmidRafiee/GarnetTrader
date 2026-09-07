"""آداپترهای کارگزاری — **فقط خواندن**.

این لایه عمداً هیچ راهی برای ثبت سفارش ندارد. یک تست گارد AST که کل مخزن
را می‌پاید تضمین می‌کند هیچ ماژولی بیرون از `execution/` به آن لایه وصل
نشود، و این پکیج هم از همان قاعده پیروی می‌کند.

قرارداد `AccountDataSource` تنها چیزی است که هسته می‌شناسد؛ اگر کارگزاری
عوض شد، فقط یک پیاده‌سازی تازه از همین قرارداد لازم است.
"""

from brokers.base import (
    AccountBalance,
    AccountDataSource,
    BrokerAuthError,
    BrokerError,
    BrokerUnavailableError,
    OptionContractSpec,
    OptionPosition,
    UnderlyingLimit,
)

__all__ = [
    "AccountBalance",
    "AccountDataSource",
    "BrokerAuthError",
    "BrokerError",
    "BrokerUnavailableError",
    "OptionContractSpec",
    "OptionPosition",
    "UnderlyingLimit",
]

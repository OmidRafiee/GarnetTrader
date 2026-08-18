"""ابزار HTTP مشترک کلاینت‌های TSETMC.

فقط `urllib` استاندارد؛ هیچ وابستگی خارجی لازم نیست. تلاش مجدد با backoff خطی
تا به TSETMC فشار نیاید (این API عمومی است و rate limit نرم دارد).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; option-signal-bot/0.1)"


def fetch_json(
    url: str,
    timeout: int = 15,
    retries: int = 3,
    user_agent: str = DEFAULT_USER_AGENT,
    label: str = "TSETMC",
) -> dict[str, Any]:
    """دریافت JSON با تلاش مجدد؛ در صورت شکست کامل، خطا بالا می‌رود.

    عمداً استثنا را نمی‌بلعیم: داده نداشتن باید بلند و واضح باشد، نه پنهان.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "application/json"}
    )
    last_error: Exception | None = None
    for attempt in range(1, max(retries, 1) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_error = exc
            logger.warning(
                "دریافت %s ناموفق بود (تلاش %s از %s): %s", label, attempt, retries, exc
            )
            if attempt < retries:
                time.sleep(attempt)
    raise RuntimeError(f"دریافت {label} شکست خورد: {last_error}")

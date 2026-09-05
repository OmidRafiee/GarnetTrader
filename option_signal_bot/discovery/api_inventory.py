"""کشف و مستندسازی سطح API یک پلتفرم، **بدون ثبت هیچ داده‌ی حساب**.

اصل طراحی: از هر درخواست/پاسخ فقط **شکل** نگه داشته می‌شود، نه مقدارها.
یعنی به‌جای `{"balance": 12345000}` این ذخیره می‌شود: `{"balance": "number"}`.

چرا؟ چون برای نوشتن آداپتر، دانستن *نام فیلدها و نوعشان* کافی است؛ مقدار موجودی و
شماره حساب و توکن هیچ‌وقت لازم نیست. با این کار خروجی کشف، قابل مرور و اشتراک
می‌ماند و راز تازه‌ای نمی‌سازد.

سه لایه محافظت:
    ۱. هدرهای حساس (Authorization, Cookie, ...) فقط با **نام** ثبت می‌شوند، نه مقدار
    ۲. مقدار پارامترهای حساس در URL پاک می‌شود و رشته‌های عددی بلند (شماره حساب،
       کد ملی) به `<NUM>` تبدیل می‌شوند
    ۳. بدنه‌ی درخواست و پاسخ هرگز عیناً ذخیره نمی‌شود — فقط اسکلت نوع‌ها
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<REDACTED>"
VALUE_MASK = "<v>"
NUM_MASK = "<NUM>"

#: هدرهایی که مقدارشان هرگز ثبت نمی‌شود (فقط نامشان گزارش می‌شود)
SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-auth-token",
    "x-authorization",
    "x-api-key",
    "apikey",
    "api-key",
    "x-csrf-token",
    "x-xsrf-token",
    "x-access-token",
    "x-refresh-token",
}

#: نام کلیدهایی که مقدارشان راز یا اطلاعات هویتی است
SENSITIVE_KEY_PARTS = (
    "token",
    "password",
    "passwd",
    "pass",
    "otp",
    "secret",
    "apikey",
    "api_key",
    "credential",
    "captcha",
    "nationalcode",
    "nationalid",
    "melicode",
    "codemeli",
    "mobile",
    "phone",
    "email",
    "username",
    "userid",
    "customerid",
    "accountnumber",
    "accountid",
    "bourse",
    "sessionid",
    "refresh",
    "signature",
)

#: دنباله‌ی عددی بلند در مسیر = احتمالاً شناسه حساب/مشتری
# آستانه ۴ رقم: کدهای حساب/مشتری کارگزاری‌های ایرانی معمولاً ۵-۶ رقمی‌اند
# و با آستانه ۶ از فیلتر رد می‌شدند. ۱-۳ رقم عمداً باقی می‌ماند چون
# ساختار مفید است، نه شناسه: /market/1، /api/v2، /GetMarketOverview/2
_LONG_DIGITS = re.compile(r"\d{4,}")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.\-]{0,12}$")


def looks_sensitive(name: str) -> bool:
    """آیا این نام کلید/هدر، راز یا اطلاعات هویتی حمل می‌کند؟"""
    lowered = re.sub(r"[^a-z0-9]", "", name.lower())
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_header_value(name: str, value: str) -> str:
    """مقدار هدر حساس را پاک می‌کند، ولی **نوع احراز هویت** را نگه می‌دارد.

    دانستن این‌که توکن `Bearer` است یا `Basic`، برای نوشتن آداپتر لازم است؛
    خودِ توکن لازم نیست.
    """
    if name.lower() in SENSITIVE_HEADERS or looks_sensitive(name):
        scheme = value.split(" ", 1)[0] if " " in value else ""
        if scheme and scheme.isalpha() and len(scheme) <= 10:
            return f"{scheme} {REDACTED}"
        return REDACTED
    return value


def redact_url(url: str) -> str:
    """پاک‌سازی URL: مقدار پارامترهای حساس، و شناسه‌های عددی بلند در مسیر."""
    parts = urlsplit(url)
    path = _LONG_DIGITS.sub(NUM_MASK, parts.path)

    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if looks_sensitive(key):
            query_pairs.append((key, REDACTED))
        elif _SAFE_VALUE.match(value) and not _LONG_DIGITS.search(value):
            # مقدارهای کوتاه و بی‌خطر (مثل type=option یا market=0) مفیدند
            query_pairs.append((key, value))
        else:
            query_pairs.append((key, VALUE_MASK))

    query = urlencode(query_pairs, safe="<>")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def endpoint_key(method: str, url: str) -> str:
    """کلید گروه‌بندی: متد + مسیر بدون query (تا تکرارها یکی شمرده شوند)."""
    parts = urlsplit(redact_url(url))
    return f"{method.upper()} {parts.scheme}://{parts.netloc}{parts.path}"


# ----------------------------------------------------------------------
# اسکلت نوع‌ها (بدون هیچ مقداری)
# ----------------------------------------------------------------------
def sketch(value: Any, depth: int = 0, max_depth: int = 5) -> Any:
    """ساختار داده را به «نقشه‌ی نوع‌ها» تبدیل می‌کند؛ هیچ مقداری عبور نمی‌کند.

    >>> sketch({"balance": 12345, "symbol": "ضخود", "ok": True})
    {'balance': 'number', 'symbol': 'string', 'ok': 'bool'}
    """
    if depth >= max_depth:
        return "..."
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return {str(k): sketch(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if not value:
            return ["<empty>"]
        # فقط شکل عضو اول؛ طول لیست داده است، نه شکل
        return [sketch(value[0], depth + 1, max_depth)]
    return type(value).__name__


def sketch_json_text(text: str | None, max_chars: int = 2_000_000) -> Any | None:
    """اگر متن JSON بود، اسکلتش را برمی‌گرداند؛ وگرنه None."""
    if not text or len(text) > max_chars:
        return None
    try:
        return sketch(json.loads(text))
    except (ValueError, TypeError):
        return None


# ----------------------------------------------------------------------
# مدل
# ----------------------------------------------------------------------
@dataclass
class ApiCall:
    """یک endpoint کشف‌شده (تکرارها در `count` جمع می‌شوند)."""

    method: str
    url: str
    status: int | None = None
    resource_type: str = ""
    content_type: str = ""
    auth_headers: list[str] = field(default_factory=list)
    query_keys: list[str] = field(default_factory=list)
    request_schema: Any | None = None
    response_schema: Any | None = None
    count: int = 1

    @property
    def key(self) -> str:
        return endpoint_key(self.method, self.url)

    def merge(self, other: ApiCall) -> None:
        """ادغام یک فراخوان تکراری؛ اسکلت غنی‌تر و وضعیت موفق ترجیح دارد."""
        self.count += other.count
        if self.response_schema is None or (
            other.status == 200 and self.status != 200
        ):
            self.response_schema = other.response_schema or self.response_schema
            self.status = other.status or self.status
        if self.request_schema is None:
            self.request_schema = other.request_schema
        for header in other.auth_headers:
            if header not in self.auth_headers:
                self.auth_headers.append(header)
        for key in other.query_keys:
            if key not in self.query_keys:
                self.query_keys.append(key)


@dataclass
class SocketChannel:
    """یک اتصال WebSocket/SignalR و نام متدهایی که روی آن رد و بدل می‌شود."""

    url: str
    sent_targets: list[str] = field(default_factory=list)
    received_targets: list[str] = field(default_factory=list)
    frames_sent: int = 0
    frames_received: int = 0


@dataclass
class ApiInventory:
    """فهرست کشف‌شده‌ی APIها، آماده‌ی رندر به Markdown/JSON."""

    title: str = "فهرست API کشف‌شده"
    calls: dict[str, ApiCall] = field(default_factory=dict)
    sockets: dict[str, SocketChannel] = field(default_factory=dict)

    def add_call(self, call: ApiCall) -> None:
        existing = self.calls.get(call.key)
        if existing is None:
            self.calls[call.key] = call
        else:
            existing.merge(call)

    def add_socket_frame(self, url: str, payload: str, sent: bool) -> None:
        """ثبت یک فریم سوکت — فقط **نام متد** (`target`)، نه آرگومان‌ها."""
        channel = self.sockets.setdefault(redact_url(url), SocketChannel(redact_url(url)))
        if sent:
            channel.frames_sent += 1
        else:
            channel.frames_received += 1
        for target in extract_signalr_targets(payload):
            bucket = channel.sent_targets if sent else channel.received_targets
            if target not in bucket:
                bucket.append(target)

    # ------------------------------------------------------------------
    def hosts(self) -> dict[str, int]:
        counter: dict[str, int] = {}
        for call in self.calls.values():
            host = urlsplit(call.url).netloc
            counter[host] = counter.get(host, 0) + call.count
        return dict(sorted(counter.items(), key=lambda kv: -kv[1]))

    def auth_schemes(self) -> list[str]:
        """چه هدرهایی احراز هویت را حمل می‌کنند (نام‌ها، نه مقدارها)."""
        names: list[str] = []
        for call in self.calls.values():
            for header in call.auth_headers:
                if header not in names:
                    names.append(header)
        return sorted(names)

    def to_json(self) -> str:
        payload = {
            "title": self.title,
            "hosts": self.hosts(),
            "auth_headers": self.auth_schemes(),
            "calls": [
                {
                    "method": c.method,
                    "url": c.url,
                    "status": c.status,
                    "resource_type": c.resource_type,
                    "content_type": c.content_type,
                    "auth_headers": c.auth_headers,
                    "query_keys": c.query_keys,
                    "count": c.count,
                    "request_schema": c.request_schema,
                    "response_schema": c.response_schema,
                }
                for c in sorted(self.calls.values(), key=lambda c: c.key)
            ],
            "sockets": [
                {
                    "url": s.url,
                    "frames_sent": s.frames_sent,
                    "frames_received": s.frames_received,
                    "sent_targets": s.sent_targets,
                    "received_targets": s.received_targets,
                }
                for s in self.sockets.values()
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = [
            f"# {self.title}",
            "",
            "> این گزارش خودکار ساخته شده و **هیچ مقداری از داده‌های حساب را ندارد**:",
            "> از بدنه‌ی درخواست/پاسخ فقط نام فیلدها و نوعشان ثبت شده، مقدار هدرهای",
            "> احراز هویت پاک شده، و شناسه‌های عددی بلند به `<NUM>` تبدیل شده‌اند.",
            "> با این حال قبل از اشتراک‌گذاری، یک مرور چشمی بکنید.",
            "",
            f"- تعداد endpoint یکتا: **{len(self.calls)}**",
            f"- تعداد اتصال سوکت: **{len(self.sockets)}**",
            "",
            "## دامنه‌ها",
            "",
        ]
        for host, count in self.hosts().items():
            lines.append(f"- `{host}` — {count} فراخوان")

        auth = self.auth_schemes()
        lines += ["", "## هدرهای حامل احراز هویت", ""]
        lines += [f"- `{name}`" for name in auth] if auth else ["- (چیزی دیده نشد)"]

        if self.sockets:
            lines += ["", "## اتصال‌های Realtime (WebSocket / SignalR)", ""]
            for socket in self.sockets.values():
                lines += [
                    f"### `{socket.url}`",
                    "",
                    f"- فریم ارسالی: {socket.frames_sent} | دریافتی: {socket.frames_received}",
                ]
                if socket.received_targets:
                    lines.append(
                        "- متدهای دریافتی (`target`): "
                        + ", ".join(f"`{t}`" for t in socket.received_targets)
                    )
                if socket.sent_targets:
                    lines.append(
                        "- متدهای ارسالی: " + ", ".join(f"`{t}`" for t in socket.sent_targets)
                    )
                lines.append("")

        lines += ["", "## Endpointها", ""]
        for call in sorted(self.calls.values(), key=lambda c: (-c.count, c.key)):
            lines += [
                f"### `{call.method} {urlsplit(call.url).path or '/'}`",
                "",
                f"- آدرس: `{call.url}`",
                f"- وضعیت: `{call.status}` | نوع: `{call.resource_type}` "
                f"| content-type: `{call.content_type}` | تکرار: {call.count}",
            ]
            if call.query_keys:
                lines.append("- پارامترهای query: " + ", ".join(f"`{k}`" for k in call.query_keys))
            if call.auth_headers:
                lines.append("- احراز هویت با: " + ", ".join(f"`{h}`" for h in call.auth_headers))
            if call.request_schema is not None:
                lines += [
                    "- شکل بدنه‌ی درخواست:",
                    "",
                    "```json",
                    json.dumps(call.request_schema, ensure_ascii=False, indent=2),
                    "```",
                ]
            if call.response_schema is not None:
                lines += [
                    "- شکل پاسخ:",
                    "",
                    "```json",
                    json.dumps(call.response_schema, ensure_ascii=False, indent=2),
                    "```",
                ]
            lines.append("")
        return "\n".join(lines)


def extract_signalr_targets(payload: str) -> list[str]:
    """نام متدهای SignalR را از یک فریم بیرون می‌کشد (فقط `target`، بدون آرگومان).

    SignalR با پروتکل JSON، هر پیام را با کاراکتر جداکننده `\\x1e` می‌بندد.
    """
    targets: list[str] = []
    for chunk in payload.split("\x1e"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            message = json.loads(chunk)
        except (ValueError, TypeError):
            continue
        if isinstance(message, dict):
            target = message.get("target")
            if isinstance(target, str) and target not in targets:
                targets.append(target)
    return targets

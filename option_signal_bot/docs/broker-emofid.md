# API پلتفرم ایزی‌تریدر (مفید) — آنچه از کشف به دست آمد

منبع: اجرای `scripts/emofid_login.py` روی یک سشن لاگین‌شده‌ی واقعی.
گزارش خام در `var/emofid/api_inventory.md` (خارج از گیت).

> ⚠️ **مفید API عمومی مستندی ندارد.** هر چه اینجاست از مشاهده‌ی ترافیک
> وب‌اپ با حساب خودِ کاربر آمده. یعنی **هر آپدیت وب‌اپ می‌تواند این‌ها را
> بشکند**. کد باید شکست را با خطای خوانا اعلام کند، نه اینکه بی‌صدا
> داده‌ی غلط بدهد.

## خلاصه

- **۵۰۵ endpoint یکتا**، **۲ اتصال realtime**
- هاست اصلی API: `api-mts.orbis.easytrader.ir`
- احراز هویت: هدر `authorization` (توکن Bearer)
- هاست لاگین: `login.emofid.com` (جدا از `easytrader.ir`)

### دامنه‌ها

| دامنه | نقش |
|---|---|
| `api-mts.orbis.easytrader.ir` | **API اصلی** — سفارش، پوزیشن، دارایی |
| `d.easytrader.ir` | فایل‌های استاتیک وب‌اپ (بی‌ربط به ما) |
| `login.emofid.com` | احراز هویت |
| `ls.easytrader.ir` | **Lightstreamer** — مظنه‌ی زنده |
| `ls-marketsheet.easytrader.ir` | Lightstreamer — دیده‌بان |
| `sapi.emofid.com` | سرویس‌های جانبی |

> **realtime با Lightstreamer است، نه SignalR و نه WebSocket خام.**
> پروتکل اختصاصی دارد و کتابخانه‌ی خودش را می‌خواهد
> (`lightstreamer-client-python`). این را قبل از تخمین زمان بدانید.

---

## Endpointهای مورد نیاز

همه با `GET` و هدر `authorization`. مسیرها نسبت به
`https://api-mts.orbis.easytrader.ir` هستند.

### پوزیشن‌های آپشن

```
GET /option/api/Positions
```

پاسخ آرایه‌ای است:

| فیلد | نوع | معنی |
|---|---|---|
| `symbolIsin` | string | ISIN نماد آپشن |
| `symbolName` | string | نام نماد (مثلاً `ضخود7136`) |
| `side` | number | جهت پوزیشن (خرید/فروش) |
| `executedQuantity` | number | تعداد قرارداد باز |
| `openBuyQuantity` / `openSellQuantity` | number | سفارش باز |
| `strikePrice` | number | قیمت اعمال |
| `baseIsin` | string | ISIN دارایی پایه |
| `totalMargin` | number | وجه تضمین بلوکه‌شده |
| `contractRequiredMargin` | number | وجه تضمین لازم هر قرارداد |
| `buyAveragePrice` / `sellAveragePrice` | number | میانگین قیمت |
| `closedPositionProfitLoss` | number | سود/زیان بسته‌شده |
| `cashSettlementDate` / `physicalSettlementDate` | string | تاریخ تسویه |
| `cefo` | bool | قابلیت اعمال زودتر |

### مشخصات قرارداد

```
GET /option/api/Contracts/{symbolIsin}/symbol
```

**این منبع معتبر مشخصات قرارداد است** — `contractSize` و `strikePrice` را
از اینجا بخوانید، نه از حدس یا هاردکد:

| فیلد | معنی |
|---|---|
| `strikePrice` | قیمت اعمال |
| `contractSize` | **اندازه قرارداد** (معمولاً ۱۰۰۰، ولی هاردکد نکنید) |
| `initialMargin` | وجه تضمین اولیه |
| `requiredMargin` | وجه تضمین لازم |
| `maintenanceMargin` | حداقل وجه تضمین |
| `startDate` / `endDate` | شروع و سررسید |
| `cashSettlementDate` / `physicalSettlementDate` | تاریخ تسویه |
| `maxOrders` | سقف تعداد سفارش |
| `maxCOP` / `maxCAOP` / `maxBrokerOP` / `maxMarketOP` | سقف موقعیت باز (مشتری/کارگزار/بازار) |
| `openPositions` | موقعیت باز فعلی |
| `cefo` | قابلیت اعمال زودتر |

> **وجه تضمین را خودتان حساب نکنید.** پلتفرم `initialMargin` و
> `requiredMargin` را می‌دهد. فرمول دستی (که در پلن آمده) فقط برای
> تخمین آفلاین است و باید با همین اعداد اعتبارسنجی شود.

### سقف موقعیت روی دارایی پایه

```
GET /option/api/contracts/underlying-asset/{baseIsin}?endDate=YYYY-MM-DD
```

| فیلد | معنی |
|---|---|
| `maxOpenPosition` | سقف موقعیت |
| `sumOpenPositions` | مجموع فعلی |
| `lowLimitOpenPosition` | کف |
| `isRequestAllowed` | **آیا سفارش مجاز است** |

قبل از هر سفارش فروش باید چک شود.

### سفارش‌ها

```
GET /core/api/order
```

| فیلد | معنی |
|---|---|
| `id` | شناسه سفارش |
| `symbolIsin` / `symbolName` | نماد |
| `price` / `quantity` | قیمت و تعداد |
| `executedQuantity` | مقدار پرشده |
| `side` | خرید/فروش (عدد) |
| `orderState` / `orderStateStr` | وضعیت |
| `validityType` / `validityDate` | اعتبار |
| `orderFrom` | منشأ سفارش |
| `referenceId` | شناسه‌ی سمت کلاینت (**جای idempotency**) |
| `trades[]` | معاملات انجام‌شده |

### دارایی

```
GET /assetmodule/api/performance
```

`items[]` با `symbolIsin`, `asset`, `totalBuyAveragePrice`,
`totalRealizedProfitAndLoss` و چند ده فیلد دیگر.

---

## آنچه هنوز کشف نشده

| نیاز | وضعیت |
|---|---|
| **ثبت سفارش** (`POST`) | ❌ در آن سشن سفارشی ثبت نشده، پس در گزارش نیست |
| فرمت دقیق فریم‌های Lightstreamer | ❌ فقط تعداد فریم ثبت شده |
| مکانیزم تازه‌سازی توکن | ❌ |

برای پیدا کردن endpoint ثبت سفارش، `3-discover-api.bat` را **در ساعت
بازار** اجرا کنید و **فرم ثبت سفارش را باز کنید** (لازم نیست ارسال کنید؛
باز کردن فرم، درخواست‌های پیش‌نیاز مثل محاسبه وجه تضمین را می‌زند).

---

## نکات امنیتی

- `var/emofid/session.json` **معادل دسترسی کامل به حساب** است. در
  `.gitignore` است؛ جایی نفرستید.
- فایل HAR خام هم توکن دارد. فقط گزارش پاک‌سازی‌شده قابل اشتراک است.
- این پروژه **هیچ سفارشی ثبت نمی‌کند**. آداپتر فعلی فقط `GET` می‌زند و
  یک تست گارد AST مانع رسیدن هر ماژولی به لایه‌ی `execution` می‌شود.

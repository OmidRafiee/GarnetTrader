# API پلتفرم ایزی‌تریدر (مفید) — آنچه از کشف به دست آمد

منبع: اجرای `scripts/emofid_login.py` روی یک سشن لاگین‌شده‌ی واقعی.
گزارش خام در `var/emofid/api_inventory.md` (خارج از گیت).

> ⚠️ **مفید API عمومی مستندی ندارد.** هر چه اینجاست از مشاهده‌ی ترافیک
> وب‌اپ با حساب خودِ کاربر آمده. یعنی **هر آپدیت وب‌اپ می‌تواند این‌ها را
> بشکند**. کد باید شکست را با خطای خوانا اعلام کند، نه اینکه بی‌صدا
> داده‌ی غلط بدهد.

## خلاصه

- **۵۷۹ endpoint یکتا**، **۲ اتصال realtime** (دو اجرای کشف)
- هاست اصلی API: `api-mts.orbis.easytrader.ir`
- هاست لاگین: `login.emofid.com` (جدا از `easytrader.ir`)

### احراز هویت — OpenID Connect

پلتفرم OIDC استاندارد است:

```
GET  https://login.emofid.com/.well-known/openid-configuration
POST https://login.emofid.com/connect/token     ← توکن اینجا ساخته می‌شود
```

**توکن Bearer در فایل سشن ذخیره نمی‌شود** — وب‌اپ آن را در لحظه می‌گیرد.
کوکی `.AspNetCore.Identity.Application` (روی `login.emofid.com`) همان چیزی
است که این تبادل را ممکن می‌کند.

⚠️ **آزموده شد: کوکی به‌تنهایی کافی نیست.** تمام `/option/api/*` هدر
`authorization` می‌خواهد و با کوکی تنها **۴۰۱** می‌دهد. توزیع در کشف:

| احراز هویت | تعداد endpoint |
|---|---|
| فقط `cookie` | ۳۶۶ |
| `authorization` | ۸۵ |
| بدون هدر | ۱۲۸ |

برای استفاده‌ی فعلی، توکن را دستی بدهید (از DevTools → Network → یک
درخواست `api-mts` → هدر `authorization`؛ عمر کوتاهی دارد):

```python
client = EmofidAccountClient(token="eyJ...")
```

پیاده‌سازی تبادل خودکار توکن (`connect/token`) هنوز انجام نشده.

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

### موجودی و قدرت خرید ✅ پیاده شد

```
GET /easy/api/money
```

| فیلد | معنی |
|---|---|
| `t0` / `t1` / `t2` | نقد در هر مرحله‌ی تسویه |
| `buyPowerT0` / `T1` / `T2` | قدرت خرید در هر مرحله |
| `marginBlock` | وجه تضمین بلوکه‌شده‌ی موقعیت‌های آپشن |
| `block` / `blockT2` / `withdrawBlockT2` | انواع بلوکه |
| `credit` / `avandCredit` / `warrantValueCredit` | اعتبار |
| `walletWithdrawBalanceT0` / `hamiBalance` | کیف پول |

`AccountBalance.equity` روی `buyPowerT2` بسته می‌شود (محافظه‌کارانه‌ترین
تعریف)، و اگر نبود به `t2` برمی‌گردد. اینجا هیچ فیلدی `_require` نیست:
کارگزاری برای حساب‌های مختلف بخشی از این‌ها را نمی‌فرستد و صفر معنای
درستی دارد.

⚠️ روی حساب زنده تست نشده — فقط روی شکل ضبط‌شده‌ی همین سشن.

---

### دارایی سهم ✅ پیاده شد

```
GET /assetmodule/api/performance
```

`items[]` با `symbolIsin`, `symbolName`, `asset` (تعداد سهم فعلی) و
`totalBuyAveragePrice`.

⚠️ این **کارنامه‌ی عملکرد** است، نه لیست دارایی: ردیفِ سهمی که فروخته‌ای
هم می‌ماند، با `asset: 0`. آداپتر آن‌ها را فیلتر می‌کند — نگه‌داشتن‌شان
یعنی Covered Call روی سهمی که نداری تأیید شود.

مصرف‌کننده: شرط مالکیت Covered Call.

---

### دارایی

```
GET /assetmodule/api/performance
```

`items[]` با `symbolIsin`, `asset`, `totalBuyAveragePrice`,
`totalRealizedProfitAndLoss` و چند ده فیلد دیگر.

---

## ثبت سفارش — کشف شد

⚠️ **این پروژه هیچ‌کدام را صدا نمی‌زند.** فقط ثبت شده‌اند تا وقتی
لازم شد، از روی داده‌ی واقعی پیاده شوند، نه از روی حدس.

```
POST   /option/api/Orders/Buy      ثبت خرید
POST   /option/api/Orders/Sell     ثبت فروش
PUT    /option/api/Orders/Sell     ویرایش
DELETE /option/api/Orders          لغو
```

**payload خرید و فروش:**

| فیلد | نوع |
|---|---|
| `symbolIsin` | string |
| `symbolName` | string |
| `price` | number |
| `quantity` | number |
| `validityType` | number |
| `orderModelType` | number |
| `totalValue` | number |
| `orderFrom` | number |

`PUT` همان‌ها به‌اضافه‌ی `id`, `side`, `parentId`.
`DELETE` فقط `{orderId, orderFrom}`.

پاسخ خطا: `{error: string, code: number, name: string}`.

### گزارش سفارش‌ها

```
POST /easy/api/orderHistory/orderReport
POST /option/api/Positions/history-read-model
```

هر دو صفحه‌بندی‌شده (`page`, `pageSize`, `sort`) و پاسخ‌شان `records[]`
شامل `referenceId` است — همان جایی که idempotency می‌نشیند.

---

## آنچه هنوز کشف نشده

| نیاز | وضعیت |
|---|---|
| تبادل خودکار توکن (`connect/token`) | ❌ پارامترهایش ثبت نشده |
| فرمت فریم‌های Lightstreamer | ❌ فقط تعداد فریم |
| معنی عددی `validityType` و `orderModelType` | ❌ باید از UI استخراج شود |

---

## نکات امنیتی

- `var/emofid/session.json` **معادل دسترسی کامل به حساب** است. در
  `.gitignore` است؛ جایی نفرستید.
- فایل HAR خام هم توکن دارد. فقط گزارش پاک‌سازی‌شده قابل اشتراک است.
- این پروژه **هیچ سفارشی ثبت نمی‌کند**. آداپتر فعلی فقط `GET` می‌زند و
  یک تست گارد AST مانع رسیدن هر ماژولی به لایه‌ی `execution` می‌شود.

# سیستم سیگنال‌دهی دستی معاملات آپشن بورس تهران

ربات بازار آپشن بورس تهران را رصد می‌کند و وقتی شرایط یک استراتژی برقرار شد،
یک **سیگنال معاملاتی** (نه سفارش خودکار) تولید و از طریق تلگرام / لاگ / کنسول نمایش می‌دهد.
کاربر خودش سیگنال را دستی در سامانه معاملاتی‌اش اجرا می‌کند.

> ⚠️ **هیچ اتصالی به API خرید/فروش کارگزاری در این پروژه وجود ندارد.**
> فقط یک اینترفیس انتزاعی خالی (`execution/order_executor_interface.py`) برای آینده گذاشته شده
> که پیاده‌سازی واقعی‌اش را بعداً خود کاربر می‌دهد.

**وضعیت فعلی:** مایل‌استون ۱ کامل + **داده واقعی بازار وصل شد** (TSETMC، بدون لاگین).
**۱۵۵ تست پاس** (تأییدشده). برای اجرا اول بخش «آماده‌سازی محیط» را ببینید.

---

## Getting Started

### ۰. آماده‌سازی محیط (یک بار)

```bash
cd option_signal_bot

py -m venv .venv                                  # ویندوز
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

از این به بعد در همه‌ی دستورها **`.venv\Scripts\python.exe`** را به‌جای `python`
بگذارید (یا اول `.venv\Scripts\activate` را اجرا کنید تا `python` خودش درست شود).

⚠️ **روی ویندوز `PYTHONUTF8=1` را لازم دارید.** خروجی این پروژه فارسی است و کنسول
پیش‌فرض ویندوز `cp1252` است؛ بدون این متغیر، دستورها با `UnicodeEncodeError` می‌افتند:

```bash
set PYTHONUTF8=1        # cmd — یک بار در هر پنجره
$env:PYTHONUTF8=1       # PowerShell
```

برای همیشگی‌کردنش: `setx PYTHONUTF8 1` (پنجره‌ی جدید لازم دارد).

> **PyYAML اجباری است، نه اختیاری.** اگر نصب نباشد، `settings.yaml` شما **بی‌صدا
> نادیده گرفته می‌شود** و ربات به پیش‌فرض `provider: mock` برمی‌گردد — یعنی سیگنال
> با قیمت‌های ساختگی تولید می‌کند که شکلش عیناً شبیه سیگنال واقعی است. تنها نشانه‌اش
> یک خط `PyYAML نصب نیست` در ابتدای لاگ و برچسب `منبع داده: mock+mock` روی سیگنال است.
> همیشه آن برچسب را چک کنید.

### ۱. اجرای اولین سیگنال تستی (بدون تنظیمات)

```bash
set PYTHONUTF8=1
.venv\Scripts\python.exe main.py --dry-run
```

در حالت `--dry-run`:
- داده بازار از کلاینت **mock** می‌آید (نه TSETMC)
- خروجی فقط در **کنسول** چاپ می‌شود؛ نیازی به توکن تلگرام نیست
- هیچ چیزی روی دیسک نوشته نمی‌شود
- هیچ سفارشی ثبت نمی‌شود

نمونه خروجی:

```
────────────────────────────────────────────────────────────────
🟢 خرید Call | ضفول-0+0
استراتژی: directional_ma_cross
نماد پایه: فولاد @ 6,427
استرایک: 6,500
سررسید: 2026-09-07 (21 روز)
پرمیوم پیشنهادی: 355
تعداد پیشنهادی: 50 قرارداد
حد ضرر پیشنهادی: 231
حد سود پیشنهادی: 603
اعتماد: 1.00
دلیل: روند صعودی روی فولاد: MA5 نسبت به MA20 +5.82٪ و مومنتوم 10 روزه +10.79٪ ...
اعتبار تا: 2026-08-17 16:42

⚠️ این فقط یک سیگنال است؛ ثبت سفارش را خودتان دستی انجام دهید.
────────────────────────────────────────────────────────────────
```

> هسته پروژه (Black-Scholes، IV، SQLite، تلگرام) فقط با کتابخانه استاندارد پایتون کار می‌کند،
> پس `--dry-run` بدون `pip install` هم اجرا می‌شود.

### ۲. نصب کامل وابستگی‌ها (برای داده واقعی)

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> اگر بخش ۰ را انجام داده‌اید، این مرحله تکمیل است.

### ۳. تنظیمات

```bash
cp config/settings.example.yaml config/settings.yaml
```

فایل نمونه از پیش روی داده **واقعی** TSETMC تنظیم شده (`provider: tsetmc`).
کافی است:
- `market_data.symbols` را با نمادهایی که آپشن دارند پر کنید
  (لیست را با `.venv\Scripts\python.exe scripts/fetch_tsetmc_sample.py` ببینید)
- `risk.*` را با اندازه حساب خودتان تنظیم کنید
- برای اجرای کاملاً آفلاین، `provider` هر دو بخش را `mock` بگذارید

**توکن تلگرام** را در فایل ننویسید؛ از متغیرهای محیطی استفاده کنید
(مقدار متغیر محیطی بر مقدار فایل اولویت دارد):

```bash
set TELEGRAM_BOT_TOKEN=123456:ABC...
set TELEGRAM_CHAT_ID=-1001234567890
```

و در `settings.yaml` مقدار `notifiers.telegram.enabled` را `true` کنید.
`config/settings.yaml` در `.gitignore` است تا توکن به گیت نرود.

### ۴. اجرای عادی

```bash
set PYTHONUTF8=1

.venv\Scripts\python.exe main.py --once            # یک پاس رصد بازار
.venv\Scripts\python.exe main.py                   # حلقه دائمی با فاصله poll_interval_seconds
.venv\Scripts\python.exe main.py --json            # چاپ سیگنال‌ها به‌صورت JSON
.venv\Scripts\python.exe main.py --symbols خودرو شستا وبملت
.venv\Scripts\python.exe main.py --backtest        # گزارش کیفیت سیگنال روی داده تاریخی
.venv\Scripts\python.exe main.py --mock            # اجبار به داده mock (بدون شبکه)
```

> بیرون ساعت بازار، `--once` فقط `بازار بسته است` می‌دهد و برمی‌گردد.
> برای تست در بازار بسته، در `settings.yaml` مقدار `general.run_only_when_market_open`
> را `false` بگذارید — داده از TSETMC واقعی می‌آید ولی مظنه‌ها آخرین وضعیت روز قبل‌اند.

### ۵. تست‌ها

```bash
set PYTHONUTF8=1
.venv\Scripts\python.exe -m pytest -q
```

### ۶. داده واقعی بازار (بدون لاگین و بدون توکن)

زنجیره آپشن و تاریخچه نماد پایه از **API عمومی TSETMC** می‌آید؛ هیچ احراز هویتی،
کوکی یا حساب کارگزاری لازم نیست:

| داده | endpoint |
|---|---|
| زنجیره کامل آپشن بازار (یک درخواست) | `cdn.tsetmc.com/api/Instrument/GetInstrumentOptionMarketWatch/{market}` |
| تاریخچه روزانه نماد پایه | `cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{insCode}/0` |

هر ردیف پاسخ دیده‌بان، یک استرایک با **جفت** کال و پوت است و قیمت لحظه‌ای نماد پایه
(`pDrCotVal_UA`) و کد یکتای آن (`uaInsCode`) را هم دارد. نتیجه:

- کل بازار با یک درخواست می‌آید، نه یکی به‌ازای هر نماد
- قیمت پایه و پرمیوم آپشن از **یک پاسخ و یک لحظه**اند (اختلاف منبع، محاسبه IV را خراب می‌کند)
- نگاشت نماد→`insCode` از همان پاسخ ساخته می‌شود؛ به جدول نماد جداگانه نیازی نیست

دیدن لیست نمادهایی که آپشن دارند و وضعیت بازار:

```bash
set PYTHONUTF8=1
.venv\Scripts\python.exe scripts/fetch_tsetmc_sample.py                 # گزارش
.venv\Scripts\python.exe scripts/fetch_tsetmc_sample.py --save-fixture  # به‌روزرسانی نمونه تست
```

**سه لایه داده** پشت همان اینترفیس، با `option_chain.provider` انتخاب می‌شوند:

| provider | داده | کاربرد |
|---|---|---|
| `tsetmc` | زنده | اجرای واقعی |
| `fixture` | پاسخ واقعی **ضبط‌شده** | تست آفلاین، بازتولید یک روز خاص |
| `mock` | مصنوعی با seed ثابت | `--dry-run` و تست‌های قطعی |

**گیت‌های کیفیت داده** (`option_chain.quality`) لازم‌اند، نه تشریفاتی: در یک اجرای
واقعی روی خودرو، ۲۳ قرارداد از ۷۸ حذف شد (۱۱ اسپرد پهن، ۱۲ بدون موقعیت باز).
هر حذف در لاگ شمرده می‌شود تا بی‌صدا نباشد.

### ۷. کشف API پلتفرم کارگزاری (ایزی‌تریدر / emofid)

آدرس درست پلتفرم **`easytrader.ir`** است (متعلق به مفید، و ارجاع‌دهنده به `emofid.com`).
⚠️ `easytrader.com` یک صفحه‌ی **فروش دامنه**ی بی‌ربط است؛ هرگز اعتبارنامه‌تان را آن‌جا وارد نکنید.

TSETMC داده‌ی بازار را می‌دهد ولی سه چیز را نمی‌دهد: **پوزیشن و موجودی حساب**،
**عمق مظنه**، و **push زیرثانیه**. برای آن‌ها به سشن احرازهویت‌شده‌ی کارگزاری نیاز است.
گام اول، کشف سطح API است — با دو مسیر:

> ⏰ **حتماً در ساعت بازار اجرا کنید** (شنبه–چهارشنبه، ~۹:۰۰ تا ۱۲:۳۰).
> در بازار بسته، دیده‌بان و عمق مظنه هیچ داده‌ای push نمی‌کنند و گزارش تقریباً خالی
> درمی‌آید. تست‌شده: اجرای یک‌دقیقه‌ای بیرون ساعت بازار فقط ۲ endpoint داد که
> هر دو آنالیتیکس بودند.

**مسیر ۱ — مرورگر قابل‌مشاهده (توصیه‌شده)**

نصب یک‌باره:
```bash
.venv\Scripts\python.exe -m pip install playwright
```
اگر Chrome سیستم را دارید، به `playwright install` نیازی نیست؛ اسکریپت با
`channel="chrome"` همان Chrome خودتان را می‌راند (بدون دانلود ~۴۰۰ مگابایتی).

اجرا:
```bash
set PYTHONUTF8=1
.venv\Scripts\python.exe scripts/emofid_login.py --url https://easytrader.ir --minutes 15
```

چه اتفاقی می‌افتد:
1. یک پنجره‌ی واقعی Chrome باز می‌شود و به `easytrader.ir` می‌رود
2. **خودتان** نام کاربری، رمز و OTP را وارد می‌کنید — اسکریپت هیچ فرمی پر نمی‌کند
3. بعد از لاگین، این صفحات را باز کنید (هرچه بیشتر، فهرست کامل‌تر):
   **دیده‌بان بازار آپشن**، **سبد دارایی/پوزیشن**، **موجودی حساب**،
   **عمق مظنه یک نماد آپشن**
4. برای پایان زودتر از ۱۵ دقیقه: `Ctrl+C`

**مسیر ۲ — بدون هیچ نصبی (HAR)**

اگر نمی‌خواهید playwright نصب کنید یا مرورگر را به اسکریپت بسپارید:

1. Chrome → `F12` → تب **Network**
2. تیک **«Preserve log»** را بزنید
3. لاگین کنید و همان صفحات بالا را باز کنید
4. راست‌کلیک روی لیست درخواست‌ها → **«Save all as HAR with content»**
5. سپس:

```bash
set PYTHONUTF8=1
.venv\Scripts\python.exe scripts/har_to_inventory.py مسیر\فایل.har
```

خروجی هر دو مسیر یکی است: `var/emofid/api_inventory.md` و `.json`.

**بعد از اجرا، این‌ها را در گزارش نگاه کنید:**

```bash
type var\emofid\api_inventory.md
```

- هر endpoint مربوط به **پوزیشن / موجودی / سبد** → ورودی آداپتر `AccountDataSource`
- **اتصال سوکت** (SignalR/WebSocket) → مسیر `RealtimeQuoteSource` برای push زیرثانیه
- **هدرهای احراز هویت** (`authorization` یا `cookie`) → نحوه‌ی نگه‌داشتن سشن

**تضمین حریم خصوصی — «شکل را ضبط کن، نه داده را»:** برای نوشتن آداپتر، نام فیلدها
و نوعشان کافی است؛ مقدار موجودی و توکن هیچ‌وقت لازم نیست. پس:

| داده | چه چیزی ثبت می‌شود |
|---|---|
| بدنه‌ی پاسخ | فقط اسکلت نوع‌ها: `{"balance": "number"}` نه `{"balance": 987654321}` |
| هدر احراز هویت | فقط **نوعش**: `Bearer <REDACTED>` |
| شناسه در مسیر URL | `/accounts/<NUM>/positions` |
| پارامتر حساس در query | `token=<REDACTED>` (ولی `type=option` می‌ماند چون مفید است) |
| فریم SignalR | فقط نام متد (`OnBestLimitChanged`)، نه آرگومان‌ها |

این تضمین **تست دارد**: `tests/test_api_inventory.py` یک HAR ساختگی با توکن و کد ملی و
موجودی جعلی می‌سازد و بررسی می‌کند هیچ‌کدام در خروجی نباشند.

⚠️ دو فایل حساس‌اند و زیر `var/` (در `.gitignore`) می‌مانند: `session.json`
(معادل دسترسی به حساب) و فایل HAR خام. **گزارش خروجی** قابل اشتراک است، ولی
قبلش یک مرور چشمی بکنید.

---

## کارهای انجام‌شده

### مایل‌استون ۱ — تولید سیگنال دستی ✅

**هدف**
- [x] رصد بازار آپشن و تولید سیگنال بر اساس شرایط استراتژی
- [x] نمایش سیگنال از طریق تلگرام / لاگ / کنسول
- [x] اجرای دستی توسط کاربر (بدون سفارش خودکار)
- [x] بدون هیچ اتصال به API خرید/فروش کارگزاری
- [x] فقط یک Interface انتزاعی خالی برای آینده

**الزامات فنی**
- [x] Python 3.11+ با type hints و docstring فارسی برای هر ماژول
- [x] `pytse-client` برای دیتای بورس + کلاینت mock جایگزین‌شدنی (سقوط خودکار در صورت خطا)
- [x] `signal_generator.py` و `order_executor_interface.py` کاملاً مستقل
- [x] `signal_model.py` ساده، تایپ‌شده (dataclass) و قابل serialize به JSON
- [x] فلگ `--dry-run` برای چاپ سیگنال بدون تنظیم تلگرام
- [x] `requirements.txt` با pytse-client, python-telegram-bot, pydantic, pandas, numpy, scipy, pytest
- [x] بخش Getting Started در README

**ماژول‌های پیاده‌شده**

| بخش | چه چیزی ساخته شد |
|---|---|
| `data/` | `MarketDataClient` و `OptionChainClient` انتزاعی؛ mock قطعی (seed ثابت)؛ `PytseMarketDataClient` با سقوط خودکار به mock در خطای شبکه/نصب |
| `pricing/` | Black-Scholes اروپایی + Delta/Gamma/Theta/Vega/Rho (تتا به‌ازای روز، وگا و رو به‌ازای ۱٪)؛ IV با نیوتن-رافسون + پشتیبان تنصیف؛ نوسان تاریخی. بدون نیاز به scipy (`math.erf`) |
| `strategies/` | `BaseStrategy` + `StrategyContext` فقط-خواندنی؛ دو استراتژی نمونه: تقاطع MA (جهت‌دار) و IV گران/ارزان (Covered Call / Long Straddle)؛ رجیستری خودکار |
| `signals/` | `Signal` قابل JSON؛ `SignalGenerator` با فیلتر اعتماد، اعمال ریسک، پنجره ضدتکرار و مقاومت در برابر خطای یک استراتژی/نماد |
| `risk/` | اندازه پوزیشن بر پایه درصد ریسک حساب، سقف ارزش پوزیشن و سقف تعداد قرارداد؛ حد ضرر/سود پیشنهادی |
| `notifiers/` | قالب متنی مشترک؛ کنسول (با حالت JSON)؛ تلگرام با `urllib` (بدون وابستگی سخت) و خودخاموش‌شو در نبود توکن |
| `storage/` | SQLite برای Audit + فایل JSONL انسان‌خوان؛ ذخیره idempotent روی `signal_id` |
| `backtest/` | سنجش کیفیت **جهت‌دهی** سیگنال روی داده تاریخی، با گزارش تفکیک‌شده به‌ازای استراتژی |
| `execution/` | فقط `OrderExecutorInterface` انتزاعی + `MockBroker` درون‌حافظه (پوزیشن، میانگین وزنی، پر شدن جزئی) |

**پاس تمیزکاری معماری** (برای گسترش راحت در آینده)

| کار | چه چیزی را آسان می‌کند |
|---|---|
| `bootstrap.py` — لایه wiring جدا از CLI | افزودن entrypoint دوم (داشبورد، نوتبوک) بدون کپی‌کردن wiring |
| `config/loader.py` — تک‌منبعِ پیش‌فرض‌ها + ادغام عمیق | بازنویسی جزئی yaml دیگر بقیه پارامترها را پاک نمی‌کند |
| `strategies/registry.py` با دکوریتور `@register_strategy` | افزودن استراتژی = یک فایل + یک خط import، بدون دست‌زدن به `main.py` |
| رجیستری provider و notifier در `bootstrap.py` | افزودن TSETMC یا کانال جدید = یک ورودی دیکشنری |
| `build_dataclass` با نادیده‌گرفتن کلید ناشناخته | یک غلط‌املایی در yaml دیگر ربات را با TypeError نمی‌خواباند |
| provider اشتباه = خطای خوانا در startup | خطای تنظیمات زودتر و واضح‌تر دیده می‌شود |
| خروجی‌های اجرا به `var/` منتقل شد | پوشه‌های کد، کد می‌مانند |
| `pyproject.toml` (pytest + ruff) | لینت و تست یکدست، آماده CI |
| تست گارد سراسری AST | هر ماژول **آینده** هم خودکار از import کردن `execution` منع می‌شود |

### مایل‌استون ۲ — اتصال داده واقعی بازار ✅

| کار | جزئیات |
|---|---|
| کشف endpoint عمومی TSETMC | دیده‌بان بازار آپشن + تاریخچه روزانه، **بدون لاگین/توکن/کوکی** — نیازی به حساب کارگزاری نشد |
| `TsetmcOptionChainClient` | نگاشت کامل فیلدهای واقعی؛ کل زنجیره بازار با یک درخواست |
| `TsetmcMarketDataClient` | قیمت لحظه‌ای پایه از همان پاسخ (بدون درخواست اضافه) + تاریخچه روزانه |
| گیت‌های کیفیت داده | مظنه، اسپرد نسبی، موقعیت باز، سررسید نزدیک — با گزارش شمارش در لاگ |
| `provider: fixture` | پخش پاسخ واقعی ضبط‌شده؛ تست یکپارچگی بدون شبکه |
| یکسان‌سازی منبع قیمت پایه | `StrategyContext.spot` اول از قیمت داخل زنجیره می‌خواند تا با پرمیوم هم‌لحظه باشد |
| برچسب `data_source` | هر سیگنال منبع داده‌اش را حمل و در متن تلگرام/کنسول نشان می‌دهد |
| رفع سقوط بی‌صدا به mock | `allow_fallback: false` + لاگ ERROR، تا سیگنال با قیمت مصنوعی شبیه سیگنال واقعی نباشد |

---

## تسک‌های آینده

### مایل‌استون ۲ — داده واقعی بازار ✅ (عمدتاً انجام شد)
- [x] `TsetmcOptionChainClient`: زنجیره واقعی آپشن (استرایک، سررسید، مظنه، OI) ثبت‌شده در `OPTION_CHAIN_PROVIDERS`
- [x] نگاشت نمادهای واقعی آپشن تهران (مثل `ضخود6053`) به `OptionContract`
- [x] `TsetmcMarketDataClient`: قیمت لحظه‌ای و تاریخچه روزانه نماد پایه از TSETMC
- [x] کش دوسطحی با TTL (زنجیره ~۲۰ثانیه، تاریخچه ~۱۵دقیقه) + تلاش مجدد با backoff
- [x] گیت‌های کیفیت داده (مظنه، اسپرد نسبی، موقعیت باز، سررسید نزدیک) با گزارش شمارش
- [x] تست‌های یکپارچگی روی نمونه پاسخ **واقعی** ضبط‌شده (بدون شبکه)
- [x] برچسب `data_source` روی هر سیگنال + رفع سقوط بی‌صدای pytse به mock
- [ ] تقویم معاملاتی: تعطیلات رسمی (جای `is_market_open` تقریبی فعلی)
- [ ] عمق مظنه (چند سطح سفارش) — فعلاً فقط بهترین مظنه خرید/فروش داریم
- [ ] تعدیل استرایک و اندازه قرارداد پس از افزایش سرمایه/سود نقدی

### مایل‌استون ۳ — کیفیت سیگنال و استراتژی‌ها
- [ ] استراتژی‌های چندپایه: Bull/Bear Spread، Iron Condor، Calendar Spread
- [ ] سیگنال چندپایه به‌عنوان **یک** شیء (به‌جای دو سیگنال مستقل در Straddle فعلی)
- [ ] سطح نوسان ضمنی: ساخت IV surface و تشخیص گران/ارزان نسبت به تاریخ خودِ نماد
- [ ] بک‌تست واقعی روی **پرمیوم تاریخی** آپشن (محدودیت فعلی: فقط جهت‌دهی پایه سنجیده می‌شود)
- [ ] معیارهای حرفه‌ای بک‌تست: Sharpe، حداکثر افت، انتظار ریاضی به‌ازای استراتژی
- [ ] بهینه‌سازی پارامترها + اعتبارسنجی walk-forward برای پرهیز از overfit
- [ ] پیگیری نتیجه هر سیگنال صادرشده (`signal_outcome`) برای سنجش کیفیت واقعی در بازار زنده

### مایل‌استون ۴ — تجربه کاربری و عملیات
- [ ] داشبورد وب (FastAPI + صفحه ساده) برای سیگنال‌های زنده و تاریخچه
- [ ] دستورهای تلگرام: `/signals`, `/status`, `/mute`, تأیید دریافت سیگنال
- [ ] گزارش روزانه/هفتگی خودکار از عملکرد سیگنال‌ها
- [ ] هشدار سلامت سیستم (قطع دیتا، خطای مکرر استراتژی، سیگنال صفر در چند روز)
- [ ] اجرای دائمی: سرویس ویندوز / systemd / Docker
- [ ] CI (اجرای pytest + ruff روی هر push) و بسته‌بندی با `pyproject.toml`

### مایل‌استون ۴.۵ — اتصال حساب کارگزاری (در جریان)
- [x] اسکلت لاگین با مرورگر قابل‌مشاهده (کاربر خودش لاگین می‌کند، OTP دستی)
- [x] ابزار کشف API با تضمین تست‌شده‌ی «شکل بدون داده»
- [x] مسیر جایگزین HAR بدون نیاز به نصب
- [ ] اجرای کشف روی سشن واقعی و ساخت `var/emofid/api_inventory.md`
- [ ] اینترفیس `AccountDataSource` (پوزیشن، موجودی) + آداپتر emofid
- [ ] وصل کردن `account_equity` به موجودی واقعی (جای عدد دستی در yaml)
- [ ] مشروط کردن سیگنال Covered Call به مالکیت واقعی سهم پایه
- [ ] اینترفیس `RealtimeQuoteSource` + آداپتر SignalR (روی پولینگ TSETMC)
- [ ] بررسی وجود API رسمی مفید (اگر باشد، بر مهندسی معکوس اولویت دارد)

### مایل‌استون ۵ — اجرای سفارش (فقط با تصمیم صریح کاربر)
- [ ] پیاده‌سازی واقعی `OrderExecutorInterface` برای کارگزاری هدف
- [ ] حالت نیمه‌خودکار: تأیید دستی هر سفارش قبل از ارسال
- [ ] کنترل‌های ایمنی: سقف روزانه، kill-switch، محدودیت نرخ سفارش
- [ ] آشتی‌دهی پوزیشن واقعی با پوزیشن مورد انتظار
- [ ] ⚠️ تا زمانی که کاربر صریحاً نخواهد، این مایل‌استون شروع نمی‌شود

### بدهی فنی شناخته‌شده
- [x] ~~`MockOptionChainClient` تنها پیاده‌سازی زنجیره است~~ → `tsetmc` و `fixture` اضافه شدند
- [ ] `pytse-client` در این محیط نصب نیست؛ مسیر `provider: pytse` تست واقعی نشده (مسیر `tsetmc` جایگزین و تست‌شده است)
- [ ] بک‌تست از زنجیره امروز با سررسید جابه‌جاشده استفاده می‌کند (`_shift_chain`)؛ پرمیوم تاریخی نیست
- [ ] `is_market_open` تقویم تعطیلات ندارد
- [ ] `Signal.notional` اندازه قرارداد را از `metadata` می‌خواند (پیش‌فرض ۱۰۰۰) — بهتر است فیلد درجه‌یک شود
- [ ] `pydantic` در وابستگی‌ها هست ولی مدل‌ها فعلاً dataclass‌اند (عمدی: هسته بدون وابستگی خارجی)
- [ ] کارمزد و مالیات معاملات آپشن در حد سود/ضرر پیشنهادی لحاظ نشده

---

## معماری

```
market_data_client ─┐
                    ├─→ StrategyContext ─→ strategies ─→ Signal ─┬─→ notifiers (تلگرام/کنسول)
option_chain_client ┘                          ↑                 └─→ storage/signal_log (Audit)
                                        risk_calculator
                                    (تعداد و حد ضرر پیشنهادی)

bootstrap.py  → از settings همه اجزای بالا را می‌سازد (DI)
main.py       → فقط CLI و حلقه اجرا

execution/order_executor_interface.py   ← جدا و منفصل؛ هیچ‌کس در این مایل‌استون آن را صدا نمی‌زند
```

قواعد سختی که رعایت و **تست** شده‌اند:

| قاعده | جای اعمال |
|---|---|
| استراتژی فقط `Signal` برمی‌گرداند | `strategies/base_strategy.py` |
| استراتژی به نوتیفایر/دیتابیس/اجرا دسترسی ندارد | `StrategyContext` فقط داده می‌دهد + تست AST |
| هیچ ماژولی بیرون از `execution/` آن را import نمی‌کند | تست گارد سراسری در `tests/test_signal_generator.py` |
| اندازه پوزیشن و حد ضرر فقط «پیشنهاد» است | `risk/risk_calculator.py` |
| هیچ I/O شبکه‌ای در لایه اجرا نیست | تست در `tests/test_mock_broker.py` |

### افزودن یک استراتژی جدید

```python
# strategies/my_strategy.py
from strategies.base_strategy import BaseStrategy
from strategies.registry import register_strategy

@register_strategy
class MyStrategy(BaseStrategy):
    name = "my_strategy"

    @classmethod
    def default_params(cls):
        return {"threshold": 2.0}

    def generate(self, context):
        contract = self.select_contract(context, "call", moneyness=0.03)
        return [] if contract is None else [
            self.build_signal(context, contract, Side.BUY, "دلیل سیگنال")
        ]
```

بعد یک خط `from strategies.my_strategy import MyStrategy` به `strategies/__init__.py`
اضافه کنید. تمام — نه `main.py` تغییر می‌کند و نه `bootstrap.py`.

---

## ساختار پوشه‌ها

```
option_signal_bot/
├── README.md                   ← همین فایل
├── pyproject.toml              تنظیمات pytest و ruff
├── conftest.py                 ریشه پروژه را به sys.path اضافه می‌کند
├── main.py                     CLI و حلقه اصلی
├── bootstrap.py                لایه wiring (DI) — ساخت اجزا از تنظیمات
├── requirements.txt
├── config/
│   ├── loader.py               پیش‌فرض‌ها، ادغام عمیق yaml، اعتبارسنجی نرم
│   └── settings.example.yaml   نمونه تنظیمات (بدون مقدار واقعی)
├── data/
│   ├── market_data_client.py         اینترفیس داده پایه + mock + pytse-client
│   ├── option_chain_client.py        اینترفیس زنجیره آپشن + mock
│   ├── tsetmc_http.py                ابزار HTTP مشترک (retry + backoff)
│   ├── tsetmc_market_data_client.py  داده واقعی نماد پایه از TSETMC
│   └── tsetmc_option_chain_client.py زنجیره واقعی آپشن + گیت‌های کیفیت داده
├── pricing/
│   ├── black_scholes.py        قیمت‌گذاری اروپایی + Greeks
│   └── implied_volatility.py   IV از پرمیوم بازار + نوسان تاریخی
├── strategies/
│   ├── base_strategy.py        Abstract base class، خروجی فقط Signal
│   ├── registry.py             رجیستری خودکار استراتژی‌ها
│   ├── directional_strategy.py خرید Call/Put بر اساس تکنیکال
│   └── neutral_strategy.py     Covered Call / Straddle
├── signals/
│   ├── signal_model.py         dataclass تایپ‌شده و قابل JSON
│   └── signal_generator.py     orchestrator (کاملاً جدا از execution)
├── execution/
│   └── order_executor_interface.py  Abstract خالی + MockBroker
├── notifiers/
│   ├── base_notifier.py
│   ├── telegram_notifier.py
│   └── console_notifier.py
├── risk/
│   └── risk_calculator.py      position sizing و stop-loss پیشنهادی
├── backtest/
│   └── signal_backtester.py    بک‌تست کیفیت سیگنال
├── storage/
│   └── signal_log.py           SQLite + JSONL برای Audit
├── discovery/
│   └── api_inventory.py        کشف API: پاک‌سازی راز + اسکلت نوع‌ها
├── scripts/
│   ├── fetch_tsetmc_sample.py  ضبط نمونه واقعی TSETMC برای fixture
│   ├── emofid_login.py         لاگین دستی در مرورگر قابل‌مشاهده + کشف API
│   └── har_to_inventory.py     HAR → فهرست API (بدون نیاز به نصب)
├── tests/
│   ├── fixtures/               پاسخ واقعی ضبط‌شده TSETMC
│   ├── test_tsetmc_clients.py  نگاشت فیلدها، گیت کیفیت، کش
│   ├── test_pricing.py
│   ├── test_signal_generator.py
│   ├── test_mock_broker.py
│   └── test_bootstrap.py       تنظیمات، رجیستری، wiring، ذخیره‌سازی
└── var/                        (ساخته‌شده در اجرا، gitignore) دیتابیس و لاگ سیگنال‌ها
```

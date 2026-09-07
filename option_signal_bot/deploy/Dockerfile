# اجرای دائمی ربات سیگنال — **بدون هیچ اتصال به ثبت سفارش**.
#
# چرا `python:3.12-slim` و نه `alpine`: پروژه اختیاری pandas/numpy دارد و
# روی alpine (بدون glibc) کامپایل چرخ لازم می‌شود که ساخت را چند دقیقه
# طولانی‌تر و شکننده‌تر می‌کند.

FROM python:3.12-slim

# .pyc نساز و لاگ را بافر نکن — در کانتینر، لاگِ بافرشده یعنی
# `docker logs` تا زمان خروج چیزی نشان نمی‌دهد.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Tehran

WORKDIR /app

# لایه‌ی وابستگی جدا از کد: تغییر کد، نصب را از نو اجرا نمی‌کند.
# هسته وابستگی اجباری ندارد؛ فقط yaml و داشبورد نصب می‌شوند.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir PyYAML fastapi uvicorn

COPY . .

# کاربر غیر-root. کانتینری که به شبکه وصل است و با root اجرا شود،
# ریسکِ بی‌دلیلی است — این ربات هیچ کاری با root لازم ندارد.
RUN useradd --create-home --shell /usr/sbin/nologin garnet \
    && mkdir -p /app/var \
    && chown -R garnet:garnet /app
USER garnet

# خروجی اجرا (دیتابیس سیگنال، تقویم، تاریخچه IV) باید بیرون از ایمیج
# بماند، وگرنه هر بازسازی همه‌ی تاریخچه را پاک می‌کند.
VOLUME ["/app/var", "/app/config"]

# بازار تهران ۰۹:۰۰ تا ۱۲:۳۰ باز است. `run_only_when_market_open` در
# تنظیمات، پاس‌های بیرون ساعت بازار را رد می‌کند.
HEALTHCHECK --interval=5m --timeout=30s --start-period=1m --retries=3 \
    CMD python -c "import bootstrap, main" || exit 1

# ⚠️ هیچ آرگومانی اینجا سفارش ثبت نمی‌کند و راهی به لایه‌ی `execution` نیست.
ENTRYPOINT ["python", "main.py"]
CMD []

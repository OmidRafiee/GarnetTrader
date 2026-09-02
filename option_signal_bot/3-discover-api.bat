@echo off
chcp 65001 >nul
title کشف API ایزی‌تریدر — لاگین دستی در مرورگر
call "%~dp0_common.bat" || goto :end

echo ============================================================
echo   کشف API پلتفرم کارگزاری (ایزی‌تریدر)
echo ============================================================
echo.
echo این ابزار:
echo   - یک پنجره واقعی Chrome باز می‌کند
echo   - شما خودتان نام کاربری، رمز و OTP را وارد می‌کنید
echo   - هیچ فرمی خودکار پر نمی‌شود و هیچ رمزی ذخیره نمی‌شود
echo   - فقط «شکل» APIها ثبت می‌شود، نه مقدار داده‌ها
echo   - هیچ سفارشی ثبت نمی‌شود
echo.
echo بعد از لاگین، این صفحات را باز کنید:
echo   * دیده‌بان / تابلوی بازار آپشن
echo   * سبد دارایی (پوزیشن‌ها)
echo   * موجودی حساب
echo   * عمق مظنه یک نماد آپشن
echo.
echo ------------------------------------------------------------
echo  مهم: در ساعت بازار اجرا کنید (شنبه-چهارشنبه، ۹:۰۰-۱۲:۳۰)
echo  در بازار بسته صفحات داده push نمی‌کنند و گزارش خالی می‌شود.
echo ------------------------------------------------------------
echo.

"%PY%" -c "import playwright" 2>nul
if errorlevel 1 (
    echo [نصب] playwright نصب نیست؛ نصب می‌شود...
    echo.
    "%PY%" -m pip install playwright
    if errorlevel 1 (
        echo.
        echo [خطا] نصب playwright ناموفق بود.
        goto :end
    )
    echo.
)

set /p MINUTES="مدت ضبط به دقیقه [پیش‌فرض 15]: "
if "%MINUTES%"=="" set "MINUTES=15"

echo.
echo ضبط %MINUTES% دقیقه‌ای شروع می‌شود. برای پایان زودتر: Ctrl+C
echo.

"%PY%" scripts\emofid_login.py --url https://easytrader.ir --minutes %MINUTES%

echo.
echo ============================================================
echo  گزارش: var\emofid\api_inventory.md
echo.
echo  هشدار: فایل var\emofid\session.json معادل دسترسی به حساب
echo  شماست. آن را جایی نفرستید. (در .gitignore است)
echo ============================================================

:end
echo.
pause

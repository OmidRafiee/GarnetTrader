@echo off
chcp 65001 >nul
title تبدیل فایل HAR به فهرست API
call "%~dp0_common.bat" || goto :end

echo ============================================================
echo   تبدیل HAR به فهرست API (بدون نیاز به playwright)
echo ============================================================
echo.
echo روش ساخت فایل HAR:
echo   1. در Chrome کلید F12 را بزنید، تب Network
echo   2. تیک «Preserve log» را بزنید
echo   3. لاگین کنید و صفحات پوزیشن/موجودی/دیده‌بان را باز کنید
echo   4. راست‌کلیک روی لیست درخواست‌ها
echo   5. «Save all as HAR with content»
echo.
echo هشدار: فایل HAR خام حاوی توکن و داده حساب شماست.
echo این ابزار پاک‌سازی می‌کند، ولی فایل خام را جایی نفرستید.
echo.

if not "%~1"=="" (
    set "HARFILE=%~1"
) else (
    echo فایل HAR را روی همین پنجره بکشید و رها کنید، سپس Enter
    echo (یا مسیرش را دستی بنویسید)
    echo.
    set /p HARFILE="مسیر فایل HAR: "
)

@rem حذف گیومه‌های احتمالی از drag-and-drop
set "HARFILE=%HARFILE:"=%"

if "%HARFILE%"=="" (
    echo.
    echo [خطا] مسیری داده نشد.
    goto :end
)

if not exist "%HARFILE%" (
    echo.
    echo [خطا] فایل پیدا نشد: %HARFILE%
    goto :end
)

echo.
"%PY%" scripts\har_to_inventory.py "%HARFILE%"

echo.
echo گزارش: var\emofid\api_inventory.md

:end
echo.
pause

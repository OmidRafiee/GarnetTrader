@echo off
chcp 65001 >nul
title داشبورد وب GarnetTrader
call "%~dp0_common.bat" || goto :end

if not exist "config\settings.yaml" (
    copy /y "config\settings.example.yaml" "config\settings.yaml" >nul
)

"%PY%" -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo [نصب] وابستگی‌های داشبورد نصب می‌شود...
    echo.
    "%PY%" -m pip install fastapi uvicorn
    if errorlevel 1 (
        echo.
        echo [خطا] نصب ناموفق بود.
        goto :end
    )
    echo.
)

echo ============================================================
echo   داشبورد وب — سیگنال‌ها، استراتژی‌ها، نمادها، ریسک
echo ============================================================
echo.
echo مرورگر خودکار باز می‌شود. اگر نشد، این آدرس را بروید:
echo    http://127.0.0.1:8787
echo.
echo فقط از همین کامپیوتر در دسترس است.
echo داشبورد هیچ سفارشی ثبت نمی‌کند.
echo.
echo برای توقف: Ctrl+C
echo.

"%PY%" -m web --port 8787

:end
echo.
pause

@echo off
chcp 65001 >nul
title ربات سیگنال — داده واقعی، بدون بررسی ساعت بازار
call "%~dp0_common.bat" || goto :end

if not exist "config\settings.yaml" (
    copy /y "config\settings.example.yaml" "config\settings.yaml" >nul
)

echo ============================================================
echo   داده واقعی TSETMC — بدون بررسی ساعت بازار
echo ============================================================
echo.
echo برای تست بیرون ساعت معاملات. داده از TSETMC واقعی است،
echo ولی مظنه‌ها آخرین وضعیت روز معاملاتی قبل هستند — پس
echo سیگنال‌ها برای تصمیم واقعی معتبر نیستند.
echo.
echo برچسب «منبع داده» را چک کنید:
echo    tsetmc+tsetmc = داده واقعی
echo    mock+mock     = داده ساختگی
echo.

"%PY%" -c "import io,sys; p='config/settings.yaml'; s=io.open(p,encoding='utf-8').read(); s=s.replace('run_only_when_market_open: true','run_only_when_market_open: false'); io.open(p,'w',encoding='utf-8',newline='').write(s)"

"%PY%" main.py --once

:end
echo.
pause

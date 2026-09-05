@echo off
chcp 65001 >nul
title ربات سیگنال — داده واقعی TSETMC
call "%~dp0_common.bat" || goto :end

if not exist "config\settings.yaml" (
    echo [تنظیمات] settings.yaml ساخته می‌شود از فایل نمونه...
    copy /y "config\settings.example.yaml" "config\settings.yaml" >nul
    echo.
)

echo ============================================================
echo   ربات سیگنال — داده واقعی TSETMC
echo   یک پاس رصد بازار، سپس خروج.
echo   هیچ سفارشی ثبت نمی‌شود؛ سیگنال را خودتان دستی اجرا کنید.
echo ============================================================
echo.
echo اگر «بازار بسته است» دیدید: جلسه معاملاتی شنبه تا چهارشنبه
echo ساعت ۹:۰۰ تا ۱۲:۳۰ است. برای تست بیرون ساعت بازار،
echo فایل 5-signal-live-force.bat را اجرا کنید.
echo.

"%PY%" main.py --once

:end
echo.
pause

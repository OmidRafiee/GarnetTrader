@echo off
chcp 65001 >nul
title تست ربات سیگنال (داده mock)
call "%~dp0_common.bat" || goto :end

echo ============================================================
echo   تست ربات — داده mock، بدون شبکه، بدون تلگرام
echo   هیچ سفارشی ثبت نمی‌شود.
echo ============================================================
echo.

"%PY%" main.py --dry-run

:end
echo.
pause

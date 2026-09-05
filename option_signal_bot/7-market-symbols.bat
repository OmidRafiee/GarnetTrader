@echo off
chcp 65001 >nul
title نمادهای دارای آپشن در بازار
call "%~dp0_common.bat" || goto :end

echo ============================================================
echo   نمادهای پایه‌ای که آپشن دارند (از TSETMC زنده)
echo ============================================================
echo.
echo از این لیست برای پر کردن market_data.symbols در
echo config\settings.yaml استفاده کنید.
echo.

"%PY%" scripts\fetch_tsetmc_sample.py

:end
echo.
pause

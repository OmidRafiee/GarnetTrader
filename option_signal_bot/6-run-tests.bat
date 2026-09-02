@echo off
chcp 65001 >nul
title اجرای تست‌ها
call "%~dp0_common.bat" || goto :end

"%PY%" -c "import pytest" 2>nul
if errorlevel 1 (
    echo [نصب] pytest نصب نیست؛ نصب می‌شود...
    "%PY%" -m pip install pytest
    echo.
)

echo ============================================================
echo   اجرای تست‌ها
echo ============================================================
echo.

"%PY%" -m pytest
if errorlevel 1 (
    echo.
    echo [نتیجه] بعضی تست‌ها شکست خوردند. خروجی بالا را ببینید.
) else (
    echo.
    echo [نتیجه] همه تست‌ها پاس شدند.
)

:end
echo.
pause

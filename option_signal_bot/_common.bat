@rem ============================================================
@rem  بوت‌استرپ مشترک همه batch fileها. مستقیم اجرا نکنید.
@rem  کارش: رفتن به پوشه پروژه، ساخت venv در صورت نبود،
@rem  و ست کردن PY به مفسر داخل venv.
@rem ============================================================

@rem %~dp0 = پوشه همین فایل. یعنی دابل‌کلیک از هر جایی کار می‌کند
@rem و دیگر لازم نیست کاربر cd بزند.
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo [نصب] محیط پایتون پیدا نشد؛ یک بار ساخته می‌شود...
    echo.
    py -m venv .venv
    if errorlevel 1 (
        echo.
        echo [خطا] ساخت venv ناموفق بود. آیا Python نصب است؟
        echo        تست کنید:  py --version
        exit /b 1
    )
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [خطا] نصب وابستگی‌ها ناموفق بود.
        exit /b 1
    )
    echo.
    echo [نصب] تمام شد.
    echo.
)

# اجرای دائمی ربات روی ویندوز، با Task Scheduler.
#
#   powershell -ExecutionPolicy Bypass -File deploy\install-windows-task.ps1
#
# ⚠️ این تسک هیچ سفارشی ثبت نمی‌کند.
#
# چرا Task Scheduler و نه یک سرویس ویندوزی واقعی:
# سرویس ویندوزی به یک wrapper (nssm، pywin32) نیاز دارد که یعنی یک
# نصبِ اضافه. Task Scheduler در خودِ ویندوز است، «اجرا در ورود به
# سیستم» و «ری‌استارت در خطا» را دارد، و برای رباتی که روی لپ‌تاپ کاربر
# اجرا می‌شود همان کار را می‌کند.

[CmdletBinding()]
param(
    [string]$TaskName = "GarnetTrader",
    # پیش‌فرض: همین مخزن
    [string]$ProjectPath = (Split-Path -Parent $PSScriptRoot),
    # اگر تیک بخورد، تسک حذف می‌شود
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "تسک $TaskName حذف شد." -ForegroundColor Yellow
    exit 0
}

$python = Join-Path $ProjectPath ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "پایتون محیط مجازی پیدا نشد: $python`nاول بخش «آماده‌سازی محیط» در README را اجرا کنید."
}

$mainScript = Join-Path $ProjectPath "main.py"
if (-not (Test-Path $mainScript)) {
    throw "main.py پیدا نشد: $mainScript"
}

# فارسی در stdout ویندوز بدون این با UnicodeEncodeError می‌افتد
# (cp1252 پیش‌فرض است).
$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "main.py" `
    -WorkingDirectory $ProjectPath

# در ورود به سیستم شروع شود، با یک دقیقه تأخیر تا شبکه بالا بیاید.
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT1M"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -RestartCount 3 `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)   # بدون محدودیت زمان: حلقه‌ی دائمی است

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "ربات سیگنال آپشن بورس تهران — فقط سیگنال، بدون ثبت سفارش" `
    -Force | Out-Null

Write-Host "تسک $TaskName ساخته شد." -ForegroundColor Green
Write-Host ""
Write-Host "شروع دستی :  Start-ScheduledTask -TaskName $TaskName"
Write-Host "توقف      :  Stop-ScheduledTask  -TaskName $TaskName"
Write-Host "وضعیت     :  Get-ScheduledTask   -TaskName $TaskName"
Write-Host "حذف       :  powershell -File deploy\install-windows-task.ps1 -Uninstall"
Write-Host ""
Write-Host "توکن تلگرام را به‌عنوان متغیر محیطیِ کاربر بگذارید (نه در فایل):" -ForegroundColor Yellow
Write-Host '  [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "...", "User")'

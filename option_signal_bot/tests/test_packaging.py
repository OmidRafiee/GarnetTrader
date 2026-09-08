"""تست متادیتای بسته‌بندی و فایل‌های اجرای دائمی.

**چرا این تست‌ها ارزش دارند**

خرابیِ بسته‌بندی روی محیط توسعه **دیده نمی‌شود**: همه‌چیز از پوشه‌ی پروژه
کار می‌کند و فقط روی نصبِ واقعی می‌شکند. اولین بار که ویل ساخته شد،
`main.py` و فایل‌های استاتیک داشبورد در آن نبودند — یعنی نقطه‌ی ورود
`garnet-trader` بعد از نصب با `ImportError` می‌افتاد و کسی تا لحظه‌ی نصب
نمی‌فهمید.

پس این تست‌ها همان چیزهایی را می‌گیرند که فقط روی نصب معلوم می‌شوند.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# ======================================================================
# متادیتای بسته
# ======================================================================
def test_project_metadata_is_present(pyproject):
    project = pyproject["project"]
    assert project["name"] == "garnet-trader"
    assert project["version"]
    assert project["requires-python"] == ">=3.11"


def test_core_has_no_mandatory_dependencies(pyproject):
    """هسته با کتابخانه‌ی استاندارد کار می‌کند و ۷۳۷ تست بدون
    numpy/pandas/scipy پاس می‌شوند. یک نصبِ بسته نباید pytest بیاورد."""
    assert pyproject["project"]["dependencies"] == []


def test_optional_extras_cover_the_real_needs(pyproject):
    extras = pyproject["project"]["optional-dependencies"]
    assert "web" in extras and "dev" in extras
    # داشبورد بدون fastapi بالا نمی‌آید
    assert any("fastapi" in dep for dep in extras["web"])
    # yaml برای خواندن settings لازم است
    assert any("PyYAML" in dep for dep in extras["config"])


def test_entry_points_target_real_functions(pyproject):
    """نقطه‌ی ورودی که تابعش وجود نداشته باشد، فقط روی نصب می‌شکند."""
    import importlib

    for target in pyproject["project"]["scripts"].values():
        module_name, _, function_name = target.partition(":")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function_name)), target


def test_root_modules_are_packaged(pyproject):
    """`main.py` پکیج نیست، ماژول است.

    بدون `py-modules`، نقطه‌ی ورود `garnet-trader` روی نصبِ واقعی با
    ImportError می‌افتد — و این خرابی روی محیط توسعه دیده نمی‌شود.
    """
    modules = pyproject["tool"]["setuptools"]["py-modules"]
    assert "main" in modules
    assert "bootstrap" in modules


def test_every_source_package_is_listed(pyproject):
    """پکیجِ جامانده یعنی ImportError روی نصب، نه روی توسعه."""
    listed = set(pyproject["tool"]["setuptools"]["packages"])
    on_disk = {
        path.parent.name
        for path in ROOT.glob("*/__init__.py")
        if not path.parent.name.startswith((".", "_"))
        and path.parent.name not in {"tests", "var", "deploy"}
    }
    assert on_disk <= listed, f"پکیج جامانده: {sorted(on_disk - listed)}"


def test_dashboard_static_files_are_included(pyproject):
    """بدون این، `garnet-dashboard` نصب می‌شد ولی صفحه‌ی خالی می‌داد."""
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert any("static" in pattern for pattern in package_data["web"])


def test_execution_layer_is_still_packaged(pyproject):
    """لایه‌ی اجرا بسته‌بندی می‌شود ولی کسی صدایش نمی‌زند.

    حذفش از بسته، قرارداد را **پنهان** می‌کرد؛ گارد AST تضمین می‌کند که
    هیچ ماژولی import نمی‌کندش.
    """
    assert "execution" in pyproject["tool"]["setuptools"]["packages"]


# ======================================================================
# فایل‌های اجرای دائمی
# ======================================================================
def test_deploy_artifacts_exist():
    for name in (
        "Dockerfile",
        "docker-compose.yml",
        "garnet-trader.service",
        "install-windows-task.ps1",
    ):
        assert (DEPLOY / name).exists(), name


def test_compose_is_valid_yaml():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(data["services"]) == {"bot", "dashboard"}


def test_dashboard_is_bound_to_localhost_only():
    """🔒 داشبورد احراز هویت ندارد و تنظیمات را می‌نویسد.

    باز کردنش روی شبکه یعنی هر کسی تنظیمات ریسک و توکن کارگزاری را
    می‌بیند. این تست همان اشتباه را می‌گیرد.
    """
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))

    for mapping in data["services"]["dashboard"]["ports"]:
        assert str(mapping).startswith("127.0.0.1:"), mapping


def test_containers_restart_but_do_not_loop_forever():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))
    for service in data["services"].values():
        assert service["restart"] == "unless-stopped"
        # لاگ محدود، وگرنه اجرای چندماهه دیسک را پر می‌کند
        assert service["logging"]["options"]["max-size"]


def test_secrets_come_from_the_environment_not_a_file():
    """توکن در فایل گیت‌شده نباید بنشیند."""
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))
    env = data["services"]["bot"]["environment"]
    joined = " ".join(env)
    assert "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN" in joined


def test_container_runs_as_a_non_root_user():
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "USER garnet" in text
    assert "useradd" in text


def test_container_keeps_run_output_outside_the_image():
    """بدون volume، هر بازسازی تاریخچه‌ی سیگنال را پاک می‌کرد."""
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "/app/var" in text


def test_dockerfile_entrypoint_places_no_order():
    """هیچ آرگومان پیش‌فرضی نباید مسیری به لایه‌ی اجرا باز کند."""
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["python", "main.py"]' in text
    assert "execution" not in text.replace("لایه‌ی `execution`", "")


def test_systemd_unit_waits_for_the_network():
    """منبع داده TSETMC است؛ بدون شبکه شروع شدن یعنی شکست تضمینی."""
    text = (DEPLOY / "garnet-trader.service").read_text(encoding="utf-8")
    assert "After=network-online.target" in text


def test_systemd_unit_limits_restart_loops():
    text = (DEPLOY / "garnet-trader.service").read_text(encoding="utf-8")
    assert "Restart=on-failure" in text
    # حلقه‌ی ری‌استارت سریع لاگ را پر می‌کند و مشکل واقعی را پنهان
    assert "StartLimitBurst" in text


def test_systemd_unit_does_not_embed_a_token():
    """فایل سرویس معمولاً قابل خواندن است."""
    text = (DEPLOY / "garnet-trader.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=" in text
    assert "TELEGRAM_BOT_TOKEN=" not in text


def test_powershell_script_has_a_utf8_bom():
    """Windows PowerShell 5.1 فایل بدون BOM را ANSI می‌خواند.

    نتیجه‌اش خطای گمراه‌کننده‌ی «string is missing the terminator» است که
    به رشته اشاره می‌کند، نه به انکودینگ. این اشتباه واقعاً رخ داد.
    """
    raw = (DEPLOY / "install-windows-task.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "فایل ps1 فارسی باید BOM داشته باشد"


def test_powershell_script_parses():
    """اگر PowerShell در دسترس باشد، نحوش را واقعاً بررسی کن."""
    import shutil
    import subprocess

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:  # pragma: no cover - محیط غیرویندوزی
        pytest.skip("PowerShell در دسترس نیست")

    script = DEPLOY / "install-windows-task.ps1"
    command = (
        "$e=$null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$e); "
        "if ($e) { exit 1 } else { exit 0 }"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")


def test_powershell_script_supports_uninstall():
    """نصبی که راه برگشت نداشته باشد، کسی امتحانش نمی‌کند."""
    text = (DEPLOY / "install-windows-task.ps1").read_text(encoding="utf-8-sig")
    assert "-Uninstall" in text
    assert "Unregister-ScheduledTask" in text


def test_powershell_script_tells_the_user_not_to_store_the_token():
    text = (DEPLOY / "install-windows-task.ps1").read_text(encoding="utf-8-sig")
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "SetEnvironmentVariable" in text

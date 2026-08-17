"""ریشه پروژه را به sys.path اضافه می‌کند تا import مطلق ماژول‌ها در تست کار کند.

`pyproject.toml` هم `pythonpath = ["."]` دارد، اما آن تنظیم فقط وقتی اعمال می‌شود
که pytest از داخل همین پوشه اجرا شود. این conftest تضمین می‌کند اجرای
`pytest option_signal_bot` از ریشه مخزن هم کار کند.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

"""چاپ سیگنال در ترمینال — کانال پیش‌فرض حالت `--dry-run`."""

from __future__ import annotations

import sys

from notifiers.base_notifier import BaseNotifier
from signals.signal_model import Signal

_WIDTH = 64


class ConsoleNotifier(BaseNotifier):
    """سیگنال را با قالب خوانا در stdout می‌نویسد. بدون هیچ وابستگی خارجی."""

    name = "console"

    def __init__(self, as_json: bool = False, stream=None) -> None:
        self.as_json = as_json
        self.stream = stream or sys.stdout

    def send(self, signal: Signal) -> bool:
        if self.as_json:
            self._write(signal.to_json(indent=2))
        else:
            self._write("─" * _WIDTH)
            self._write(self.format_signal(signal))
            self._write("─" * _WIDTH)
        return True

    def send_text(self, text: str) -> bool:
        self._write(text)
        return True

    def _write(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

"""Не давать компьютеру засыпать, пока работает агент, и замечать сон и пробуждение.

Запрещается только сон по бездействию: экран гаснет, закрытие крышки и ручной сон работают как обычно.
macOS - дочерний `caffeinate -i -w <pid>` (умирает вместе с приложением, виден в `pmset -g assertions`);
Windows - `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` из отдельного потока (`powercfg /requests`).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time

from koddi.logs import log_event

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class SleepGuard:
    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._release = threading.Event()
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            if self._process is not None:
                return self._process.poll() is None
            return self._thread is not None and self._thread.is_alive()

    def hold(self, value: bool) -> bool:
        """Включить или снять запрет; повторный вызов с тем же значением ничего не делает."""
        if value == self.active:
            return value
        if value:
            self._start()
        else:
            self._stop()
        return self.active

    def _start(self) -> None:
        with self._lock:
            if sys.platform == "darwin":
                try:
                    self._process = subprocess.Popen(
                        ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except OSError as exc:
                    self._process = None
                    log_event("WARNING", "remote.power.hold", type(exc).__name__, {})
            elif os.name == "nt":
                self._release.clear()
                self._thread = threading.Thread(target=self._windows, name="dokky-keep-awake", daemon=True)
                self._thread.start()

    def _stop(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            thread, self._thread = self._thread, None
            self._release.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        if thread is not None:
            thread.join(timeout=2)

    def _windows(self) -> None:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        try:
            # состояние живёт, пока жив поток: повторяем на случай, если система его сбросит
            while not self._release.wait(0):
                kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                if self._release.wait(30):
                    break
        finally:
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)


_power_cache: tuple[float, str] = (0.0, "unknown")


def power_source() -> str:
    """`ac`, `battery` или `unknown`; на macOS спрашивается не чаще раза в минуту."""
    global _power_cache
    if time.monotonic() - _power_cache[0] < 60:
        return _power_cache[1]
    value = "unknown"
    try:
        if sys.platform == "darwin":
            output = subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True, timeout=3).stdout
            if re.search(r"'AC Power'", output):
                value = "ac"
            elif re.search(r"'Battery Power'", output):
                value = "battery"
        elif os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_ubyte),
                    ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                ]

            status = Status()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):  # type: ignore[attr-defined]
                value = {1: "ac", 0: "battery"}.get(status.ACLineStatus, "unknown")
    except (OSError, subprocess.SubprocessError, AttributeError):
        value = "unknown"
    _power_cache = (time.monotonic(), value)
    return value


def watch_macos_sleep(on_sleep, on_wake) -> object | None:
    """Уведомления NSWorkspace о сне и пробуждении. Приходят в главный поток окна (Qt крутит его цикл)."""
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import NSWorkspace  # type: ignore[import-not-found]
        from Foundation import NSOperationQueue  # type: ignore[import-not-found]
    except ImportError:
        return None
    center = NSWorkspace.sharedWorkspace().notificationCenter()
    queue = NSOperationQueue.mainQueue()
    return [
        center.addObserverForName_object_queue_usingBlock_("NSWorkspaceWillSleepNotification", None, queue, lambda _note: on_sleep()),
        center.addObserverForName_object_queue_usingBlock_("NSWorkspaceDidWakeNotification", None, queue, lambda _note: on_wake()),
    ]


def unwatch_macos_sleep(tokens: object | None) -> None:
    if not tokens or sys.platform != "darwin":
        return
    try:
        from AppKit import NSWorkspace  # type: ignore[import-not-found]
    except ImportError:
        return
    center = NSWorkspace.sharedWorkspace().notificationCenter()
    for token in tokens:  # type: ignore[union-attr]
        center.removeObserver_(token)

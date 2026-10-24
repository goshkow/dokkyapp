from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from koddi.config import data_dir, is_packaged_build, launcher_path


def activation_server():
    name = "koddi-" + hashlib.sha256(str(data_dir().resolve()).lower().encode()).hexdigest()[:24]
    if "--after-elevation" in sys.argv:
        # перезапуск от администратора: прежняя копия ещё закрывается, ждём, пока она отпустит канал
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            waiting = QLocalSocket()
            waiting.connectToServer(name)
            if not waiting.waitForConnected(300):
                break
            waiting.disconnectFromServer()
            time.sleep(0.4)
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(500):
        probe.write(b"activate")
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        return None
    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        raise RuntimeError("Не удалось открыть канал активации Докки")
    return server


def register_protocol():
    if os.name != "nt" or not is_packaged_build():
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\koddi") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Dokky")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, r"Software\Classes\koddi\shell\open\command"
    ) as key:
        target = launcher_path() or Path(sys.executable)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{target}" "%1"')

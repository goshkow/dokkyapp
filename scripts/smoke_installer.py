"""Проверка установщика Windows: тихая установка, обновление поверх, удаление и лаунчер Dokky.exe.

python scripts/smoke_installer.py dist_release/Dokky-0.2.0-Windows-x64-Setup.exe

Режим --smoke: свой ключ реестра и ярлык «Докки (проверка)», настоящая установка не затрагивается.
С правами администратора (как в GitHub Actions) дополнительно проверяется установка в Program Files для всех.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import winreg
from pathlib import Path

KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DokkySmoke"
LINK = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Докки (проверка).lnk"
COMMON_LINK = Path(os.environ["PROGRAMDATA"]) / "Microsoft/Windows/Start Menu/Programs/Докки (проверка).lnk"


def run(setup: Path, *args: str, log: Path) -> None:
    result = subprocess.run([str(setup), "--silent", "--smoke", "--log", str(log), *args], timeout=900)
    if result.returncode != 0:
        details = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        raise SystemExit(f"{' '.join(args)}: код {result.returncode}\n{details}")


def registry(root: int = winreg.HKEY_CURRENT_USER) -> dict | None:
    try:
        with winreg.OpenKey(root, KEY) as key:
            names = ("InstallLocation", "DisplayVersion", "UninstallString", "DisplayIcon")
            return {name: winreg.QueryValueEx(key, name)[0] for name in names}
    except FileNotFoundError:
        return None


def check_layout(target: Path) -> str:
    current = json.loads((target / "current.json").read_text(encoding="utf-8"))
    version = current["version"]
    assert current == {"v": 1, "version": version, "dir": f"app-{version}", "previous": None}, current
    assert (target / "Dokky.exe").is_file(), "нет лаунчера Dokky.exe"
    assert (target / f"app-{version}" / "DokkyApp.exe").is_file(), "нет DokkyApp.exe в папке версии"
    assert (target / "updates").is_dir(), "нет папки updates"
    assert (target / "Uninstall.exe").is_file(), "нет программы удаления"
    assert not (target / "DokkyApp.exe").exists(), "сборка лежит в корне, а не в папке версии"
    others = [p.name for p in target.iterdir() if p.name.startswith("app-") and p.name != f"app-{version}"]
    assert not others, others
    # программа удаления - тот же exe без архива: маленькая
    assert (target / "Uninstall.exe").stat().st_size < 5 * 1024 * 1024
    return version


def check_launcher(launcher: Path, work: Path) -> None:
    """Лаунчер передаёт аргументы как есть и в переносном режиме задаёт KODDI_DATA_DIR рядом с собой."""
    root = work / "лаунчер с пробелом"
    (root / "app-1.2.3").mkdir(parents=True)
    shutil.copy2(launcher, root / "Dokky.exe")
    # вместо приложения - копия cmd.exe: она запишет полученные аргументы и окружение
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe", root / "app-1.2.3" / "DokkyApp.exe")
    current = {"v": 1, "version": "1.2.3", "dir": "app-1.2.3", "previous": None}
    (root / "current.json").write_text(json.dumps(current), encoding="utf-8")
    (root / "portable.dokky").write_text("x", encoding="utf-8")
    report = root / "app-1.2.3" / "report.txt"
    env = {k: v for k, v in os.environ.items() if k.upper() != "KODDI_DATA_DIR"}
    # /u: cmd пишет в файл UTF-16, иначе кириллица пути зависит от кодовой страницы системы (на раннерах cp437)
    command = f'"{root / "Dokky.exe"}" /d /u /c "echo koddi://open?chat=1 "a b"> report.txt & set KODDI_DATA_DIR>> report.txt"'
    subprocess.run(command, env=env, timeout=60, check=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if report.exists() and "KODDI_DATA_DIR" in report.read_text(encoding="utf-16-le", errors="replace"):
            break
        time.sleep(0.2)
    text = report.read_text(encoding="utf-16-le", errors="replace")
    assert 'koddi://open?chat=1 "a b"' in text, text
    assert f"KODDI_DATA_DIR={root / '.koddi-data'}" in text, text
    print("лаунчер: аргументы и папка данных переносной версии на месте")


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка установщика Докки")
    parser.add_argument("setup", type=Path)
    args = parser.parse_args()
    setup = args.setup.resolve()
    # resolve раскрывает короткие имена вроде RUNNER~1: в реестр установщик пишет полный путь
    work = Path(tempfile.gettempdir()).resolve() / f"dokky-installer-smoke-{uuid.uuid4().hex[:8]}"
    target = work / "Докки с пробелом"
    log = work / "setup.log"
    work.mkdir(parents=True)
    try:
        started = time.monotonic()
        run(setup, "--no-desktop", "--dir", str(target), log=log)
        print(f"установка: {time.monotonic() - started:.1f} с")
        version = check_layout(target)
        assert not (target / f"app-{version}.setup").exists(), "осталась промежуточная папка"
        values = registry()
        assert values and Path(values["InstallLocation"]) == target, values
        assert "--uninstall" in values["UninstallString"]
        assert values["DisplayIcon"].lower().startswith(str(target / "Dokky.exe").lower()), values
        assert LINK.is_file(), "нет ярлыка в меню «Пуск»"
        check_launcher(target / "Dokky.exe", work)

        # обновление поверх: папка версии заменяется целиком, старые версии убираются, чужое в корне остаётся
        (target / f"app-{version}" / "старый-файл.txt").write_text("x", encoding="utf-8")
        (target / "app-0.0.1").mkdir()
        (target / "заметка.txt").write_text("моё", encoding="utf-8")
        run(setup, "--no-desktop", "--dir", str(target), log=log)
        check_layout(target)
        assert not (target / f"app-{version}" / "старый-файл.txt").exists(), "обновление не заменило папку версии"
        assert not (target / f"app-{version}.old").exists(), "осталась прежняя версия"

        # удаление: только своё, чужой файл и корень с ним остаются
        run(setup, "--uninstall", "--dir", str(target), log=log)
        assert sorted(p.name for p in target.iterdir()) == ["заметка.txt"], list(target.iterdir())
        assert registry() is None, "запись в реестре осталась"
        assert not LINK.exists(), "ярлык остался"
        (target / "заметка.txt").unlink()
        run(setup, "--no-desktop", "--dir", str(target), log=log)
        run(setup, "--uninstall", "--dir", str(target), log=log)
        assert not target.exists(), "пустая папка программы не удалена"

        # в чужую непустую папку не ставим поверх: программа ложится во вложенную Dokky
        foreign = work / "чужая"
        foreign.mkdir()
        (foreign / "важное.txt").write_text("не трогать", encoding="utf-8")
        run(setup, "--no-desktop", "--dir", str(foreign), log=log)
        check_layout(foreign / "Dokky")
        run(setup, "--uninstall", "--dir", str(foreign / "Dokky"), log=log)
        assert (foreign / "важное.txt").read_text(encoding="utf-8") == "не трогать", "задеты чужие файлы"
        assert not (foreign / "Dokky").exists()
        print("установщик: установка, обновление, чужая папка и удаление прошли")
        if ctypes.windll.shell32.IsUserAnAdmin():
            machine_scope(setup, log)
        else:
            print("Program Files не проверял: нужны права администратора")
    finally:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            if registry(root) is not None:
                winreg.DeleteKey(root, KEY)
        LINK.unlink(missing_ok=True)
        COMMON_LINK.unlink(missing_ok=True)
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(work)], capture_output=True)


def machine_scope(setup: Path, log: Path) -> None:
    target = Path(os.environ["ProgramFiles"]) / f"DokkySmoke-{uuid.uuid4().hex[:8]}"
    try:
        run(setup, "--no-desktop", "--dir", str(target), log=log)
        check_layout(target)
        values = registry(winreg.HKEY_LOCAL_MACHINE)
        assert values and Path(values["InstallLocation"]) == target, values
        assert registry() is None, "установка для всех записалась в HKCU"
        assert COMMON_LINK.is_file(), "нет общего ярлыка в меню «Пуск»"
        run(setup, "--uninstall", "--dir", str(target), log=log)
        assert not target.exists(), "папка в Program Files не удалена"
        assert registry(winreg.HKEY_LOCAL_MACHINE) is None, "запись в HKLM осталась"
        assert not COMMON_LINK.exists(), "общий ярлык остался"
        print("установщик: Program Files для всех пользователей прошёл")
    finally:
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(target)], capture_output=True)


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Проверка установщика работает только в Windows")
    main()

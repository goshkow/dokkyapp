"""Платформенный слой macOS: хранилище сессии, оболочка агента, автозапуск, токен процесса."""

import json
import os
import re
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from koddi.tools import ToolRunner
from koddi.tools.runner import shell_argv

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX shell")


@pytest.fixture()
def keychain(monkeypatch, tmp_path):
    from koddi.dokky import keychain as module

    key_path = tmp_path / "keys" / module.KEY_FILE
    monkeypatch.setattr(module, "_key_path", lambda: key_path)
    module.forget_cached_key()
    yield module, key_path
    module.forget_cached_key()


def test_keychain_roundtrip_hides_plaintext(keychain):
    module, key_path = keychain
    raw = b'{"access_token":"token-should-not-be-plain"}'
    sealed = module.seal(raw)
    assert b"token-should-not-be-plain" not in sealed
    assert module.unseal(sealed) == raw
    key = key_path.read_bytes()
    assert len(key) == 32
    # права POSIX есть только на macOS, где модуль и работает; Windows хранит сессию через DPAPI
    if os.name != "nt":
        assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    # ключ создаётся один раз, повторная защита использует его же
    module.forget_cached_key()
    assert module.unseal(sealed) == raw
    assert key_path.read_bytes() == key


def test_keychain_rejects_tampered_or_foreign_data(keychain):
    module, _fake = keychain
    sealed = bytearray(module.seal(b"secret"))
    sealed[-1] ^= 1
    with pytest.raises(module.KeychainError):
        module.unseal(bytes(sealed))
    with pytest.raises(module.KeychainError):
        module.unseal(b"plain text session")


def test_keychain_does_not_invent_key_on_read(keychain):
    module, key_path = keychain
    sealed = module.seal(b"secret")
    key_path.unlink()
    module.forget_cached_key()
    # ключ потерян: расшифровать нельзя, новый ключ при чтении не создаётся
    with pytest.raises(module.KeychainError):
        module.unseal(sealed)
    assert not key_path.exists()


def test_unwritable_key_dir_is_a_clean_error(monkeypatch, tmp_path):
    from koddi.dokky import keychain as module

    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(module, "_key_path", lambda: blocker / module.KEY_FILE)
    module.forget_cached_key()
    with pytest.raises(module.KeychainError):
        module.seal(b"x")


def test_credential_store_uses_keychain_on_macos(tmp_path, monkeypatch, keychain):
    from koddi.dokky import client

    monkeypatch.setattr(client, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(client, "data_dir", lambda: tmp_path)
    store = client.CredentialStore()
    value = {"access_token": "a" * 20, "refresh_token": "r" * 20, "expires_at": time.time() + 600}
    store.save(value)
    assert b"aaaaaaaa" not in store.path.read_bytes()
    assert store.load() == value
    # повреждённый файл сбрасывает сессию, а не роняет приложение
    store.path.write_bytes(b"garbage")
    assert store.load() == {}
    assert not store.path.exists()


def test_unreadable_key_file_preserves_encrypted_session(tmp_path, monkeypatch, keychain):
    from koddi.dokky import client

    module, key_path = keychain
    monkeypatch.setattr(client, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(client, "data_dir", lambda: tmp_path)
    store = client.CredentialStore()
    value = {"access_token": "a" * 20, "refresh_token": "r" * 20, "expires_at": time.time() + 600}
    store.save(value)
    sealed = store.path.read_bytes()
    module.forget_cached_key()
    read = module._read_key

    def denied(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(module, "_read_key", denied)
    assert store.load() == {}
    assert store.path.read_bytes() == sealed
    monkeypatch.setattr(module, "_read_key", read)
    assert store.load() == value


def test_lost_key_resets_session_without_keychain_prompt(tmp_path, monkeypatch, keychain):
    from koddi.dokky import client

    module, key_path = keychain
    monkeypatch.setattr(client, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(client, "data_dir", lambda: tmp_path)
    store = client.CredentialStore()
    store.save({"access_token": "a" * 20, "refresh_token": "r" * 20, "expires_at": time.time() + 600})
    key_path.unlink()
    module.forget_cached_key()
    assert store.load() == {}
    assert not store.path.exists()


@posix_only
def test_shell_argv_uses_login_shell(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    argv = shell_argv("echo hi")
    assert argv[:3] == ["/bin/zsh", "-l", "-c"] and argv[3].endswith("echo hi") and ".zshrc" in argv[3]
    monkeypatch.setenv("SHELL", "/usr/bin/evil-shell")
    argv = shell_argv("echo hi")
    assert argv[0] in {"/bin/zsh", "/bin/sh"} and argv[1:3] == ["-l", "-c"] and argv[3].endswith("echo hi")


@posix_only
def test_posix_shell_output_is_bounded_and_utf8(tmp_path):
    result = ToolRunner(tmp_path).shell_run({
        "command": (
            "printf 'OUT-HEAD|'; head -c 200000 /dev/zero | tr '\\0' x; printf '|OUT-TAIL'; "
            "printf 'Привет!' >&2"
        ),
        "timeout_seconds": 20,
    })
    assert result["ok"], result
    assert len(result["stdout"]) <= 20000
    assert result["stdout"].startswith("OUT-HEAD|") and result["stdout"].endswith("|OUT-TAIL")
    assert re.search(r"пропущено \d+ символов", result["stdout"])
    assert result["stderr"] == "Привет!"


@posix_only
def test_posix_shell_cancel_stops_whole_process_group(tmp_path):
    marker = tmp_path / "child-alive"
    cancel = threading.Event()
    runner = ToolRunner(tmp_path, cancel_event=cancel)
    timer = threading.Timer(1, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        # дочерний процесс должен погибнуть вместе с оболочкой
        result = runner.shell_run({
            "command": f"(sleep 3; touch {marker}) & sleep 30",
            "timeout_seconds": 40,
        })
    finally:
        timer.cancel()
    assert result.get("cancelled"), result
    assert time.monotonic() - started < 10
    time.sleep(3.5)
    assert not marker.exists()


@posix_only
def test_posix_shell_timeout(tmp_path):
    result = ToolRunner(tmp_path).shell_run({"command": "sleep 30", "timeout_seconds": 1})
    assert result.get("timed_out"), result


@posix_only
def test_posix_process_list_and_kill(tmp_path):
    import subprocess

    runner = ToolRunner(tmp_path)
    child = subprocess.Popen(["sleep", "30"])
    try:
        listing = runner.process_list()
        assert listing["ok"]
        processes = json.loads(listing["processes_json"])
        assert any(item["Id"] == child.pid and item["ProcessName"] == "sleep" for item in processes)
        assert runner.process_kill({"pid": child.pid})["ok"]
        assert child.wait(timeout=5) != 0
        assert not runner.process_kill({"pid": child.pid})["ok"]
    finally:
        child.kill()
        child.wait()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ps")
def test_process_token_identifies_live_owner():
    from koddi.agents.recovery import current_owner, owner_alive, process_token

    pid, token = current_owner()
    assert token and token.startswith("ps:")
    assert owner_alive(pid, token)
    assert not owner_alive(pid, "ps:Thu Jan  1 00:00:00 1970")
    assert process_token(2**31 - 2) is None


def test_macos_autostart_launch_agent(tmp_path, monkeypatch):
    from koddi.desktop import autostart

    app = tmp_path / "DokkyApp.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    monkeypatch.setattr(autostart, "sys", SimpleNamespace(
        platform="darwin", executable=str(app / "Contents" / "MacOS" / "DokkyApp"), argv=[],
    ))
    monkeypatch.setattr(autostart.Path, "home", classmethod(lambda cls: tmp_path))
    assert autostart.available() and not autostart.enabled()
    assert autostart.set_enabled(True) is True
    assert autostart.enabled()
    agent = tmp_path / "Library" / "LaunchAgents" / "com.goshkow.dokky.plist"
    assert agent.is_file()
    assert autostart.set_enabled(False) is False
    assert not agent.exists() and not autostart.enabled()


def test_macos_autostart_unavailable_from_sources(monkeypatch):
    from koddi.desktop import autostart

    monkeypatch.setattr(autostart, "sys", SimpleNamespace(
        platform="darwin", executable="/opt/python/bin/python3", argv=["web_host.py"],
    ))
    assert not autostart.available()
    with pytest.raises(ValueError):
        autostart.set_enabled(True)


def test_macos_data_dir(tmp_path, monkeypatch):
    from koddi import config

    monkeypatch.delenv("KODDI_DATA_DIR", raising=False)
    monkeypatch.delenv("KODDI_PORTABLE", raising=False)
    monkeypatch.setattr(config, "sys", SimpleNamespace(platform="darwin", executable=sys.executable, frozen=False))
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    assert config.data_dir() == tmp_path / "Library" / "Application Support" / "Koddi"


def test_menu_commands_are_handled_by_the_page():
    """Каждая команда строки меню macOS должна быть известна интерфейсу, иначе пункт молча ничего не делает."""
    import re
    from pathlib import Path

    root = Path(__file__).parents[1]
    menu = (root / "backend/koddi/desktop/macos_menu.py").read_text(encoding="utf-8")
    handler = (root / "frontend/src/state/menu.ts").read_text(encoding="utf-8")
    commands = set(re.findall(r'command\("([a-z-]+)"\)', menu)) | set(
        re.findall(r'\("[^"]+", "(page-[a-z]+)"\)', menu)
    )
    assert len(commands) > 15
    for name in commands:
        assert f"'{name}'" in handler or f"{name}'" in handler, name


def test_menu_script_escapes_action():
    from koddi.desktop.macos_menu import dispatch_script

    assert dispatch_script('a"b') == 'window.koddiMenu?.("a\\"b")'


def test_glass_needs_macos_26(monkeypatch):
    from koddi.desktop import macos_glass

    monkeypatch.setattr(macos_glass.platform, "mac_ver", lambda: ("15.4", ("", "", ""), ""))
    assert macos_glass.supported() is False
    monkeypatch.setattr(macos_glass.platform, "mac_ver", lambda: ("", ("", "", ""), ""))
    assert macos_glass.supported() is False


def test_glass_color_comes_from_the_page_and_is_sanitized():
    from koddi.desktop.macos_glass import clean_color

    assert clean_color([0.1, 0.2, 0.3]) == (0.1, 0.2, 0.3)
    for bad in (None, "x", [1, 2], [2, 0, 0], [-1, 0, 0], ["a", 0, 0]):
        assert clean_color(bad) is None


def test_glass_regions_come_from_the_page_and_are_sanitized():
    from koddi.desktop.macos_glass import MAX_REGIONS, clean_regions

    good = {"id": "sidebar", "x": 8, "y": 8, "w": 272.5, "h": 831, "r": 24}
    assert clean_regions([good]) == [{"id": "sidebar", "x": 8.0, "y": 8.0, "w": 272.5, "h": 831.0, "r": 24.0}]
    bad = [
        {"id": "a", "x": 0, "y": 0, "w": 0, "h": 10},
        {"id": "b", "x": "x", "y": 0, "w": 10, "h": 10},
        {"id": "c", "x": 1e9, "y": 0, "w": 10, "h": 10},
        {"x": 0, "y": 0, "w": 10, "h": 10},
        "строка",
        {"id": "r", "x": 0, "y": 0, "w": 10, "h": 10, "r": 9999},
    ]
    result = clean_regions(bad)
    assert [item["id"] for item in result] == ["r"] and result[0]["r"] == 200.0
    assert clean_regions("не список") == []
    assert len(clean_regions([good] * 100)) == MAX_REGIONS


def test_glass_flight_catches_up_with_delivery_and_finishes_without_delay():
    from koddi.desktop.macos_glass import advance_flight, clean_regions

    item = {"id": "sidebar", "x": 0, "y": 8, "w": 200, "h": 600,
            "to": {"x": 200, "y": 8, "w": 200, "h": 600},
            "ms": 400, "c": [0, 0, 1, 1], "at": 1000}
    item = clean_regions([item])[0]
    start, ms, curve = advance_flight(item, 1100)
    assert start["x"] == pytest.approx(50, abs=0.001)
    assert ms == 300
    assert curve[0] == pytest.approx(curve[1])
    assert curve[2] == pytest.approx(curve[3])
    assert advance_flight(item, 1600)[:2] == (item["to"], 0)
    assert advance_flight(item, 900)[1] == 400
    for at in [float("nan"), float("inf"), -1]:
        assert clean_regions([{**item, "at": at}]) == []


def test_glass_look_settings_are_cleaned_saved_and_reset(tmp_path, monkeypatch):
    from koddi.desktop import macos_glass

    monkeypatch.setattr(macos_glass, "settings_path", lambda: tmp_path / "glass-look.json")
    cleaned = macos_glass._clean_settings({
        "radius": 500, "dark": {"style": 1, "tint": [2, -1, "x", 0.5], "base": 9}, "light": "мусор", "extra": 1,
    })
    assert cleaned["radius"] == 64.0
    assert cleaned["dark"]["style"] == 1 and cleaned["dark"]["tint"][:2] == [1.0, 0.0] and cleaned["dark"]["tint"][3] == 0.5
    assert cleaned["dark"]["tint"][2] == macos_glass.DEFAULTS["dark"]["tint"][2] and cleaned["dark"]["base"] == 1.5
    assert cleaned["light"] == macos_glass.DEFAULTS["light"] and "extra" not in cleaned
    assert macos_glass._clean_settings("не словарь") == macos_glass.DEFAULTS
    macos_glass.SETTINGS["dark"]["base"] = 0.55
    macos_glass.save_settings()
    macos_glass.SETTINGS["dark"]["base"] = 1.0
    macos_glass.load_settings()
    assert macos_glass.SETTINGS["dark"]["base"] == 0.55
    macos_glass.reset_settings()
    assert macos_glass.SETTINGS == macos_glass.DEFAULTS and not (tmp_path / "glass-look.json").exists()
    style, tint = macos_glass.look("dark")
    assert style == 0 and len(tint) == 4


def test_region_model_follows_window_resize_between_page_samples():
    """Хост предсказывает рамку блока по двум замерам страницы: низ держится за низ, ширина растёт с окном."""
    from koddi.desktop import macos_glass

    regions = macos_glass.RegionGlass(lambda: None, lambda _c: None, lambda: "dark")
    sample = lambda x, y, w, h: {"x": x, "y": y, "w": w, "h": h}  # noqa: E731
    # поле ввода: у нижнего края, ширина следует за колонкой
    regions._learn("composer", sample(100, 700, 600, 54), (1000.0, 800.0), False)
    # до замеров по двум размерам: низ держится за низ (нижняя часть страницы)
    assert regions._predicted("composer", 1000.0, 900.0)[1] == 800.0
    regions._learn("composer", sample(150, 700, 700, 54), (1100.0, 800.0), False)
    regions._learn("composer", sample(150, 800, 700, 54), (1100.0, 900.0), False)
    x, y, w, h = regions._predicted("composer", 1200.0, 1000.0)
    assert (round(x), round(y), round(w), round(h)) == (200, 900, 800, 54)


def test_region_follows_size_page_shows_not_current_window(monkeypatch):
    """Во время ресайза стекло берёт размер окна из прошлого: страница отстаёт на REGION_DELAY."""
    import time

    from koddi.desktop import macos_glass

    monkeypatch.setattr(macos_glass, "REGION_DELAY", 0.04)
    regions = macos_glass.RegionGlass(lambda: None, lambda _c: None, lambda: "dark")
    now = time.monotonic()
    regions.history = [(now - 0.10, 1000.0, 800.0), (now - 0.04, 900.0, 800.0), (now, 800.0, 800.0)]
    assert abs(regions._shown_size(800.0, 800.0)[0] - 900.0) < 1.0
    regions.history = [(now - 0.12, 1000.0, 800.0), (now - 0.08, 900.0, 800.0), (now - 0.06, 800.0, 800.0)]
    # окно давно не менялось: размер догнал окно
    assert regions._shown_size(800.0, 800.0) == (800.0, 800.0)
    regions.history = []
    assert regions._shown_size(700.0, 500.0) == (700.0, 500.0)


def test_region_model_clamps_wild_samples():
    from koddi.desktop import macos_glass

    regions = macos_glass.RegionGlass(lambda: None, lambda _c: None, lambda: "dark")
    box = lambda x, w: {"x": x, "y": 10, "w": w, "h": 40}  # noqa: E731
    regions._learn("a", box(0, 100), (1000.0, 800.0), False)
    regions._learn("a", box(5000, 100), (1001.0, 800.0), False)
    assert all(-1.0 <= value <= 1.0 for value in regions.models["a"]["jw"])


def test_page_size_validation():
    from koddi.desktop import web_host

    assert web_host._page_size({"vw": 1200, "vh": 800}) == (1200.0, 800.0)
    assert web_host._page_size({"vw": 0, "vh": 800}) is None
    assert web_host._page_size({"vw": "x"}) is None
    assert web_host._page_size({}) is None

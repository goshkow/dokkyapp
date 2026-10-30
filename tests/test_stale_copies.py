from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

import koddi.desktop.autostart as autostart
import koddi.desktop.stale_copies as copies


def bundle(root: Path, folder: str, version: str | None) -> Path:
    app = root / folder / "Докки.app"
    (app / "Contents").mkdir(parents=True)
    if version is not None:
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))
    return app


@pytest.fixture
def mac(tmp_path, monkeypatch):
    monkeypatch.setattr(copies, "__version__", "0.2.4")
    monkeypatch.setattr(copies, "LSREGISTER", tmp_path / "lsregister")
    (tmp_path / "lsregister").write_text("")
    monkeypatch.setattr(autostart, "_launch_agent", lambda: tmp_path / "agent.plist")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(copies, "_lsregister", lambda *args: calls.append(args) or True)
    return calls


def test_older_and_unversioned_copies_are_stale_newer_are_kept(tmp_path):
    own = tmp_path / "Applications" / "Докки.app"
    other = tmp_path / "dist" / "Докки.app"
    assert copies.is_stale(other, "0.2.0", own, "0.2.3")
    assert copies.is_stale(other, None, own, "0.2.3")
    assert not copies.is_stale(other, "0.3.0", own, "0.2.3")
    assert not copies.is_stale(own, "0.2.0", own, "0.2.3")
    # та же версия: копия сборки мешает, установленная в «Программах» - нет
    assert copies.is_stale(other, "0.2.3", own, "0.2.3")
    assert not copies.is_stale(Path("/Applications/Докки.app"), "0.2.3", other, "0.2.3")


def test_clean_up_unregisters_stale_copies_and_registers_itself(tmp_path, monkeypatch, mac):
    own = bundle(tmp_path, "Applications", "0.2.4")
    old = bundle(tmp_path, "dist_release_mac", "0.2.0")
    newer = bundle(tmp_path, "dist_next", "0.3.0")
    monkeypatch.setattr(copies, "own_bundle", lambda: own)
    monkeypatch.setattr(copies, "registered_copies", lambda: [own, old, newer])
    assert copies.clean_up() == [old]
    assert mac == [("-u", str(old)), ("-f", str(own))]


def test_copy_from_disk_image_changes_nothing(monkeypatch, mac):
    monkeypatch.setattr(copies, "own_bundle", lambda: Path("/Volumes/Докки/Докки.app"))
    monkeypatch.setattr(copies, "registered_copies", lambda: pytest.fail("образ не должен чистить регистрацию"))
    assert copies.clean_up() == []
    assert mac == []


def test_autostart_pointing_to_old_or_missing_copy_moves_to_this_one(tmp_path, monkeypatch, mac):
    own = bundle(tmp_path, "Applications", "0.2.4")
    old = bundle(tmp_path, "dist_release_mac", "0.2.0")
    newer = bundle(tmp_path, "dist_next", "0.3.0")
    monkeypatch.setattr(copies, "own_bundle", lambda: own)
    monkeypatch.setattr(copies, "registered_copies", lambda: [own])
    agent = tmp_path / "agent.plist"

    def point(target: Path) -> None:
        agent.write_bytes(plistlib.dumps({"Label": "com.goshkow.dokky", "ProgramArguments": ["/usr/bin/open", "-a", str(target)], "RunAtLoad": True}))

    for target in (old, tmp_path / "gone" / "Докки.app"):
        point(target)
        copies.clean_up()
        assert plistlib.loads(agent.read_bytes())["ProgramArguments"][-1] == str(own)
    # более новую копию, которую выбрал пользователь, не трогаем
    point(newer)
    copies.clean_up()
    assert plistlib.loads(agent.read_bytes())["ProgramArguments"][-1] == str(newer)


def test_no_autostart_file_is_not_created(tmp_path, monkeypatch, mac):
    own = bundle(tmp_path, "Applications", "0.2.4")
    monkeypatch.setattr(copies, "own_bundle", lambda: own)
    monkeypatch.setattr(copies, "registered_copies", lambda: [own])
    copies.clean_up()
    assert not (tmp_path / "agent.plist").exists()


def test_source_runs_skip_the_check(monkeypatch):
    monkeypatch.setattr(copies, "is_packaged_build", lambda: False)
    monkeypatch.setattr(copies.threading, "Thread", lambda **kwargs: pytest.fail("из исходников проверки нет"))
    copies.clean_up_later()

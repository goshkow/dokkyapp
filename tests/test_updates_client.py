import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from koddi.updates import service
from koddi.updates.service import UpdateError, Updater, is_newer, parse_version

BODY = b"dokky-release" * 1000
DIGEST = hashlib.sha256(BODY).hexdigest()


def latest(version="9.9.9", size=len(BODY), digest=DIGEST, url="/api/v1/updates/download/stable/macos-arm64/dmg"):
    return {"stable": {"macos-arm64": {"dmg": {
        "version": version, "name": f"Dokky-{version}-macOS-arm64.dmg", "size": size, "sha256": digest, "url": url,
    }}}}


@pytest.fixture
def updater(tmp_path, monkeypatch):
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "data"))
    from koddi.storage import init_db

    init_db()
    monkeypatch.setattr(service, "platform_id", lambda: "macos-arm64")
    monkeypatch.setattr(service, "install_kind", lambda: ("dmg", "bundle"))
    value = Updater()
    yield value
    value.transport = None


def serve(updater, handler):
    updater.transport = httpx.MockTransport(handler)


def test_versions_compare_by_numbers_and_beta_is_lower():
    assert parse_version("0.10.0") > parse_version("0.9.9")
    assert is_newer("0.3.0", "0.3.0-beta.2")
    assert not is_newer("0.3.0-beta.2", "0.3.0")
    assert not is_newer("0.2.1", "0.2.1")
    assert parse_version("1.2") is None and parse_version("v1.2.3") is None


def test_check_finds_newer_version_without_auto_download(updater):
    updater._save(auto_download=False)
    serve(updater, lambda request: httpx.Response(200, json=latest()))
    state = asyncio.run(updater.check(manual=True))
    assert state["status"] == "available"
    assert state["available"]["version"] == "9.9.9"
    assert updater._download is None


def test_same_version_is_up_to_date(updater):
    from koddi import __version__

    serve(updater, lambda request: httpx.Response(200, json=latest(version=__version__)))
    assert asyncio.run(updater.check(manual=True))["status"] == "up_to_date"


@pytest.mark.parametrize("entry", [
    latest(url="https://evil.example/file.dmg"),
    latest(url="/api/v1/updates/download/../../etc/passwd"),
    latest(digest="0" * 63),
    latest(size=0),
    latest(version="9.9"),
])
def test_bad_metadata_is_rejected(updater, entry):
    serve(updater, lambda request: httpx.Response(200, json=entry))
    with pytest.raises(UpdateError):
        asyncio.run(updater.check(manual=True))


def test_download_checks_hash_and_resumes(updater):
    calls = []

    def handler(request):
        calls.append(request.headers.get("range"))
        start = int(request.headers["range"].split("=")[1].rstrip("-")) if "range" in request.headers else 0
        return httpx.Response(206 if start else 200, content=BODY[start:])

    serve(updater, handler)
    entry = latest()["stable"]["macos-arm64"]["dmg"]
    partial = service.updates_dir() / (entry["name"] + ".part")
    partial.write_bytes(BODY[:500])
    path = asyncio.run(updater.download(entry))
    assert path.read_bytes() == BODY
    assert calls == ["bytes=500-"]
    assert not partial.exists()


def test_download_with_wrong_hash_is_deleted(updater):
    serve(updater, lambda request: httpx.Response(200, content=b"x" * len(BODY)))
    entry = latest()["stable"]["macos-arm64"]["dmg"]
    with pytest.raises(UpdateError) as failure:
        asyncio.run(updater.download(entry))
    assert failure.value.code == "hash_mismatch"
    assert not list(service.updates_dir().glob("Dokky-*"))


def test_download_refuses_foreign_redirect(updater):
    serve(updater, lambda request: httpx.Response(302, headers={"location": "https://evil.example/x.dmg"}))
    with pytest.raises(UpdateError) as failure:
        asyncio.run(updater.download(latest()["stable"]["macos-arm64"]["dmg"]))
    assert failure.value.code == "bad_redirect"


def test_download_refuses_bigger_file(updater):
    serve(updater, lambda request: httpx.Response(200, content=BODY + b"extra"))
    with pytest.raises(UpdateError) as failure:
        asyncio.run(updater.download(latest()["stable"]["macos-arm64"]["dmg"]))
    assert failure.value.code == "size_mismatch"


def test_failed_version_is_not_offered_again_automatically(updater):
    (service.updates_dir() / "failed.json").write_text(json.dumps({"version": "9.9.9"}))
    serve(updater, lambda request: httpx.Response(200, json=latest()))
    assert asyncio.run(updater.check())["status"] == "up_to_date"


def test_install_needs_prepared_update(updater):
    with pytest.raises(UpdateError):
        updater.install()


def _run_helper(tmp_path, monkeypatch, *, ready: bool):
    import subprocess

    bundle = tmp_path / "Applications" / "Докки.app"
    (bundle / "Contents").mkdir(parents=True)
    (bundle / "Contents" / "old").write_text("0.2.1")
    stage = tmp_path / "Applications" / ".Dokky-update-9.9.9.app"
    (stage / "Contents").mkdir(parents=True)
    (stage / "Contents" / "new").write_text("9.9.9")
    monkeypatch.setattr(service, "macos_bundle", lambda: bundle)
    opener = tmp_path / "open.sh"
    # «запуск» новой версии пишет отметку готовности, как это делает настоящее приложение при старте
    ready_line = f'printf \'{{"version": "9.9.9", "pid": 1}}\' > "{service._ready_file()}"' if ready else "true"
    opener.write_text(f"#!/bin/sh\n{ready_line}\n")
    opener.chmod(0o755)
    gone = subprocess.Popen(["true"])
    gone.wait()
    script = service._macos_script(stage, "9.9.9", tmp_path / "log", pid=gone.pid, opener=str(opener), limit=3)
    helper = tmp_path / "update.sh"
    helper.write_text(script)
    subprocess.run(["/bin/sh", str(helper)], check=False, timeout=30)
    return bundle, stage


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="помощник macOS")
def test_macos_helper_swaps_bundle_when_new_version_starts(updater, monkeypatch, tmp_path):
    bundle, stage = _run_helper(tmp_path, monkeypatch, ready=True)
    assert (bundle / "Contents" / "new").exists() and not stage.exists()
    assert not list(bundle.parent.glob(".Dokky-previous-*"))


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="помощник macOS")
def test_macos_helper_rolls_back_when_new_version_does_not_start(updater, monkeypatch, tmp_path):
    bundle, _stage = _run_helper(tmp_path, monkeypatch, ready=False)
    assert (bundle / "Contents" / "old").exists()
    assert json.loads((service.updates_dir() / "failed.json").read_text())["version"] == "9.9.9"


def test_windows_script_rejects_unsafe_paths():
    with pytest.raises(UpdateError):
        service._cmd_quote('C:\\Users\\a"b')
    assert service._cmd_quote("C:\\Users\\Иван\\Dokky") == '"C:\\Users\\Иван\\Dokky"'


def test_cleanup_keeps_newer_downloads(updater, monkeypatch):
    monkeypatch.setattr(service, "is_packaged_build", lambda: True)
    folder = service.updates_dir()
    old = folder / "Dokky-0.0.1-macOS-arm64.dmg"
    new = folder / "Dokky-99.0.0-macOS-arm64.dmg"
    old.write_bytes(b"1")
    new.write_bytes(b"2")
    monkeypatch.setattr(service, "macos_bundle", lambda: None)
    monkeypatch.setattr(service, "_windows_root", lambda: None)
    updater.cleanup()
    assert not old.exists() and new.exists()


def test_hidden_flags_only_on_windows(monkeypatch):
    from koddi import hidden_process

    monkeypatch.setattr(hidden_process.os, "name", "nt")
    assert hidden_process.hidden_flags(0) & hidden_process.CREATE_NO_WINDOW
    assert hidden_process.hidden_flags(hidden_process.CREATE_NEW_CONSOLE) == hidden_process.CREATE_NEW_CONSOLE
    monkeypatch.setattr(hidden_process.os, "name", "posix")
    assert hidden_process.hidden_flags(0) == 0


def test_status_route_reports_disabled_in_dev(client=None):
    from fastapi.testclient import TestClient

    from koddi.api.app import create_app

    with TestClient(create_app()) as api:
        state = api.get("/api/updates/status").json()
    assert state["status"] == "disabled" and state["reason"] == "dev"
    assert Path(state.get("current", "0")).name


def test_name_must_start_with_version_and_match_kind(updater):
    # ручная проверка чаще раза в минуту не ходит на сервер, поэтому здесь фоновые
    for name in ("Dokky-10.2.1-macOS-arm64.dmg", "Dokky-9.9.9-macOS-arm64.zip"):
        payload = latest()
        payload["stable"]["macos-arm64"]["dmg"]["name"] = name
        version = "0.2.1" if name.startswith("Dokky-10") else "9.9.9"
        payload["stable"]["macos-arm64"]["dmg"]["version"] = version
        serve(updater, lambda request, payload=payload: httpx.Response(200, json=payload))
        with pytest.raises(UpdateError):
            asyncio.run(updater.check())


def test_failed_check_keeps_ready_update(updater):
    updater.state.update(status="ready", available=latest()["stable"]["macos-arm64"]["dmg"])
    serve(updater, lambda request: httpx.Response(503))
    with pytest.raises(UpdateError):
        asyncio.run(updater.check())
    assert updater.state["status"] == "ready"


def test_cancel_does_nothing_outside_download(updater):
    updater.state.update(status="ready", available=latest()["stable"]["macos-arm64"]["dmg"])
    asyncio.run(updater.cancel_download())
    assert updater.state["status"] == "ready"


def test_full_partial_file_is_not_downloaded_again(updater):
    calls = []
    serve(updater, lambda request: calls.append(request) or httpx.Response(416))
    entry = latest()["stable"]["macos-arm64"]["dmg"]
    (service.updates_dir() / (entry["name"] + ".part")).write_bytes(BODY)
    assert asyncio.run(updater.download(entry)).read_bytes() == BODY
    assert calls == []


def test_install_rechecks_installer_before_launch(updater, monkeypatch, tmp_path):
    setup = tmp_path / "Dokky-9.9.9-Windows-x64-Setup.exe"
    setup.write_bytes(b"other")
    entry = latest()["stable"]["macos-arm64"]["dmg"]
    updater.state.update(status="ready", available=entry)
    updater.prepared = {"version": "9.9.9", "method": "setup", "file": str(setup)}
    spawned = []
    monkeypatch.setattr(service, "_spawn_windows", spawned.append)
    with pytest.raises(UpdateError) as failure:
        updater.install()
    assert failure.value.code == "hash_mismatch"
    assert spawned == [] and updater.state["status"] == "available" and updater.prepared is None
    assert not service._quit_signal().exists()


def test_cleanup_keeps_previous_bundle_for_rollback(updater, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "is_packaged_build", lambda: True)
    bundle = tmp_path / "Applications" / "Докки.app"
    bundle.mkdir(parents=True)
    previous = bundle.parent / ".Dokky-previous-abcd.app"
    previous.mkdir()
    monkeypatch.setattr(service, "macos_bundle", lambda: bundle)
    monkeypatch.setattr(service, "_windows_root", lambda: None)
    updater.cleanup()
    assert previous.exists()


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="помощник macOS")
def test_macos_helper_keeps_new_version_when_backup_is_gone(updater, monkeypatch, tmp_path):
    import subprocess

    bundle = tmp_path / "Applications" / "Докки.app"
    (bundle / "Contents").mkdir(parents=True)
    stage = tmp_path / "Applications" / ".Dokky-update-9.9.9.app"
    (stage / "Contents").mkdir(parents=True)
    (stage / "Contents" / "new").write_text("9.9.9")
    monkeypatch.setattr(service, "macos_bundle", lambda: bundle)
    opener = tmp_path / "open.sh"
    # «запуск» удаляет резервную копию, как это делала старая уборка при старте
    opener.write_text(f'#!/bin/sh\nrm -rf "{bundle.parent}"/.Dokky-previous-*\n')
    opener.chmod(0o755)
    gone = subprocess.Popen(["true"])
    gone.wait()
    helper = tmp_path / "update.sh"
    helper.write_text(service._macos_script(stage, "9.9.9", tmp_path / "log", pid=gone.pid, opener=str(opener), limit=2))
    subprocess.run(["/bin/sh", str(helper)], check=False, timeout=30)
    assert (bundle / "Contents" / "new").exists()


def test_start_checks_for_updates_without_long_delay(updater, monkeypatch):
    seen = []
    updater._save(auto_download=False)
    serve(updater, lambda request: seen.append(request.url.path) or httpx.Response(200, json=latest()))
    monkeypatch.setattr(service, "FIRST_CHECK", (0, 1))
    monkeypatch.setattr(service.secrets, "randbelow", lambda _: 0)
    monkeypatch.setattr(updater, "mark_ready", lambda: None)
    monkeypatch.setattr(updater, "cleanup", lambda: None)

    async def run():
        await updater.start()
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.01)
        await updater.stop()
    asyncio.run(run())
    assert seen == [service.LATEST_PATH]
    assert updater.state["status"] == "available"


def test_check_does_not_interrupt_preparation(updater):
    updater.state.update(status="preparing", available=latest()["stable"]["macos-arm64"]["dmg"])
    assert asyncio.run(updater.check(manual=True))["status"] == "preparing"


def test_update_waits_for_user_by_default(updater):
    serve(updater, lambda request: httpx.Response(200, json=latest()))
    state = asyncio.run(updater.check())
    assert state["status"] == "available" and state["auto_download"] is False
    assert updater._download is None


def test_explicit_auto_download_is_preserved(updater, monkeypatch):
    updater._save(auto_download=True)
    started = []
    monkeypatch.setattr(updater, "start_download", lambda: started.append(True))
    serve(updater, lambda request: httpx.Response(200, json=latest()))
    state = asyncio.run(updater.check())
    assert state["auto_download"] is True and started == [True]

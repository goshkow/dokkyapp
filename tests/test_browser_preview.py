"""Встроенный браузер на настоящем Chromium: свой localhost, файлы проекта, закрытые границы."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from koddi.browser import service
from koddi.policy.network import NetworkAccessError

PAGE = """<!doctype html><html><head><title>Демо</title></head><body>
<h1>Заказ</h1>
<input id="name" placeholder="Имя" aria-label="Имя">
<select id="size"><option value="s">S</option><option value="m">M</option></select>
<button id="go" onclick="document.getElementById('out').textContent='Привет, ' + document.getElementById('name').value; console.log('clicked'); console.error('сломалось')">Отправить</button>
<p id="out"></p>
<a href="/next">Дальше</a>
<div style="height:3000px"></div>
<script>fetch('/missing.json').catch(() => {}); window.addEventListener('load', () => { throw new Error('boom') })</script>
</body></html>""".encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://blocked.test/secret")
            self.end_headers()
            return
        if self.path in {"/", "/next"}:
            body = PAGE if self.path == "/" else "<title>Next</title><h1>Вторая</h1>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module", autouse=True)
def real_browser():
    from koddi.storage import init_db

    init_db()
    try:
        service._BROWSER_THREAD.submit(service._ensure_browser).result()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Chromium недоступен: {exc}")
    yield
    service.browser_close_all()


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    return root


def ref_of(snapshot: str, needle: str) -> str:
    for line in snapshot.splitlines():
        if needle in line and "[e" in line:
            return line.split("]")[0].split("[")[1]
    raise AssertionError(f"{needle} не найден:\n{snapshot}")


def test_local_page_full_cycle(server, workspace):
    opened = service.browser_open(server + "/", 1, local_preview=True, workspace=workspace, replace=True)
    sid = opened["session_id"]
    assert opened["local"] and opened["title"] == "Демо"
    snap = service.browser_event(sid, "snapshot")
    assert snap["ok"] and "heading1 \"Заказ\"" in snap["snapshot"]
    name = ref_of(snap["snapshot"], 'textbox "Имя"')
    button = ref_of(snap["snapshot"], 'button "Отправить"')
    assert service.browser_event(sid, "type", {"ref": name, "text": "Док"})["ok"]
    clicked = service.browser_event(sid, "click", {"ref": button})
    assert clicked["ok"] and any("сломалось" in item for item in clicked["new_console_problems"])
    assert "Привет, Док" in service.browser_event(sid, "snapshot")["snapshot"]
    assert service.browser_event(sid, "select", {"selector": "#size", "value": "m"})["ok"]
    assert service.browser_event(sid, "eval", {"expression": "document.getElementById('size').value"})["result"] == '"m"'
    messages = service.browser_event(sid, "console", {})["messages"]
    kinds = {item["type"] for item in messages}
    assert {"log", "error", "pageerror"} <= kinds
    network = service.browser_event(sid, "network", {"failed_only": True})["requests"]
    assert any(item["url"].endswith("/missing.json") and item["status"] == 404 for item in network)
    assert service.browser_event(sid, "inspect", {"ref": button})["tag"] == "button"
    assert "<h1>" in service.browser_event(sid, "dom", {})["html"]
    scrolled = service.browser_event(sid, "scroll", {"to": "bottom"})
    assert scrolled["scroll"]["y"] > 0
    assert service.browser_event(sid, "resize", {"width": 400, "height": 700})["viewport"]["width"] == 400
    shot = service.browser_event(sid, "screenshot", {})
    assert Path(shot["path"]).stat().st_size > 500
    frame = service.browser_frame(sid)
    assert frame["ok"] and frame["width"] == 400 and frame["image"]
    link = ref_of(service.browser_event(sid, "snapshot")["snapshot"], 'link "Дальше"')
    assert service.browser_event(sid, "click", {"ref": link})["url"].endswith("/next")
    assert service.browser_event(sid, "navigate", {"action": "back"})["url"].endswith("/")
    assert service.live_sessions()[-1]["session_id"] == sid
    assert service.browser_close(sid)["ok"]
    assert service.session_info(sid) is None
    assert service.browser_event(sid, "snapshot")["ok"] is False


def test_stale_ref_is_reported(server, workspace):
    sid = service.browser_open(server + "/", 1, local_preview=True, workspace=workspace, replace=True)["session_id"]
    service.browser_event(sid, "snapshot")
    service.browser_event(sid, "navigate", {"url": server + "/next"})
    result = service.browser_event(sid, "click", {"ref": "e0"})
    assert not result["ok"]
    service.browser_close(sid)


def test_redirect_to_other_host_is_blocked(server, workspace):
    with pytest.raises(Exception):
        service.browser_open(server + "/redirect", 1, local_preview=True, workspace=workspace, replace=True)
    assert service.live_sessions() == []


def test_loopback_needs_local_preview(server, workspace):
    with pytest.raises(NetworkAccessError):
        service.browser_open(server + "/", 1, workspace=workspace, replace=True)


def test_local_session_cannot_open_other_site_or_file(server, workspace):
    sid = service.browser_open(server + "/", 1, local_preview=True, workspace=workspace, replace=True)["session_id"]
    for url in ("file:///etc/hosts", "http://169.254.169.254/", "http://localhost.evil.test/"):
        result = service.browser_event(sid, "navigate", {"url": url})
        assert not result["ok"], url
    service.browser_close(sid)


def test_file_preview_is_confined_to_workspace(workspace, tmp_path):
    (workspace / "index.html").write_text(
        "<title>Файл</title><button onclick=\"document.title='ok'\">Жми</button><script src='app.js'></script>", encoding="utf-8"
    )
    (workspace / "app.js").write_text("console.log('loaded')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    url = service.file_url_for("index.html", workspace)
    sid = service.browser_open(url, 2, workspace=workspace, replace=True)["session_id"]
    snap = service.browser_event(sid, "snapshot")
    assert service.browser_event(sid, "click", {"ref": ref_of(snap["snapshot"], "Жми")})["title"] == "ok"
    assert any(item["text"] == "loaded" for item in service.browser_event(sid, "console", {})["messages"])
    fetched = service.browser_event(sid, "eval", {"expression": f"fetch({(tmp_path / 'secret.txt').as_uri()!r}).then(r => r.text()).catch(() => 'blocked')"})
    assert "secret" not in fetched.get("result", "")
    assert not service.browser_event(sid, "navigate", {"url": (tmp_path / "secret.txt").as_uri()})["ok"]
    service.browser_close(sid)
    with pytest.raises(ValueError):
        service.file_url_for("../secret.txt", workspace)
    with pytest.raises(NetworkAccessError):
        service.browser_open((tmp_path / "secret.txt").as_uri(), 2, workspace=workspace)


def test_replace_closes_previous_page_of_project(server, workspace):
    first = service.browser_open(server + "/", 7, local_preview=True, workspace=workspace, replace=True)["session_id"]
    second = service.browser_open(server + "/next", 7, local_preview=True, workspace=workspace, replace=True)["session_id"]
    assert service.session_info(first) is None and service.session_info(second)
    service.browser_close(second)


def test_notifier_gets_open_and_close(server, workspace):
    events = []
    service.set_notifier(lambda event, data: events.append((event, data["action"])))
    try:
        sid = service.browser_open(server + "/", 3, local_preview=True, workspace=workspace, replace=True)["session_id"]
        service.browser_close(sid)
    finally:
        service.set_notifier(None)
    assert events == [("browser.session", "opened"), ("browser.session", "closed")]

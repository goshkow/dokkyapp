import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from koddi.desktop import web_host


@pytest.fixture(autouse=True)
def isolate_desktop_access(monkeypatch):
    from koddi.desktop.transport import desktop_access

    monkeypatch.setattr(desktop_access,'key',None)
    monkeypatch.setattr(desktop_access,'origin',None)


def test_window_waits_for_backend_before_loading_page(monkeypatch):
    callbacks, loaded, errors = [], [], []
    backend = SimpleNamespace(server=SimpleNamespace(started=False), failed=threading.Event())
    host = SimpleNamespace(
        backend=backend, _initial_show_done=True, _load_attempts=0, api_base="http://127.0.0.1:12345",
        view=SimpleNamespace(load=loaded.append),
        startup_label=SimpleNamespace(setText=errors.append), _load_ui=lambda: None, _ensure_tiles=lambda: None,
    )
    monkeypatch.setattr(web_host.QTimer, "singleShot", lambda ms, fn: callbacks.append((ms, fn)))
    for _ in range(120):
        web_host.WebHost._load_ui(host)
    assert len(callbacks) == 120
    assert not loaded and host._load_attempts == 0
    backend.server.started = True
    web_host.WebHost._load_ui(host)
    assert loaded[0].toString() == host.api_base + "/ui/"
    assert host._load_attempts == 1
    backend.server.started = False
    backend.failed.set()
    web_host.WebHost._load_ui(host)
    assert len(loaded) == 1 and len(callbacks) == 120
    assert "локальный сервер" in errors[0]


def test_window_keeps_splash_and_retries_until_page_loads(monkeypatch):
    callbacks, retried, errors = [], [], []
    backend = SimpleNamespace(server=SimpleNamespace(started=True), failed=threading.Event())
    page = SimpleNamespace(requestedUrl=lambda: web_host.QUrl("http://127.0.0.1:12345/ui/"))
    host = SimpleNamespace(
        backend=backend, _ui_loaded=False, _load_attempts=0, api_base="http://127.0.0.1:12345",
        view=SimpleNamespace(page=lambda: page), startup_label=SimpleNamespace(setText=errors.append),
        _load_ui=lambda: retried.append(1),
    )
    host._retry_initial_load = lambda: web_host.WebHost._retry_initial_load(host)
    monkeypatch.setattr(web_host.QTimer, "singleShot", lambda ms, fn: callbacks.append((ms, fn)))
    # медленный первый запуск: сотня отказов подряд не обрывает попытки, пауза не больше 2 секунд
    for attempt in range(1, 101):
        host._load_attempts = attempt
        web_host.WebHost._on_load_finished(host, False)
    assert len(callbacks) == 100 and max(ms for ms, _ in callbacks) == 2000
    callbacks[-1][1]()
    assert retried == [1] and not errors
    backend.failed.set()
    callbacks[-1][1]()
    assert retried == [1] and "локальный сервер" in errors[0]


@pytest.mark.parametrize("case", ["normal", "early", "stream", "write", "bind_failure"])
def test_real_uvicorn_shutdown_in_isolated_profile(tmp_path, case):
    source = r'''
import asyncio
import json
import os
import socket
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from koddi.desktop import web_host
from koddi.desktop.transport import DesktopTransportGuard, desktop_access

case = os.environ['SHUTDOWN_CASE']
events = []
release = threading.Event()
bootstrapping = threading.Event()
writing = threading.Event()
streaming = threading.Event()
path = Path(os.environ['KODDI_DATA_DIR']) / 'active-write.txt'
path.parent.mkdir(parents=True, exist_ok=True)

def writer():
    with path.open('w', encoding='utf-8') as output:
        output.write('first\n')
        output.flush()
        writing.set()
        assert release.wait(10)
        output.write('last\n')
        output.flush()
        os.fsync(output.fileno())
    events.append('write-complete')

@asynccontextmanager
async def lifespan(app):
    events.append('startup')
    work = asyncio.create_task(asyncio.to_thread(writer)) if case == 'write' else None
    try:
        yield
    finally:
        events.append('shutdown')
        if work:
            await work
        events.append('cleanup-complete')

api = FastAPI(lifespan=lifespan)
api.add_middleware(DesktopTransportGuard)
@api.get('/health')
async def health():
    return {'ok': True}
@api.get('/stream')
async def stream():
    async def content():
        try:
            streaming.set()
            yield 'data: started\n\n'
            await asyncio.Event().wait()
        finally:
            events.append('stream-closed')
    return StreamingResponse(content(), media_type='text/event-stream')

web_host.desktop_server.app = api
web_host.desktop_server.migrate_legacy_data = lambda: events.append('migrate')
web_host.desktop_server.configure_logging = lambda: None
def init():
    events.append('init-db')
    if case == 'early':
        bootstrapping.set()
        assert release.wait(10)
web_host.desktop_server.init_db = init

occupied = socket.socket() if case == 'bind_failure' else None
if occupied:
    occupied.bind(('127.0.0.1', 0))
    occupied.listen()
backend = web_host.DesktopBackend(occupied.getsockname()[1] if occupied else 0)
backend.server.config.timeout_graceful_shutdown = 0.05
backend.server.config.log_config = None
connection = None
stopper = None
try:
    assert backend.thread.daemon is False
    assert backend.server.config.host == '127.0.0.1'
    assert backend.server.config.lifespan == 'on'
    backend.start()
    if case == 'early':
        assert bootstrapping.wait(5)
        backend.request_shutdown()
        assert not backend.server.should_exit
        release.set()
    elif case == 'bind_failure':
        backend.thread.join(timeout=5)
        assert not backend.thread.is_alive()
        assert backend.failed.is_set()
    else:
        assert backend.server._main_loop_entered.wait(5)
        port = backend.server.servers[0].sockets[0].getsockname()[1]
        if case == 'normal':
            url = f'http://127.0.0.1:{port}/health'
            assert httpx.get(url, trust_env=False).status_code == 404
            response = httpx.get(url, headers={'X-Koddi-Desktop-Session': desktop_access.key}, trust_env=False)
            assert response.json() == {'ok': True}
            assert desktop_access.key not in response.text
        elif case == 'stream':
            connection = socket.create_connection(('127.0.0.1', port), timeout=5)
            request = f'GET /stream HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Koddi-Desktop-Session: {desktop_access.key}\r\n\r\n'
            connection.sendall(request.encode('ascii'))
            assert streaming.wait(5)
        elif case == 'write':
            assert writing.wait(5)
            stopper = threading.Thread(target=backend.stop, daemon=False)
            stopper.start()
            time.sleep(0.3)
            assert stopper.is_alive() and backend.thread.is_alive()
            assert path.read_text(encoding='utf-8') == 'first\n'
            release.set()
            stopper.join(timeout=5)
            assert not stopper.is_alive()
            assert path.read_text(encoding='utf-8') == 'first\nlast\n'
    backend.stop()
    backend.stop()
    assert not backend.thread.is_alive()
    assert backend.server.force_exit is False
    assert events.count('startup') == events.count('shutdown') == 1, events
    assert events.index('shutdown') < events.index('cleanup-complete'), events
    if case == 'write':
        assert events.index('write-complete') < events.index('cleanup-complete'), events
    if case == 'stream':
        assert 'stream-closed' in events
    if case != 'bind_failure':
        assert not backend.failed.is_set()
    print(json.dumps({'ok': True, 'case': case}), flush=True)
finally:
    release.set()
    if connection:
        connection.close()
    backend.stop()
    if stopper:
        stopper.join(timeout=5)
    if occupied:
        occupied.close()
'''
    env = dict(os.environ, KODDI_DATA_DIR=str(tmp_path / "profile"), KODDI_PORTABLE="1",
               KODDI_INTERNAL_TEST="1", SHUTDOWN_CASE=case)
    result = subprocess.run([sys.executable, "-c", source], env=env, capture_output=True,
                            text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["ok"]


def test_join_does_not_force_exit_on_slow_writes_or_repeated_interrupt(monkeypatch, caplog):
    calls = []

    class Worker:
        ident = 123
        alive = True
        joins = 0

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            assert timeout == 0.2
            self.joins += 1
            if self.joins == 1:
                raise KeyboardInterrupt
            if self.joins == 3:
                self.alive = False

    backend = object.__new__(web_host.DesktopBackend)
    backend.server = SimpleNamespace(request_shutdown=lambda: calls.append("stop"), force_exit=False)
    backend.thread = Worker()
    ticks = iter([0, 16, 16, 32, 32])
    monkeypatch.setattr(web_host.time, "monotonic", lambda: next(ticks))
    backend.stop()
    assert calls == ["stop", "stop"]
    assert backend.thread.joins == 3
    assert backend.server.force_exit is False
    assert "Ожидаем безопасного завершения" in caplog.text


@pytest.mark.asyncio
async def test_existing_backend_lifespan_runs_all_cleanup_hooks(monkeypatch):
    import asyncio
    from importlib import import_module
    from koddi.browser import service as browser_service
    from koddi.dokky import monitor

    api_module = import_module("koddi.api.app")
    events = []

    async def watch():
        await asyncio.Event().wait()

    async def stop_queue():
        events.append("queue")

    async def stop_runs():
        events.append("runs")

    monkeypatch.setattr(monitor, "watch_account", watch)
    monkeypatch.setattr(api_module.request_queue, "recover", lambda: None)
    monkeypatch.setattr(api_module.request_queue, "shutdown", stop_queue)
    monkeypatch.setattr(api_module.agent_runs, "shutdown", stop_runs)
    monkeypatch.setattr(browser_service, "browser_close_all", lambda: events.append("browser"))
    async with api_module.app.router.lifespan_context(api_module.app):
        pass
    assert events == ["queue", "runs", "browser"]


@pytest.mark.parametrize("failure", [None, "backend", "window"])
def test_main_always_joins_before_releasing_profile_broker(tmp_path, monkeypatch, failure):
    from koddi import config
    from koddi.desktop import session, tray

    calls = []

    class Signal:
        def connect(self, callback):
            self.callback = callback

    broker = SimpleNamespace(newConnection=Signal(), close=lambda: calls.append("broker-close"))
    about_to_quit = Signal()

    def execute():
        about_to_quit.callback()
        return 7

    app = SimpleNamespace(setApplicationName=lambda name: None, aboutToQuit=about_to_quit, exec=execute, quit=lambda: None)
    timer = SimpleNamespace(timeout=Signal(), start=lambda interval: None, stop=lambda: calls.append("timer-stop"))

    class Backend:
        failed = threading.Event()

        def __init__(self, port):
            if failure == "backend":
                raise RuntimeError("backend creation failed")

        def start(self):
            calls.append("start")

        def request_shutdown(self):
            calls.append("request-stop")

        def stop(self):
            assert "broker-close" not in calls
            calls.append("joined")

    def window(*args):
        if failure == "window":
            raise RuntimeError("window creation failed")
        return SimpleNamespace(restore_geometry=lambda: None, show=lambda: None, bring_to_front=lambda: None, tray=None)

    monkeypatch.setattr(web_host, "QApplication", lambda args: app)
    monkeypatch.setattr(web_host, "QTimer", lambda parent: timer)
    monkeypatch.setattr(web_host, "DesktopBackend", Backend)
    monkeypatch.setattr(web_host, "WebHost", window)
    monkeypatch.setattr(web_host, "_api_port", lambda: 12345)
    monkeypatch.setattr(tray, "install", lambda actions, parent: None)
    monkeypatch.setattr(session, "activation_server", lambda: broker)
    monkeypatch.setattr(session, "register_protocol", lambda: None)
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    with pytest.raises(RuntimeError if failure else SystemExit) as error:
        web_host.main()
    assert calls[-1] == "broker-close"
    if failure != "backend":
        assert calls[-2] == "joined"
    if not failure:
        assert error.value.code == 7

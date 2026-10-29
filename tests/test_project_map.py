"""Карта проекта: разбор файлов, граф, инкрементальное обновление, пути Windows и macOS, инструменты агента и API."""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import time
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.projectmap import service
from koddi.projectmap.graph import Graph
from koddi.projectmap.parse import PARSER_VERSION, language_of, parse_file
from koddi.projectmap.views import overview, runtime_snapshot, treemap_payload
from koddi.tools.runner import ToolRunner

OWNER = "__test__"


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def bump_mtime(path: Path) -> None:
    # на быстрых файловых системах две записи подряд могут получить одинаковый mtime
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "map.sqlite3")
    storage.init_db()
    yield
    service.shutdown()


@pytest.fixture
def project(database, tmp_path):
    from koddi.projects import add_project

    root = tmp_path / "proj"
    write(root, "app/__init__.py", '"""Пакет приложения."""\n')
    write(root, "app/api.py", (
        '"""HTTP-обработчики заказов."""\n'
        "from fastapi import APIRouter\n"
        "from app.store import load_orders\n"
        "from .models import Order\n"
        'router = APIRouter(prefix="/api/orders")\n\n'
        '@router.get("/{order_id}")\n'
        "def get_order(order_id: int):\n    return load_orders()\n\n"
        '@router.post("")\n'
        "def create_order():\n    return Order()\n"
    ))
    write(root, "app/store.py", "import json\n\ndef load_orders():\n    return json.loads('[]')\n")
    write(root, "app/models.py", "class Order:\n    pass\n")
    write(root, "lib/json.py", "# локальный модуль с именем из stdlib\n")
    write(root, "web/src/main.ts", (
        "// точка входа интерфейса\n"
        "import { api } from './api';\n"
        "import { Card } from './components';\n"
        "import React from 'react';\n"
        "export function start() { return api(`/api/orders/${id}`); }\n"
    ))
    write(root, "web/src/api.ts", "export const api = (path: string) => fetch(path);\n")
    write(root, "web/src/components/index.tsx", "export function Card() { return null; }\n")
    write(root, "docs/README.md", "# Документация\n\nСм. [[api]] и [хранилище](../app/store.py).\n")
    write(root, "node_modules/pkg/index.js", "module.exports = 1;\n")
    write(root, "dist/bundle.js", "console.log(1)\n")
    write(root, ".env", "API_KEY=secret-value\n")
    write(root, "ignored/data.py", "x = 1\n")
    write(root, ".gitignore", "ignored/\n")
    write(root, "image.png", "")
    (root / "image.png").write_bytes(b"\x89PNG\x00\x00")
    project = add_project(str(root))
    return project["id"], root.resolve()


def build(project_id: int, root: Path) -> Graph:
    service.scan(project_id, root)
    return service.graph(project_id, OWNER)


# ---------- разбор файлов

def test_parse_python_routes_with_router_prefix_and_imports():
    info = parse_file("app/api.py", b'@app.get("/health")\ndef health():\n    return 1\n')
    assert info["routes"] == [{"m": "GET", "p": "/health", "l": 2, "h": "health"}]
    info = parse_file("x.py", b"def broken(:\n    pass\nclass Ok:\n    pass\n")
    assert info["parse_error"] is True and ["Ok", "symbol", 3] in info["symbols"]


def test_parse_typescript_imports_symbols_and_api_calls():
    info = parse_file("a.ts", b"import x from './x';\nexport const load = () => api(`/api/items/${id}/files?x=1`);\nconst hidden = 1;\n")
    assert info["imports"] == ["./x"]
    assert ["load", "export const", 2] in info["symbols"] and not any(item[0] == "hidden" for item in info["symbols"])
    assert info["calls"] == ["/api/items/{}/files"]


def test_secret_lock_and_binary_files_are_not_mapped():
    assert language_of(".env") is None and language_of("config/.env.production") is None
    assert language_of(".env.example") == "config"
    assert language_of("certs/server.pem") is None and language_of("package-lock.json") is None
    assert language_of("assets/logo.png") is None
    info = parse_file("blob.txt", b"abc\x00def")
    assert info.get("skipped") == "binary" and "symbols" not in info


def test_summary_is_redacted():
    info = parse_file("conf.py", b'"""token=abcdef123456 used for tests"""\n')
    assert "abcdef123456" not in info["summary"]


# ---------- граф

def test_graph_resolves_imports_routes_calls_and_links(project):
    project_id, root = project
    graph = build(project_id, root)
    assert "app/store.py" in graph.imports["app/api.py"]
    assert "app/models.py" in graph.imports["app/api.py"]
    assert graph.externals["app/api.py"] == ["fastapi"]
    # import json не связывается с lib/json.py
    assert graph.imports["app/store.py"] == []
    assert set(graph.imports["web/src/main.ts"]) == {"web/src/api.ts", "web/src/components/index.tsx"}
    assert graph.externals["web/src/main.ts"] == ["react"]
    assert "api:GET /api/orders/{order_id}" in graph.routes
    assert graph.calls["web/src/main.ts"] == ["api:GET /api/orders/{order_id}"]
    # [[api]] - вики-ссылка по стему файла, вторая - обычная относительная ссылка
    assert graph.links["docs/README.md"] == ["app/api.py", "app/store.py"]
    assert graph.resolve_ref("app\\api.py") == "file:app/api.py"
    assert graph.resolve_ref("GET /api/orders/7") == "api:GET /api/orders/{order_id}"


def test_scan_respects_ignores_dependencies_and_secrets(project):
    project_id, root = project
    graph = build(project_id, root)
    assert not any(path.startswith(("node_modules/", "dist/", "ignored/")) for path in graph.files)
    assert ".env" not in graph.files and "image.png" not in graph.files
    with storage.db() as conn:
        dump = json.dumps([dict(row) for row in conn.execute("SELECT * FROM project_map_files")], ensure_ascii=False)
    assert "secret-value" not in dump


def test_map_is_stored_in_application_data(project):
    project_id, root = project
    service.scan(project_id, root)
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_map_files WHERE project_id=?", (project_id,)).fetchone()[0] > 5
    assert not any(name.startswith(".koddi") or name.endswith(".sqlite3") for name in os.listdir(root))


# ---------- инкрементальное обновление

def test_incremental_scan_tracks_mtime_and_hash(project):
    project_id, root = project
    first = service.scan(project_id, root)
    assert first["added"] >= 9 and first["version"] == 1
    again = service.scan(project_id, root)
    assert again["added"] == again["changed"] == again["removed"] == 0 and again["version"] == 1

    store = root / "app/store.py"
    bump_mtime(store)
    touched = service.scan(project_id, root)
    assert touched["touched"] == 1 and touched["changed"] == 0 and touched["version"] == 1

    store.write_text("def load_orders():\n    return []\n\ndef save_order(order):\n    return order\n", encoding="utf-8")
    bump_mtime(store)
    changed = service.scan(project_id, root)
    assert changed["changed"] == 1 and changed["version"] == 2
    graph = service.graph(project_id, OWNER)
    assert any(symbol[0] == "save_order" for symbol in graph.files["app/store.py"]["info"]["symbols"])

    write(root, "app/new_module.py", "from app.models import Order\n")
    (root / "app/models.py").unlink()
    result = service.scan(project_id, root)
    assert result["added"] == 1 and result["removed"] == 1 and result["version"] == 3
    graph = service.graph(project_id, OWNER)
    assert "app/models.py" not in graph.files and "app/new_module.py" in graph.files


def test_parser_version_change_reparses_everything(project, monkeypatch):
    project_id, root = project
    service.scan(project_id, root)
    monkeypatch.setattr(service, "PARSER_VERSION", PARSER_VERSION + 1)
    result = service.scan(project_id, root)
    assert result["changed"] >= 9 and result["version"] == 2


def test_file_limit_marks_map_truncated_without_dropping_unseen_files(project, monkeypatch):
    project_id, root = project
    service.scan(project_id, root)
    monkeypatch.setattr(service, "MAX_FILES", 3)
    result = service.scan(project_id, root)
    assert result["truncated"] is True and result["removed"] == 0
    assert service.state(project_id)["truncated"] is True


def test_missing_project_folder_and_unknown_project_fail_cleanly(database, tmp_path):
    from koddi.projects import add_project

    root = tmp_path / "gone"
    project = add_project(str(root))
    root.rmdir()
    with pytest.raises(service.MapError, match="not available"):
        service.scan(project["id"], root)
    assert service.state(project["id"])["status"] == "error"
    with pytest.raises(service.MapError, match="project not found"):
        service.scan(9999, tmp_path)


def test_unreadable_file_keeps_previous_record(project, monkeypatch):
    project_id, root = project
    service.scan(project_id, root)
    target = root / "app/store.py"
    target.write_text("def other():\n    pass\n", encoding="utf-8")
    bump_mtime(target)
    original = Path.read_bytes

    def locked(self):
        if self.name == "store.py":
            raise PermissionError("file is locked by another process")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    result = service.scan(project_id, root)
    assert result["errors"] == 1 and result["removed"] == 0
    assert "app/store.py" in service.graph(project_id, OWNER).files


@pytest.mark.skipif(os.name == "nt", reason="symlink needs privileges on Windows")
def test_symlinked_folder_outside_project_is_not_followed(project, tmp_path):
    project_id, root = project
    outside = tmp_path / "outside"
    write(outside, "secret.py", "PASSWORD = 1\n")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    graph = build(project_id, root)
    assert not any(path.startswith("linked/") for path in graph.files)


# ---------- пути Windows и macOS

@pytest.mark.parametrize(("root", "path", "expected"), [
    (r"C:\Users\Anna\Project", r"C:\Users\Anna\Project\src\App.tsx", "src/App.tsx"),
    (r"C:\Users\Anna\Project", r"c:\users\anna\project\Src\app.tsx", "Src/app.tsx"),
    (r"C:\Users\Anna\Project", r"C:/Users/Anna/Project/docs/a.md", "docs/a.md"),
    (r"C:\Users\Anna\Project", r"C:\Users\Anna\Project", ""),
    (r"C:\Users\Anna\Project", r"C:\Users\Anna\Project2\x.py", None),
    (r"C:\Users\Anna\Project", r"D:\Project\x.py", None),
    (r"C:\Users\Anna\Project", r"C:\Users\Anna\Project\..\Other\x.py", None),
    (r"\\server\share\proj", r"\\server\share\proj\a\b.py", "a/b.py"),
])
def test_windows_event_paths(root, path, expected):
    assert service.relative_key(root, path, pathmod=ntpath) == expected


def test_macos_event_paths_with_unicode_forms_and_case():
    root = "/Users/anna/Проект"
    decomposed = unicodedata.normalize("NFD", "/Users/anna/Проект/Файл й.py")
    assert service.relative_key(root, decomposed, pathmod=posixpath) is not None
    assert service.relative_key(root, "/Users/anna/Проект2/a.py", pathmod=posixpath) is None
    assert service.relative_key(root, "/Users/anna/Проект/src/../../x.py", pathmod=posixpath) is None


@pytest.mark.parametrize(("relative", "expected"), [
    ("src/app.py", True), ("node_modules/x/index.js", False), (".git/index", False), ("src/.cache/a.py", False),
    (".github/workflows/ci.yml", True), ("assets/logo.png", False), ("src", True), (".env", False),
])
def test_relevant_changes(relative, expected):
    assert service.relevant(relative) is expected


# ---------- инструменты агента

def test_agent_reads_overview_path_query_and_api(project):
    project_id, root = project
    runner = ToolRunner(root, project_id)
    overview_result = runner.run("project.map", {})
    assert overview_result["ok"] is True and "Project map v1" in overview_result["map"]
    assert "app/ (" in overview_result["map"] and "API routes by prefix" in overview_result["map"]
    detail = runner.run("project.map", {"path": "app/api.py"})
    assert "Defines API: GET /api/orders/{order_id}" in detail["map"] and detail["node"] == "file:app/api.py"
    folder = runner.run("project.map", {"path": "app"})
    assert "app/store.py" in folder["map"] and "Files:" in folder["map"]
    found = runner.run("project.map", {"query": "orders"})
    assert "app/api.py" in found["map"] and "[api] GET /api/orders/{order_id}" in found["map"]
    routes = runner.run("project.map", {"api": "/api/orders"})
    assert "called from web/src/main.ts" in routes["map"]
    missing = runner.run("project.map", {"path": "nope/missing.py"})
    assert missing["ok"] is True and "is not in the project map" in missing["map"]


def test_map_answer_respects_character_budget(project):
    project_id, root = project
    for index in range(80):
        write(root, f"many/module_{index}.py", f'"""Модуль номер {index} с длинным описанием."""\ndef f{index}():\n    pass\n')
    result = ToolRunner(root, project_id).run("project.map", {"path": "many", "max_chars": 1000})
    assert len(result["map"]) <= 1300 and "more lines omitted" in result["map"]


def test_agent_writes_updates_and_deletes_notes(project):
    project_id, root = project
    runner = ToolRunner(root, project_id)
    created = runner.run("project.map_update", {"action": "upsert", "kind": "decision", "title": "Заказы через роутер", "body": "Маршруты в [[app/api.py]], хранение в store.", "targets": ["app/store.py", "GET /api/orders/{order_id}"]})
    assert created["ok"] is True and created["action"] == "created"
    assert set(created["targets"]) == {"file:app/store.py", "api:GET /api/orders/{order_id}"}
    detail = runner.run("project.map", {"path": "app/store.py"})
    assert "Заказы через роутер" in detail["map"] and "stale" not in detail["map"]
    same = runner.run("project.map_update", {"action": "upsert", "kind": "decision", "title": "Заказы через роутер", "body": "Обновлено.", "targets": ["app/store.py"]})
    assert same["action"] == "updated" and same["id"] == created["id"]

    store = root / "app/store.py"
    store.write_text("def load_orders():\n    return [1]\n", encoding="utf-8")
    bump_mtime(store)
    # без наблюдателя в тесте правку вне инструментов агента отмечаем вручную, как это сделал бы watchdog
    service.mark_dirty(project_id)
    stale = runner.run("project.map", {"path": "app/store.py"})
    assert "stale: linked file changed" in stale["map"]

    deleted = runner.run("project.map_update", {"action": "delete", "note_id": created["id"]})
    assert deleted["ok"] is True and "(0 of 0)" in runner.run("project.map", {"query": "Заказы роутер"})["map"]


@pytest.mark.parametrize(("args", "error"), [
    ({"action": "upsert", "kind": "decision", "title": "x", "targets": ["missing/file.py"]}, "targets not found"),
    ({"action": "upsert", "kind": "decision", "title": "x", "body": "a" * 4001}, "Некорректное поле body"),
    ({"action": "upsert", "kind": "secret", "title": "x"}, "Некорректное поле kind"),
    ({"action": "delete"}, "note_id is required"),
    ({"action": "delete", "note_id": 999}, "note not found"),
    ({"action": "upsert", "kind": "topic", "title": "   "}, "title is required"),
])
def test_note_validation_errors(project, args, error):
    project_id, root = project
    result = ToolRunner(root, project_id).run("project.map_update", args)
    assert result["ok"] is False and error in result["error"]


def test_notes_are_isolated_by_owner(project):
    project_id, root = project
    service.scan(project_id, root)
    note = service.save_note(project_id, "other-account", kind="topic", title="Чужая заметка", body="текст")
    assert "Чужая заметка" not in overview(service.graph(project_id, OWNER), service.state(project_id))
    with pytest.raises(service.MapError, match="note not found"):
        service.delete_note(project_id, OWNER, note["id"])


def test_note_secrets_are_redacted(project):
    project_id, root = project
    ToolRunner(root, project_id).run("project.map_update", {"action": "upsert", "kind": "gotcha", "title": "Ключ", "body": "password=hunter2 в конфиге"})
    with storage.db() as conn:
        body = conn.execute("SELECT body FROM project_map_notes").fetchone()[0]
    assert "hunter2" not in body


def test_agent_edit_marks_map_dirty_and_next_read_sees_it(project):
    project_id, root = project
    runner = ToolRunner(root, project_id, explicit_write_consent=True)
    runner.run("project.map", {})
    storage.set_setting("permission_profile", "full")
    created = runner.run("file.create", {"path": "app/billing.py", "content": '"""Счета."""\ndef invoice():\n    pass\n'})
    assert created.get("ok") is True, created
    result = runner.run("project.map", {"path": "app/billing.py"})
    assert "Summary: Счета." in result["map"]


def test_map_requires_registered_project(database, tmp_path):
    root = tmp_path / "loose"
    root.mkdir()
    result = ToolRunner(root, None).run("project.map", {})
    assert result["ok"] is False and "project" in result["error"]


def test_map_tools_policy_and_schemas(project):
    from koddi.policy.engine import decide
    from koddi.tools.schemas import tool_schemas_for_mode

    project_id, root = project
    storage.set_setting("permission_profile", "read-only")
    assert decide("project.map", {}, root, project_id).action == "allow"
    assert decide("project.map_update", {"title": "x"}, root, project_id).action == "allow"
    assert decide("project.map_update", {"title": "x"}, None, project_id).action == "block"
    for mode in ("development", "debug", "plan", "review"):
        names = {tool["function"]["name"] for tool in tool_schemas_for_mode(mode)}
        assert {"project.map", "project.map_update"} <= names
    assert tool_schemas_for_mode("chat") == []


# ---------- снимок контекста, интерфейс, владельцы

def test_runtime_snapshot_and_context_field(project):
    from koddi.agents.prompts import CONTEXT_PREFIX, compose_coding_context

    project_id, root = project
    assert service.context_snapshot(project_id, root, OWNER) is None
    service.scan(project_id, root)
    snapshot = service.context_snapshot(project_id, root, OWNER)
    assert snapshot["tool"] == "project.map" and any(line.startswith("app/") for line in snapshot["folders"])
    message = compose_coding_context(mode="development", workspace=root, project_id=project_id, project_map=snapshot)
    assert json.loads(message["content"][len(CONTEXT_PREFIX):])["project_map"]["folders"] == snapshot["folders"]
    plain = compose_coding_context(mode="development", workspace=root, project_id=project_id)
    assert "project_map" not in json.loads(plain["content"][len(CONTEXT_PREFIX):])
    # число файлов не входит в снимок: новый файл в той же папке не порождает дельту контекста
    write(root, "app/extra.py", "x = 1\n")
    service.scan(project_id, root)
    assert runtime_snapshot(service.graph(project_id, OWNER)) == snapshot


def test_treemap_payload_weights_links_and_folds_small_files(project):
    project_id, root = project
    graph = build(project_id, root)
    full = treemap_payload(graph)
    paths = [item["p"] for item in full["files"]]
    assert not full["rest"] and len(paths) == full["totals"]["files"] == len(graph.files)
    assert not any(path.startswith(("node_modules/", "dist/", "ignored/")) or path == ".env" for path in paths)
    item = {entry["p"]: entry for entry in full["files"]}
    api = item["app/api.py"]
    assert api["w"] == api["l"] > 0 and api["api"] >= 1 and api["lang"] == "python"
    assert {paths[target] for target in api["i"]} >= {"app/store.py", "app/models.py"}
    assert all(entry["w"] >= 1 for entry in full["files"])
    # в крупном проекте мелкие файлы складываются в «ещё N» своей папки, связи ссылаются только на показанные
    small = treemap_payload(graph, limit=3)
    assert len(small["files"]) == 3
    assert sum(bucket["count"] for bucket in small["rest"]) == len(graph.files) - 3
    assert all(0 <= target < 3 for entry in small["files"] for target in entry.get("i", []))
    kept = {entry["p"] for entry in small["files"]}
    assert min(entry["w"] for entry in small["files"]) >= max(item[path]["w"] for path in paths if path not in kept)


def test_adopted_project_keeps_notes_for_new_account(project):
    from koddi.dokky.coding import adopt_local_data

    project_id, root = project
    service.scan(project_id, root)
    service.save_note(project_id, "old", kind="topic", title="Общая заметка", body="")
    service.save_note(project_id, "new", kind="topic", title="Общая заметка", body="своя")
    service.save_note(project_id, "old", kind="topic", title="Только у старого", body="")
    adopt_local_data("new")
    titles = {note["title"]: note["body"] for note in service._load_notes(project_id, "new")}
    assert titles == {"Общая заметка": "своя", "Только у старого": ""}


def test_api_map_endpoints(project):
    from koddi.api import create_app

    project_id, root = project
    with TestClient(create_app()) as client:
        state = client.get(f"/api/projects/{project_id}/map/state")
        assert state.status_code == 200 and state.json()["project_id"] == project_id
        tree = client.get(f"/api/projects/{project_id}/map")
        assert tree.status_code == 200, tree.text
        body = tree.json()
        assert body["totals"]["files"] >= 9 and body["state"]["status"] in {"ready", "updating"}
        assert any(item["p"] == "app/api.py" for item in body["files"]) and not any(".env" in item["p"] for item in body["files"])
        assert client.get(f"/api/projects/{project_id}/map/node", params={"id": "file:app/api.py"}).status_code == 404
        assert client.get("/api/projects/999/map").status_code == 404
        assert client.post(f"/api/projects/{project_id}/map/rescan").json()["status"] in {"ready", "updating"}
        note = service.save_note(project_id, OWNER, kind="topic", title="Удаляемая", body="")
        assert client.delete(f"/api/projects/{project_id}/map/notes/{note['id']}").status_code == 200
        assert client.delete(f"/api/projects/{project_id}/map/notes/{note['id']}").status_code == 404
        assert client.delete(f"/api/projects/{project_id}").status_code == 200
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_map_files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM project_map_state").fetchone()[0] == 0


# ---------- отслеживание файлов

def test_watcher_filters_events_and_debounces(project, monkeypatch):
    project_id, root = project
    submitted = []
    monkeypatch.setattr(service, "_submit", lambda pid, path: submitted.append(pid))
    monkeypatch.setattr(service, "DEBOUNCE_SECONDS", 0.05)
    monkeypatch.setattr(service, "_background", True)
    watcher = service._Watch(project_id, root)
    watcher.changed([str(root / "node_modules/x/index.js"), str(root / ".git/index")])
    time.sleep(0.2)
    assert submitted == []
    for _ in range(5):
        watcher.changed([str(root / "app/api.py")])
    time.sleep(0.3)
    assert submitted == [project_id]
    watcher.stop()


def test_live_watcher_updates_map_after_file_change(project):
    project_id, root = project
    events = []
    service.enable_background(lambda event, data: events.append((event, data)))
    try:
        service.ensure(project_id, root, wait=20)
        assert service.watched_mode(project_id) in {"events", "poll"}
        if service.watched_mode(project_id) != "events":
            pytest.skip("watchdog недоступен, работает опрос")
        time.sleep(0.5)
        write(root, "app/late.py", '"""Появился позже."""\n')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and "app/late.py" not in service.graph(project_id, OWNER).files:
            time.sleep(0.2)
        assert "app/late.py" in service.graph(project_id, OWNER).files
        assert any(event == "project_map.updated" and data["project_id"] == project_id for event, data in events)
    finally:
        service.shutdown()

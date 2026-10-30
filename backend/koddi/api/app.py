from __future__ import annotations

import asyncio
import uuid
import json
import hashlib
import threading
import re
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from koddi import __version__
from koddi.agents import AgentOrchestrator, agent_runs
from koddi.agents.queue import request_queue
from koddi.agents.modes import list_modes, save_mode
from koddi.agent_runtime import create_task, list_tasks, spawn_child, task_graph, update_task_status
from koddi.attachments import delete_attachment, forget_attachment_files, list_attachments, store_attachment
from koddi.automations import (
    AutomationError, cancel_automation, create_automation, list_automations, run_any, run_scheduler, update_automation,
)
from koddi.browser import (
    browser_close,
    browser_frame,
    browser_input,
    browser_open,
    is_loopback_url,
    live_sessions,
    local_preview_allowed,
    web_fetch,
    web_search,
)
from koddi.changes import (
    create_backup,
    latest_change_set,
    list_backups,
    list_change_sets,
    restore_backup,
    rollback_change_set,
    rollback_latest,
)
from koddi.config import app_dir, settings
from koddi.changes.service import attach_change_files
from koddi.documents import index_document, list_documents
from koddi.events import event_bus
from koddi.installer import diagnostics, update_check
from koddi.integrations import (
    issue_import,
    issue_update,
    mcp_tool_call,
    mcp_tools_list,
    open_diff,
    open_file,
    terminal_open,
)
from koddi.logs import list_logs, log_event, search_logs
from koddi.remote import RemoteAIClient, remote_config, save_remote_config
from koddi.remote_control import Hooks, remote_control
from koddi.projects import add_project, create_worktree, get_project, home_project, list_projects, remove_worktree
from koddi.review import diff_review, safety_review, secret_scan
from koddi.skills import SkillError, apply_skill, create_skill, delete_skill, list_skills, skill_block
from koddi.speech import consent_status, set_consent, transcribe_audio, whisper_model_status
from koddi.storage import init_db
from koddi.storage.db import db, get_setting, set_setting
from koddi.tools import TOOL_SCHEMAS, ToolRunner
from koddi.tools.schemas import AGENT_UNAVAILABLE_TOOLS
from koddi.policy.account import local_owner, owns, require_chat


class SkillEnabledIn(BaseModel):
    enabled: bool


class ProjectSkillDecisionIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern="^[a-f0-9]{64}$")
    trust: bool


class KnowledgeSettingsIn(BaseModel):
    location: str = Field(pattern="^(app|workspace|off)$")


class QaCommandIn(BaseModel):
    kind: str = Field(pattern="^(lint|typecheck|test|build)$")
    command: str = Field(min_length=1, max_length=2000)
    cwd: str = Field(default="", max_length=500)


class QaCommandsIn(BaseModel):
    commands: list[QaCommandIn] = Field(default_factory=list, max_length=20)


class AiReviewIn(BaseModel):
    mode: str = Field(pattern="^(auto|always|off)$")


class QualityRunIn(BaseModel):
    gates: list[str] = Field(default_factory=list, max_length=5)
    scope: str = Field(default="changed", pattern="^(changed|full)$")
    paths: list[str] = Field(default_factory=list, max_length=200)
    coverage: str | None = Field(default=None, pattern="^(existing|collect|none)$")
    max_mutants: int | None = Field(default=None, ge=0, le=60)


class QualityDecisionIn(BaseModel):
    gate: str = Field(pattern="^(dry|mutation|architecture)$")
    key: str = Field(min_length=1, max_length=600)
    decision: str = Field(pattern="^(review|accepted|refactor)$")
    reason: str = Field(default="", max_length=500)


class ArchitecturePolicyIn(BaseModel):
    policy: dict[str, Any] | None = None


class QualityBudgetIn(BaseModel):
    mode: str = Field(pattern="^(auto|economy|strict|off)$")


class WorktreeCleanupIn(BaseModel):
    dry_run: bool = True


class CheckpointRestoreIn(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=500)
    force: bool = False


class KnowledgeNoteIn(BaseModel):
    kind: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=4000)
    targets: list[str] = Field(default_factory=list, max_length=20)


class ProjectIn(BaseModel):
    path: str
    name: str | None = None


class EmptyProjectIn(BaseModel):
    name: str


class ChatIn(BaseModel):
    project_id: int | None = None
    title: str = "Новый чат"
    mode: str = "development"
    model: str | None = None


class ChatUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=72)
    model: str | None = None
    mode: str | None = None
    project_id: int | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=200000)
    mode: str = "development"
    model: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)
    # подключённые навыки: их инструкции идут в начале этого сообщения как указание пользователя
    # навыки и команды плагинов идут строками plugin:<плагин>:<имя>
    skill_ids: list[int | Annotated[str, StringConstraints(pattern=r"^plugin:[a-z0-9._-]{1,64}:[a-z0-9._:-]{1,64}$")]] = Field(
        default_factory=list, max_length=5)


class QueueIn(MessageIn):
    request_id: str


class QueueEditIn(BaseModel):
    version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=200000)


class MemoryIn(BaseModel):
    scope: str = Field(pattern=r"^(user|project)$")
    project_id: int | None = None
    content: str = Field(min_length=1, max_length=2000)


class MemoryEditIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class MemoryClearIn(BaseModel):
    scope: str = Field(pattern=r"^(user|project)$")
    project_id: int | None = None


class MemorySettingIn(BaseModel):
    enabled: bool


class ToolIn(BaseModel):
    project_id: int | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class TaskIn(BaseModel):
    title: str
    project_id: int | None = None
    chat_id: int | None = None
    mode: str = "build"
    role: str = "parent"


class AutomationIn(BaseModel):
    name: str
    schedule: str = "manual"
    action: dict[str, Any] = Field(default_factory=dict)


class AutomationPatch(BaseModel):
    name: str | None = None
    schedule: str | None = None
    action: dict[str, Any] | None = None
    enabled: bool | None = None


class AutomationAssistIn(BaseModel):
    project_id: int
    request: str = Field(min_length=3, max_length=4000)


class SkillIn(BaseModel):
    name: str
    description: str = ""
    workflow: dict[str, Any] = Field(default_factory=dict)


class ModelSelectIn(BaseModel):
    model: str


def _safe_project_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned[:80].strip(" .") or "Новый проект"
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³")
    if cleaned.partition(".")[0].upper() in reserved:
        cleaned = "Проект " + cleaned
    return cleaned


def project_directory() -> Path:
    # у кого проекты уже лежат в старой папке, там и остаются
    legacy = Path.home() / "Documents" / "Koddi Projects"
    return legacy if legacy.is_dir() else Path.home() / "Documents" / "Dokky Projects"


async def until_disconnect(request: Request, coroutine):
    operation = asyncio.create_task(coroutine)
    try:
        while not operation.done():
            await asyncio.wait({operation}, timeout=0.2)
            if await request.is_disconnected():
                raise asyncio.CancelledError
        return await operation
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


MAX_INSTRUCTIONS = 12000
INSPECTOR_CARDS = frozenset({"environment", "context", "diff", "files", "map", "quality", "browser", "plan"})


def _autostart_enabled() -> bool:
    from koddi.desktop import autostart

    try:
        return autostart.enabled()
    except OSError:
        return False


def _saved_render_mode() -> str:
    from koddi.desktop import saved_render_mode

    return saved_render_mode()


class UiStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app() -> FastAPI:
    init_db()
    from koddi.agents.recovery import recover_interrupted_runs, running_here

    recover_interrupted_runs(agent_runs.active)

    @asynccontextmanager
    async def lifespan(app):
        from koddi.dokky.monitor import watch_account

        monitor = asyncio.create_task(watch_account(), name="dokky-account-monitor")
        scheduler = asyncio.create_task(run_scheduler(launch_automation_agent), name="automation-scheduler")
        request_queue.recover()
        await remote_control.start()
        from koddi.updates import updater

        await updater.start()
        loop = asyncio.get_running_loop()
        from koddi.browser import set_notifier

        def browser_notice(event: str, data: dict[str, Any]) -> None:
            # вызывается из потока браузера
            if not loop.is_closed():
                asyncio.run_coroutine_threadsafe(event_bus.publish(event, data), loop)

        set_notifier(browser_notice)
        from koddi import projectmap

        projectmap.enable_background(browser_notice)
        try:
            yield
        finally:
            set_notifier(None)
            await asyncio.to_thread(projectmap.shutdown)
            await updater.stop()
            monitor.cancel()
            scheduler.cancel()
            await asyncio.gather(monitor, scheduler, return_exceptions=True)
            from koddi.automations.service import cancel_all

            cancel_all()
            await request_queue.shutdown()
            await agent_runs.shutdown()
            # после остановки ходов: конец хода успевает уйти на телефон
            await remote_control.shutdown()
            from koddi.browser.service import browser_close_all

            await asyncio.to_thread(browser_close_all)

    app = FastAPI(title="Докки API", version=__version__, lifespan=lifespan)
    from koddi.desktop.transport import DesktopTransportGuard

    app.add_middleware(DesktopTransportGuard)
    from koddi.dokky.router import router as dokky_router

    app.include_router(dokky_router)
    from koddi.plugins.api import router as plugins_router

    app.include_router(plugins_router)

    @app.middleware("http")
    async def local_chat_access(request: Request, call_next):
        match = re.match(r"^/api/chats/([0-9]+)(?:/|$)", request.url.path)
        if match:
            try:
                require_chat(int(match[1]))
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "app": "Dokky"}

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(event_bus.subscribe(), media_type="text/event-stream")

    @app.websocket("/api/events/ws")
    async def events_websocket(socket: WebSocket) -> None:
        await socket.accept()
        try:
            async for payload in event_bus.subscribe_payloads():
                await socket.send_json(payload)
        except WebSocketDisconnect:
            return

    @app.get("/api/licenses")
    def licenses_get() -> dict:
        from koddi.licenses import catalog

        value = catalog()
        return {**value, "components": [{**item, "notices": [{key: entry[key] for key in ("id", "name")} for entry in item["notices"]]} for item in value["components"]]}

    @app.get("/api/licenses/{component_id}/{notice_id}")
    def license_notice(component_id: str, notice_id: str) -> dict:
        from koddi.licenses import notice

        return notice(component_id, notice_id)

    @app.get("/api/computer/status")
    def computer_status() -> dict:
        local_owner()
        from koddi.computer_use.service import status

        return status()

    @app.put("/api/computer/consent")
    def computer_consent(payload: dict) -> dict:
        from koddi.computer_use.service import set_consent, status

        if set(payload) != {"enabled"} or type(payload["enabled"]) is not bool:
            raise HTTPException(422, "Ожидалось enabled: true или false")
        set_consent(payload["enabled"])
        return status()

    @app.post("/api/computer/restart-elevated")
    def computer_restart_elevated() -> dict:
        local_owner()
        from koddi.computer_use.service import restart_elevated

        try:
            restart_elevated()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/chats/{chat_id}/metrics")
    def chat_metrics(chat_id: int) -> dict:
        require_chat(chat_id)
        with db() as conn:
            rows = conn.execute("SELECT m.turn_id,m.metrics_json,t.status AS runtime_status FROM turn_metrics m JOIN turns t ON t.id=m.turn_id WHERE t.chat_id=? ORDER BY m.turn_id DESC LIMIT 100", (chat_id,)).fetchall()
        return {"turns": [{"turn_id": row["turn_id"], "runtime_status": row["runtime_status"], **json.loads(row["metrics_json"])} for row in rows]}

    @app.get("/api/settings")
    def settings_get(project_id: int | None = None) -> dict[str, Any]:
        from koddi.policy.grants import permission_profile, private_network_allowed
        from koddi.policy.preferences import reasoning_mode

        return {
            "theme": get_setting("theme", "dark"),
            "locale": get_setting("locale", "ru"),
            "permission_profile": permission_profile(project_id),
            "private_network": private_network_allowed(project_id),
            "selected_model": get_setting("selected_model", settings.default_model),
            "context_budget": get_setting("context_budget", "medium"),
            "left_panel_collapsed": get_setting("left_panel_collapsed", False),
            "right_panel_collapsed": get_setting("right_panel_collapsed", False),
            "inspector_order": get_setting("inspector_order", []),
            "ui_motion": get_setting("ui_motion", "system"),
            "chat_width": get_setting("chat_width", "standard"),
            "text_size": get_setting("text_size", 14),
            "ui_surface": get_setting("ui_surface", "classic"),
            "render_mode": _saved_render_mode(),
            "code_font": get_setting("code_font", "system"),
            "ui_font": get_setting("ui_font", "inter"),
            "dev_instructions": get_setting("dev_instructions", ""),
            "launch_at_login": _autostart_enabled(),
            "enter_sends": get_setting("enter_sends", True),
            "coding_reasoning_mode": reasoning_mode(),
            "coding_runtime_extensions": get_setting("coding_runtime_extensions:" + str(get_setting("dokky_user", {}).get("id") or get_setting("dokky_user", {}).get("user_id") or ""), False),
            "coding_keepalive": get_setting("coding_keepalive:" + str(get_setting("dokky_user", {}).get("id") or get_setting("dokky_user", {}).get("user_id") or "__test__"), True),
        }

    @app.put("/api/settings")
    def settings_put(payload: dict[str, Any]) -> dict[str, Any]:
        project_id = payload.get("project_id")
        if project_id is not None and (type(project_id) is not int or not get_project(project_id)):
            raise HTTPException(422, "Выберите существующий проект")
        if payload.get("permission_profile") == "allow-all":
            payload = dict(payload, permission_profile="full")
        if "permission_profile" in payload:
            profile = payload["permission_profile"]
            if not isinstance(profile, str) or profile not in {"ask", "trusted", "full", "read-only"}:
                raise HTTPException(422, "Неизвестный режим подтверждений")
            if profile == "read-only" and project_id is not None:
                raise HTTPException(422, "Режим только чтения задаётся для аккаунта")
            if profile == "full" and payload.get("confirm_full_access") is not True:
                raise HTTPException(422, "Режим «Разрешить всё» требует подтверждения")
        choices = {
            "permission_profile": {"ask", "trusted", "full", "read-only"},
            "ui_motion": {"system", "full", "reduced"},
            "chat_width": {"compact", "standard", "wide"},
            "ui_surface": {"classic", "chat-dark", "side-dark"},
            "render_mode": {"gpu", "software"},
            "ui_font": {"inter", "system"},
            "theme": {"dark", "light", "system"},
            "coding_reasoning_mode": {"light", "pro", "max"},
        }
        if "code_font" in payload and (
            not isinstance(payload["code_font"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._,-]{0,79}", payload["code_font"].strip())
        ):
            raise HTTPException(422, "Название шрифта: до 80 символов, буквы, цифры и пробелы")
        if "dev_instructions" in payload and (
            not isinstance(payload["dev_instructions"], str) or len(payload["dev_instructions"]) > MAX_INSTRUCTIONS
        ):
            raise HTTPException(422, f"Личные инструкции: не больше {MAX_INSTRUCTIONS} символов")
        if "launch_at_login" in payload and type(payload["launch_at_login"]) is not bool:
            raise HTTPException(422, "Ожидалось логическое значение")
        for key, values in choices.items():
            if key in payload and (not isinstance(payload[key], str) or payload[key] not in values):
                raise HTTPException(422, "Недопустимое значение настройки")
        if "text_size" in payload and (
            type(payload["text_size"]) is not int or not 8 <= payload["text_size"] <= 20
        ):
            raise HTTPException(422, "Размер текста должен быть от 8 до 20")
        scoped = {"permission_profile", "private_network", "coding_keepalive", "coding_runtime_extensions", "coding_reasoning_mode"}
        owner = local_owner() if scoped.intersection(payload) else None
        if "render_mode" in payload:
            from koddi.desktop import save_render_mode

            save_render_mode(payload["render_mode"])
        if "launch_at_login" in payload:
            from koddi.desktop import autostart

            try:
                autostart.set_enabled(payload["launch_at_login"])
            except (ValueError, OSError) as exc:
                raise HTTPException(409, str(exc) or "Не удалось изменить автозапуск") from exc
        if "enter_sends" in payload and type(payload["enter_sends"]) is not bool:
            raise HTTPException(422, "Ожидалось логическое значение")
        if "inspector_order" in payload:
            order = payload["inspector_order"]
            # порядок блоков правой панели: только известные блоки и без повторов
            if (
                not isinstance(order, list) or len(order) > len(INSPECTOR_CARDS)
                or any(not isinstance(item, str) or item not in INSPECTOR_CARDS for item in order)
                or len(set(order)) != len(order)
            ):
                raise HTTPException(422, "Неизвестный порядок блоков панели")
        if "coding_keepalive" in payload:
            if type(payload["coding_keepalive"]) is not bool:
                raise HTTPException(422, "Ожидалось логическое значение")
        if "coding_runtime_extensions" in payload and type(payload["coding_runtime_extensions"]) is not bool:
            raise HTTPException(422, "Ожидалось логическое значение")
        if 'private_network' in payload and (type(payload['private_network']) is not bool or project_id is None):
            raise HTTPException(422, 'Доступ к локальной сети разрешается отдельно для проекта')
        if "coding_keepalive" in payload:
            set_setting("coding_keepalive:" + owner, payload["coding_keepalive"])
        if "coding_runtime_extensions" in payload:
            set_setting("coding_runtime_extensions:" + owner, payload["coding_runtime_extensions"])
        if "permission_profile" in payload and project_id is not None:
            with db() as conn:
                conn.execute(
                    "INSERT INTO project_grants(owner_id,project_id,automatic,private_network,approval_profile) VALUES (?,?,?,1,?) "
                    "ON CONFLICT(owner_id,project_id) DO UPDATE SET automatic=excluded.automatic,approval_profile=excluded.approval_profile",
                    (owner, project_id, {"trusted": 1, "full": 2}.get(payload["permission_profile"], 0), "allow-all" if payload["permission_profile"] == "full" else payload["permission_profile"]),
                )
        if 'private_network' in payload:
            from koddi.policy.grants import permission_profile

            inherited = permission_profile(project_id, owner=owner)
            profile = inherited if inherited in {"ask", "trusted", "full"} else "ask"
            with db() as conn:
                conn.execute(
                    'INSERT INTO project_grants(owner_id,project_id,private_network,automatic,approval_profile) VALUES (?,?,?,?,?) '
                    'ON CONFLICT(owner_id,project_id) DO UPDATE SET private_network=excluded.private_network',
                    (owner, project_id, int(payload['private_network']), {"trusted": 1, "full": 2}.get(profile, 0), "allow-all" if profile == "full" else profile),
                )
        if "permission_profile" in payload:
            set_setting("permission_profile:" + owner, payload["permission_profile"])
        if "coding_reasoning_mode" in payload:
            from koddi.policy.preferences import save_reasoning_mode

            save_reasoning_mode(payload["coding_reasoning_mode"], owner=owner)
        for key in (
            "theme",
            "locale",
            "selected_model",
            "context_budget",
            "left_panel_collapsed",
            "right_panel_collapsed",
            "ui_motion",
            "chat_width",
            "text_size",
            "ui_surface",
            "code_font",
            "ui_font",
            "dev_instructions",
            "enter_sends",
            "inspector_order",
        ):
            if key in payload:
                set_setting(key, payload[key].strip() if key in {"code_font", "dev_instructions"} else payload[key])
        # личные инструкции не пишутся в журнал целиком
        safe = {k: ("<текст>" if k == "dev_instructions" else v) for k, v in payload.items()}
        log_event("INFO", "settings.update", "settings updated", safe)
        if owner is not None and local_owner() != owner:
            raise HTTPException(409, "Аккаунт изменился; повторите изменение настроек")
        return settings_get(project_id)

    @app.get("/api/projects")
    def projects_get() -> list[dict]:
        return list_projects(include_status=False)

    @app.get("/api/projects/{project_id}/status")
    def project_status_get(project_id: int) -> dict:
        from koddi.projects.service import project_status

        project = get_project(project_id)
        if not project:
            raise HTTPException(404, "Проект не найден")
        return project_status(Path(project["path"]))

    @app.get('/api/projects/{project_id}/write-recovery')
    def write_recovery_get(project_id: int) -> list[dict]:
        from koddi.changes.recovery import inspect_pending

        return inspect_pending(project_id)

    @app.post('/api/projects/{project_id}/write-recovery/{operation_id}')
    def write_recovery_restore(project_id: int, operation_id: str, payload: dict) -> dict:
        from koddi.changes.recovery import inspect_pending, restore, finish, WriteRecoveryError

        if payload.get('confirm') is not True:
            raise HTTPException(422,'Нужно явно подтвердить восстановление')
        if operation_id not in {item['id'] for item in inspect_pending(project_id)}:
            raise HTTPException(404,'Операция не найдена')
        try:
            if payload.get('action')=='keep':
                return finish(operation_id)
            return restore(operation_id,expected_hashes=payload.get('expected_hashes'),allow_conflicts=False)
        except WriteRecoveryError as exc:
            raise HTTPException(409,str(exc)) from exc

    @app.delete("/api/projects/{project_id}")
    def project_remove(project_id: int) -> dict:
        with db() as conn:
            if conn.execute(
                "SELECT 1 FROM turns t JOIN chats c ON c.id=t.chat_id WHERE c.project_id=? AND t.status='running'",
                (project_id,),
            ).fetchone():
                raise HTTPException(409, "Сначала остановите агента в проекте")
            conn.execute("UPDATE chats SET project_id=NULL WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        from koddi.projectmap import forget

        forget(project_id)
        return {"ok": True, "files_deleted": False}

    def _map_project(project_id: int) -> tuple[dict, Path]:
        project = get_project(project_id)
        if not project:
            raise HTTPException(404, "Проект не найден")
        root = Path(project["path"])
        if not root.is_dir():
            raise HTTPException(409, "Папка проекта недоступна")
        return project, root

    @app.get("/api/projects/{project_id}/map")
    def project_map_get(project_id: int) -> dict:
        from koddi.projectmap import MapError
        from koddi.projectmap.service import ui_tree

        _, root = _map_project(project_id)
        try:
            return ui_tree(project_id, root, local_owner())
        except MapError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/projects/{project_id}/map/state")
    def project_map_state(project_id: int) -> dict:
        from koddi.projectmap import schedule
        from koddi.projectmap.service import state, watched_mode

        _, root = _map_project(project_id)
        # открытие проекта в окне: первая сборка или проверка изменений идёт в фоне
        schedule(project_id, root)
        return {**state(project_id), "watch": watched_mode(project_id)}

    @app.post("/api/projects/{project_id}/map/rescan")
    def project_map_rescan(project_id: int) -> dict:
        from koddi.projectmap import MapError, mark_dirty
        from koddi.projectmap.service import ensure

        _, root = _map_project(project_id)
        mark_dirty(project_id)
        try:
            return ensure(project_id, root, wait=30.0)
        except MapError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/projects/{project_id}/map/notes/{note_id}")
    def project_map_note_delete(project_id: int, note_id: int) -> dict:
        from koddi.projectmap import MapError
        from koddi.projectmap.service import delete_note

        _map_project(project_id)
        try:
            return delete_note(project_id, local_owner(), note_id, source="user")
        except MapError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/projects/{project_id}/knowledge")
    def project_knowledge_get(project_id: int) -> dict:
        from koddi.knowledge.service import folder, location
        from koddi.projectmap.service import ensure, graph as map_graph

        _, root = _map_project(project_id)
        ensure(project_id, root, wait=3.0)
        value = map_graph(project_id, local_owner())
        from koddi.knowledge.service import _view

        where = location(project_id)
        return {"location": where, "folder": str(folder(project_id, local_owner(), root, where)) if where != "off" else None,
                "notes": [_view(value, note, limit=4000) for note in value.notes]}

    @app.post("/api/projects/{project_id}/knowledge/settings")
    def project_knowledge_settings(project_id: int, payload: KnowledgeSettingsIn) -> dict:
        from koddi.knowledge.service import KnowledgeError, export_sync, set_location

        _, root = _map_project(project_id)
        try:
            set_location(project_id, payload.location)
            return export_sync(project_id, local_owner(), root)
        except (KnowledgeError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/projects/{project_id}/knowledge/sync")
    def project_knowledge_sync(project_id: int) -> dict:
        from koddi.knowledge.service import KnowledgeError, export_sync

        _, root = _map_project(project_id)
        try:
            return export_sync(project_id, local_owner(), root)
        except (KnowledgeError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.put("/api/projects/{project_id}/knowledge/{note_id}")
    def project_knowledge_put(project_id: int, note_id: int, payload: KnowledgeNoteIn) -> dict:
        from koddi.knowledge.service import sync_after_change
        from koddi.projectmap import MapError
        from koddi.projectmap.service import save_note

        _, root = _map_project(project_id)
        try:
            # правка человека: агент её больше не перезаписывает
            result = save_note(project_id, local_owner(), kind=payload.kind, title=payload.title, body=payload.body,
                               targets=payload.targets, note=note_id, source="user", confidence="high",
                               basis="edited by the user", root=root)
        except MapError as exc:
            raise HTTPException(422, str(exc)) from exc
        sync_after_change(project_id, local_owner(), root)
        return result

    @app.get("/api/projects/{project_id}/qa-commands")
    def project_qa_commands(project_id: int) -> dict:
        from koddi.quality.checks import user_checks

        _map_project(project_id)
        return {"commands": user_checks(project_id)}

    @app.put("/api/projects/{project_id}/qa-commands")
    def project_qa_commands_put(project_id: int, payload: QaCommandsIn) -> dict:
        from koddi.quality.checks import set_user_checks

        _map_project(project_id)
        try:
            return {"commands": set_user_checks(project_id, [item.model_dump() for item in payload.commands])}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/projects/{project_id}/quality/runs")
    def project_quality_runs(project_id: int, gate: str | None = None, limit: int = 30) -> dict:
        from koddi.quality.contract import latest

        _map_project(project_id)
        return {"runs": latest(project_id, local_owner(), gate=gate, limit=limit)}

    @app.get("/api/projects/{project_id}/quality/runs/{run_id}")
    def project_quality_report(project_id: int, run_id: int) -> dict:
        from koddi.quality.contract import report

        _map_project(project_id)
        found = report(run_id, local_owner(), project_id=project_id)
        if found is None:
            raise HTTPException(404, "Отчёт не найден")
        return found

    @app.post("/api/projects/{project_id}/quality/run")
    def project_quality_run(project_id: int, payload: QualityRunIn) -> dict:
        _, root = _map_project(project_id)
        args: dict[str, Any] = {"scope": payload.scope}
        if payload.gates:
            args["gates"] = payload.gates
        if payload.paths:
            args["paths"] = payload.paths
        if payload.coverage:
            args["coverage"] = payload.coverage
        if payload.max_mutants is not None:
            args["max_mutants"] = payload.max_mutants
        # запуск кнопкой - явное согласие человека, как подтверждение действия агента
        result = ToolRunner(root, project_id).run("quality.run", args, approved=True)
        if result.get("blocked"):
            raise HTTPException(403, str(result.get("reason") or "действие запрещено профилем доступа"))
        return result

    @app.post("/api/projects/{project_id}/quality/decision")
    def project_quality_decision(project_id: int, payload: QualityDecisionIn) -> dict:
        from koddi.quality.metrics import set_decision

        _map_project(project_id)
        set_decision(project_id, local_owner(), payload.gate, payload.key, payload.decision)
        log_event("INFO", "quality.decision", payload.gate,
                  {"decision": payload.decision, "key": payload.key[:200], "reason": payload.reason[:200]})
        return {"ok": True, "gate": payload.gate, "key": payload.key, "decision": payload.decision}

    @app.get("/api/projects/{project_id}/quality/heatmap")
    def project_quality_heatmap(project_id: int) -> dict:
        from koddi.quality.architecture import heatmap

        _map_project(project_id)
        return {"files": heatmap(project_id, local_owner())}

    @app.get("/api/projects/{project_id}/architecture")
    def project_architecture(project_id: int) -> dict:
        from koddi.quality.architecture import PROPOSAL_LABEL, get_policy, proposals

        _map_project(project_id)
        return {"policy": get_policy(project_id), "proposals": proposals(project_id, local_owner()),
                "proposal_label": PROPOSAL_LABEL}

    @app.put("/api/projects/{project_id}/architecture/policy")
    def project_architecture_policy(project_id: int, payload: ArchitecturePolicyIn) -> dict:
        from koddi.quality.architecture import set_policy

        _map_project(project_id)
        try:
            policy = set_policy(project_id, payload.policy)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        log_event("INFO", "architecture.policy", "policy updated" if policy else "policy removed",
                  {"project_id": project_id, "layers": len((policy or {}).get("layers", []))})
        return {"policy": policy}

    def _architecture_graph(project_id: int, root: Path):
        from koddi.projectmap import MapError
        from koddi.projectmap.service import ensure, graph

        try:
            ensure(project_id, root, wait=20.0)
            return graph(project_id, local_owner())
        except MapError as exc:
            raise HTTPException(409, str(exc)) from exc

    def _reader(root: Path):
        from koddi.policy.paths import WorkspacePaths

        access = WorkspacePaths(root)

        def read(path: str) -> str | None:
            target = root / path
            if not access.accessible(target) or not target.is_file():
                return None
            return target.read_text(encoding="utf-8", errors="replace")

        return read

    @app.post("/api/projects/{project_id}/architecture/suggest")
    def project_architecture_suggest(project_id: int) -> dict:
        from koddi.quality.architecture import PROPOSAL_LABEL, evaluate_proposal, suggest_layers

        _, root = _map_project(project_id)
        graph = _architecture_graph(project_id, root)
        suggestion = suggest_layers(graph)
        evaluation = evaluate_proposal(graph, {"layers": suggestion["layers"]}, read=_reader(root)) \
            if len(suggestion["layers"]) >= 2 else None
        return {**suggestion, "label": PROPOSAL_LABEL, "evaluation": evaluation}

    @app.post("/api/projects/{project_id}/architecture/proposals/{proposal_id}/evaluate")
    def project_architecture_evaluate(project_id: int, proposal_id: int) -> dict:
        from koddi.quality.architecture import evaluate_proposal, proposal

        _, root = _map_project(project_id)
        found = proposal(project_id, local_owner(), proposal_id)
        if found is None:
            raise HTTPException(404, "Предложение не найдено")
        return evaluate_proposal(_architecture_graph(project_id, root), found["policy"], read=_reader(root))

    @app.post("/api/projects/{project_id}/architecture/proposals/{proposal_id}/apply")
    def project_architecture_apply(project_id: int, proposal_id: int) -> dict:
        from koddi.quality.architecture import proposal, set_policy

        _map_project(project_id)
        found = proposal(project_id, local_owner(), proposal_id)
        if found is None:
            raise HTTPException(404, "Предложение не найдено")
        policy = set_policy(project_id, found["policy"])
        log_event("INFO", "architecture.policy", "proposal applied by the user", {"project_id": project_id,
                                                                                 "proposal": proposal_id})
        return {"policy": policy}

    @app.delete("/api/projects/{project_id}/architecture/proposals/{proposal_id}")
    def project_architecture_proposal_delete(project_id: int, proposal_id: int) -> dict:
        from koddi.quality.architecture import delete_proposal

        _map_project(project_id)
        if not delete_proposal(project_id, local_owner(), proposal_id):
            raise HTTPException(404, "Предложение не найдено")
        return {"ok": True}

    @app.get("/api/settings/quality-budget")
    def quality_budget_get() -> dict:
        from koddi.quality.budgets import BUDGETS, mode

        return {"mode": mode(), "levels": {name: {key: value for key, value in item.items() if key != "blocking"}
                                           for name, item in BUDGETS.items()}}

    @app.post("/api/settings/quality-budget")
    def quality_budget_set(payload: QualityBudgetIn) -> dict:
        from koddi.quality.budgets import set_mode

        return {"mode": set_mode(payload.mode)}

    @app.post("/api/projects/{project_id}/worktrees/cleanup")
    def project_worktrees_cleanup(project_id: int, payload: WorktreeCleanupIn) -> dict:
        from koddi.changes.writer import active_keys
        from koddi.projects.gitsafe import cleanup

        _, root = _map_project(project_id)
        if not (root / ".git").exists():
            raise HTTPException(409, "Проект не в git")
        result = cleanup(project_id, root, dry_run=payload.dry_run, active_paths=active_keys())
        if not payload.dry_run:
            log_event("INFO", "worktree.cleanup", str(project_id),
                      {"removed": len(result["removed"]), "kept": len(result["kept"])})
        return result

    @app.get("/api/chats/{chat_id}/engineering")
    def chat_engineering(chat_id: int, limit: int = 50) -> dict:
        from koddi.quality import workflow

        require_chat(chat_id)
        turn_id, state = workflow.latest(chat_id)
        quality = state.get("quality") or {}
        return {"turn_id": turn_id, "workflow": workflow.summary(state) if state else {},
                "quality": {gate: {key: item.get(key) for key in ("status", "run_id", "summary", "top")}
                            for gate, item in quality.items()},
                "decisions": workflow.decisions(chat_id, limit=limit)}

    @app.get("/api/settings/ai-review")
    def ai_review_get() -> dict:
        from koddi.quality.reviewer import policy

        return {"mode": policy()}

    @app.post("/api/settings/ai-review")
    def ai_review_set(payload: AiReviewIn) -> dict:
        set_setting("ai_review", payload.mode)
        return {"mode": payload.mode}

    def _owned_turn(turn_id: int) -> dict:
        with db() as conn:
            row = conn.execute("SELECT t.id,t.chat_id,c.owner_id FROM turns t JOIN chats c ON c.id=t.chat_id WHERE t.id=?", (turn_id,)).fetchone()
        if row is None or not owns(row["owner_id"], local_owner()):
            raise HTTPException(404, "Ход не найден")
        return dict(row)

    @app.get("/api/turns/{turn_id}/quality")
    def turn_quality(turn_id: int) -> dict:
        from koddi.quality.gate import STATE_LABELS, load

        _owned_turn(turn_id)
        state = load(turn_id)
        return {"turn_id": turn_id, **state, "label": STATE_LABELS.get(state["state"], state["state"])}

    @app.get("/api/turns/{turn_id}/checkpoint")
    def turn_checkpoint(turn_id: int) -> dict:
        from koddi.changes.checkpoints import preview

        _owned_turn(turn_id)
        try:
            return preview(turn_id, local_owner())
        except (LookupError, ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/turns/{turn_id}/checkpoint/restore")
    def turn_checkpoint_restore(turn_id: int, payload: CheckpointRestoreIn) -> dict:
        from koddi.changes.checkpoints import restore

        turn = _owned_turn(turn_id)
        if agent_runs.active(turn["chat_id"]):
            raise HTTPException(409, "Задача в этом чате ещё выполняется")
        try:
            result = restore(turn_id, local_owner(), payload.paths or None, force=payload.force)
        except (LookupError, ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        log_event("INFO", "changes.checkpoint_restore", "turn restore requested",
                  {key: result.get(key) for key in ("restored", "reason", "change_set_id", "forced")})
        return result

    @app.get("/api/projects/{project_id}/files")
    def project_files(project_id: int, query: str = "") -> dict:
        project = get_project(project_id)
        if not project:
            raise HTTPException(404, "Проект не найден")
        root = Path(project["path"])
        if not root.is_dir():
            raise HTTPException(409, "Папка проекта недоступна")
        result = ToolRunner(root, project_id).run(
            "directory.tree", {"path": ".", "max_depth": 8, "limit": 1500}
        )
        if query:
            result = ToolRunner(root, project_id).run("search.files", {"query": query})
        return result

    @app.get("/api/projects/{project_id}/file")
    def project_file(project_id: int, path: str) -> dict:
        project = get_project(project_id)
        if not project:
            raise HTTPException(404, "Проект не найден")
        result = ToolRunner(Path(project["path"]), project_id).run("file.read", {"path": path})
        if not result.get("ok"):
            raise HTTPException(
                422, result.get("error") or result.get("reason") or "Файл недоступен"
            )
        content = str(result.get("content", ""))
        if result.get("truncated"):
            result["read_only"] = True
        result["hash"] = hashlib.sha256(content.encode()).hexdigest()
        return result

    @app.put("/api/projects/{project_id}/file")
    def project_file_save(project_id: int, payload: dict) -> dict:
        project = get_project(project_id)
        if not project:
            raise HTTPException(404, "Проект не найден")
        from koddi.policy.grants import permission_profile

        if permission_profile(project_id) == "read-only":
            raise HTTPException(403, "Выбран режим только для чтения")
        path, content = payload.get("path"), payload.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise HTTPException(422, "Нужны путь и содержимое файла")
        runner = ToolRunner(Path(project["path"]), project_id, explicit_write_consent=True)
        current = runner.run("file.read", {"path": path})
        if not current.get("ok"):
            raise HTTPException(422, current.get("error") or "Файл недоступен")
        if current.get("truncated"):
            raise HTTPException(422, "Файл слишком большой для этого редактора")
        if hashlib.sha256(str(current.get("content", "")).encode()).hexdigest() != payload.get(
            "hash"
        ):
            raise HTTPException(
                409, "Файл изменился на диске. Откройте его заново перед сохранением"
            )
        result = runner.run(
            "file.write_patch",
            {"path": path, "content": content, "description": "Изменено в редакторе"},
        )
        if not result.get("ok"):
            raise HTTPException(
                422, result.get("error") or result.get("reason") or "Не удалось сохранить"
            )
        return {**result, "hash": hashlib.sha256(content.encode()).hexdigest()}

    @app.post("/api/projects")
    def projects_post(payload: ProjectIn) -> dict:
        project = add_project(payload.path, payload.name)
        log_event("INFO", "project.open", project["path"], {"project_id": project["id"]})
        from koddi.projectmap import schedule

        # карта собирается сразу при открытии, к первому вопросу агенту она обычно уже готова
        schedule(project["id"], Path(project["path"]))
        return project

    @app.post("/api/projects/create-empty")
    def projects_create_empty(payload: EmptyProjectIn) -> dict:
        name = _safe_project_name(payload.name)
        base = project_directory()
        base.mkdir(parents=True, exist_ok=True)
        base = base.resolve()
        index = 1
        while True:
            target = base / (name if index == 1 else f"{name} {index}")
            if target.exists() or target.is_symlink():
                index += 1
                continue
            if not target.resolve().is_relative_to(base):
                raise HTTPException(422, "Недопустимый путь нового проекта")
            try:
                target.mkdir(exist_ok=False)
                break
            except FileExistsError:
                index += 1
        project = add_project(str(target), target.name)
        log_event("INFO", "project.create_empty", project["path"], {"project_id": project["id"]})
        return project

    @app.post("/api/workspaces/create")
    def workspace_create(payload: ProjectIn) -> dict:
        return add_project(payload.path, payload.name)

    @app.get("/api/worktrees")
    def worktrees_get(project_id: int | None = None) -> list[dict]:
        with db() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM worktrees WHERE project_id=? ORDER BY id DESC", (project_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM worktrees ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]

    @app.get("/api/ai/config")
    def ai_config_get() -> dict:
        return remote_config()

    @app.post("/api/ai/test-enable")
    def enable_test_dokky(request: Request) -> dict:
        if os.getenv("KODDI_INTERNAL_TEST") != "1":
            raise HTTPException(
                403, "Тестовое подключение доступно только во внутреннем тестовом режиме"
            )
        from koddi.remote.mock_server import MODEL_ID, NOTICE

        config = save_remote_config(
            {
                "provider": "dokky-test",
                "base_url": f"http://127.0.0.1:{request.url.port or settings.port}/api/test-dokky/v1",
                "model": MODEL_ID,
                "models": [{"id": MODEL_ID, "name": "Докки-тест", "test_only": True}],
                "context_tokens": 32768,
            }
        )
        return {"ok": True, "config": config, "notice": NOTICE}

    @app.get("/api/modes")
    def modes_get() -> list[dict]:
        return list_modes()

    @app.put("/api/modes/{mode_id}")
    def modes_put(mode_id: str, payload: dict) -> dict:
        try:
            return save_mode({**payload, "id": mode_id})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/modes/{mode_id}")
    def modes_delete(mode_id: str) -> dict:
        set_setting(
            "custom_modes", [m for m in get_setting("custom_modes", []) if m["id"] != mode_id]
        )
        return {"ok": True}

    @app.put("/api/ai/config")
    def ai_config_put(payload: dict) -> dict:
        try:
            return save_remote_config(payload)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/ai/discover")
    async def ai_discover() -> dict:
        try:
            models = await RemoteAIClient().models()
            save_remote_config({"models": models})
            return {"ok": True, "models": models}
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/models/options")
    def models_options() -> list[dict]:
        config = remote_config()
        models = config["models"] or ([{"id": config["model"]}] if config["model"] else [])
        return [
            {
                "tag": item["id"],
                "name": item.get("name", item["id"]),
                "source": "test" if config.get("provider") == "dokky-test" else "remote",
                "context_tokens": item.get("context_tokens", config["context_tokens"]),
                # окно контекста у каждого режима своё: Light, Pro и Max сжимаются на разных порогах
                "reasoning_modes": [
                    {"id": mode["id"], "name": mode.get("name", mode["id"]), "compact_at_tokens": mode["compact_at_tokens"]}
                    for mode in (item.get("reasoning_modes") or [])
                    if isinstance(mode, dict)
                    and isinstance(mode.get("id"), str)
                    and type(mode.get("compact_at_tokens")) is int
                    and mode["compact_at_tokens"] >= 4096
                ],
            }
            for item in models
        ]

    @app.post("/api/models/select")
    def models_select(payload: ModelSelectIn) -> dict:
        if payload.model not in {m["tag"] for m in models_options()}:
            raise HTTPException(422, "Модель не найдена в настройках AI-сервера")
        set_setting("selected_model", payload.model)
        return {"ok": True, "model": payload.model}

    @app.get("/api/agent/requests")
    def agent_requests_get() -> list[dict]:
        owner = local_owner()
        with db() as conn:
            rows = conn.execute(
                "SELECT r.*,t.chat_id,c.owner_id FROM agent_requests r JOIN turns t ON t.id=r.turn_id JOIN chats c ON c.id=t.chat_id WHERE r.response_json IS NULL AND t.status='running' ORDER BY r.id"
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows if owns(row["owner_id"], owner)]

    @app.post("/api/agent/requests/{request_id}/respond")
    def agent_requests_respond(request_id: int, payload: dict) -> dict:
        return respond_request(request_id, payload)

    def respond_request(request_id: int, payload: dict) -> dict:
        """Ответ на запрос агента: кнопки окна и команды с телефона идут одним путём."""
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                "SELECT r.*,t.status,t.chat_id FROM agent_requests r JOIN turns t ON t.id=r.turn_id WHERE r.id=?",
                (request_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Запрос не найден")
            require_chat(row["chat_id"])
            payload = dict(payload)
            # номер удалённого запроса может достаться новому: окно говорит, на какой ход оно отвечает
            expected_turn = payload.pop("turn_id", None)
            if row["response_json"] is not None or row["status"] != "running" or (
                expected_turn is not None and str(expected_turn) != str(row["turn_id"])
            ):
                raise HTTPException(409, "Запрос уже закрыт")
            if row["kind"] == "approval" and not isinstance(payload.get("approved"), bool):
                raise HTTPException(422, "Нужно подтвердить или отклонить действие")
            if row["kind"] == "question":
                from koddi.agents.questions import accept, normalize

                try:
                    payload = accept(normalize(json.loads(row["payload_json"])), payload)
                except ValueError as error:
                    raise HTTPException(422, str(error)) from None
            conn.execute(
                "UPDATE agent_requests SET response_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), request_id),
            )
        remote_control.resolved(request_id, row["kind"], payload)
        return {"ok": True}

    @app.get("/api/chats/{chat_id}/plan")
    def agent_plan_get(chat_id: int) -> dict:
        with db() as conn:
            row = conn.execute(
                "SELECT p.steps_json,t.id,t.status,c.mode,p.document_markdown,p.execution_started_at FROM agent_plans p JOIN turns t ON t.id=p.turn_id JOIN chats c ON c.id=t.chat_id WHERE t.chat_id=? ORDER BY t.id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            latest = conn.execute(
                "SELECT id FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)
            ).fetchone()
            tracking_steps = json.loads(row[0]) if row else []
            tracking_turn_id = row[1] if row else None
            tracking_status = row[2] if row else None
            if row and not row[4] and row[3] != "plan":
                contract = conn.execute(
                    "SELECT p.steps_json,t.id,t.status,c.mode,p.document_markdown,p.execution_started_at FROM agent_plans p JOIN turns t ON t.id=p.turn_id JOIN chats c ON c.id=t.chat_id WHERE t.chat_id=? AND p.document_markdown<>'' ORDER BY t.id DESC LIMIT 1",
                    (chat_id,),
                ).fetchone()
                row = contract or row
            document = row[4] if row else ""
            if row and not document and row[3] == "plan":
                texts = conn.execute(
                    "SELECT content FROM messages WHERE chat_id=? AND role='assistant' AND content<>'' AND id>(SELECT max(id) FROM messages WHERE chat_id=? AND role='user') ORDER BY id",
                    (chat_id, chat_id),
                ).fetchall()
                document = "\n\n".join(item[0] for item in texts)
        return {
            "steps": json.loads(row[0]) if row else [],
            "tracking_steps": tracking_steps,
            "tracking_turn_id": tracking_turn_id,
            "tracking_status": tracking_status,
            "turn_id": row[1] if row else None,
            "document": document,
            "executed": bool(row and row[5]),
            "ready": bool(
                row
                and latest
                and row[1] == latest[0]
                and row[2] == "completed"
                and row[3] == "plan"
                and not row[5]
            ),
        }

    @app.get("/api/chats")
    def chats_get(project_id: int | None = None) -> list[dict]:
        owner = local_owner()
        with db() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM chats WHERE project_id=? ORDER BY updated_at DESC", (project_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
            asking = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT t.chat_id FROM agent_requests r JOIN turns t ON t.id=r.turn_id "
                    "WHERE r.response_json IS NULL AND t.status='running'"
                )
            }
        from koddi.agents.runs import agent_runs

        # состояние для меню чатов: агент работает или ждёт ответа человека
        def activity(chat_id: int) -> str | None:
            if not agent_runs.active(chat_id):
                return None
            return "question" if chat_id in asking else "working"

        return [{**dict(row), "activity": activity(row["id"])} for row in rows if owns(row["owner_id"], owner)]

    @app.post("/api/chats")
    async def chats_post(payload: ChatIn) -> dict:
        await guard_developer()
        # чат без проекта работает в папке «Докки» в документах, в списке он остаётся в «Недавнем»
        project_id = payload.project_id if payload.project_id is not None else home_project()["id"]
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO chats(project_id, title, mode, model, owner_id) VALUES (?, ?, ?, ?, ?)",
                (project_id, payload.title, payload.mode, payload.model, local_owner()),
            )
            chat_id = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if remote_config().get("provider") == "dokky":
            from koddi.dokky.coding import ensure
            from koddi.dokky.client import DokkyError

            try:
                await ensure(chat_id)
            except DokkyError as exc:
                with db() as conn:
                    conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))
                raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc
        remote_control.chat_created(chat_id)
        return dict(row)

    @app.get("/api/chats/{chat_id}/coding-session")
    async def coding_session_get(chat_id: int):
        from koddi.dokky.coding import ensure, public_session
        from koddi.dokky.client import DokkyError

        try:
            value = await ensure(chat_id)
            session = public_session(value["session"])
            # у чатов, где модель ещё не отвечала по этой сессии, точных токенов нет: отдаём оценку по истории
            from koddi.agents.compaction import context_size

            session["estimated_context_tokens"] = context_size(
                value.get("messages") or [], value.get("tools") or [], dokky=True
            )
            return session
        except DokkyError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/chats/{chat_id}/context-breakdown")
    async def context_breakdown_get(chat_id: int):
        from koddi.agents.breakdown import context_breakdown
        from koddi.agents.compaction import context_limit
        from koddi.dokky.client import DokkyError
        from koddi.dokky.context import compact_threshold, route_context
        from koddi.dokky.coding import ensure

        try:
            value = await ensure(chat_id)
        except DokkyError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc
        config = remote_config()
        session = value["session"]
        mode = session.get("pending_reasoning_mode") or session.get("reasoning_mode") or "pro"
        usage = (session.get("last_usage") or {}).get("usage") or {}
        exact = next((int(usage[key]) for key in ("context_tokens", "prompt_tokens", "input_tokens") if isinstance(usage.get(key), int) and usage[key] > 0), None)
        return context_breakdown(
            value.get("messages") or [], value.get("tools") or [],
            window=min(context_limit(config), route_context(value).get("limit_tokens") or context_limit(config)),
            compact_at=compact_threshold(config, mode, value), exact=exact,
        )

    @app.patch("/api/chats/{chat_id}/coding-session")
    async def coding_session_patch(chat_id: int, payload: dict, request: Request):
        from koddi.dokky.coding import switch_mode, public_session
        from koddi.dokky.client import DokkyError

        if agent_runs.active(chat_id):
            raise HTTPException(409, "Нельзя менять режим во время выполнения задачи")
        try:
            owner = local_owner()
            result = await until_disconnect(request, switch_mode(
                chat_id,
                payload.get("reasoning_mode"),
                payload.get("acknowledge_cache_cost") is True,
            ))
            if local_owner() != owner:
                raise HTTPException(409, "Аккаунт изменился; повторите выбор режима")
            from koddi.policy.preferences import save_reasoning_mode

            save_reasoning_mode(payload["reasoning_mode"], owner=owner)
            return public_session(result)
        except DokkyError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc

    @app.get("/api/chats/{chat_id}/coding-session/switch-plan")
    async def coding_switch_plan(chat_id: int, mode: str):
        from koddi.dokky.coding import ensure, MODES
        from koddi.dokky.client import dokky, DokkyError

        if mode not in MODES:
            raise HTTPException(422, "Неизвестный режим")
        try:
            value = await ensure(chat_id)
            return (await dokky.request("GET", f"/coding/sessions/{value['session']['id']}/switch-plan", params={"mode": mode})).json()
        except DokkyError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/chats/{chat_id}/coding-session/compact")
    async def coding_compact(chat_id: int, request: Request):
        from koddi.dokky.context import transition
        from koddi.dokky.coding import load, public_session
        from koddi.dokky.client import DokkyError

        if agent_runs.active(chat_id):
            raise HTTPException(409, "Дождитесь завершения задачи")
        async def collect():
            return [event async for event in transition(chat_id, config=remote_config())]

        try:
            events = await until_disconnect(request, collect())
            return {"session": public_session(load(chat_id)["session"]), "result": events[-1]}
        except DokkyError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/chats/{chat_id}/coding-session/activity")
    def coding_activity(chat_id: int):
        from koddi.dokky.activity import mark

        require_chat(chat_id)
        mark(local_owner(), chat_id)
        return {"ok": True}

    @app.post("/api/chats/{chat_id}/coding-session/warm")
    async def coding_keepalive(chat_id: int):
        from koddi.dokky.context import keepalive
        from koddi.dokky.client import DokkyError

        if agent_runs.active(chat_id):
            return {"warmed": False, "reason": "busy"}
        try:
            return await keepalive(chat_id)
        except DokkyError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc

    @app.patch("/api/chats/{chat_id}")
    def chat_update(chat_id: int, payload: ChatUpdateIn) -> dict:
        if agent_runs.active(chat_id) and "project_id" in payload.model_fields_set:
            raise HTTPException(409, "Нельзя менять папку во время работы агента")
        with db() as conn:
            existing = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="chat not found")
            title = existing["title"]
            if payload.title is not None:
                title = " ".join(payload.title.split())
                if not title:
                    raise HTTPException(status_code=422, detail="chat title cannot be empty")
            mode = payload.mode or existing["mode"]
            model = payload.model or existing["model"]
            project_id = (
                payload.project_id
                if "project_id" in payload.model_fields_set
                else existing["project_id"]
            )
            if project_id is not None and not get_project(project_id):
                raise HTTPException(404, "Проект не найден")
            conn.execute(
                "UPDATE chats SET title=?, mode=?, model=?, project_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (title, mode, model, project_id, chat_id),
            )
            row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if title != existing["title"]:
            remote_control.chat_renamed(chat_id, title)
        return dict(row)

    @app.delete("/api/chats/{chat_id}")
    def chat_delete(chat_id: int) -> dict:
        if agent_runs.active(chat_id):
            raise HTTPException(
                status_code=409, detail="cannot delete a chat while its agent is running"
            )
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="chat not found")
            # дочерний чат делегирования не в реестре запусков, но его ход идёт
            if running_here(conn, chat_id):
                raise HTTPException(status_code=409, detail="cannot delete a chat while its agent is running")
            turn_ids = [
                row["id"]
                for row in conn.execute("SELECT id FROM turns WHERE chat_id=?", (chat_id,))
            ]
            if turn_ids:
                placeholders = ",".join("?" for _ in turn_ids)
                conn.execute(f"DELETE FROM tool_calls WHERE turn_id IN ({placeholders})", turn_ids)
                conn.execute(f"DELETE FROM approvals WHERE turn_id IN ({placeholders})", turn_ids)
            conn.execute("DELETE FROM tasks WHERE chat_id=?", (chat_id,))
            conn.execute("DELETE FROM memory_summaries WHERE chat_id=?", (chat_id,))
            files = [dict(row) for row in conn.execute("SELECT * FROM attachments WHERE chat_id=?", (chat_id,))]
            # текст документов из вложений тоже принадлежит чату: после удаления его не должно найтись поиском
            conn.execute(
                "DELETE FROM document_chunks WHERE document_id IN "
                "(SELECT d.id FROM documents d JOIN attachments a ON a.id=d.attachment_id WHERE a.chat_id=?)",
                (chat_id,),
            )
            conn.execute(
                "DELETE FROM documents WHERE attachment_id IN (SELECT id FROM attachments WHERE chat_id=?)", (chat_id,)
            )
            conn.execute("DELETE FROM attachments WHERE chat_id=?", (chat_id,))
            conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            conn.execute("DELETE FROM turns WHERE chat_id=?", (chat_id,))
            conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        forget_attachment_files(files)
        agent_runs.forget(chat_id)
        remote_control.chat_deleted(chat_id)
        return {"ok": True, "chat_id": chat_id}

    @app.get("/api/chats/{chat_id}/messages")
    def messages_get(chat_id: int, before: int | None = None, limit: int = Query(default=200, ge=1, le=200)) -> list[dict]:
        with db() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE chat_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?", (chat_id,before,before,limit)
            ).fetchall()[::-1]
            needed = {json.loads(row['metadata_json'] or '{}').get('turn_id') for row in rows}
            needed.discard(None)
            placeholders=','.join('?' for _ in needed) or 'NULL'
            turns = {
                row["id"]: dict(row)
                for row in conn.execute(
                    f"SELECT id,status,created_at,completed_at,summary,error_json FROM turns WHERE chat_id=? AND id IN ({placeholders})",
                    (chat_id,*needed),
                )
            }
            for turn in turns.values():
                turn['timeline'] = [dict(event) | {'payload':json.loads(event['payload_json'])} for event in conn.execute(
                    'SELECT id,sequence,kind,payload_json,created_at FROM turn_events WHERE turn_id=? ORDER BY sequence LIMIT 2000', (turn['id'],)
                )]
        result = []
        projected_turns=set()
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            turn = turns.get(item["metadata"].get("turn_id"))
            if turn:
                item['metadata']['turn']={key:value for key,value in turn.items() if key!='timeline'}
                if item['role']=='assistant' and turn['id'] not in projected_turns:
                    item['metadata']['turn']['timeline']=turn['timeline']
                if item['role']=='assistant':
                    projected_turns.add(turn['id'])
            result.append(item)
        return result

    @app.get("/api/chats/{chat_id}/export")
    def chat_export(chat_id: int) -> StreamingResponse:
        with db() as conn:
            chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if not chat:
            raise HTTPException(404, "Чат не найден")
        owner = local_owner()

        def export():
            yield '{"chat":' + json.dumps(dict(chat), ensure_ascii=False) + ',"messages":['
            first = True
            with db() as conn:
                for row in conn.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY id', (chat_id,)):
                    if local_owner() != owner:
                        return
                    item = dict(row)
                    item['metadata'] = json.loads(item.pop('metadata_json') or '{}')
                    yield ('' if first else ',') + json.dumps(item,ensure_ascii=False)
                    first = False
            yield ']}'

        return StreamingResponse(export(), media_type='application/json', headers={
            'Content-Disposition':f'attachment; filename=koddi-chat-{chat_id}.json', 'Cache-Control':'no-store',
        })

    @app.get("/api/chats/{chat_id}/changes")
    def chat_changes(chat_id: int) -> list[dict]:
        with db() as conn:
            changes = [
                dict(row)
                for row in conn.execute(
                    "SELECT c.* FROM change_sets c JOIN turns t ON t.id=c.turn_id WHERE t.chat_id=? ORDER BY c.id DESC LIMIT 100",
                    (chat_id,),
                )
            ]
            return attach_change_files(conn, changes)

    @app.get("/api/chats/{chat_id}/turn/events")
    async def chat_turn_events(chat_id: int, after: int = 0) -> StreamingResponse:
        run = agent_runs.get(chat_id, owner_id=local_owner())
        if not run:
            raise HTTPException(404, "Нет активного потока для этого чата")
        return StreamingResponse(
            run.subscribe(after=max(0, after)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def guard_developer() -> None:
        """Без доступа к Докки Code (Free, пробный период, Base) агент не запускается; чтение чатов это не трогает."""
        if remote_config().get("provider") == "dokky":
            from koddi.dokky.access import require_developer

            await require_developer()

    async def start_chat_turn(
        chat_id: int, payload: MessageIn, *, display_message: str | None = None, queued_request_id: str | None = None,
        origin: str | None = None,
    ) -> StreamingResponse:
        await guard_developer()
        chat = require_chat(chat_id)
        from koddi.dokky.activity import mark

        mark(local_owner(), chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="chat not found")
        if agent_runs.active(chat_id):
            raise HTTPException(status_code=409, detail="Задача уже выполняется")
        try:
            from koddi.skills.service import slash_skill

            # «/плагин:команда текст» в начале сообщения подключает команду или навык плагина, как в Claude Code
            slash = slash_skill(payload.content)
            skills_text, skill_names = skill_block(list(dict.fromkeys([*payload.skill_ids, *([slash] if slash else [])])))
        except SkillError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # содержимое вложений добавляет к запросу оркестратор (attachments/recognize.py)
        bind_attachments(chat_id, payload.attachment_ids)
        normalized_mode = (payload.mode or chat["mode"] or "development").lower().strip()
        if normalized_mode == "build":
            normalized_mode = "development"
        if normalized_mode not in {item["id"] for item in list_modes()}:
            raise HTTPException(status_code=422, detail="Неизвестный режим агента")
        with db() as conn:
            conn.execute(
                "UPDATE chats SET mode=?, model=COALESCE(?, model), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (normalized_mode, payload.model, chat_id),
            )
        if chat["project_id"] is None:
            # чат без проекта из прежних версий получает ту же папку, что новые
            chat = {**dict(chat), "project_id": home_project()["id"]}
            with db() as conn:
                conn.execute("UPDATE chats SET project_id=? WHERE id=? AND project_id IS NULL", (chat["project_id"], chat_id))
        project = get_project(chat["project_id"])
        workspace = Path(project["path"]) if project else None
        if agent_runs.active(chat_id):
            raise HTTPException(status_code=409, detail="agent turn is already running")
        orchestrator = AgentOrchestrator()
        cancel_event = threading.Event()
        run = await agent_runs.start(
            chat_id,
            orchestrator.run(
                chat_id=chat_id,
                project_id=chat["project_id"],
                workspace=workspace,
                user_message=(skills_text + "\n\nЗапрос пользователя:\n" if skills_text else "") + payload.content,
                display_message=display_message if display_message is not None else (("Навыки: " + ", ".join(skill_names) + "\n\n" if skill_names else "") + payload.content),
                attachment_ids=payload.attachment_ids,
                queued_request_id=queued_request_id,
                mode=normalized_mode,
                model=payload.model or chat["model"],
                options=payload.options,
                cancel_event=cancel_event,
                origin=origin,
            ),
            cancel_event=cancel_event,
            owner_id=local_owner(),
        )
        return StreamingResponse(
            run.subscribe(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chats/{chat_id}/messages")
    async def messages_post(chat_id: int, payload: MessageIn) -> StreamingResponse:
        return await start_chat_turn(chat_id, payload)

    async def launch_queued(chat_id: int, payload: dict, key: str) -> str:
        with db() as conn:
            previous=conn.execute('SELECT status FROM turns WHERE chat_id=? AND queued_request_id=?',(chat_id,key)).fetchone()
        if previous:
            return previous['status']
        await start_chat_turn(chat_id, MessageIn(**payload),queued_request_id=key)
        run = agent_runs.get(chat_id, owner_id=local_owner())
        if run and run.task:
            await asyncio.shield(run.task)
        return last_turn_status(chat_id)

    def last_turn_status(chat_id: int) -> str:
        with db() as conn:
            row = conn.execute("SELECT status FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
        return row[0] if row else "failed"

    request_queue.launch = launch_queued
    request_queue.active = agent_runs.active
    request_queue.last_status = last_turn_status

    # память о пользователе и проекте (agents/profile_memory.py): видна и правится в настройках разработки
    def memory_project(project_id: int | None) -> int | None:
        if project_id is not None and not get_project(project_id):
            raise HTTPException(404, "Проект не найден")
        return project_id

    def memory_call(action):
        try:
            return action()
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/memory")
    def memory_get(project_id: int | None = None) -> dict:
        from koddi.agents import profile_memory

        return profile_memory.list_memory(memory_project(project_id))

    @app.post("/api/memory")
    def memory_add(payload: MemoryIn) -> dict:
        from koddi.agents import profile_memory

        project_id = memory_project(payload.project_id)
        return memory_call(lambda: profile_memory.add(payload.scope, project_id, payload.content))

    @app.patch("/api/memory/{memory_id}")
    def memory_edit(memory_id: int, payload: MemoryEditIn, project_id: int | None = None) -> dict:
        from koddi.agents import profile_memory

        return memory_call(lambda: profile_memory.edit(memory_id, payload.content, memory_project(project_id)))

    @app.delete("/api/memory/{memory_id}")
    def memory_delete(memory_id: int, project_id: int | None = None) -> dict:
        from koddi.agents import profile_memory

        return memory_call(lambda: profile_memory.remove(memory_id, memory_project(project_id)))

    @app.post("/api/memory/clear")
    def memory_clear(payload: MemoryClearIn) -> dict:
        from koddi.agents import profile_memory

        project_id = memory_project(payload.project_id)
        return memory_call(lambda: profile_memory.clear(payload.scope, project_id))

    @app.put("/api/memory/settings")
    def memory_settings(payload: MemorySettingIn, project_id: int | None = None) -> dict:
        from koddi.agents import profile_memory

        profile_memory.set_enabled(payload.enabled)
        return profile_memory.list_memory(memory_project(project_id))

    @app.get("/api/chats/{chat_id}/queue")
    def queue_get(chat_id: int) -> dict:
        return request_queue.snapshot(chat_id)

    @app.post("/api/chats/{chat_id}/queue")
    async def queue_add(chat_id: int, payload: QueueIn) -> dict:
        await guard_developer()
        bind_attachments(chat_id, payload.attachment_ids)
        return request_queue.add(chat_id, payload.model_dump(exclude={"request_id"}), payload.request_id)

    @app.patch("/api/chats/{chat_id}/queue/{key}")
    def queue_edit(chat_id: int, key: str, payload: QueueEditIn) -> dict:
        return request_queue.edit(chat_id, key, payload.version, payload.content)

    @app.delete("/api/chats/{chat_id}/queue/{key}")
    def queue_delete(chat_id: int, key: str, version: int) -> dict:
        return request_queue.edit(chat_id, key, version, None)

    @app.post("/api/chats/{chat_id}/queue/resume")
    async def queue_resume(chat_id: int) -> dict:
        await guard_developer()
        return request_queue.resume(chat_id)

    @app.post("/api/chats/{chat_id}/queue/{key}/promote")
    async def queue_promote(chat_id: int, key: str, version: int) -> dict:
        await guard_developer()
        return request_queue.promote(chat_id, key, version)

    @app.post("/api/chats/{chat_id}/plan/execute")
    async def execute_plan(chat_id: int, payload: dict) -> StreamingResponse:
        snapshot = agent_plan_get(chat_id)
        if (
            not snapshot["ready"]
            or type(payload.get("turn_id")) is not int
            or payload["turn_id"] != snapshot["turn_id"]
        ):
            raise HTTPException(409, "План изменился или ещё не готов. Обновите чат")
        with db() as conn:
            chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
            messages = conn.execute(
                "SELECT content FROM messages WHERE chat_id=? AND role='assistant' AND content<>'' AND id>(SELECT max(id) FROM messages WHERE chat_id=? AND role='user') ORDER BY id",
                (chat_id, chat_id),
            ).fetchall()
        project = get_project(chat["project_id"])
        if not project or not Path(project["path"]).is_dir():
            raise HTTPException(409, "Выберите существующую рабочую папку перед выполнением плана")
        text = snapshot["document"] or "\n\n".join(row[0] for row in messages)
        steps = "\n".join(f"- {step['title']}" for step in snapshot["steps"])
        content = (
            "Implement the following user-approved plan in the current workspace. Complete and verify the work.\n\n"
            + text
            + "\n\nPlan steps:\n"
            + steps
        )
        with db() as conn:
            claimed = conn.execute(
                "UPDATE agent_plans SET execution_started_at=CURRENT_TIMESTAMP,document_markdown=CASE WHEN document_markdown='' THEN ? ELSE document_markdown END WHERE turn_id=? AND execution_started_at IS NULL",
                (snapshot["document"], snapshot["turn_id"]),
            ).rowcount
        if not claimed:
            raise HTTPException(409, "Этот план уже принят к выполнению")
        try:
            return await start_chat_turn(
                chat_id,
                MessageIn(content=content, mode="development", model=chat["model"]),
                display_message="Реализуй план",
            )
        except Exception:
            with db() as conn:
                conn.execute(
                    "UPDATE agent_plans SET execution_started_at=NULL WHERE turn_id=?",
                    (snapshot["turn_id"],),
                )
                if not agent_runs.active(chat_id):
                    conn.execute("UPDATE chats SET mode=? WHERE id=?", (chat["mode"], chat_id))
            raise

    @app.post("/api/chats/{chat_id}/turn/cancel")
    async def chat_turn_cancel(chat_id: int) -> dict[str, Any]:
        request_queue.pause(chat_id)
        cancelled = await agent_runs.cancel(chat_id)
        return {"ok": True, "cancelled": cancelled, "chat_id": chat_id}

    async def remote_start_turn(chat_id: int, text: str) -> None:
        chat = require_chat(chat_id)
        await start_chat_turn(chat_id, MessageIn(content=text, mode=chat["mode"] or "development"), origin="remote")

    async def remote_cancel_turn(chat_id: int) -> bool:
        request_queue.pause(chat_id)
        return await agent_runs.cancel(chat_id)

    async def remote_create_chat(project_id: int | None, title: str) -> dict:
        return await chats_post(ChatIn(project_id=project_id, title=title))

    remote_control.bind(Hooks(remote_start_turn, remote_cancel_turn, respond_request, remote_create_chat))

    def remote_guard(function):
        from koddi.remote_control.bridge import LoginRequired

        try:
            return function()
        except LoginRequired as exc:
            raise HTTPException(401, "Войдите с помощью Докки") from exc

    @app.get("/api/remote-control")
    def remote_control_get() -> dict:
        local_owner()
        return remote_control.status()

    @app.put("/api/remote-control")
    async def remote_control_put(payload: dict) -> dict:
        from koddi.remote_control.bridge import LoginRequired

        allowed = {"enabled", "prevent_sleep", "new_session_project_id", "share_new"}
        if not payload or set(payload) - allowed:
            raise HTTPException(422, "Неизвестные поля настройки")
        local_owner()
        try:
            return await remote_control.configure(payload)
        except LoginRequired as exc:
            raise HTTPException(401, "Войдите с помощью Докки") from exc

    @app.post("/api/remote-control/reconnect")
    async def remote_control_reconnect() -> dict:
        local_owner()
        return await remote_control.reconnect()

    @app.put("/api/chats/{chat_id}/remote")
    def chat_remote_put(chat_id: int, payload: dict) -> dict:
        if set(payload) != {"shared"} or not isinstance(payload["shared"], bool):
            raise HTTPException(422, "shared: true или false")
        return remote_guard(lambda: remote_control.set_shared(chat_id, payload["shared"]))

    @app.get("/api/chats/{chat_id}/turn")
    def chat_turn_get(chat_id: int) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)
            ).fetchone()
        return {"active": agent_runs.active(chat_id), "turn": dict(row) if row else None}

    @app.get("/api/chats/{chat_id}/messages/{message_id}/rewind")
    def message_rewind_preview(chat_id: int, message_id: int) -> dict:
        from koddi.agents.rewind import preview

        return preview(chat_id, message_id)

    @app.post("/api/chats/{chat_id}/messages/{message_id}/rewind")
    def message_rewind(chat_id: int, message_id: int, payload: dict) -> dict:
        from koddi.agents.rewind import rewind

        if "files" in payload and type(payload["files"]) is not bool:
            raise HTTPException(422, "files: true или false")
        result = rewind(chat_id, message_id, files=payload.get("files") is True)
        return result

    @app.put("/api/chats/{chat_id}/messages/{message_id}")
    def message_update(chat_id: int, message_id: int, payload: dict[str, str]) -> dict:
        require_chat(chat_id)
        with db() as conn:
            has_context = conn.execute("SELECT 1 FROM agent_history WHERE chat_id=? LIMIT 1", (chat_id,)).fetchone()
        if has_context or agent_runs.active(chat_id):
            raise HTTPException(409, "История уже использована агентом. Создайте новую ветку или отправьте уточнение")
        content = str(payload.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=422, detail="message content is required")
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id=? AND chat_id=?", (message_id, chat_id)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="message not found")
            conn.execute("UPDATE messages SET content=? WHERE id=?", (content, message_id))
            updated = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        item = dict(updated)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        return item

    @app.delete("/api/chats/{chat_id}/messages/{message_id}")
    def message_delete(chat_id: int, message_id: int) -> dict:
        require_chat(chat_id)
        with db() as conn:
            has_context = conn.execute("SELECT 1 FROM agent_history WHERE chat_id=? LIMIT 1", (chat_id,)).fetchone()
        if has_context or agent_runs.active(chat_id):
            raise HTTPException(409, "Нельзя менять использованный контекст. Создайте новую ветку")
        with db() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE id=? AND chat_id=?", (message_id, chat_id)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="message not found")
            conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
        return {"ok": True, "id": message_id}

    @app.post("/api/attachments")
    async def attachments_post(file: UploadFile, chat_id: int | None = None) -> dict:
        if chat_id is not None:
            require_chat(chat_id)
        try:
            item = await asyncio.to_thread(
                store_attachment, chat_id, file.filename or "attachment.bin", file.file
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            document = await asyncio.to_thread(index_document, item["stored_path"], item["id"])
            item["document"] = {"id": document["id"], "status": "indexed"}
        except Exception as exc:
            item["document"] = {"status": "index_failed", "error": str(exc)}
        await event_bus.publish(
            "attachment.stored", {"id": item["id"], "name": item["original_name"]}
        )
        return item

    @app.get("/api/attachments")
    def attachments_get(chat_id: int | None = None) -> list[dict]:
        return list_attachments(chat_id)

    def bind_attachments(chat_id: int, identifiers: list[int]) -> list[dict]:
        from koddi.attachments.service import require_attachment

        require_chat(chat_id)
        items = [require_attachment(identifier) for identifier in set(identifiers)]
        if any(item['chat_id'] not in {None,chat_id} for item in items):
            raise HTTPException(404, 'Вложение не найдено')
        owner = local_owner()
        with db() as conn:
            for item in items:
                changed = conn.execute(
                    'UPDATE attachments SET chat_id=? WHERE id=? AND (chat_id IS NULL OR chat_id=?) AND owner_id=? AND status<>?',
                    (chat_id,item['id'],chat_id,item['owner_id'],'deleted'),
                ).rowcount
                if not changed or local_owner()!=owner:
                    raise HTTPException(409, 'Вложение изменилось, прикрепите файл заново')
        return items

    @app.get("/api/attachments/{attachment_id}/content")
    def attachments_content(attachment_id: int) -> FileResponse:
        from koddi.attachments.service import require_attachment

        row = require_attachment(attachment_id)
        if row is None or not Path(row["stored_path"]).is_file():
            raise HTTPException(404, "Вложение не найдено")
        image_types = {"image/png", "image/jpeg", "image/webp", "image/gif"}
        return FileResponse(
            row["stored_path"],
            media_type=row["media_type"],
            filename=row["original_name"],
            content_disposition_type="inline" if row["media_type"] in image_types else "attachment",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.delete("/api/attachments/{attachment_id}")
    def attachments_delete(attachment_id: int) -> dict:
        return delete_attachment(attachment_id)

    @app.post("/api/documents/index")
    def documents_index(payload: dict[str, Any]) -> dict:
        return index_document(payload["path"], payload.get("attachment_id"))

    @app.get("/api/documents")
    def documents_get() -> list[dict]:
        return list_documents()

    @app.get("/api/speech/status")
    def speech_status() -> dict:
        return consent_status()

    @app.post("/api/speech/consent")
    def speech_consent(payload: dict[str, Any]) -> dict:
        return set_consent(bool(payload.get("consented")), payload.get("model", "tiny"))

    @app.get("/api/speech/model")
    def speech_model() -> dict:
        return whisper_model_status()

    @app.get("/api/logs")
    def logs_get(limit: int = 200) -> list[dict]:
        return list_logs(limit)

    @app.get("/api/logs/search")
    def logs_search(query: str, limit: int = 200) -> list[dict]:
        return search_logs(query, limit)

    @app.get("/api/logs/export")
    def logs_export(limit: int = 1000) -> JSONResponse:
        return JSONResponse(
            {"generated_by": "Dokky", "logs": list_logs(limit)},
            headers={"Content-Disposition": "attachment; filename=koddi-logs.json"},
        )

    @app.get("/api/changes/latest")
    def changes_latest() -> dict:
        return {"ok": True, "change_set": latest_change_set()}

    @app.post("/api/changes/rollback")
    def changes_rollback() -> dict:
        result = rollback_latest()
        log_event("INFO", "changes.rollback", "rollback requested", result)
        return result

    @app.get("/api/changes")
    def changes_get(project_id: int | None = None, limit: int = 100) -> list[dict]:
        return list_change_sets(project_id, min(max(limit, 1), 500))

    @app.post("/api/changes/{change_set_id}/rollback")
    def changes_rollback_selected(
        change_set_id: int, payload: dict[str, Any] | None = None
    ) -> dict:
        result = rollback_change_set(change_set_id, bool((payload or {}).get("force", False)))
        log_event(
            "INFO" if result.get("rolled_back") else "WARNING",
            "changes.rollback_selected",
            str(change_set_id),
            result,
        )
        return result

    @app.get("/api/backups")
    def backups_get(project_id: int | None = None) -> list[dict]:
        return list_backups(project_id)

    @app.post("/api/backups")
    def backups_create(payload: dict[str, Any]) -> dict:
        project = get_project(payload.get("project_id")) if payload.get("project_id") else None
        workspace = Path(project["path"]) if project else Path(str(payload.get("workspace") or ""))
        result = create_backup(
            workspace,
            project["id"] if project else None,
            str(payload.get("reason") or "manual backup"),
        )
        log_event("INFO" if result.get("ok") else "ERROR", "backup.create", str(workspace), result)
        return result

    @app.post("/api/backups/{backup_id}/restore")
    def backups_restore(backup_id: int) -> dict:
        result = restore_backup(backup_id)
        log_event("INFO" if result.get("ok") else "ERROR", "backup.restore", str(backup_id), result)
        return result

    @app.get("/api/tools/schema")
    def tools_schema() -> list[dict]:
        return [
            tool for tool in TOOL_SCHEMAS if tool["function"]["name"] not in AGENT_UNAVAILABLE_TOOLS
        ]

    @app.post("/api/tools/run")
    def tools_run(payload: ToolIn) -> dict:
        project = get_project(payload.project_id)
        workspace = Path(project["path"]) if project else None
        return ToolRunner(workspace=workspace, project_id=payload.project_id).run(
            payload.name, payload.arguments
        )

    @app.post("/api/speech/transcribe")
    async def speech_transcribe(payload: dict[str, str]) -> dict:
        return transcribe_audio(payload["path"])

    @app.get("/api/tasks")
    def tasks_get(project_id: int | None = None) -> list[dict]:
        return list_tasks(project_id)

    @app.post("/api/tasks")
    def tasks_post(payload: TaskIn) -> dict:
        return create_task(
            title=payload.title,
            project_id=payload.project_id,
            chat_id=payload.chat_id,
            mode=payload.mode,
            role=payload.role,
        )

    @app.put("/api/tasks/{task_id}/status")
    def tasks_status(task_id: int, payload: dict[str, Any]) -> dict:
        return update_task_status(task_id, payload["status"], payload.get("progress"))

    @app.post("/api/tasks/{task_id}/spawn")
    def tasks_spawn(task_id: int, payload: dict[str, str]) -> dict:
        return spawn_child(task_id, payload["title"], payload.get("role", "researcher"))

    @app.get("/api/mission-control")
    def mission_control(project_id: int | None = None) -> dict:
        return task_graph(project_id)

    async def launch_automation_agent(item: dict) -> dict:
        """Задание для агента из автоматизации: новый чат в проекте и запрос в очередь, как у навыка."""
        action = item["action"]
        if not get_project(action.get("project_id")):
            return {"ok": False, "error": "Проект автоматизации не найден"}
        chat = await chats_post(ChatIn(project_id=action["project_id"], title=("Автоматизация: " + item["name"])[:72], mode="development"))
        request_queue.add(chat["id"], MessageIn(content=action["prompt"], mode="development").model_dump(), str(uuid.uuid4()))
        return {"ok": True, "chat": chat}

    @app.get("/api/automations")
    def automations_get() -> list[dict]:
        return list_automations()

    @app.delete("/api/automations/{automation_id}")
    def automations_delete(automation_id: int) -> dict:
        with db() as conn:
            row = conn.execute(
                "SELECT status FROM automations WHERE id=?", (automation_id,)
            ).fetchone()
            if row and row["status"] == "running":
                raise HTTPException(409, "Автоматизация выполняется")
            conn.execute("DELETE FROM automations WHERE id=?", (automation_id,))
        return {"ok": True}

    @app.post("/api/automations")
    def automations_post(payload: AutomationIn) -> dict:
        try:
            return create_automation(payload.name, payload.action, payload.schedule)
        except AutomationError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/automations/{automation_id}")
    def automations_patch(automation_id: int, payload: AutomationPatch) -> dict:
        try:
            return update_automation(automation_id, payload.model_dump(exclude_unset=True))
        except AutomationError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/automations/{automation_id}/run")
    async def automations_run(automation_id: int) -> dict:
        return await run_any(automation_id, launch_automation_agent)

    @app.post("/api/automations/{automation_id}/cancel")
    def automations_cancel(automation_id: int) -> dict:
        return cancel_automation(automation_id)

    @app.post("/api/automations/assist")
    async def automations_assist(payload: AutomationAssistIn) -> dict:
        """Создание автоматизации с помощью ИИ: открывается чат, где агент по описанию сам вызывает automation.create."""
        project = get_project(payload.project_id)
        if not project:
            raise HTTPException(422, "Выберите проект")
        chat = await chats_post(ChatIn(project_id=project["id"], title="Новая автоматизация", mode="development"))
        task = (
            "Создай автоматизацию для приложения Докки по описанию пользователя.\n\n"
            f"Описание: {payload.request.strip()}\n"
            f"Проект: «{project['name']}», project_id={project['id']}.\n\n"
            "Порядок: при необходимости изучи проект, затем один раз вызови automation.create. "
            "Поля: name (короткое название по-русски); schedule (manual, hourly, interval@МИНУТЫ, daily@ЧЧ:ММ, weekdays@ЧЧ:ММ или "
            "weekly@ДЕНЬ@ЧЧ:ММ, где ДЕНЬ от 1 понедельника до 7 воскресенья, время местное; если пользователь не назвал время, выбери разумное и скажи об этом); "
            f"action: либо {{\"kind\": \"agent\", \"prompt\": \"...\", \"project_id\": {project['id']}}} - задание для агента, либо "
            f"{{\"kind\": \"command\", \"command\": \"...\", \"project_id\": {project['id']}}} - одна команда оболочки.\n"
            "Задание в prompt должно быть самодостаточным: оно выполняется без этого разговора, в новом чате, поэтому укажи, что именно проверить или сделать, "
            "где смотреть и какой результат вернуть. Команды выбирай безопасные и неразрушающие (тесты, сборка, проверки, статус git); ничего не удаляй и не отправляй наружу. "
            "Используй только automation.create и, если нужно, инструменты чтения проекта: MCP и сторонние инструменты не нужны. "
            "После создания в двух-трёх предложениях скажи, что создано и когда сработает. Не запускай автоматизацию сам."
        )
        queue = request_queue.add(chat["id"], MessageIn(content=task, mode="development").model_dump(), str(uuid.uuid4()))
        return {"chat": chat, "queue": queue}

    @app.get("/api/runs")
    def runs_get(project_id: int | None = None, limit: int = 100) -> list[dict]:
        """Запросы к агенту: что он делал, чем закончилось, где смотреть. Очередь показывается тут же."""
        owner = local_owner()
        limit = max(1, min(int(limit), 300))
        items: list[dict] = []
        with db() as conn:
            queued = conn.execute(
                "SELECT q.id, q.chat_id, q.payload_json, q.created_at, q.status, c.title, c.project_id, c.owner_id, p.name AS project_name "
                "FROM queued_requests q JOIN chats c ON c.id=q.chat_id LEFT JOIN projects p ON p.id=c.project_id "
                "WHERE q.status IN ('queued','failed') ORDER BY q.sequence"
            ).fetchall()
            turns = conn.execute(
                "SELECT t.id, t.chat_id, t.status, t.summary, t.error_json, t.created_at, t.completed_at, c.title, c.project_id, c.owner_id, p.name AS project_name, "
                "(SELECT content FROM messages m WHERE m.chat_id=t.chat_id AND m.role='user' AND json_extract(m.metadata_json,'$.turn_id')=t.id ORDER BY m.id LIMIT 1) AS request "
                "FROM turns t JOIN chats c ON c.id=t.chat_id LEFT JOIN projects p ON p.id=c.project_id ORDER BY t.id DESC LIMIT ?",
                (limit * 3,),
            ).fetchall()
        for row in queued:
            if not owns(row["owner_id"], owner) or (project_id and row["project_id"] != project_id):
                continue
            try:
                text = str(json.loads(row["payload_json"]).get("content") or "")
            except ValueError:
                text = ""
            items.append({"id": f"q:{row['id']}", "chat_id": row["chat_id"], "chat_title": row["title"], "project_id": row["project_id"],
                          "project_name": row["project_name"], "status": row["status"], "request": text[:400], "started_at": row["created_at"], "finished_at": None, "error": ""})
        for row in turns:
            if not owns(row["owner_id"], owner) or (project_id and row["project_id"] != project_id):
                continue
            try:
                error = str(json.loads(row["error_json"] or "{}").get("code") or "") if row["status"] == "failed" else ""
            except ValueError:
                error = ""
            items.append({"id": f"t:{row['id']}", "chat_id": row["chat_id"], "chat_title": row["title"], "project_id": row["project_id"],
                          "project_name": row["project_name"], "status": row["status"], "request": str(row["request"] or row["summary"] or "")[:400],
                          "started_at": row["created_at"], "finished_at": row["completed_at"], "error": error,
                          "summary": str(row["summary"] or "")[:300] if row["status"] == "failed" else ""})
            if len(items) >= limit + len(queued):
                break
        return items

    @app.get("/api/skills")
    def skills_get() -> list[dict]:
        return list_skills()

    @app.delete("/api/skills/{skill_id}")
    def skills_delete(skill_id: int) -> dict:
        try:
            delete_skill(skill_id)
        except SkillError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/skills")
    def skills_post(payload: SkillIn) -> dict:
        try:
            return create_skill(payload.name, payload.description, payload.workflow)
        except SkillError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/api/skills/{skill_id}")
    def skills_put(skill_id: int, payload: SkillIn) -> dict:
        try:
            return create_skill(payload.name, payload.description, payload.workflow, skill_id=skill_id)
        except SkillError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/skills/{skill_id}/enabled")
    def skills_enabled(skill_id: int, payload: SkillEnabledIn) -> dict:
        from koddi.skills.service import set_skill_enabled

        try:
            return set_skill_enabled(skill_id, payload.enabled)
        except SkillError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/projects/{project_id}/skills")
    def project_skills_get(project_id: int) -> dict:
        from koddi.skills.project import sync

        _, root = _map_project(project_id)
        return {"skills": sync(project_id, local_owner(), root)}

    @app.post("/api/projects/{project_id}/skills/decision")
    def project_skills_decide(project_id: int, payload: ProjectSkillDecisionIn) -> dict:
        from koddi.skills.project import ProjectSkillError, decide

        _, root = _map_project(project_id)
        try:
            return decide(project_id, local_owner(), root, payload.path, payload.sha256, payload.trust)
        except ProjectSkillError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/skills/{skill_id}/apply")
    def skills_apply(skill_id: int, payload: dict[str, str]) -> dict:
        return apply_skill(skill_id, payload.get("task", ""))

    skill_launch_lock=asyncio.Lock()

    @app.post('/api/skills/{skill_id}/execute')
    async def skills_execute(skill_id: int, payload: dict) -> dict:
        async with skill_launch_lock:
            return await execute_skill(skill_id,payload)

    async def execute_skill(skill_id: int, payload: dict) -> dict:
        project_id=payload.get('project_id')
        if type(project_id) is not int or not get_project(project_id):
            raise HTTPException(422,'Выберите проект для запуска навыка')
        skill=next((item for item in list_skills() if item['id']==skill_id),None)
        if not skill:
            raise HTTPException(404,'Навык не найден')
        task=str(payload.get('task') or skill['description'] or skill['name']).strip()
        if not task or len(task)>200000:
            raise HTTPException(422,'Недопустимое задание')
        queue_payload=MessageIn(content=task,mode='development',skill_ids=[skill_id]).model_dump()
        key=payload.get('request_id','')
        try:
            import uuid

            uuid.UUID(key)
        except (ValueError,TypeError,AttributeError) as exc:
            raise HTTPException(422,'Нужен UUID запроса') from exc
        with db() as conn:
            old=conn.execute('SELECT chat_id,owner_id FROM queued_requests WHERE id=?',(key,)).fetchone()
        if old:
            if old['owner_id']!=local_owner():
                raise HTTPException(409,'Идентификатор запроса уже использован')
            chat=require_chat(old['chat_id'])
            if chat['project_id']!=project_id:
                raise HTTPException(409,'Идентификатор запроса связан с другим проектом')
        else:
            chat=await chats_post(ChatIn(project_id=project_id,title=skill['name'][:72],mode='development'))
        queue=request_queue.add(chat['id'],queue_payload,key)
        return {'chat':chat,'queue':queue}

    @app.post("/api/review/diff")
    def review_diff(payload: dict[str, str]) -> dict:
        return diff_review(payload.get("diff", ""))

    @app.post("/api/review/safety")
    def review_safety(payload: dict[str, str]) -> dict:
        return safety_review(payload.get("diff", ""), payload.get("command", ""))

    @app.post("/api/review/secrets")
    def review_secrets(payload: dict[str, str]) -> dict:
        return secret_scan(payload.get("text", ""))

    @app.post("/api/browser/open")
    def api_browser_open(payload: dict[str, Any]) -> dict:
        """Ссылка из чата во вкладке браузерной панели; localhost - по той же политике, что у агента."""
        from koddi.policy.grants import private_network_allowed
        from koddi.policy.network import NetworkAccessError

        url = payload.get("url")
        project_id = payload.get("project_id")
        if not isinstance(url, str) or not re.match(r"^https?://", url, re.I) or len(url) > 4096:
            raise HTTPException(422, "нужна ссылка http(s)")
        if project_id is not None and not isinstance(project_id, int):
            raise HTTPException(422, "project_id: число")
        project = get_project(project_id)
        workspace = Path(project["path"]) if project else None
        loopback = is_loopback_url(url)
        if loopback and not local_preview_allowed(project_id if project else None, workspace):
            raise HTTPException(403, "localhost открывается, когда в проекте запущен сервер")
        try:
            return browser_open(
                url, project_id if project else None,
                allow_private=private_network_allowed(project_id) if project else False,
                local_preview=loopback, workspace=workspace,
            )
        except NetworkAccessError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/browser/live")
    def api_browser_live() -> list[dict]:
        return live_sessions()

    @app.get("/api/browser/{session_id}/frame")
    def api_browser_frame(session_id: int, quality: int = 55) -> dict:
        result = browser_frame(session_id, quality)
        if not result.get("ok"):
            raise HTTPException(404, result.get("error", "browser session not found"))
        return result

    @app.get("/api/browser/{session_id}/frame.jpg")
    def api_browser_frame_jpeg(session_id: int, quality: int = 60) -> Response:
        from urllib.parse import quote

        result = browser_frame(session_id, quality, raw=True)
        if not result.get("ok"):
            raise HTTPException(404, result.get("error", "browser session not found"))
        # адрес и заголовок в заголовках ответа: так кадр идёт одним запросом без JSON и base64
        return Response(result["data"], media_type="image/jpeg", headers={
            "cache-control": "no-store",
            "x-browser-url": quote(result["url"], safe=""),
            "x-browser-title": quote(result["title"], safe=""),
            "x-browser-size": f"{result['width']}x{result['height']}",
        })

    @app.post("/api/browser/{session_id}/input")
    def api_browser_input(session_id: int, payload: dict[str, Any]) -> dict:
        return browser_input(session_id, payload)

    @app.post("/api/browser/{session_id}/close")
    def api_browser_close(session_id: int) -> dict:
        return browser_close(session_id)

    @app.get("/api/browser/sessions")
    def api_browser_sessions(project_id: int | None = None) -> list[dict]:
        with db() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM browser_sessions WHERE project_id=? ORDER BY id DESC",
                    (project_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM browser_sessions ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]

    @app.post("/api/worktrees")
    def worktrees_create(payload: dict[str, Any]) -> dict:
        result = create_worktree(
            int(payload["project_id"]),
            str(payload["path"]),
            str(payload["branch"]),
            payload.get("task_id"),
        )
        log_event(
            "INFO" if result.get("ok") else "ERROR",
            "worktree.create",
            str(payload.get("path")),
            result,
        )
        return result

    @app.delete("/api/worktrees/{worktree_id}")
    def worktrees_remove(worktree_id: int, force: bool = False) -> dict:
        result = remove_worktree(worktree_id, force)
        log_event(
            "INFO" if result.get("ok") else "ERROR", "worktree.remove", str(worktree_id), result
        )
        return result

    @app.get("/api/web/fetch")
    async def api_web_fetch(url: str) -> dict:
        return await web_fetch(url)

    @app.get("/api/web/search")
    async def api_web_search(query: str, limit: int = 10) -> dict:
        return await web_search(query, limit)

    @app.get("/api/mcp/tools")
    def api_mcp_tools() -> dict:
        return mcp_tools_list()

    @app.post("/api/mcp/call")
    async def api_mcp_call(payload: dict[str, Any]) -> dict:
        return await mcp_tool_call(payload["server"], payload["name"], payload.get("arguments", {}))

    @app.post("/api/ide/open-file")
    def api_ide_open_file(payload: dict[str, Any]) -> dict:
        return open_file(payload["path"], payload.get("line"))

    @app.post("/api/ide/open-diff")
    def api_ide_open_diff(payload: dict[str, Any]) -> dict:
        return open_diff(payload.get("path"))

    @app.post("/api/terminal/open")
    def api_terminal_open(payload: dict[str, Any]) -> dict:
        return terminal_open(payload.get("workspace"))

    @app.post("/api/issues/import")
    async def api_issue_import(payload: dict[str, str]) -> dict:
        return await issue_import(payload["url"])

    @app.post("/api/issues/update")
    def api_issue_update(payload: dict[str, str]) -> dict:
        return issue_update(payload["issue_id"], payload["body"])

    @app.get("/api/app/diagnostics")
    async def app_diagnostics() -> dict:
        return await diagnostics()

    @app.get("/api/app/update-check")
    def app_update_check() -> dict:
        return update_check()

    def update_failure(exc: Exception) -> HTTPException:
        code = getattr(exc, "code", "failed")
        return HTTPException(503 if code in {"network", "unavailable"} else 409, str(exc))

    @app.get("/api/updates/status")
    def updates_status() -> dict:
        from koddi.updates import updater

        return updater.status()

    @app.post("/api/updates/check")
    async def updates_check() -> dict:
        from koddi.updates import UpdateError, updater

        try:
            return await updater.check(manual=True)
        except UpdateError as exc:
            raise update_failure(exc) from exc

    @app.post("/api/updates/download")
    async def updates_download() -> dict:
        from koddi.updates import updater

        if not updater.state.get("available"):
            raise HTTPException(409, "Новой версии нет")
        updater.start_download()
        return updater.status()

    @app.post("/api/updates/cancel")
    async def updates_cancel() -> dict:
        from koddi.updates import updater

        await updater.cancel_download()
        return updater.status()

    @app.post("/api/updates/skip")
    async def updates_skip() -> dict:
        from koddi.updates import updater

        available = updater.state.get("available")
        if not available:
            raise HTTPException(409, "Новой версии нет")
        if updater.state.get("status") in {"preparing", "installing"}:
            raise HTTPException(409, "Обновление уже готовится, дождитесь окончания")
        updater._save(skipped=available["version"])
        await updater.cancel_download()
        await updater._set(status="skipped")
        return updater.status()

    @app.put("/api/updates/settings")
    def updates_settings(payload: dict) -> dict:
        from koddi.updates import updater

        if set(payload) - {"auto_download"} or type(payload.get("auto_download", True)) is not bool:
            raise HTTPException(422, "auto_download: true или false")
        if "auto_download" in payload:
            updater._save(auto_download=payload["auto_download"])
        return updater.status()

    @app.post("/api/updates/install")
    def updates_install(payload: dict | None = None) -> dict:
        from koddi.updates import UpdateError, updater

        payload = payload or {}
        busy = agent_runs.active_ids()
        if busy and payload.get("force") is not True:
            raise HTTPException(409, "Агент ещё работает. Дождитесь конца задачи или остановите её")
        try:
            return updater.install()
        except UpdateError as exc:
            raise update_failure(exc) from exc

    from koddi.remote.mock_server import create_mock_app

    app.mount("/api/test-dokky", create_mock_app(), name="test-dokky")
    web_root = app_dir() / "koddi" / "desktop" / "web-next"
    if not web_root.is_dir():
        web_root = Path(__file__).resolve().parents[1] / "desktop" / "web-next"
    if web_root.is_dir():
        app.mount("/ui", UiStaticFiles(directory=str(web_root), html=True), name="ui")
    return app


app = create_app()

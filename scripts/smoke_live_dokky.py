"""Один платный Light-прогон, только после явного подтверждения пользователя."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("Нужен --confirm-live: сценарий расходует лимит Докки")

    from koddi.config import data_dir

    credentials_path = data_dir() / "dokky-session.dpapi"
    output = Path(tempfile.mkdtemp(prefix="koddi-live-light-"))
    profile = output / "profile"
    workspace = output / "workspace"
    workspace.mkdir()
    os.environ["KODDI_DATA_DIR"] = str(profile)
    os.environ.pop("KODDI_INTERNAL_TEST", None)
    logging.disable(logging.CRITICAL)

    # путь входа фиксируем до смены профиля; открытые токены не копируем на диск
    from koddi.dokky.client import CredentialStore, DokkyError, dokky
    from koddi.dokky import coding
    from koddi.storage import init_db
    from koddi.storage.db import db, set_setting
    from koddi.projects import add_project
    from koddi.agents.orchestrator import AgentOrchestrator

    class ExistingLogin(CredentialStore):
        @property
        def path(self):
            return credentials_path

    dokky.store = ExistingLogin()
    init_db()
    set_setting("coding_reasoning_mode", "light")
    safe_tools = {
        "plan.update", "task.complete", "task.ask_user", "project.instructions",
        "directory.list", "directory.tree", "file.create", "file.read", "file.read_many",
        "file.read_range", "file.read_chunk", "file.write", "file.write_patch",
        "file.apply_patch", "verification.run", "tool.output_read",
    }
    report = {"ok": False, "mode": "light", "tools": [], "model_steps": 0}
    cancellation = threading.Event()
    started = time.monotonic()
    try:
        if not dokky.store.load():
            raise RuntimeError("Нет сохранённого входа; генерация не запускалась")
        await dokky.bootstrap()
        project = add_project(str(workspace))
        with db() as conn:
            chat_id = int(conn.execute(
                "INSERT INTO chats(project_id,title,mode,model) VALUES (?,?,'development','dokky-code')",
                (project["id"], "Проверка Light"),
            ).lastrowid)
        session = await coding.ensure(chat_id)
        if session["session"]["reasoning_mode"] != "light":
            raise RuntimeError("Сервер не подтвердил Light; генерация не запускалась")
        stream = AgentOrchestrator().run(
            chat_id=chat_id, project_id=project["id"], workspace=workspace,
            user_message=(
                'Создай файл greeting.py: функция greet(text) возвращает "Привет!", '
                'если text.strip().lower() равно "привет", иначе возвращает "Не понял.". '
                'Только стандартный Python, без ввода при импорте и без зависимостей. '
                'Проверь синтаксис своим инструментом. Дай краткий итог. '
                'Не запускай команды и не используй интернет. Это одна маленькая задача.'
            ),
            mode="development", cancel_event=cancellation,
        )
        try:
            async with asyncio.timeout(180):
                async for event in stream:
                    kind = event.splitlines()[0][7:]
                    payload = json.loads(event.split("data: ", 1)[1])
                    if kind == "tool.call":
                        name = payload.get("name", "")
                        if name not in safe_tools:
                            raise RuntimeError("Тест остановлен: непредусмотренный инструмент")
                        report["tools"].append(name)
                        print("Действие:", name, flush=True)
                    elif kind == "agent.input_required":
                        if payload["kind"] == "approval":
                            answer = {"approved": payload.get("name") in safe_tools}
                        else:
                            answer = {"answer": "Выполни указанную маленькую задачу без уточнений."}
                        with db() as conn:
                            conn.execute("UPDATE agent_requests SET response_json=? WHERE id=?",
                                         (json.dumps(answer), payload["id"]))
                    elif kind == "agent.step":
                        report["model_steps"] += 1
                        if report["model_steps"] > 10:
                            raise RuntimeError("Тест остановлен: превышен бюджет короткого прогона")
                    elif kind == "turn.failed":
                        report["failure"] = str(payload.get("error", "Ошибка агента"))[:400]
        finally:
            cancellation.set()
            await stream.aclose()
        with db() as conn:
            report["status"] = conn.execute(
                "SELECT status FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)
            ).fetchone()[0]
            report["title_generated"] = conn.execute(
                "SELECT title FROM chats WHERE id=?", (chat_id,)
            ).fetchone()[0] != "Проверка Light"
        file = workspace / "greeting.py"
        report["file_created"] = file.is_file()
        report["syntax_ok"] = False
        if file.is_file():
            compile(file.read_text(encoding="utf-8"), str(file), "exec")
            report["syntax_ok"] = True
        report["ok"] = report["status"] == "completed" and report["syntax_ok"]
    except DokkyError as exc:
        report["failure"] = f"HTTP {exc.status}, код {exc.code}"
    except (RuntimeError, TimeoutError, SyntaxError) as exc:
        report["failure"] = str(exc)[:400]
    finally:
        cancellation.set()
        report["seconds"] = round(time.monotonic() - started, 1)
        with db() as conn:
            sessions = [json.loads(row[0])["id"] for row in conn.execute(
                "SELECT session_json FROM coding_sessions"
            )]
        report["server_cleanup_ok"] = True
        for session_id in sessions:
            try:
                await dokky.request(
                    "DELETE", "/coding/sessions/" + session_id, params={"confirm": "true"}
                )
            except DokkyError:
                report["server_cleanup_ok"] = False
        # удаляем только созданный этим сценарием профиль, не файлы проекта и не вход
        resolved = profile.resolve()
        if resolved.parent != output.resolve() or resolved.name != "profile":
            raise RuntimeError("Небезопасный путь очистки тестового профиля")
        shutil.rmtree(resolved, ignore_errors=False)
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(output, flush=True)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

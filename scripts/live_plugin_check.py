"""Короткий платный прогон плагина с GitHub: ставит EGM из каталога и запускает его команду drift-report.

Запускается только явно: --confirm-live. Вход читается без изменения исходного профиля, refresh запрещён,
профиль и проект временные. Ход обрывается по оценке расхода (--max-usd), шагам и времени.
Печатаются только поля отчёта: ни настроек, ни токенов в выводе нет.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from live_ub_check import cost  # noqa: E402

DESIGN = """# Cart design

`cart.py` defines `Cart` with fields `items` (list of prices) and `coupon` (optional code).
`Cart.total()` returns the sum of items rounded to 2 digits.
`Cart.apply_coupon(code)` accepts `SAVE10` and lowers the total by 10 percent; other codes raise `ValueError`.
"""
SOURCE = '''class Cart:
    def __init__(self):
        self.items = []

    def total(self):
        return round(sum(self.items), 2)
'''
PROMPT = "/egm:egm-drift-report docs"
SAFE = {
    "skill.load", "project.instructions", "directory.list", "directory.tree", "file.read", "file.read_many",
    "file.read_range", "file.metadata", "file.create", "file.write_patch", "file.edit", "search.text", "search.files",
    "symbol.search", "code.outline", "code.references", "context.retrieve", "project.map", "tool.output_read",
    "workspace.inspect", "plan.update", "task.complete",
}
# проект временный: команды только на чтение без цепочек, перенаправлений и подстановок можно пропустить
READ_ONLY_SHELL = re.compile(r"^\s*(ls|cat|head|tail|wc|find|grep|rg|pwd|tree|file|stat|date)(\s[^;&|<>`$()\n]*)?$")


def allowed(name: str, arguments: dict) -> bool:
    if name == "shell.run":
        command = str(arguments.get("command") or "")
        return bool(READ_ONLY_SHELL.match(command)) and "-exec" not in command and "-delete" not in command
    return name in SAFE


def read_only_login():
    from koddi.config import data_dir
    from koddi.dokky.client import CredentialStore, DokkyError, dokky

    original = data_dir()

    class ReadLogin(CredentialStore):
        @property
        def path(self):
            return original / ("dokky-session.dpapi" if os.name == "nt" else "dokky-session.keychain")

        def clear(self):
            pass

        def save(self, value):
            raise RuntimeError("прогон не меняет исходный вход")

    login = ReadLogin().load()
    if not login or login.get("expires_at", 0) <= time.time() + 90:
        return None

    class MemoryLogin:
        def load(self):
            fresh = ReadLogin().load()
            if fresh:
                login.update(fresh)
            return dict(login)

        def clear(self):
            login.clear()

        def save(self, value):
            raise RuntimeError("обновление токенов во время прогона запрещено")

    async def no_refresh(*_a, **_kw):
        raise DokkyError("срок входа истёк: прогон остановлен без обновления токенов", 401, "live_token_expired")

    return lambda: (setattr(dokky, "store", MemoryLogin()), setattr(dokky, "tokens", no_refresh))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--max-usd", type=float, default=0.10)
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--seconds", type=int, default=420)
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("Нужен --confirm-live: сценарий расходует лимит Докки")
    activate = read_only_login()
    if activate is None:
        print(json.dumps({"ok": False, "error": "нужен действующий вход; refresh не выполнялся"}, ensure_ascii=False))
        return 2
    output = Path(tempfile.mkdtemp(prefix="dokky-live-plugin-")).resolve()
    profile, workspace = output / "profile", output / "workspace"
    profile.mkdir()
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "cart.md").write_text(DESIGN, encoding="utf-8")
    (workspace / "cart.py").write_text(SOURCE, encoding="utf-8")

    os.environ["KODDI_DATA_DIR"] = str(profile)
    os.environ.pop("KODDI_INTERNAL_TEST", None)
    logging.disable(logging.CRITICAL)
    import koddi.storage.db as storage

    storage.DB_PATH = profile / "koddi.sqlite3"
    storage.init_db()
    activate()
    from koddi.dokky.client import dokky

    report: dict = {"ok": False, "workspace": str(workspace), "tools": [], "plugin_files": [], "steps": 0,
                    "estimated_usd": 0.0}
    cancellation = threading.Event()
    started = time.monotonic()
    try:
        await dokky.bootstrap()
        from koddi.policy.preferences import save_reasoning_mode

        save_reasoning_mode("light")
        user = str(storage.get_setting("dokky_user", {})["id"])
        storage.set_setting("permission_profile:" + user, "trusted")
        storage.set_setting("coding_keepalive:" + user, False)
        storage.set_setting("computer_use_enabled:" + user, False)
        from koddi.plugins import catalog, store

        entry = catalog.BY_ID["egm"]
        installed = await asyncio.to_thread(store.install, entry["repo"], user, path=entry["path"], pinned=entry["commit"],
                                            catalog_id="egm")
        report["plugin"] = {key: installed["plugin"][key] for key in ("name", "commit", "file_count")}
        from koddi.skills.service import skill_block, slash_skill

        slash = slash_skill(PROMPT)
        skills_text, names = skill_block([slash] if slash else [])
        report["attached"] = names
        from koddi.agents.orchestrator import AgentOrchestrator
        from koddi.dokky import coding
        from koddi.projects import add_project

        project = add_project(str(workspace))
        with storage.db() as conn:
            chat_id = conn.execute(
                "INSERT INTO chats(project_id,title,mode,model,owner_id) VALUES (?,?,'development','dokky-code',?)",
                (project["id"], "Проверка плагина", coding.owner())).lastrowid
        session = await coding.ensure(chat_id)
        if session["session"]["reasoning_mode"] != "light":
            raise RuntimeError("сервер не подтвердил Light; генерация не запускалась")
        message = skills_text + "\n\nЗапрос пользователя:\n" + PROMPT
        stream = AgentOrchestrator().run(chat_id=chat_id, project_id=project["id"], workspace=workspace,
                                         user_message=message, mode="development", cancel_event=cancellation)
        try:
            async with asyncio.timeout(args.seconds):
                async for event in stream:
                    kind = event.splitlines()[0][7:]
                    payload = json.loads(event.split("data: ", 1)[1])
                    if kind == "agent.usage" and isinstance(payload.get("usage"), dict):
                        report["estimated_usd"] = round(report["estimated_usd"] + cost(payload["usage"]), 5)
                        if report["estimated_usd"] >= args.max_usd:
                            raise RuntimeError("остановлено по бюджету расхода")
                    elif kind == "agent.step":
                        report["steps"] += 1
                        if report["steps"] > args.max_steps:
                            raise RuntimeError("остановлено по числу шагов")
                    elif kind == "tool.call":
                        name = payload.get("name", "")
                        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
                        if not allowed(name, arguments):
                            shown = json.dumps(arguments, ensure_ascii=False)[:300]
                            raise RuntimeError(f"остановлено до непредусмотренного инструмента: {name} {shown}")
                        report["tools"].append(name)
                        if name == "skill.load" and arguments.get("file"):
                            report["plugin_files"].append(str(arguments["file"])[:200])
                        print(f"{time.monotonic() - started:6.1f} с  {name}  ~${report['estimated_usd']:.4f}", flush=True)
                    elif kind == "agent.input_required":
                        approved = payload.get("kind") == "approval" and allowed(
                            str(payload.get("name") or ""),
                            payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {})
                        answer = {"approved": approved} if payload.get("kind") == "approval" else \
                            {"answer": "Каталог - docs, проверь его целиком без уточнений."}
                        with storage.db() as conn:
                            conn.execute("UPDATE agent_requests SET response_json=? WHERE id=?",
                                         (json.dumps(answer), payload["id"]))
                    elif kind in {"turn.failed", "turn.cancelled", "turn.completed"}:
                        report["status"] = kind[5:]
                        if kind == "turn.failed":
                            report["error"] = str(payload.get("error"))[:300]
                        report["report_tail"] = str(payload.get("report") or "")[:600]
        except (RuntimeError, TimeoutError) as exc:
            report["stopped"] = str(exc)[:200]
        finally:
            cancellation.set()
            await stream.aclose()
        drift = sorted(workspace.glob("*DRIFT_REPORT*.md")) + sorted(workspace.glob("**/*DRIFT_REPORT*.md"))
        text = drift[0].read_text(encoding="utf-8") if drift else ""
        report["drift_report"] = drift[0].name if drift else None
        report["drift_mentions_coupon"] = "apply_coupon" in text or "coupon" in text.lower()
        report["ok"] = report.get("status") == "completed" and bool(drift) and report["drift_mentions_coupon"]
    finally:
        report["seconds"] = round(time.monotonic() - started, 1)
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

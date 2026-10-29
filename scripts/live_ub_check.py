"""Короткий платный прогон UB-режима: агент дописывает функцию с тестами, проверяет qa.run и quality.run.

Запускается только явно: --confirm-live. Вход читается без изменения исходного профиля, refresh запрещён.
Расход оценивается по токенам сверху (цены с запасом) и ход обрывается до --max-usd. Печатаются только
поля отчёта: ни настроек, ни токенов входа в выводе нет.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# долларов за 1M токенов, с запасом выше прайса DeepSeek: оценка - потолок, а не точная цена
PRICE_MISS, PRICE_HIT, PRICE_OUT = 0.60, 0.07, 2.20

SOURCE = '''def total(prices):
    """Сумма цен корзины."""
    return round(sum(prices), 2)
'''
TESTS = '''from shop import total


def test_total():
    assert total([1.5, 2.25]) == 3.75
'''
PROMPT = (
    "В shop.py добавь функцию discount(price, percent): цена со скидкой, округление до 2 знаков; "
    "percent вне 0..100 или отрицательная цена - ValueError. Сначала напиши тесты в tests/test_shop.py, "
    "которые падают, затем реализуй. Проверь qa.run, затем quality.run по изменённым файлам. "
    "Не устанавливай зависимости, не используй интернет и git. Закончи кратким итогом."
)
SAFE = {
    "plan.update", "task.complete", "project.instructions", "directory.list", "directory.tree", "file.read",
    "file.read_many", "file.read_range", "file.read_chunk", "file.metadata", "file.create", "file.write",
    "file.write_patch", "file.edit", "file.search_replace", "file.apply_patch", "patch.apply", "patch.preview",
    "search.text", "search.files", "tool.output_read", "qa.run", "quality.run", "verification.run",
    "code.outline", "code.symbols", "code.references", "context.retrieve", "git.diff", "git.status",
}


def cost(usage: dict) -> float:
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    hit = int(usage.get("prompt_cache_hit_tokens") or (details.get("cached_tokens") if isinstance(details, dict) else 0) or 0)
    out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    return (max(0, prompt - hit) * PRICE_MISS + hit * PRICE_HIT + out * PRICE_OUT) / 1_000_000


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--max-usd", type=float, default=0.10)
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--seconds", type=int, default=420)
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("Нужен --confirm-live: сценарий расходует лимит Докки")

    from koddi.config import data_dir

    original = data_dir()
    from koddi.dokky.client import CredentialStore, DokkyError, dokky

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
        print(json.dumps({"ok": False, "error": "нужен действующий вход; refresh не выполнялся"}, ensure_ascii=False))
        return 2
    output = Path(tempfile.mkdtemp(prefix="dokky-live-ub-")).resolve()
    profile, workspace = output / "profile", output / "workspace"
    profile.mkdir()
    (workspace / "tests").mkdir(parents=True)
    (workspace / "shop.py").write_text(SOURCE, encoding="utf-8")
    (workspace / "tests" / "test_shop.py").write_text(TESTS, encoding="utf-8")
    (workspace / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n', encoding="utf-8")
    for command in (["init", "-q", "-b", "main"], ["add", "-A"],
                    ["-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "base"]):
        subprocess.run(["git", *command], cwd=workspace, check=True, capture_output=True)

    os.environ["KODDI_DATA_DIR"] = str(profile)
    os.environ.pop("KODDI_INTERNAL_TEST", None)
    logging.disable(logging.CRITICAL)
    import koddi.storage.db as storage

    storage.DB_PATH = profile / "koddi.sqlite3"
    storage.init_db()

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

    dokky.store = MemoryLogin()
    dokky.tokens = no_refresh
    report: dict = {"ok": False, "workspace": str(workspace), "tools": [], "steps": 0, "estimated_usd": 0.0,
                    "requests_with_usage": 0, "gates": [], "qa": []}
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
        from koddi.agents.orchestrator import AgentOrchestrator
        from koddi.dokky import coding
        from koddi.projects import add_project

        project = add_project(str(workspace))
        with storage.db() as conn:
            chat_id = conn.execute(
                "INSERT INTO chats(project_id,title,mode,model,owner_id) VALUES (?,?,'development','dokky-code',?)",
                (project["id"], "Проверка UB", coding.owner())).lastrowid
        session = await coding.ensure(chat_id)
        if session["session"]["reasoning_mode"] != "light":
            raise RuntimeError("сервер не подтвердил Light; генерация не запускалась")
        stream = AgentOrchestrator().run(chat_id=chat_id, project_id=project["id"], workspace=workspace,
                                         user_message=PROMPT, mode="development", cancel_event=cancellation)
        try:
            async with asyncio.timeout(args.seconds):
                async for event in stream:
                    kind = event.splitlines()[0][7:]
                    payload = json.loads(event.split("data: ", 1)[1])
                    if kind == "agent.usage" and isinstance(payload.get("usage"), dict):
                        report["requests_with_usage"] += 1
                        report["estimated_usd"] = round(report["estimated_usd"] + cost(payload["usage"]), 5)
                        if report["estimated_usd"] >= args.max_usd:
                            raise RuntimeError("остановлено по бюджету расхода")
                    elif kind == "agent.step":
                        report["steps"] += 1
                        if report["steps"] > args.max_steps:
                            raise RuntimeError("остановлено по числу шагов")
                    elif kind == "tool.call":
                        name = payload.get("name", "")
                        if name not in SAFE:
                            raise RuntimeError(f"остановлено до непредусмотренного инструмента: {name}")
                        report["tools"].append(name)
                        print(f"{time.monotonic() - started:6.1f} с  {name}  ~${report['estimated_usd']:.4f}", flush=True)
                    elif kind == "tool.result":
                        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                        if payload.get("name") == "qa.run" or "state" in result and "attempt" in result:
                            report["qa"].append({key: result.get(key) for key in ("status", "state", "stale", "attempt")})
                        for gate in result.get("gates") or []:
                            if isinstance(gate, dict):
                                report["gates"].append({key: gate.get(key) for key in ("gate", "status", "error")})
                    elif kind == "agent.input_required":
                        approved = payload.get("kind") == "approval" and payload.get("name") in SAFE
                        answer = {"approved": approved} if payload.get("kind") == "approval" else \
                            {"answer": "Выполни задачу без уточнений."}
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
        with storage.db() as conn:
            row = conn.execute("SELECT metrics_json FROM turn_metrics ORDER BY turn_id DESC LIMIT 1").fetchone()
            row_quality = conn.execute("SELECT state FROM turn_quality ORDER BY turn_id DESC LIMIT 1").fetchone()
            runs = conn.execute("SELECT gate,status FROM quality_runs ORDER BY id").fetchall()
        metrics = json.loads(row[0]) if row else {}
        report["metrics"] = {key: metrics.get(key) for key in (
            "llm_requests", "input_tokens", "cached_input_tokens", "output_tokens", "quality_gates", "quality_metrics")}
        report["quality_state"] = row_quality[0] if row_quality else None
        report["quality_runs"] = [dict(item) for item in runs]
        tests = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=workspace,
                               capture_output=True, text=True, timeout=120)
        report["pytest_after"] = tests.stdout.strip().splitlines()[-1] if tests.stdout.strip() else tests.returncode
        report["shop_has_discount"] = "def discount" in (workspace / "shop.py").read_text(encoding="utf-8")
        report["ok"] = report.get("status") == "completed" and tests.returncode == 0 and report["shop_has_discount"]
    finally:
        report["seconds"] = round(time.monotonic() - started, 1)
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

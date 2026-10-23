"""Ручная проверка с настоящим AI на локальном бэкенде под вашим аккаунтом."""

import asyncio
import ast
import json
import tempfile
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8878"


async def main():
    workspace = Path(tempfile.mkdtemp(prefix="koddi-live-plan-"))
    async with httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(20, read=240)) as client:
        session = (await client.get("/api/dokky/session")).json()
        if not session.get("authenticated"):
            raise RuntimeError("Требуется вход в Докки")
        project = await client.post(
            "/api/projects", json={"path": str(workspace), "name": "Проверка планирования"}
        )
        project.raise_for_status()
        chat = await client.post(
            "/api/chats",
            json={
                "project_id": project.json()["id"],
                "mode": "plan",
                "model": "dokky-code",
                "title": "Проверка плана",
            },
        )
        chat.raise_for_status()
        chat_id = chat.json()["id"]
        actions = []
        finished = asyncio.Event()

        async def approvals():
            while not finished.is_set():
                requests = (await client.get("/api/agent/requests")).json()
                for request in requests:
                    if request["chat_id"] != chat_id:
                        continue
                    payload = request["payload"]
                    if request["kind"] == "approval":
                        # файловые операции ToolRunner и так держит внутри тестовой папки
                        answer = {"approved": str(payload.get("name", "")).startswith("file.")}
                    else:
                        answer = {
                            "answer": "Создай только bubble_sort.py. Дополнительные библиотеки и команды не нужны."
                        }
                    reply = await client.post(
                        f"/api/agent/requests/{request['id']}/respond", json=answer
                    )
                    reply.raise_for_status()
                await asyncio.sleep(0.2)

        async def consume(path, body):
            terminal = None
            async with client.stream("POST", path, json=body) as response:
                if response.is_error:
                    await response.aread()
                response.raise_for_status()
                kind = ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        kind = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:])
                        if kind == "tool.call":
                            actions.append(data["name"])
                        elif kind in {"turn.completed", "turn.failed", "turn.cancelled"}:
                            terminal = kind
                            if kind == "turn.failed":
                                raise RuntimeError(data.get("error", "Ошибка агента"))
            assert terminal == "turn.completed", terminal

        waiter = asyncio.create_task(approvals())
        try:
            async with asyncio.timeout(240):
                await consume(
                    f"/api/chats/{chat_id}/messages",
                    {
                        "mode": "plan",
                        "model": "dokky-code",
                        "content": "Подготовь короткий план создания bubble_sort.py с функцией bubble_sort(values), возвращающей новый отсортированный список. Исходный список не менять. Используй plan.update. Пока ничего не создавай; опубликуй готовый план и остановись.",
                    },
                )
                assert not (workspace / "bubble_sort.py").exists()
                snapshot = (await client.get(f"/api/chats/{chat_id}/plan")).json()
                assert snapshot["ready"] and snapshot["steps"], snapshot
                await consume(
                    f"/api/chats/{chat_id}/plan/execute", {"turn_id": snapshot["turn_id"]}
                )
                file = workspace / "bubble_sort.py"
                assert file.is_file(), "Агент не создал файл"
                compile(file.read_text(encoding="utf-8"), str(file), "exec")
                source = ast.parse(file.read_text(encoding="utf-8"))
                assert all(
                    isinstance(node, ast.FunctionDef)
                    or (
                        isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    )
                    for node in source.body
                )
                assert not any(
                    isinstance(
                        node, (ast.Import, ast.ImportFrom, ast.Attribute, ast.Global, ast.Nonlocal)
                    )
                    for node in ast.walk(source)
                )
                assert all(
                    isinstance(node.func, ast.Name)
                    and node.func.id in {"len", "list", "range", "bubble_sort"}
                    for node in ast.walk(source)
                    if isinstance(node, ast.Call)
                )
                namespace = {"__builtins__": {"len": len, "list": list, "range": range}}
                exec(compile(source, str(file), "exec"), namespace)
                for values in ([], [1], [3, 1, 2], [5, -1, 5, 0], [9, 8, 7, 6, 5, 4, 3, 2, 1]):
                    before = values.copy()
                    assert namespace["bubble_sort"](values) == sorted(before)
                    assert values == before
                messages = (await client.get(f"/api/chats/{chat_id}/messages")).json()
                assert any(
                    message["role"] == "user" and message["content"] == "Реализуй план"
                    for message in messages
                )
                print(
                    json.dumps(
                        {
                            "workspace": str(workspace),
                            "chat_id": chat_id,
                            "actions": actions,
                            "file_created": True,
                            "syntax_valid": True,
                        },
                        ensure_ascii=False,
                    )
                )
        finally:
            finished.set()
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            await client.post(f"/api/chats/{chat_id}/turn/cancel")


if __name__ == "__main__":
    asyncio.run(main())
